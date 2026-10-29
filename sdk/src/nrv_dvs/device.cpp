#include "device.h" // Device::Impl
#include <memory>
#include <nrv_dvs/device.h>
#include <system_error>

namespace delta {

Device::Device(std::string_view dev)
    : impl_(std::make_unique<Device::Impl>(dev)) {}
Device::~Device() = default;
Device::Device(Device &&) noexcept = default;
Device &Device::operator=(Device &&) noexcept = default;

std::error_code
Device::SetRegisterList(const std::span<const std::uint32_t> ops) {
  if (impl_)
    return impl_->SetRegisterList(ops);
  return std::make_error_code(std::errc::bad_file_descriptor);
}

} // namespace delta
