#include "device.h"
#ifdef NRV_DVS_DUMMY_DEVICE
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <linux/videodev2.h>
#include <string>
#include <thread>

namespace delta {

// 더미 장치 (NRV_DVS_DUMMY_DEVICE): 드라이버와 센서 없이 Stream 의 버퍼 흐름을
// 시험. dev = 녹화 원본(.bin, 센서 출력을 그대로 이어 붙인 것) 경로. 레지스터
// 목록은 받기만 함. 넘겨받은 버퍼는 DQBUF 때 원본의 다음 부분으로 바로 채워
// 돌려줌 (원본이 끝나면 처음부터).

Device::Impl::Impl(std::string_view dev) {
  const std::string path(dev);
  std::error_code ec;
  raw_.resize(std::filesystem::file_size(path, ec));
  std::ifstream file(path, std::ios::binary);
  if (ec || raw_.empty() ||
      !file.read(reinterpret_cast<char *>(raw_.data()), static_cast<std::streamsize>(raw_.size())))
    throw std::system_error(ec ? ec : std::make_error_code(std::errc::io_error),
                            "open " + path);
}

Device::Impl::~Impl() = default;

std::error_code
Device::Impl::SetRegisterList(std::span<const std::uint32_t> words) {
  if (words.empty())
    return std::make_error_code(std::errc::invalid_argument);
  return {};
}

std::error_code Device::Impl::Ioctl(unsigned long req, void *arg) noexcept {
  const auto einval = std::make_error_code(std::errc::invalid_argument);
  switch (req) {
  case VIDIOC_S_FMT: {
    ::v4l2_pix_format &pix = static_cast<::v4l2_format *>(arg)->fmt.pix;
    pix.bytesperline = pix.width;
    pix.sizeimage = pix.width * pix.height;
    frame_bytes_ = pix.sizeimage;
    return {};
  }
  case VIDIOC_REQBUFS: {
    auto &r = *static_cast<::v4l2_requestbuffers *>(arg);
    if (r.memory != V4L2_MEMORY_USERPTR || streaming_)
      return einval;
    count_ = r.count = std::min<std::uint32_t>(r.count, 32); // vb2 상한
    queue_.clear();
    return {};
  }
  case VIDIOC_QBUF: {
    const auto &b = *static_cast<::v4l2_buffer *>(arg);
    // VI 는 64바이트 정렬이 아닌 주소를 받지 않음 (Orin 실측: 16 실패, 64 성공)
    if (b.memory != V4L2_MEMORY_USERPTR || b.index >= count_ || b.length < frame_bytes_ ||
        b.m.userptr % 64 ||
        std::ranges::any_of(queue_, [&](const Queued &q) { return q.index == b.index; }))
      return einval;
    queue_.push_back({.index = b.index, .data = reinterpret_cast<std::byte *>(b.m.userptr)});
    return {};
  }
  case VIDIOC_DQBUF: {
    if (!streaming_ || queue_.empty())
      return std::make_error_code(std::errc::resource_unavailable_try_again);
    const Queued q = queue_.front();
    queue_.pop_front();
    for (std::size_t done = 0; done < frame_bytes_;) { // 원본 끝에서 처음으로 이어짐
      const std::size_t n = std::min(frame_bytes_ - done, raw_.size() - next_);
      const auto from = raw_.begin() + static_cast<std::ptrdiff_t>(next_);
      std::copy(from, from + static_cast<std::ptrdiff_t>(n), q.data + done);
      done += n;
      next_ = (next_ + n) % raw_.size();
    }
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    const auto sec = std::chrono::duration_cast<std::chrono::seconds>(now);
    auto &b = *static_cast<::v4l2_buffer *>(arg);
    b.index = q.index;
    b.flags = 0;
    b.bytesused = static_cast<std::uint32_t>(frame_bytes_);
    b.sequence = sequence_++;
    b.timestamp = {.tv_sec = sec.count(),
                   .tv_usec = std::chrono::duration_cast<std::chrono::microseconds>(now - sec)
                                  .count()};
    b.m.userptr = reinterpret_cast<unsigned long>(q.data);
    b.length = static_cast<std::uint32_t>(frame_bytes_);
    return {};
  }
  case VIDIOC_QUERYBUF: {
    auto &b = *static_cast<::v4l2_buffer *>(arg);
    if (b.index >= count_)
      return einval;
    b.flags = std::ranges::any_of(queue_, [&](const Queued &q) { return q.index == b.index; })
                  ? V4L2_BUF_FLAG_QUEUED
                  : 0;
    return {};
  }
  case VIDIOC_STREAMON:
    streaming_ = true;
    return {};
  case VIDIOC_STREAMOFF:
    streaming_ = false;
    queue_.clear();
    return {};
  default:
    return std::make_error_code(std::errc::inappropriate_io_control_operation);
  }
}

std::error_code
Device::Impl::Poll(std::chrono::milliseconds timeout) noexcept {
  if (streaming_ && !queue_.empty())
    return {};
  std::this_thread::sleep_for(timeout);
  return std::make_error_code(std::errc::timed_out);
}

}; // namespace delta
#else
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <mutex>
#include <poll.h>
#include <string>
#include <sys/ioctl.h>
#include <system_error>
#include <unistd.h>

