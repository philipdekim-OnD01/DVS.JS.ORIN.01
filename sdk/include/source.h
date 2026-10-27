// 빌드 전용(설치 안 됨): Source::Impl 정의. 붙은 파이프들의 큐를 관리하고,
// 데이터를 만드는 일은 구현체(Stream::Impl, Decoder::Impl)가 Produce 로 합니다.
#pragma once
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <nrv_dvs/pipe.h>
#include <nrv_dvs/source.h>
#include <system_error>
#include <vector>

namespace delta {

// Source 에 붙은 파이프 하나의 큐 (Pipe::Impl 이 소유)
struct PipeQueue {
  struct Entry {
    std::vector<Block> data;
    bool discontinuous;
  };
  // 링 버퍼 크기와 꽉 찼을 때 할 일. drop: 가장 오래된 것을 버림(DropPipe),
  // 아니면 Source 가 멈춤(WaitPipe)
  bool drop;
  std::size_t capacity;
  std::deque<Entry> entries;                 // 아직 안 읽은 데이터 (오래된 것부터)
  std::vector<std::vector<Block>> spare; // 돌려받은/버린 메모리 (다음 복사에 씀)
  std::atomic<std::uint64_t> dropped;        // drop: 안 읽힌 채 버린 수
};

class Source::Impl {
public:
  virtual ~Impl() = default;
  Impl(const Impl &) = delete;
  Impl &operator=(const Impl &) = delete;

  void Attach(PipeQueue &pipe);
  void Detach(PipeQueue &pipe);
  std::error_code Read(PipeQueue &pipe, std::vector<Block> &data,
                       bool &discontinuous, std::chrono::milliseconds timeout);

protected:
  Impl() = default;

  // 다음 데이터를 data 에 만듦 (크기 포함, data 의 메모리는 다시 써도 됨).
  // discontinuous: 바로 앞에서 데이터가 빠졌음. 잠금 없이 불리지만 한 번에
  // 하나만 실행됨.
  virtual std::error_code
  Produce(std::vector<Block> &data, bool &discontinuous,
          std::chrono::steady_clock::time_point deadline) = 0;
  // Produce 가 기다리지 않고 바로 데이터를 줄 수 있음 (Stream: 드라이버에
  // 채워진 버퍼가 있음, Decoder: 해석해 둔 센서 프레임이 남음). DropPipe 는
  // 읽기 전에 이런 데이터를 모두 받아 둠 (넘치면 오래된 것부터 버려짐).
  // (mutex_ 잠금 상태, Produce 실행 중에는 불리지 않음)
  virtual bool Ready() noexcept { return false; }

private:
  // Produce 하나를 잠금 없이 실행하고 결과를 파이프마다 넣음 (lock 은 잡힌 채로 반환)
  std::error_code ProduceOne(std::unique_lock<std::mutex> &lock,
                             std::chrono::steady_clock::time_point deadline);
  bool Full() const noexcept; // 꽉 찬 WaitPipe 가 있음 (mutex_ 잠금 상태)

  std::mutex mutex_;                // 아래 상태 보호
  std::condition_variable changed_; // 데이터가 들어오거나 WaitPipe 에 자리가 남
  std::vector<PipeQueue *> pipes_;
  bool producing_ = false;      // 어떤 파이프가 Produce 중 (다른 파이프는 기다림)
  std::vector<Block> made_; // Produce 결과 (ProduceOne 안에서만 씀)
};

} // namespace delta
