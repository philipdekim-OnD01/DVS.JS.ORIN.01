// 녹화 테스트(internal/record_test)와 압축 벤치(internal/compression_bench, 먼저 녹화한
// 뒤 압축)가 함께 쓰는 녹화 목록과 녹화 함수. 경로는 테스트 실행 디렉터리 기준.
#pragma once
#include "register_lists.h"
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <format>
#include <fstream>
#include <iterator>
#include <nrv_dvs/delta.h>
#include <span>
#include <string>
#include <vector>

inline constexpr std::chrono::seconds kRecordTime{2};  // 파일마다 녹화하는 시간
inline constexpr std::chrono::seconds kRecordSettle{1}; // 스트림 시작 뒤 버리는 시간

struct RecordedFile {
  const char *name;
  const char *setfile;      // 녹화할 때 쓴 레지스터 목록 (settings/)
  std::span<const std::uint32_t> extra; // setfile 뒤에 덧붙여 쓰는 레지스터 (덮어씀)
  delta::Format format;     // 센서 출력 형식 = VI 버퍼 크기
  std::size_t buffer_bytes; // VI 버퍼 하나 (압축은 버퍼마다 따로)
  const char *path;
};

inline constexpr RecordedFile kRecordedFiles[] = {
    {"group AER (CSI 0x2A) Delta10", "settings/Delta_10_2000FPS.txt", {},
     delta::Format::Compressed, 8192 * 128, "recordings/group_aer_delta10.bin"},
    {"group AER (CSI 0x2A) max sensitivity", "settings/Delta_10_2000FPS.txt", kMaxSensitivity,
     delta::Format::Compressed, 8192 * 128, "recordings/group_aer_max_sensitivity.bin"},
    {"group AER (CSI 0x2A) viewer max sensitivity", "settings/Delta_10_2000FPS.txt",
     kViewerMaxSensitivity, delta::Format::Compressed, 8192 * 128,
     "recordings/group_aer_viewer_max_sensitivity.bin"},
    {"group AER (CSI 0x2A) min sensitivity", "settings/Delta_10_2000FPS.txt", kMinSensitivity,
     delta::Format::Compressed, 8192 * 128, "recordings/group_aer_min_sensitivity.bin"},
    {"RAW2 (CSI 0x2A) Delta10", "settings/Delta_10_2000FPS_RAW2.txt", {}, delta::Format::Raw2,
     2880 * 61, "recordings/raw2_delta10.bin"},
    {"RAW2 (CSI 0x2A) max sensitivity", "settings/Delta_10_2000FPS_RAW2.txt", kMaxSensitivity,
     delta::Format::Raw2, 2880 * 61, "recordings/raw2_max_sensitivity.bin"},
    {"RAW2 (CSI 0x2A) viewer max sensitivity", "settings/Delta_10_2000FPS_RAW2.txt",
     kViewerMaxSensitivity, delta::Format::Raw2, 2880 * 61,
     "recordings/raw2_viewer_max_sensitivity.bin"},
    {"RAW2 (CSI 0x2A) min sensitivity", "settings/Delta_10_2000FPS_RAW2.txt", kMinSensitivity,
     delta::Format::Raw2, 2880 * 61, "recordings/raw2_min_sensitivity.bin"},
};

// 사람이 읽기 쉬운 크기 (B, KiB, MiB, GiB)
inline std::string Size(double bytes) {
  constexpr const char *kUnits[] = {"B", "KiB", "MiB", "GiB"};
  std::size_t unit = 0;
  for (; bytes >= 1024 && unit + 1 < std::size(kUnits); ++unit)
    bytes /= 1024;
  return unit ? std::format("{:.1f} {}", bytes, kUnits[unit]) : std::format("{:.0f} B", bytes);
}

// file 을 녹화: file.setfile(+ extra)과 file.format 으로 수신해, 시작 뒤 kRecordSettle 을
// 버리고 kRecordTime 동안 받은 VI 버퍼를 그대로 이어 붙여 file.path 에 씀.
// 녹화 중에는 받은 버퍼를 복사 없이 모으기만 하고 끝난 뒤 파일로 씀 (RAW2 는 버퍼
// 32개가 약 20 ms 라, 쓰기나 큰 메모리 복사로 그보다 멈추면 드라이버 버퍼가 바닥남).
// 읽기/쓰기 오류면 false
inline bool Record(const RecordedFile &file) {
  std::vector<std::uint32_t> ops;
  if (!ReadSetfile(file.setfile, ops))
    return false;
  ops.insert(ops.end(), file.extra.begin(), file.extra.end());
  delta::Device dev("/dev/video0");
  if (const std::error_code ec = dev.SetRegisterList(ops)) {
    std::fprintf(stderr, "%s: SetRegisterList: %s\n", file.name, ec.message().c_str());
    return false;
  }
  delta::Stream stream(std::move(dev), file.format);
  delta::WaitPipe pipe(stream, 64);
  std::vector<delta::Block> data;
  std::vector<std::vector<delta::Block>> recorded;
  bool discontinuous = false;
  std::uint64_t buffers = 0, discontinuities = 0;
  using Clock = std::chrono::steady_clock;
  const auto settle = Clock::now() + kRecordSettle;
  for (;;) {
    if (const std::error_code ec = pipe.Read(data, discontinuous, std::chrono::seconds(1))) {
      std::fprintf(stderr, "%s: Read: %s\n", file.name, ec.message().c_str());
      return false;
    }
    if (Clock::now() >= settle)
      break;
  }
  const delta::StreamStats before = stream.Stats();
  const auto start = Clock::now();
  while (Clock::now() - start < kRecordTime) {
    if (const std::error_code ec = pipe.Read(data, discontinuous, std::chrono::seconds(1))) {
      std::fprintf(stderr, "%s: Read: %s\n", file.name, ec.message().c_str());
      return false;
    }
    const std::span<const std::byte> bytes = std::as_bytes(std::span(data));
    if (bytes.size() != file.buffer_bytes) {
      std::fprintf(stderr, "%s: buffer %zu B, expected %zu B\n", file.name, bytes.size(),
                   file.buffer_bytes);
      return false;
    }
    recorded.push_back(std::move(data)); // data 는 비어서 다음 Read 에 새 메모리가 감
    discontinuities += discontinuous;
    ++buffers;
  }
  const double seconds = std::chrono::duration<double>(Clock::now() - start).count();
  const delta::StreamStats after = stream.Stats();

  std::error_code ec;
  std::filesystem::create_directories(std::filesystem::path(file.path).parent_path(), ec);
  std::ofstream out(file.path, std::ios::binary | std::ios::trunc);
  for (const std::vector<delta::Block> &buffer : recorded)
    out.write(reinterpret_cast<const char *>(buffer.data()),
              static_cast<std::streamsize>(buffer.size() * sizeof(delta::Block)));
  if (ec || !out) {
    std::fprintf(stderr, "%s: cannot write\n", file.path);
    return false;
  }
  const double bytes = static_cast<double>(buffers * file.buffer_bytes);
  std::printf("%s: %.2f s, %s (%llu x %s, %.1f MiB/s) -> %s, discontinuous %llu, "
              "stream gaps %llu starved %llu\n",
              file.name, seconds, Size(bytes).c_str(), static_cast<unsigned long long>(buffers),
              Size(static_cast<double>(file.buffer_bytes)).c_str(), bytes / (1 << 20) / seconds,
              file.path, static_cast<unsigned long long>(discontinuities),
              static_cast<unsigned long long>(after.dropped - before.dropped),
              static_cast<unsigned long long>(after.starved - before.starved));
  return buffers > 0;
}
