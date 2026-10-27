// 디코더 확인: 먼저 recordings.h 의 파일을 모두 같은 시간씩 녹화하고(Record), 형식에
// 맞는 디코더(group AER: GroupAerDecoder, RAW2: Raw2Decoder)로 해석해 센서 프레임이
// 정상인지 출력: 프레임 수와 센서 fps, 프레임 번호 공백, 형식 오류, 이벤트 수,
// 프레임 간격(period_us) 범위. 녹화 중 CSI 오류로 빠진 버퍼가 있으면 그 자리에서
// 오류가 날 수 있으므로(녹화 파일에는 빠진 자리 표시가 없음) 프레임이 하나도 없을
// 때만 실패로 침.
#include "../recordings.h"
#include "recording.h"
#include <algorithm>
#include <cstdio>
#include <memory>
#include <nrv_dvs/decoder.h>
#include <nrv_dvs/pipe.h>
#include <span>
#include <vector>

// file 을 decoder 로 끝까지 해석. 프레임이 없거나 읽기 오류면 false
static bool Check(const RecordedFile &file, std::vector<std::byte> raw) {
  const std::size_t buffers = raw.size() / file.buffer_bytes;
  Recording recording(std::move(raw), file.buffer_bytes);
  auto upstream = std::make_unique<delta::WaitPipe>(recording, 1);
  std::unique_ptr<delta::Decoder> decoder;
  if (file.format == delta::Format::Raw2)
    decoder = std::make_unique<delta::Raw2Decoder>(std::move(upstream));
  else
    decoder = std::make_unique<delta::GroupAerDecoder>(std::move(upstream));
  delta::WaitPipe frames(*decoder, 1);
  std::vector<delta::Block> data;
  bool discontinuous = false;
  std::uint64_t read = 0, discontinuities = 0, period_sum = 0, periods = 0;
  std::uint32_t period_min = UINT32_MAX, period_max = 0;
  // 마지막 원본 버퍼까지 해석하고 그 프레임을 모두 읽을 때까지 (Recording 은 처음으로 돌아감)
  while (decoder->Stats().buffers < buffers || decoder->Stats().frames > read) {
    if (const std::error_code ec = frames.Read(data, discontinuous, std::chrono::seconds(1))) {
      std::fprintf(stderr, "%s: Read: %s\n", file.name, ec.message().c_str());
      return false;
    }
    delta::SensorFrame frame{.timestamp_us = 0, .period_us = 0, .groups = 0};
    const std::span<const std::byte> bytes = std::as_bytes(std::span(data));
    std::copy(bytes.begin(), bytes.begin() + sizeof(frame),
              std::as_writable_bytes(std::span(&frame, 1)).begin());
    ++read;
    discontinuities += discontinuous;
    if (frame.period_us) {
      period_min = std::min(period_min, frame.period_us);
      period_max = std::max(period_max, frame.period_us);
      period_sum += frame.period_us;
      ++periods;
    }
  }
  const delta::DecodeStats s = decoder->Stats();
  const double seconds = std::chrono::duration<double>(kRecordTime).count();
  std::printf("%s: %llu buffers -> %llu frames (%.1f fps), discontinuous %llu, frame gaps %llu, "
              "invalid %llu, unknown %llu, events %llu (on %llu, off %llu)",
              file.name, static_cast<unsigned long long>(s.buffers),
              static_cast<unsigned long long>(s.frames), static_cast<double>(s.frames) / seconds,
              static_cast<unsigned long long>(discontinuities),
              static_cast<unsigned long long>(s.frame_gaps),
              static_cast<unsigned long long>(s.invalid),
              static_cast<unsigned long long>(s.unknown),
              static_cast<unsigned long long>(s.events), static_cast<unsigned long long>(s.on),
              static_cast<unsigned long long>(s.off));
  if (periods)
    std::printf(", period %u..%u us (mean %.1f)", period_min, period_max,
                static_cast<double>(period_sum) / static_cast<double>(periods));
  std::printf("\n");
  return s.frames > 0;
}

int main() {
  for (const RecordedFile &file : kRecordedFiles)
    if (!Record(file))
      return 1;
  bool ok = true;
  for (const RecordedFile &file : kRecordedFiles) {
    std::vector<std::byte> raw;
    if (!ReadRaw(file.path, file.buffer_bytes, raw))
      return 1;
    ok = Check(file, std::move(raw)) && ok;
  }
  return ok ? 0 : 1;
}
