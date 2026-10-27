// 빌드 전용(설치 안 됨): Stream::Impl 정의. src/stream.cpp(구현)와
// src/nrv_dvs/stream.cpp(API)가 함께 씁니다.
#pragma once
#include "source.h"
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <nrv_dvs/device.h>
#include <nrv_dvs/stream.h>
#include <system_error>
#include <vector>

namespace delta {

// Format 별 VI 버퍼 크기 (드라이버 formats[] = cam1.dts mode0, mode1)
constexpr std::uint32_t kWidth[] = {8192, 2880};
constexpr std::uint32_t kHeight[] = {128, 61};
constexpr std::uint32_t kBufferCount = 32; // vb2 상한 (REQBUFS 가 32 넘게는 주지 않음)

class Stream::Impl final : public Source::Impl {
public:
  Impl(Device dev, Format format);
  ~Impl() override;

  StreamStats Stats() const;

private:
  std::error_code
  Produce(std::vector<Block> &data, bool &discontinuous,
          std::chrono::steady_clock::time_point deadline) override;
  bool Ready() noexcept override;
  void Release() noexcept; // STREAMOFF, 버퍼 반납
  // buffers_[index] 를 빈 버퍼로 드라이버에 넘김 (VI 가 그 메모리에 직접 채움)
  std::error_code Queue(std::uint32_t index) noexcept;

  Device dev_; // 장치 소유 (Stream 이 살아 있는 동안 fd 유지)
  Device::Impl &device_; // dev_ 의 장치 연산 (dev_.impl_ 은 힙이라 이동해도 유지)
  // VI 가 채우도록 드라이버에 넘긴 버퍼 (USERPTR, 번호 = v4l2 버퍼 index). 채워진
  // 버퍼는 Produce 가 받은 vector 와 맞바꿔 복사 없이 내보내고, 받은 vector 의
  // 메모리를 그 번호의 빈 버퍼로 다시 넘김.
  std::vector<std::vector<Block>> buffers_;
  std::size_t frame_blocks_ = 0; // 버퍼 하나의 Block 수 (Format 크기)
  bool streaming_ = false;
  // VI 가 채울 빈 버퍼가 드라이버에 하나도 없으면(모두 채워졌거나 복사 중)
  // 그동안 VI 는 프레임을 버림(sequence 공백 없이). 그때까지 채워진
  // 마지막 sequence 를 gaps_after_ 에 적고, 그보다 뒤의 첫 버퍼에
  // discontinuous 를 표시함.
  void CheckStarved() noexcept;
  std::atomic<std::uint32_t> last_sequence_ = 0; // 마지막으로 꺼낸 버퍼
  std::mutex gaps_mutex_;
  std::vector<std::uint32_t> gaps_after_;
  // 아래는 Produce 안에서만 씀 (한 번에 하나만 실행)
  bool have_previous_ = false;    // 앞서 내보낸 버퍼가 있음
  std::int64_t previous_ns_ = 0;  // 그 버퍼의 VI 시각
  StreamStats work_;              // Produce 안에서 세는 값
  mutable std::mutex stats_mutex_;
  StreamStats stats_; // 버퍼마다 work_ 를 복사 (Stats() 용)
};

}; // namespace delta
