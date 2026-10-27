// 내부 테스트가 함께 쓰는 Source: 메모리의 원본(VI 버퍼의 연속)을 Stream 대신 내줌
#pragma once
#include "source.h" // Source::Impl
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <memory>
#include <new>
#include <nrv_dvs/pipe.h>
#include <nrv_dvs/source.h>
#include <span>
#include <system_error>
#include <vector>

constexpr std::size_t kBufferBytes = std::size_t{1} << 20; // group AER 버퍼 하나 (Format::Compressed)

// path(buffer_bytes 버퍼의 연속, 끝의 자투리는 버림)를 raw 에 읽음. 못 읽으면 false
inline bool ReadRaw(const char *path, std::size_t buffer_bytes, std::vector<std::byte> &raw) {
  std::error_code ec;
  const std::uintmax_t size = std::filesystem::file_size(path, ec); // 실패하면 -1
  if (ec) {
    std::fprintf(stderr, "%s: %s\n", path, ec.message().c_str());
    return false;
  }
  const std::size_t buffers = size / buffer_bytes;
  try {
    raw.resize(buffers * buffer_bytes);
  } catch (const std::bad_alloc &) {
    std::fprintf(stderr, "%s: %ju B does not fit in memory\n", path, size);
    return false;
  }
  std::ifstream file(path, std::ios::binary);
  if (!buffers ||
      !file.read(reinterpret_cast<char *>(raw.data()), static_cast<std::streamsize>(raw.size()))) {
    std::fprintf(stderr, "%s: cannot read\n", path);
    return false;
  }
  return true;
}

// 원본을 buffer_bytes 버퍼 단위로 차례로 내주는 Source. 끝나면 처음부터 다시 주고,
// 이어지지 않으므로 discontinuous 로 표시 (Decoder 가 다시 동기화)
class Recording final : public delta::Source {
public:
  Recording(std::vector<std::byte> raw, std::size_t buffer_bytes) {
    impl_ = std::make_shared<Impl>(std::move(raw), buffer_bytes);
  }

private:
  class Impl final : public delta::Source::Impl {
  public:
    Impl(std::vector<std::byte> raw, std::size_t buffer_bytes)
        : raw_(std::move(raw)), buffer_bytes_(buffer_bytes) {}

  private:
    std::error_code Produce(std::vector<delta::Block> &data, bool &discontinuous,
                            std::chrono::steady_clock::time_point) override {
      const auto from = raw_.begin() + static_cast<std::ptrdiff_t>(next_);
      data.resize(buffer_bytes_ / sizeof(delta::Block));
      std::copy(from, from + static_cast<std::ptrdiff_t>(buffer_bytes_),
                std::as_writable_bytes(std::span(data)).begin());
      discontinuous = next_ == 0;
      next_ = (next_ + buffer_bytes_) % raw_.size();
      return {};
    }

    std::vector<std::byte> raw_;
    std::size_t buffer_bytes_; // Block 의 배수
    std::size_t next_ = 0;
  };
};
