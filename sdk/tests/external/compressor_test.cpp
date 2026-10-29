// Compressor: 실제 수신 데이터를 zstd 레벨 1 로 압축. setfile(settings/, 테스트 전에
// 빌드 트리로 복사)마다 kDuration 동안: Delta10, Delta10 + 최대 감도, RAW2 출력.
// 앞 kVerify 버퍼는 원본을 따로 받아 두고 풀어서 비교함 (왕복 확인).
// 압축이 수신을 못 따라가면 Stream 이 기다리다 드라이버 버퍼가 바닥나 VI 가 버림
// (starved). 지금은 최대 감도를 1 스레드로 못 따라가므로 그것은 실패로 치지 않고
// 처리량과 함께 출력만 함.
#include "nrv_dvs/delta.h"
#include "../register_lists.h"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <memory>
#include <span>
#include <vector>
#include <zstd.h>

constexpr auto kDuration = std::chrono::seconds(20);
constexpr std::uint64_t kVerify = 32;
constexpr int kLevel = 1;

static double ThreadCpuSeconds() {
  timespec t{.tv_sec = 0, .tv_nsec = 0};
  clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
  return static_cast<double>(t.tv_sec) + static_cast<double>(t.tv_nsec) / 1e9;
}

// ops 로 format 버퍼를 수신하며 압축. 읽기/형식/왕복 오류면 false
static bool Run(const char *name, const std::vector<std::uint32_t> &ops, delta::Format format) {
  delta::Device dev("/dev/video0");
  if (const std::error_code ec = dev.SetRegisterList(ops)) {
    std::fprintf(stderr, "%s: SetRegisterList: %s\n", name, ec.message().c_str());
    return false;
  }
  delta::Stream stream(std::move(dev), format);
  // 왕복 확인용 원본: 앞 kVerify 버퍼를 받은 뒤 없앰 (그 뒤로 Stream 은 이 파이프를 기다리지 않음)
  auto raw = std::make_unique<delta::WaitPipe>(stream, kVerify);
  delta::Compressor compressor(std::make_unique<delta::WaitPipe>(stream, 64), kLevel);
  delta::WaitPipe compressed(compressor, 1);

  std::vector<delta::Block> data, original;
  std::vector<std::byte> decompressed;
  bool discontinuous = false, raw_discontinuous = false;
  std::uint64_t buffers = 0, verified = 0, discontinuities = 0, in_bytes = 0, out_bytes = 0;
  using Clock = std::chrono::steady_clock;
  const auto start = Clock::now();
  const double cpu_start = ThreadCpuSeconds();
  while (Clock::now() - start < kDuration) {
    if (const std::error_code ec = compressed.Read(data, discontinuous, std::chrono::seconds(1))) {
      std::fprintf(stderr, "%s: Read: %s\n", name, ec.message().c_str());
      return false;
    }
    const std::span<const std::byte> out = std::as_bytes(std::span(data));
    delta::CompressedFrame frame{.bytes = 0};
    std::copy(out.begin(), out.begin() + sizeof(frame),
              std::as_writable_bytes(std::span(&frame, 1)).begin());
    if (sizeof(frame) + frame.bytes > out.size()) {
      std::fprintf(stderr, "%s: frame %u B does not fit %zu B\n", name, frame.bytes, out.size());
      return false;
    }
    const std::span<const std::byte> zst = out.subspan(sizeof(frame), frame.bytes);
    const unsigned long long size = ZSTD_getFrameContentSize(zst.data(), zst.size());
    if (size == ZSTD_CONTENTSIZE_UNKNOWN || size == ZSTD_CONTENTSIZE_ERROR) {
      std::fprintf(stderr, "%s: buffer %llu: not a zstd frame\n", name,
                   static_cast<unsigned long long>(buffers));
      return false;
    }
    if (raw) {
      if (const std::error_code ec =
              raw->Read(original, raw_discontinuous, std::chrono::seconds(1))) {
        std::fprintf(stderr, "%s: raw Read: %s\n", name, ec.message().c_str());
        return false;
      }
      const std::span<const std::byte> expected = std::as_bytes(std::span(original));
      decompressed.resize(expected.size());
      const std::size_t n = ZSTD_decompress(decompressed.data(), decompressed.size(),
                                            zst.data(), zst.size());
      if (ZSTD_isError(n) || n != expected.size() ||
          !std::equal(expected.begin(), expected.end(), decompressed.begin())) {
        std::fprintf(stderr, "%s: buffer %llu: decompressed data differs\n", name,
                     static_cast<unsigned long long>(buffers));
        return false;
      }
      if (++verified == kVerify)
        raw.reset();
    }
    discontinuities += discontinuous;
    in_bytes += size;
    out_bytes += frame.bytes;
    ++buffers;
  }
  const double seconds = std::chrono::duration<double>(Clock::now() - start).count();
  const double cpu = ThreadCpuSeconds() - cpu_start;
  const delta::StreamStats s = stream.Stats();
  const double mib = static_cast<double>(in_bytes) / (1 << 20);
  std::printf("%s: %llu buffers (%llu B) in %.1f s, in %.1f MiB/s, out %.1f MiB/s, x%.3f, "
              "reader CPU %.0f%% (%.2f ms/MiB), verified %llu, discontinuous %llu, "
              "stream gaps %llu starved %llu\n",
              name, static_cast<unsigned long long>(buffers),
              static_cast<unsigned long long>(buffers ? in_bytes / buffers : 0), seconds, mib / seconds,
              static_cast<double>(out_bytes) / (1 << 20) / seconds,
              static_cast<double>(in_bytes) / static_cast<double>(out_bytes), cpu / seconds * 100,
              cpu * 1e3 / mib, static_cast<unsigned long long>(verified),
              static_cast<unsigned long long>(discontinuities),
              static_cast<unsigned long long>(s.dropped),
              static_cast<unsigned long long>(s.starved));
  return buffers >= kVerify && verified == kVerify;
}

int main() {
  std::vector<std::uint32_t> delta10, raw2;
  if (!ReadSetfile("settings/Delta_10_2000FPS.txt", delta10) ||
      !ReadSetfile("settings/Delta_10_2000FPS_RAW2.txt", raw2))
    return 1;
  std::vector<std::uint32_t> max = delta10;
  max.insert(max.end(), kMaxSensitivity.begin(), kMaxSensitivity.end());
  bool ok = Run("Delta10", delta10, delta::Format::Compressed);
  ok = Run("max sensitivity", max, delta::Format::Compressed) && ok;
  ok = Run("RAW2", raw2, delta::Format::Raw2) && ok;
  return ok ? 0 : 1;
}
