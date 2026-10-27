// 빌드 전용(설치 안 됨): Pipe::Impl 과 그 구현 Wait(WaitPipe), Drop(DropPipe) 정의.
// src/pipe.cpp(구현)와 src/nrv_dvs/pipe.cpp(API)가 함께 씁니다.
#pragma once
#include "source.h"
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <nrv_dvs/pipe.h>
#include <system_error>
#include <vector>

namespace delta {

// 파이프 공통: Source 에 등록하는 큐를 갖고, Source 를 붙잡는 방식만 구현마다 다름
class Pipe::Impl {
public:
  class Wait;
  class Drop;

  virtual ~Impl() = default;
  Impl(const Impl &) = delete;
  Impl &operator=(const Impl &) = delete;

  virtual std::error_code Read(std::vector<Block> &data, bool &discontinuous,
                               std::chrono::milliseconds timeout) = 0;
  std::uint64_t Dropped() const noexcept { return queue_.dropped; } // Wait 는 항상 0

protected:
  Impl(Type type, std::size_t capacity);

  PipeQueue queue_; // Source 에 등록된 큐
};

// WaitPipe: Source 를 함께 소유
class Pipe::Impl::Wait final : public Pipe::Impl {
public:
  Wait(std::shared_ptr<Source::Impl> source, std::size_t capacity);
  ~Wait() override;

  std::error_code Read(std::vector<Block> &data, bool &discontinuous,
                       std::chrono::milliseconds timeout) override;

private:
  std::shared_ptr<Source::Impl> source_;
};

// DropPipe: Source 를 소유하지 않음 (소유한 것이 모두 사라지면 broken_pipe)
class Pipe::Impl::Drop final : public Pipe::Impl {
public:
  Drop(const std::shared_ptr<Source::Impl> &source, std::size_t capacity);
  ~Drop() override;

  std::error_code Read(std::vector<Block> &data, bool &discontinuous,
                       std::chrono::milliseconds timeout) override;

private:
  std::weak_ptr<Source::Impl> source_;
};

} // namespace delta
