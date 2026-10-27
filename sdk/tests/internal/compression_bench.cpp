// 압축 비교: 먼저 recordings.h 의 파일을 모두 같은 시간씩 녹화하고(Record: group AER 과
// RAW2, 각각 Delta10 과 최대 감도), 원본을 버퍼마다 따로 압축하는 zstd(Compressor,
// 레벨 1~3)와 zlib Z_RLE(deflate 에서 바로 앞 바이트의 반복만 찾고 나머지는 Huffman)의
// 크기, 압축률, 압축/해제 속도와 1 MiB 를 처리하는 시간(지연)을 잼. 해제한 결과는
// 원본과 비교. 일치(LZ) 없이 허프만만 쓰는 zstd 도 같이 잼. 같은 압축을 바이트 셔플(버퍼마다
// 32비트 워드의 0번 바이트끼리, 1번 바이트끼리, ... 모아 놓음)한 데이터에도 해 보고, 셔플/복원
// 속도는 따로 잼.
#include "../recordings.h"
#include "recording.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <format>
#include <nrv_dvs/compressor.h>
#include <nrv_dvs/pipe.h>
#include <span>
#include <vector>
#include <zlib.h>
// ZSTD_compressSequences(허프만만 쓰는 압축)가 zstd.h 의 실험 API 라 필요
#define ZSTD_STATIC_LINKING_ONLY
#include <zstd.h>

constexpr int kLevels[] = {1, 2, 3};

using Clock = std::chrono::steady_clock;

static double Seconds(Clock::time_point since) {
  return std::chrono::duration<double>(Clock::now() - since).count();
}

// 버퍼마다 바이트 셔플: 워드 i 의 k 번 바이트를 k 번째 평면의 i 번째 자리로 옮김.
// unshuffle 이면 반대로 되돌림. out 은 in 과 같은 크기로 미리 잡아 둠
static void Shuffle(const std::vector<std::byte> &in, std::vector<std::byte> &out,
                    std::size_t buffer_bytes, bool unshuffle) {
  const std::size_t words = buffer_bytes / 4;
  for (std::size_t b = 0; b < in.size(); b += buffer_bytes)
    for (std::size_t k = 0; k < 4; ++k) {
      const std::byte *word = in.data() + b + k, *plane = in.data() + b + k * words;
      std::byte *word_out = out.data() + b + k, *plane_out = out.data() + b + k * words;
      if (unshuffle)
        for (std::size_t i = 0; i < words; ++i)
          word_out[4 * i] = plane[i];
      else
        for (std::size_t i = 0; i < words; ++i)
          plane_out[i] = word[4 * i];
    }
}

// 지연 = 1 MiB 를 압축/해제하는 데 걸린 평균 시간 (형식마다 버퍼 크기가 달라 MiB 로 맞춤)
static void Print(const char *method, const std::vector<std::byte> &raw, std::size_t compressed,
                  double compress_s, double decompress_s) {
  const double mib = static_cast<double>(raw.size()) / (1 << 20);
  std::printf("  %-20s %s -> %s (x%.3f)  compress %6.1f MiB/s, %.3f ms per MiB  "
              "decompress %6.1f MiB/s, %.3f ms per MiB\n",
              method, Size(static_cast<double>(raw.size())).c_str(),
              Size(static_cast<double>(compressed)).c_str(),
              static_cast<double>(raw.size()) / static_cast<double>(compressed), mib / compress_s,
              compress_s * 1e3 / mib, mib / decompress_s, decompress_s * 1e3 / mib);
}

// frames(버퍼 순서대로)를 풀어 시간을 재고 raw 와 비교.
// decompress(frame, out) 은 푼 크기를 돌려줌 (실패하면 0)
template <class F>
static bool DecompressAll(const std::vector<std::vector<std::byte>> &frames,
                          const std::vector<std::byte> &raw, std::size_t buffer_bytes,
                          double &seconds, F decompress) {
  std::vector<std::byte> out(buffer_bytes);
  const auto start = Clock::now();
  for (std::size_t i = 0; i < frames.size(); ++i)
    if (decompress(frames[i], out) != buffer_bytes ||
        !std::equal(out.begin(), out.end(),
                    raw.begin() + static_cast<std::ptrdiff_t>(i * buffer_bytes))) {
      std::fprintf(stderr, "buffer %zu: decompressed data differs\n", i);
      return false;
    }
  seconds = Seconds(start);
  return true;
}

