#include "decoder.h"
#include <bit>
#include <utility>

namespace delta {

// 원본(MIPI) 버퍼 = 4바이트 단어(big-endian 비트 배치)의 연속. 단어 종류:
//   00000000             : 패딩
//   b0 bit7 = 1          : 이벤트 그룹 (현재 컬럼에서 세로 8화소 그룹 2개)
//   b0 & 0x7C == 4       : 컬럼 주소 (x, mo, 센서 프레임 번호, epoch 시작)
//   b0 & 0x7C == 8       : 타임스탬프 (b1 bit7: 0 기준 22비트 ms / 1 보조 10비트 us)
//   b0 & 0x7C == 12      : 센서 프레임 끝
// 센서 프레임 하나 = [기준][보조 타임스탬프][컬럼, 이벤트 그룹 ...][프레임 끝].
// b0 bit1 = CID. 프레임은 CID 0 만 내보냅니다.
constexpr int kGroups = static_cast<int>(kImageHeight / 8); // 90

Decoder::Impl::GroupAer::GroupAer(std::unique_ptr<Pipe> upstream)
    : Decoder::Impl(std::move(upstream)) {}

void Decoder::Impl::GroupAer::Reset() noexcept {
  cid_[0] = Context();
  cid_[1] = Context();
  building_ = false;
  previous_ = -1;
}

void Decoder::Impl::GroupAer::Parse(std::span<const std::byte> raw) noexcept {
  const auto *words = reinterpret_cast<const std::uint8_t *>(raw.data());
  for (std::size_t i = 0; i + 4 <= raw.size(); i += 4)
    Word(words + i);
}

void Decoder::Impl::GroupAer::Word(const std::uint8_t *b) noexcept {
  if (!(b[0] | b[1] | b[2] | b[3]))
    return; // 패딩
  const unsigned cid = (b[0] >> 1) & 1u;
  const unsigned tag = b[0] & 0x7Cu;
  Context &s = cid_[cid];

  if (b[0] & 0x80u) { // 이벤트 그룹
    const int delta = static_cast<int>(tag >> 2);
    const int g1 = (b[1] >> 2) | ((b[0] & 1) << 6);
    const int g2 = s.mo ? g1 - delta : g1 + delta;
    // delta 0: 같은 그룹의 ON(b3)/OFF(b2) 마스크. 극성 10, 마스크 겹침 없음
    if (!delta && ((b[1] & 3) != 2 || (b[2] & b[3]))) {
      ++work_.invalid;
      return;
    }
    if (s.x < 0 || !s.ref_ok || !s.sub_ok || !s.epoch_ready) {
      ++work_.unsynced_groups;
      return;
    }
    const int groups[2] = {g1, g2};
    const unsigned masks[2] = {b[3], b[2]};
    const unsigned polarity[2] = {b[1] & 1u, (b[1] >> 1) & 1u};
    for (unsigned k = 0; k < 2; ++k) {
      if (!masks[k])
        continue;
      if (groups[k] < 0 || groups[k] >= kGroups) {
        ++work_.invalid;
        continue;
      }
      const auto n = static_cast<std::uint64_t>(std::popcount(masks[k]));
      work_.events += n;
      (polarity[k] ? work_.off : work_.on) += n;
      if (cid) {
        work_.cid1_events += n;
        continue;
      }
      if (building_)
        groups_.push_back({.x = static_cast<std::uint16_t>(s.x),
                           .y = static_cast<std::uint16_t>(groups[k] * 8),
                           .mask = static_cast<std::uint8_t>(masks[k]),
                           .polarity = static_cast<std::uint8_t>(polarity[k])});
    }
  } else if (tag == 8) { // 타임스탬프
    if (b[1] & 0x80u) { // 보조 (0..999)
      if ((b[0] & 1) || (b[1] & 0x7F) || (b[2] & 0xFC)) {
        ++work_.invalid;
        s.sub_ok = false;
        return;
      }
      const unsigned sub = ((b[2] & 3u) << 8) | b[3];
      s.sub_ok = s.ref_ok && sub <= 999;
      if (sub > 999)
        ++work_.invalid;
      if (!cid) { // 새 센서 프레임 시작 (끝을 못 본 앞 프레임은 버림)
        building_ = s.sub_ok;
        start_us_ = std::uint64_t{s.ref} * 1000 + sub;
        groups_.clear();
      }
    } else { // 기준
      if ((b[0] & 1) || (b[1] & 0x40)) {
        ++work_.invalid;
        s.ref_ok = false;
        return;
      }
      s.ref = ((b[1] & 0x3Fu) << 16) | (b[2] << 8) | b[3];
      s.ref_ok = true;
      s.sub_ok = false;
    }
  } else if (tag == 4) { // 컬럼 주소
    const int x = ((b[2] & 7) << 8) | b[3];
    if ((b[0] & 1) || (b[1] & 8) || x >= static_cast<int>(kImageWidth)) {
      ++work_.invalid;
      s.x = -1;
      return;
    }
    s.x = x;
    s.mo = b[1] & 0x80u;
    s.frame = ((b[1] & 7) << 5) | (b[2] >> 3);
    if ((b[1] & 0x20) && s.ref_ok && s.sub_ok)
      s.epoch_ready = true;
  } else if (tag == 12) { // 센서 프레임 끝
    const int f = ((b[1] & 7) << 5) | (b[2] >> 3);
    if ((b[0] & 1) || (b[1] & 0xE8) || (b[2] & 7) || b[3]) {
      ++work_.invalid;
      s.x = -1;
      if (!cid) {
        building_ = false;
        previous_ = -1;
      }
      return;
    }
    if (s.frame >= 0 && f != s.frame)
      ++work_.frame_gaps;
    if (s.last_end >= 0 && f != ((s.last_end + 1) & 255))
      ++work_.frame_gaps;
    s.last_end = f;
    s.x = -1;
    s.frame = -1;
    if (!cid && building_) { // 시작부터 끝까지 본 프레임을 내보낼 목록에 추가
      Finish(start_us_, previous_ >= 0 && f == ((previous_ + 1) & 255)
                            ? static_cast<std::uint32_t>(
                                  (start_us_ + kTimestampWrap - previous_us_) % kTimestampWrap)
                            : 0);
      previous_ = f;
      previous_us_ = start_us_;
      building_ = false;
    } else if (!cid) {
      previous_ = -1;
    }
  } else {
    ++work_.unknown;
    s.x = -1;
  }
}

} // namespace delta