namespace delta {

// 드라이버(drivers/dvs/include/tegra-cam/register_control.h)의 컨트롤 이름.
// ID는 하드코딩하지 않고 장치를 열 때 이 이름으로 찾습니다.
constexpr std::string_view kRegisterWritesName = "nrv_register_writes";

// 장치의 모든 컨트롤(배열 같은 compound 포함)을 훑어 이름이 같은 컨트롤 정보를
// out 에 채웁니다. 없으면 no_such_device.
// EINTR 이면 다시 호출하는 ioctl. fd 는 Device::Impl 만 갖고 있으므로 이
// 파일에서만 씁니다.
static std::error_code Ioctl(std::int32_t fd, unsigned long req, void *arg) {
  while (::ioctl(fd, req, arg) == -1)
    if (errno != EINTR)
      return std::error_code(errno, std::system_category());
  return {};
}

static std::error_code FindRegisterCtl(std::int32_t fd, std::string_view name,
                                       ::v4l2_query_ext_ctrl &out) {
  ::v4l2_query_ext_ctrl q{
      .id = V4L2_CTRL_FLAG_NEXT_CTRL | V4L2_CTRL_FLAG_NEXT_COMPOUND,
      .type = 0,
      .name = {},
      .minimum = 0,
      .maximum = 0,
      .step = 0,
      .default_value = 0,
      .flags = 0,
      .elem_size = 0,
      .elems = 0,
      .nr_of_dims = 0,
      .dims = {},
      .reserved = {},
  };
  std::error_code ec;
  // VIDIOC_QUERY_EXT_CTRL: get the next control (NEXT_CTRL | NEXT_COMPOUND
  // in q.id) including array controls; EINVAL after the last one
  while (!(ec = Ioctl(fd, VIDIOC_QUERY_EXT_CTRL, &q))) {
    if (name == std::string_view(q.name, ::strnlen(q.name, sizeof(q.name)))) {
      out = q;
      return {};
    }
    q.id |= V4L2_CTRL_FLAG_NEXT_CTRL | V4L2_CTRL_FLAG_NEXT_COMPOUND;
  }
  if (ec != std::errc::invalid_argument) // EINVAL = 더 이상 컨트롤 없음
    return ec;
  return std::make_error_code(std::errc::no_such_device);
}

// nrv_register_writes 컨트롤 정보. 프로세스에서 처음 성공한 조회 결과를 계속
// 씁니다(뮤텍스로 보호, 스레드 안전). 조회가 실패하면 저장하지 않고 다음 호출
// 때 다시 시도합니다. 주의: 저장된 뒤에는 드라이버를 다시 적재해도, 다른 장치를
// 열어도 재확인하지 않습니다.
//   no_such_device : 컨트롤 없음(nrv_dvs 장치가 아님)
//   protocol_error : 형식이 다름(드라이버 불일치)
static std::error_code GetRegisterWritesCtl(std::int32_t fd,
                                            ::v4l2_query_ext_ctrl &out) {
  static std::mutex mutex;
  static ::v4l2_query_ext_ctrl
      ctrl{}; // id == 0: 아직 조회 안 됨 (컨트롤 ID는 0이 없음)

  std::lock_guard lock(mutex);
  if (ctrl.id == 0) {
    ::v4l2_query_ext_ctrl q{};
    if (auto ec = FindRegisterCtl(fd, kRegisterWritesName, q))
      return ec;
    if (q.type != V4L2_CTRL_TYPE_U32 || q.elem_size != sizeof(std::uint32_t) ||
        !(q.flags & V4L2_CTRL_FLAG_DYNAMIC_ARRAY) || q.nr_of_dims != 1)
      return std::make_error_code(std::errc::protocol_error);
    ctrl = q;
  }
  out = ctrl;
  return {};
}

Device::Impl::Impl(std::string_view dev) {
  std::string dev_str(dev);
  fd_ = ::open(dev_str.c_str(), O_RDWR | O_NONBLOCK | O_CLOEXEC);
  if (fd_ < 0)
    throw std::system_error(std::error_code(errno, std::system_category()),
                            "open " + dev_str);

  ::v4l2_query_ext_ctrl ctrl{};
  if (auto ec = GetRegisterWritesCtl(fd_, ctrl)) { // 첫 장치에서만 실제 조회
    ::close(fd_); // 생성자가 실패하면 소멸자가 불리지 않으므로 직접 닫음
    throw std::system_error(ec,
                            dev_str + ": " + std::string(kRegisterWritesName));
  }
}

Device::Impl::~Impl() {
  if (fd_ >= 0)
    ::close(fd_);
}

std::error_code
Device::Impl::SetRegisterList(std::span<const std::uint32_t> words) {
  ::v4l2_query_ext_ctrl ctrl{};
  if (auto ec = GetRegisterWritesCtl(fd_, ctrl))
    return ec;
  if (words.empty() || words.size() > ctrl.dims[0])
    return std::make_error_code(std::errc::invalid_argument);

  ::v4l2_ext_control c{
      .id = ctrl.id,
      .size = static_cast<std::uint32_t>(words.size_bytes()), // 바이트 단위
      .reserved2 = {},
      .p_u32 = const_cast<std::uint32_t *>(words.data()),
  };
  ::v4l2_ext_controls cs{
      .which = V4L2_CTRL_WHICH_CUR_VAL,
      .count = 1,
      .error_idx = 0,
      .request_fd = 0,
      .reserved = {},
      .controls = &c,
  };
  // VIDIOC_S_EXT_CTRLS: set nrv_register_writes; the driver stores the list
  // and writes it to the sensor at the next STREAMON
  return Ioctl(VIDIOC_S_EXT_CTRLS, &cs);
}

std::error_code Device::Impl::Ioctl(unsigned long req, void *arg) noexcept {
  return delta::Ioctl(fd_, req, arg);
}

std::error_code
Device::Impl::Poll(std::chrono::milliseconds timeout) noexcept {
  ::pollfd p{.fd = fd_, .events = POLLIN, .revents = 0};
  for (;;) {
    const int r = ::poll(&p, 1, static_cast<int>(timeout.count()));
    if (r > 0)
      return {};
    if (r == 0)
      return std::make_error_code(std::errc::timed_out);
    if (errno != EINTR)
      return std::error_code(errno, std::system_category());
  }
}

}; // namespace delta
#endif
