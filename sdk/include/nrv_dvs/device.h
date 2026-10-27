#pragma once
#include <cstdint>
#include <memory>
#include <nrv_dvs/export.h>
#include <span>
#include <string_view>
#include <system_error>

namespace delta {

class NRV_DVS_API Device {
  friend class Stream;

public:
  explicit Device(std::string_view dev);
  ~Device();
  Device(Device &&) noexcept;
  Device &operator=(Device &&) noexcept;

  // ops: NRV_DVS_OP(addr, value) 로 만든 opcode 목록 (nrv_dvs/opcode.h)
  std::error_code SetRegisterList(const std::span<const std::uint32_t> ops);

private:
  class NRV_DVS_NO_EXPORT Impl; // pimpl: .so 밖으로 심볼을 내보내지 않음
  std::unique_ptr<Impl> impl_;
};

}; // namespace delta