// zstd 프레임 하나를 buffer 에 풂 (DecompressAll 의 decompress)
static std::size_t ZstdDecompress(const std::vector<std::byte> &frame,
                                  std::vector<std::byte> &buffer) {
  const std::size_t n = ZSTD_decompress(buffer.data(), buffer.size(), frame.data(), frame.size());
  return ZSTD_isError(n) ? 0 : n;
}

// Compressor 로 raw 를 버퍼마다 압축. prefix 는 출력 이름 앞에 붙임
static bool Zstd(const std::vector<std::byte> &raw, std::size_t buffer_bytes, int level,
                 const char *prefix) {
  const std::size_t buffers = raw.size() / buffer_bytes;
  Recording recording(raw, buffer_bytes);
  delta::Compressor compressor(std::make_unique<delta::WaitPipe>(recording, 1), level);
  delta::WaitPipe compressed(compressor, 1);
  std::vector<std::vector<std::byte>> frames(buffers);
  std::vector<delta::Block> data;
  bool discontinuous = false;
  std::size_t bytes = 0;
  const auto start = Clock::now();
  for (std::size_t i = 0; i < buffers; ++i) {
    if (const std::error_code ec = compressed.Read(data, discontinuous, std::chrono::seconds(1))) {
      std::fprintf(stderr, "Read: %s\n", ec.message().c_str());
      return false;
    }
    const std::span<const std::byte> out = std::as_bytes(std::span(data));
    delta::CompressedFrame frame{.bytes = 0};
    std::copy(out.begin(), out.begin() + sizeof(frame),
              std::as_writable_bytes(std::span(&frame, 1)).begin());
    const std::span<const std::byte> zst = out.subspan(sizeof(frame), frame.bytes);
    bytes += zst.size();
    frames[i].resize(zst.size());
    std::copy(zst.begin(), zst.end(), frames[i].begin());
  }
  const double compress_s = Seconds(start);
  double decompress_s = 0;
  if (!DecompressAll(frames, raw, buffer_bytes, decompress_s, ZstdDecompress))
    return false;
  Print(std::format("{}zstd -{}", prefix, level).c_str(), raw, bytes, compress_s, decompress_s);
  return true;
}

// zstd 허프만만: ZSTD_compressSequences 에 시퀀스(일치)를 하나도 주지 않으면 버퍼 전체가
// 리터럴이 되어 블록(최대 128 KiB)마다 허프만으로만 부호화됨. 결과는 보통 zstd 프레임
static bool Huffman(const std::vector<std::byte> &raw, std::size_t buffer_bytes,
                    const char *prefix) {
  const std::size_t buffers = raw.size() / buffer_bytes;
  ZSTD_CCtx *cctx = ZSTD_createCCtx();
  std::vector<std::vector<std::byte>> frames(buffers);
  std::vector<std::byte> out(ZSTD_compressBound(buffer_bytes));
  std::size_t bytes = 0;
  bool ok = cctx != nullptr;
  const auto start = Clock::now();
  for (std::size_t i = 0; ok && i < buffers; ++i) {
    const std::size_t n = ZSTD_compressSequences(cctx, out.data(), out.size(), nullptr, 0,
                                                 raw.data() + i * buffer_bytes, buffer_bytes);
    ok = !ZSTD_isError(n);
    if (ok) {
      bytes += n;
      frames[i].resize(n);
      std::copy(out.begin(), out.begin() + static_cast<std::ptrdiff_t>(n), frames[i].begin());
    }
  }
  const double compress_s = Seconds(start);
  (void)ZSTD_freeCCtx(cctx);
  if (!ok) {
    std::fprintf(stderr, "ZSTD_compressSequences failed\n");
    return false;
  }
  double decompress_s = 0;
  if (!DecompressAll(frames, raw, buffer_bytes, decompress_s, ZstdDecompress))
    return false;
  Print(std::format("{}zstd Huffman", prefix).c_str(), raw, bytes, compress_s, decompress_s);
  return true;
}

