// GroupAerDecoder 처리량: 최대 감도 녹화 원본(1 MiB 버퍼의 연속)을 메모리에서 Stream 대신
// 넣어 주고, 센서 프레임을 끝까지 읽으며 원본을 kPasses 번 되풀이해 해석하는 속도를 잼.
// 원본은 실행 디렉터리의 resources/ (tests/internal/resources 를 테스트 전에 복사)
#include "recording.h"
#include <chrono>
#include <cstdio>
#include <nrv_dvs/decoder.h>
#include <nrv_dvs/pipe.h>

constexpr const char *kRaw = "resources/raw_max_sensitivity.bin";
constexpr std::size_t kPasses = 32; // 8 MiB 원본 → 256 MiB 해석

int main() {
  std::vector<std::byte> raw;
  if (!ReadRaw(kRaw, kBufferBytes, raw))
    return 1;
  const std::size_t buffers = raw.size() / kBufferBytes;

  Recording recording(std::move(raw), kBufferBytes);
  delta::GroupAerDecoder decoder(std::make_unique<delta::WaitPipe>(recording, 1));
  delta::WaitPipe frames(decoder, 4096);
  std::vector<delta::Block> data;
  bool discontinuous = false;
  std::error_code ec;
  const auto start = std::chrono::steady_clock::now();
  while (decoder.Stats().buffers < kPasses * buffers)
    if ((ec = frames.Read(data, discontinuous, std::chrono::seconds(1)))) {
      std::fprintf(stderr, "Read: %s\n", ec.message().c_str());
      return 1;
    }
  const double seconds =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  const delta::DecodeStats s = decoder.Stats();
  std::printf("%llu MiB in %.3f s: %.1f MiB/s, frames %llu, events %llu, invalid %llu, "
              "unknown %llu\n",
              static_cast<unsigned long long>(s.buffers), seconds,
              static_cast<double>(s.buffers) / seconds,
              static_cast<unsigned long long>(s.frames),
              static_cast<unsigned long long>(s.events),
              static_cast<unsigned long long>(s.invalid),
              static_cast<unsigned long long>(s.unknown));
  return 0;
}
