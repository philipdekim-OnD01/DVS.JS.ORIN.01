#include "decoder.h"
#include <algorithm>
#include <bit>
#include <utility>

namespace delta {

// RAW2 원본 = 열의 연속. 열 하나 = 180 B = 세로 720화소 x 2비트 (0 이벤트 없음,
// 1 ON, 2 OFF, 3 미할당). 연속된 머리말 뒤 첫 데이터 열부터 다음 머리말까지
// 읽음. 머리말 개수는 0x3907 설정에 따라 달라지므로 고정하지 않음.
// 머리말 열(데이터시트 Figure 46, 현재 저장 바이트의 big-endian 단어 45개):
//   0 0xCCCCCCCC, 1 머리말 크기 16 (상위 8비트), 2 종류 0x00010000,
//   3 프레임 번호 (프레임마다 +1), 4~44 기준(22비트 ms) | 보조(10비트 us)
// .117 표본에서 sub가 머리말 도중 바뀜. 첫 변화의 원시 word가 머리말 끝까지
// 유지되고 최소2회 관측될 때만 간격 계산 후보로 사용. 이는 물리 시각 검증이 아님.
// sub 변화가 없는 metadata도 허용: Legacy처럼 마지막 머리말의 첫 timestamp를 후보로
// 사용하되 프레임 간격은 미확정(0)으로 표시. ref-only 변화도 정상 constant로 단정하지 않음.
// 이 선택 규칙과 ref(ms)/sub(us)의 물리적 시각 의미는 별도 검증이 필요함.
// Jetson MIPI 저장 표본은32비트 big-endian word. word의 낮은2비트부터 y 증가.
// wire byte를 그대로LSB-first로 읽으면16행마다byte 순서가 뒤집혀 줄어긋남이 생김.
// Legacy USB parser의byte순서 가정을 이 경로에 적용하지 않음.
// 저장 표본: embedded16에서는959열, embedded1에서는960열을 관측함.
// 959열은 별도 진단에 남기며 센서 규격과의 차이/광학 좌표 대응은 미확정.
constexpr int kGroups = static_cast<int>(kImageHeight / 8); // 90

static std::uint32_t BigEndian32(const std::uint8_t *b) noexcept {
  return (std::uint32_t{b[0]} << 24) | (std::uint32_t{b[1]} << 16) | (std::uint32_t{b[2]} << 8) |
         b[3];
}

// 짝수 비트(0, 2, ..., 14)를 비트 0..7 로 모음
static unsigned Compact(unsigned v) noexcept {
  v &= 0x5555u;
  v = (v | (v >> 1)) & 0x3333u;
  v = (v | (v >> 2)) & 0x0F0Fu;
  return (v | (v >> 4)) & 0x00FFu;
}

Decoder::Impl::Raw2::Raw2(std::unique_ptr<Pipe> upstream) : Decoder::Impl(std::move(upstream)) {}

void Decoder::Impl::Raw2::Reset() noexcept {
  synced_ = false;
  signature_ = 0;
  filled_ = 0;
  embedded_ = false;
  boundary_seen_ = false;
  metadata_ok_ = false;
  started_ = false;
  selected_repeats_ = 0;
  timestamp_ambiguous_ = false;
  data_ = -1;
  have_previous_ = false;
  previous_time_selected_ = false;
}

void Decoder::Impl::Raw2::Parse(std::span<const std::byte> raw) noexcept {
  const auto *p = reinterpret_cast<const std::uint8_t *>(raw.data());
  const std::size_t n = raw.size();
  std::size_t i = 0;
  while (i < n) {
    if (!synced_) { // 머리말 열의 시작(0xCC 4개)까지 버림
      if (p[i++] != 0xCC) {
        signature_ = 0;
      } else if (++signature_ == 4) {
        synced_ = true;
        std::fill_n(column_.begin(), 4, std::uint8_t{0xCC});
        filled_ = 4;
      }
      continue;
    }
    if (!filled_ && n - i >= kColumnBytes) { // 버퍼 안에 통째로 있는 열
      Column(p + i);
      i += kColumnBytes;
      continue;
    }
    const std::size_t take = std::min(kColumnBytes - filled_, n - i);
    std::copy(p + i, p + i + take, column_.begin() + static_cast<std::ptrdiff_t>(filled_));
    filled_ += take;
    i += take;
    if (filled_ == kColumnBytes) {
      filled_ = 0;
      Column(column_.data());
    }
  }
}

void Decoder::Impl::Raw2::Column(const std::uint8_t *c) noexcept {
  if (c[0] == 0xCC && c[1] == 0xCC && c[2] == 0xCC && c[3] == 0xCC) { // 머리말 열
    if (c[4] != 16 || c[8] || c[9] != 1 || c[10] || c[11]) { // 머리말 크기, 종류
      ++work_.invalid;
      Reset(); // 열 경계를 다시 찾음
      return;
    }
    const std::uint32_t number = BigEndian32(c + 12);
    const std::uint32_t first_timestamp = BigEndian32(c + 16);
    if (!embedded_) {
      if (data_ >= 0)
        CompleteFrame();
      embedded_ = true;
      metadata_ok_ = boundary_seen_;
      data_ = -1;
      number_ = number;
      previous_sub_ = first_timestamp & 0x3FFu;
      started_ = false;
      selected_repeats_ = 0;
      timestamp_ambiguous_ = false;
    } else if (number != number_) {
      ++work_.invalid;
      metadata_ok_ = false; // 같은 묶음에서 번호가 다르면 전체를 버림
      have_previous_ = false;
      return;
    }
    fallback_us_ = std::uint64_t{first_timestamp >> 10} * 1000 +
                   (first_timestamp & 0x3FFu);
    for (std::size_t w = 16; w < kColumnBytes; w += 4) {
      const std::uint32_t timestamp = BigEndian32(c + w);
      const std::uint32_t ref = timestamp >> 10, sub = timestamp & 0x3FFu;
      if (sub >= 1000) { // 현재 .117 profile: ref 1ms, sub 0..999us
        ++work_.invalid;
        metadata_ok_ = false;
        have_previous_ = false;
        return;
      }
      if (started_) {
        if (timestamp != selected_word_)
          timestamp_ambiguous_ = true;
        else if (selected_repeats_ < 2)
          ++selected_repeats_;
        continue;
      }
      if (sub == previous_sub_)
        continue;
      // 서로 다른 word의 ref/sub를 조합하거나 ±1000으로 시간 순서를 맞추지 않음.
      selected_word_ = timestamp;
      selected_repeats_ = 1;
      start_us_ = std::uint64_t{ref} * 1000 + sub;
      started_ = true;
    }
    return;
  }
  if (embedded_) { // 첫 non-header가 데이터 시작. 개수와 무관하게 현재 열부터 처리.
    embedded_ = false;
    if (metadata_ok_) {
      if (!started_)
        start_us_ = fallback_us_;
      data_ = 0;
      groups_.clear();
    }
  }
  boundary_seen_ = true;
  if (data_ < 0)
    return; // 머리말을 다 보지 못한 프레임 (동기화 직후 등)
  if (data_ == kMaxDataColumns) { // 다음 header 없이961번째 데이터: 이 영역 전체 폐기
    ++work_.invalid;
    have_previous_ = false;
    data_ = -1;
    return;
  }
  const int s = data_++;
  const auto x = static_cast<std::uint16_t>(s);
  // BE32 word의 하위8화소, 상위8화소 순서. 물리byte index XOR3으로
  // word 안의byte순서만 바꿈. 각byte 내2bit code의 bit 순서는 유지.
  for (int g = 0; g < kGroups; ++g) {
    const unsigned v = c[(2 * g) ^ 3] | (unsigned{c[(2 * g + 1) ^ 3]} << 8);
    if (!v)
      continue;
    const unsigned lo = v & 0x5555u, hi = (v >> 1) & 0x5555u;
    const unsigned on = Compact(lo & ~hi), off = Compact(hi & ~lo);
    work_.invalid += static_cast<std::uint64_t>(std::popcount(lo & hi));
    const auto y = static_cast<std::uint16_t>(8 * g);
    if (on) {
      groups_.push_back({.x = x, .y = y, .mask = static_cast<std::uint8_t>(on), .polarity = 0});
      work_.on += static_cast<std::uint64_t>(std::popcount(on));
    }
    if (off) {
      groups_.push_back({.x = x, .y = y, .mask = static_cast<std::uint8_t>(off), .polarity = 1});
      work_.off += static_cast<std::uint64_t>(std::popcount(off));
    }
    work_.events += static_cast<std::uint64_t>(std::popcount(on) + std::popcount(off));
  }
}

void Decoder::Impl::Raw2::CompleteFrame() noexcept {
  if (data_ < kMinDataColumns || data_ > kMaxDataColumns) {
    ++work_.invalid;
    have_previous_ = false;
    previous_time_selected_ = false;
    return;
  }
  // 프레임 완성: 바로 앞에 끝낸 프레임 다음 번호면 그 사이 간격
  if (have_previous_ && number_ != previous_number_ + 1)
    ++work_.frame_gaps;
  std::uint32_t period = 0;
  bool selected = started_ && !timestamp_ambiguous_ && selected_repeats_ >= 2;
  if (have_previous_ && number_ == previous_number_ + 1) {
    const std::uint64_t delta = (start_us_ + kTimestampWrap - previous_us_) % kTimestampWrap;
    // 작은 역행이 약70분의 거짓 간격이 되지 않도록 half-range 순서 규칙 적용.
    // 0 또는 반주기 이상 차이는 미확정이며, 센서 FPS 제한을 뜻하지 않음.
    const bool forward = delta && delta < kTimestampWrap / 2;
    if (!forward)
      ++work_.raw2_timestamp_order_errors; // fallback 후보의 역행/동일값도 별도 관측
    if (selected && previous_time_selected_) {
      if (forward)
        period = static_cast<std::uint32_t>(delta);
      else
        selected = false; // 역행한 후보를 다음 간격의 기준점으로 바로 사용하지 않음
    }
  }
  if (!started_)
    ++work_.raw2_timestamp_fallbacks;
  else if (timestamp_ambiguous_ || selected_repeats_ < 2)
    ++work_.raw2_timestamp_ambiguous;
  if (data_ < kMaxDataColumns)
    ++work_.raw2_short_frames;
  Finish(start_us_, period);
  have_previous_ = true;
  previous_time_selected_ = selected;
  previous_number_ = number_;
  previous_us_ = start_us_;
}

} // namespace delta
