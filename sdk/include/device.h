// 빌드 전용(설치 안 됨): Device::Impl 정의. fd 는 여기서만 갖고, 장치 연산
// (ioctl/poll)도 여기서만 합니다. Stream 은 이 연산을 빌려 씁니다.
// NRV_DVS_DUMMY_DEVICE: 드라이버 없이 테스트하도록 장치를 흉내 냄 (src/device.cpp)
#pragma once
#include <chrono>
#include <cstdint>
#include <nrv_dvs/device.h>
#include <span>
#include <string_view>
#include <system_error>
#ifdef NRV_DVS_DUMMY_DEVICE
#include <cstddef>
#include <deque>
#include <vector>
#endif

namespace delta {

class Device::Impl {
public:
  explicit Impl(std::string_view dev);
  ~Impl();
  Impl(const Impl &) = delete;
  Impl &operator=(const Impl &) = delete;

  std::error_code SetRegisterList(std::span<const std::uint32_t> ops);

  // EINTR 이면 다시 호출하는 ioctl
  std::error_code Ioctl(unsigned long req, void *arg) noexcept;
  // 읽을 버퍼가 생길 때까지 대기 (timed_out: 시간 초과)
  std::error_code Poll(std::chrono::milliseconds timeout) noexcept;

private:
#ifdef NRV_DVS_DUMMY_DEVICE
  // Stream 이 쓰는 V4L2 캡처 큐(USERPTR)만 흉내 냄. 넘겨받은 버퍼는 DQBUF 때
  // 녹화 원본(생성자에 준 경로의 .bin)의 다음 부분으로 바로 채움 (끝나면 처음부터).
  struct Queued {
    std::uint32_t index;
    std::byte *data;
  };
  std::vector<std::byte> raw_;  // 녹화 원본 전체
  std::size_t next_ = 0;        // 다음에 채울 원본 위치
  std::size_t frame_bytes_ = 0; // S_FMT 의 sizeimage
  std::uint32_t count_ = 0;     // REQBUFS 로 잡은 버퍼 수
  std::deque<Queued> queue_;    // 넘겨받아 채울 버퍼 (순서대로)
  bool streaming_ = false;
  std::uint32_t sequence_ = 0;
#else
  std::int32_t fd_ = -1;
#endif
};

}; // namespace delta