// zlib Z_RLE 로 버퍼마다 따로 deflate (레벨은 Z_RLE 에서 결과에 영향 없음)
static bool Rle(const std::vector<std::byte> &raw, std::size_t buffer_bytes, const char *prefix) {
  const std::size_t buffers = raw.size() / buffer_bytes;
  z_stream z{};
  if (deflateInit2(&z, 1, Z_DEFLATED, 15, 8, Z_RLE) != Z_OK)
    return false;
  std::vector<std::vector<std::byte>> frames(buffers);
  std::vector<std::byte> out(deflateBound(&z, buffer_bytes));
  std::size_t bytes = 0;
  bool ok = true;
  const auto start = Clock::now();
  for (std::size_t i = 0; ok && i < buffers; ++i) {
    (void)deflateReset(&z);
    z.next_in = reinterpret_cast<Bytef *>(const_cast<std::byte *>(raw.data())) + i * buffer_bytes;
    z.avail_in = static_cast<uInt>(buffer_bytes);
    z.next_out = reinterpret_cast<Bytef *>(out.data());
    z.avail_out = static_cast<uInt>(out.size());
    ok = deflate(&z, Z_FINISH) == Z_STREAM_END;
    if (ok) {
      bytes += z.total_out;
      frames[i].resize(z.total_out);
      std::copy(out.begin(), out.begin() + static_cast<std::ptrdiff_t>(z.total_out),
                frames[i].begin());
    }
  }
  const double compress_s = Seconds(start);
  (void)deflateEnd(&z);
  if (!ok) {
    std::fprintf(stderr, "deflate failed\n");
    return false;
  }
  double decompress_s = 0;
  if (!DecompressAll(frames, raw, buffer_bytes, decompress_s,
                     [](const std::vector<std::byte> &frame, std::vector<std::byte> &buffer) {
                       uLongf n = buffer.size();
                       return uncompress(reinterpret_cast<Bytef *>(buffer.data()), &n,
                                         reinterpret_cast<const Bytef *>(frame.data()),
                                         frame.size()) == Z_OK
                                  ? n
                                  : 0;
                     }))
    return false;
  Print(std::format("{}zlib Z_RLE", prefix).c_str(), raw, bytes, compress_s, decompress_s);
  return true;
}

int main() {
  for (const RecordedFile &file : kRecordedFiles)
    if (!Record(file))
      return 1;
  for (const RecordedFile &file : kRecordedFiles) {
    std::vector<std::byte> raw;
    if (!ReadRaw(file.path, file.buffer_bytes, raw))
      return 1;
    std::printf("%s, %s: %lld s, %s (%zu x %s, %.1f MiB/s)\n", file.name, file.path,
                static_cast<long long>(kRecordTime.count()),
                Size(static_cast<double>(raw.size())).c_str(), raw.size() / file.buffer_bytes,
                Size(static_cast<double>(file.buffer_bytes)).c_str(),
                static_cast<double>(raw.size()) / (1 << 20) /
                    std::chrono::duration<double>(kRecordTime).count());
    for (const int level : kLevels)
      if (!Zstd(raw, file.buffer_bytes, level, ""))
        return 1;
    if (!Rle(raw, file.buffer_bytes, "") || !Huffman(raw, file.buffer_bytes, ""))
      return 1;

    std::vector<std::byte> shuffled(raw.size()), restored(raw.size());
    auto start = Clock::now();
    Shuffle(raw, shuffled, file.buffer_bytes, false);
    const double shuffle_s = Seconds(start);
    start = Clock::now();
    Shuffle(shuffled, restored, file.buffer_bytes, true);
    const double unshuffle_s = Seconds(start);
    if (restored != raw) {
      std::fprintf(stderr, "unshuffled data differs\n");
      return 1;
    }
    const double mib = static_cast<double>(raw.size()) / (1 << 20);
    std::printf("  %-20s shuffle %6.1f MiB/s, %.3f ms per MiB  "
                "unshuffle %6.1f MiB/s, %.3f ms per MiB\n",
                "byte shuffle", mib / shuffle_s, shuffle_s * 1e3 / mib, mib / unshuffle_s,
                unshuffle_s * 1e3 / mib);
    for (const int level : kLevels)
      if (!Zstd(shuffled, file.buffer_bytes, level, "shuffle "))
        return 1;
    if (!Rle(shuffled, file.buffer_bytes, "shuffle ") ||
        !Huffman(shuffled, file.buffer_bytes, "shuffle "))
      return 1;
  }
  return 0;
}
