#include "stream.h" // Stream::Impl
#include <memory>
#include <nrv_dvs/stream.h>
#include <system_error>

namespace delta {
// Device 의 private(impl_) 접근은 friend 인 Stream 의 멤버에서만 가능
Stream::Stream(Device dev, Format format) {
  if (!dev.impl_)
    throw std::system_error(
        std::make_error_code(std::errc::bad_file_descriptor),
        "Stream: moved-from Device");
  impl_ = std::make_shared<Impl>(std::move(dev), format);
}

Stream::~Stream() = default;
Stream::Stream(Stream &&) noexcept = default;
Stream &Stream::operator=(Stream &&) noexcept = default;

StreamStats Stream::Stats() const {
  return impl_ ? static_cast<const Impl &>(*impl_).Stats() : StreamStats();
}

}; // namespace delta
