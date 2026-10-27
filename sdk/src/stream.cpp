#include "stream.h"
#include "device.h"
#include <algorithm>
#include <linux/videodev2.h>

namespace delta {

std::error_code Stream::Impl::Queue(std::uint32_t index) noexcept {
  // VI 는 64 바이트 정렬 주소만 받음 (Block 의 정렬)
  std::vector<Block> &buffer = buffers_[index];
  ::v4l2_buffer b{
      .index = index,
      .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
      .bytesused = 0,
      .flags = 0,
      .field = 0,
      .timestamp = {},
      .timecode = {},
      .sequence = 0,
      .memory = V4L2_MEMORY_USERPTR,
      .m = {.userptr = reinterpret_cast<unsigned long>(buffer.data())},
      .length = static_cast<std::uint32_t>(buffer.size() * sizeof(Block)),
      .reserved2 = 0,
      .request_fd = 0,
  };
  // VIDIOC_QBUF: hand the empty buffer to the driver so VI can fill it
  return device_.Ioctl(VIDIOC_QBUF, &b);
}

Stream::Impl::Impl(Device dev, Format format)
    : dev_(std::move(dev)), device_(*dev_.impl_) {
  const std::uint32_t width = kWidth[static_cast<std::size_t>(format)];
  const std::uint32_t height = kHeight[static_cast<std::size_t>(format)];
  const std::size_t frame_bytes = std::size_t{width} * height;
  frame_blocks_ = frame_bytes / sizeof(Block);
  try {
    ::v4l2_format fmt{
        .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
        .fmt = {.pix = {.width = width,
                        .height = height,
                        .pixelformat = V4L2_PIX_FMT_SRGGB8,
                        .field = V4L2_FIELD_NONE,
                        .bytesperline = 0,
                        .sizeimage = 0,
                        .colorspace = 0,
                        .priv = 0,
                        .flags = 0,
                        .ycbcr_enc = 0,
                        .quantization = 0,
                        .xfer_func = 0}},
    };
    // VIDIOC_S_FMT: set the capture format (width x height RAW8; the driver
    // picks the DT mode of that size) and get back what it applied, including
    // bytesperline and sizeimage
    if (auto ec = device_.Ioctl(VIDIOC_S_FMT, &fmt))
      throw std::system_error(ec, "VIDIOC_S_FMT");
    // 채워진 버퍼를 그대로 내보내므로 줄 끝 여백도, 뒤쪽 여분도 없어야 함
    if (fmt.fmt.pix.width != width || fmt.fmt.pix.height != height ||
        fmt.fmt.pix.bytesperline != width || fmt.fmt.pix.sizeimage != frame_bytes ||
        frame_bytes % sizeof(Block))
      throw std::system_error(std::make_error_code(std::errc::protocol_error),
                              "unexpected capture format");

    ::v4l2_requestbuffers req{
        .count = kBufferCount,
        .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
        .memory = V4L2_MEMORY_USERPTR,
        .capabilities = 0,
        .flags = 0,
        .reserved = {},
    };
    // VIDIOC_REQBUFS: set up kBufferCount buffer slots whose memory we provide
    // (USERPTR); req.count returns the number actually set up
    if (auto ec = device_.Ioctl(VIDIOC_REQBUFS, &req))
      throw std::system_error(ec, "VIDIOC_REQBUFS");
    buffers_.resize(req.count);
    for (std::uint32_t i = 0; i < req.count; ++i) {
      buffers_[i].resize(frame_blocks_);
      if (auto ec = Queue(i))
        throw std::system_error(ec, "VIDIOC_QBUF");
    }

    ::v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    // VIDIOC_STREAMON: start capturing; the driver writes the stored register
    // list to the sensor and VI starts filling queued buffers
    if (auto ec = device_.Ioctl(VIDIOC_STREAMON, &type))
      throw std::system_error(ec, "VIDIOC_STREAMON");
    streaming_ = true;
  } catch (...) {
    Release(); // 생성자가 실패하면 소멸자가 불리지 않으므로 직접 정리
    throw;
  }
}

Stream::Impl::~Impl() { Release(); }

void Stream::Impl::Release() noexcept {
  ::v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
  if (streaming_)
    // VIDIOC_STREAMOFF: stop capturing and take back all queued buffers
    // (result ignored: nothing to do on failure during cleanup)
    (void)device_.Ioctl(VIDIOC_STREAMOFF, &type);
  streaming_ = false;
  if (!buffers_.empty()) {
    ::v4l2_requestbuffers req{
        .count = 0, // 버퍼 칸 해제
        .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
        .memory = V4L2_MEMORY_USERPTR,
        .capabilities = 0,
        .flags = 0,
        .reserved = {},
    };
    // VIDIOC_REQBUFS with count 0: the driver releases all buffer slots and
    // stops referring to our memory, so it can be freed below
    (void)device_.Ioctl(VIDIOC_REQBUFS, &req);
  }
  buffers_.clear();
}

std::error_code
Stream::Impl::Produce(std::vector<Block> &data, bool &discontinuous,
                      std::chrono::steady_clock::time_point deadline) {
  ::v4l2_buffer b{
      .index = 0,
      .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
      .bytesused = 0,
      .flags = 0,
      .field = 0,
      .timestamp = {},
      .timecode = {},
      .sequence = 0,
      .memory = V4L2_MEMORY_USERPTR,
      .m = {.userptr = 0},
      .length = 0,
      .reserved2 = 0,
      .request_fd = 0,
  };
  bool starved = false;
  for (;;) {
    const auto left = std::chrono::ceil<std::chrono::milliseconds>(
        deadline - std::chrono::steady_clock::now());
    if (auto ec = device_.Poll(std::max(left, std::chrono::milliseconds(0))))
      return ec;
    // VIDIOC_DQBUF: take a filled buffer from the driver (index, flags,
    // sequence, timestamp); EAGAIN if none is ready yet
    if (auto ec = device_.Ioctl(VIDIOC_DQBUF, &b)) {
      if (ec == std::errc::resource_unavailable_try_again)
        continue;
      return ec;
    }
    last_sequence_ = b.sequence;
    {
      std::lock_guard lock(gaps_mutex_);
      // 앞서 기록한 고갈 지점을 지난 첫 버퍼 (CRC 로 버린 버퍼였으면 다음 버퍼에)
      starved = starved || std::erase_if(gaps_after_, [&](std::uint32_t s) {
                             return s < b.sequence;
                           }) != 0;
    }
    CheckStarved();
    if (!(b.flags & V4L2_BUF_FLAG_ERROR))
      break;
    ++work_.error_buffers;
    { // ERROR만 이어지거나 곧바로timeout이 와도 관측오류가 Stats에즉시보임.
      std::lock_guard lock(stats_mutex_);
      stats_.error_buffers = work_.error_buffers;
    }
    // 수신 중 깨진 버퍼(CSI CRC 오류 등)는 버리고 다음 버퍼를 기다림.
    // sequence 는 하나 건너뜀.
    // VIDIOC_QBUF: give the corrupted buffer straight back to the driver
    if (auto ec = device_.Ioctl(VIDIOC_QBUF, &b))
      return ec;
  }
  // QBUF 가 b 를 다시 채우므로 sequence, 시각은 먼저 꺼냄
  const std::uint32_t sequence = b.sequence;
  const std::int64_t ns = std::int64_t{b.timestamp.tv_sec} * 1'000'000'000 +
                          std::int64_t{b.timestamp.tv_usec} * 1'000;
  // 채워진 버퍼를 복사 없이 data 로 내보내고, data 가 갖고 있던 메모리를 그
  // 번호의 빈 버퍼로 드라이버에 넘김
  data.resize(frame_blocks_);
  std::swap(data, buffers_[b.index]);
  CheckStarved(); // 돌려주기 직전까지 고갈이었는지
  if (auto ec = Queue(b.index))
    return ec;

  const bool gap = have_previous_ && sequence != work_.sequence + 1;
  if (gap)
    work_.dropped += sequence - work_.sequence - 1;
  if (starved && have_previous_) {
    ++work_.starved;
    work_.starved_ns += ns - previous_ns_;
  }
  ++work_.buffers;
  work_.sequence = sequence;
  {
    std::lock_guard lock(stats_mutex_);
    stats_ = work_;
  }
  discontinuous = gap || starved;
  have_previous_ = true;
  previous_ns_ = ns;
  return {};
}

StreamStats Stream::Impl::Stats() const {
  std::lock_guard lock(stats_mutex_);
  return stats_;
}

bool Stream::Impl::Ready() noexcept {
  return !device_.Poll(std::chrono::milliseconds(0));
}

void Stream::Impl::CheckStarved() noexcept {
  std::uint32_t last = last_sequence_;
  for (std::uint32_t i = 0; i < buffers_.size(); ++i) {
    ::v4l2_buffer b{
        .index = i,
        .type = V4L2_BUF_TYPE_VIDEO_CAPTURE,
        .bytesused = 0,
        .flags = 0,
        .field = 0,
        .timestamp = {},
        .timecode = {},
        .sequence = 0,
        .memory = V4L2_MEMORY_USERPTR,
        .m = {.userptr = 0},
        .length = 0,
        .reserved2 = 0,
        .request_fd = 0,
    };
    // VIDIOC_QUERYBUF: read buffer i's state (QUEUED: VI may still fill it,
    // DONE: filled and waiting for DQBUF, with its sequence)
    if (device_.Ioctl(VIDIOC_QUERYBUF, &b) || (b.flags & V4L2_BUF_FLAG_QUEUED))
      return;
    if (b.flags & V4L2_BUF_FLAG_DONE)
      last = std::max(last, b.sequence);
  }
  std::lock_guard lock(gaps_mutex_);
  if (gaps_after_.empty() || gaps_after_.back() != last)
    gaps_after_.push_back(last);
}

} // namespace delta
