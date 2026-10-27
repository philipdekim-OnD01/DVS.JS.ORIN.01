#pragma once
#include <memory>
#include <nrv_dvs/export.h>

namespace delta {

// 데이터를 내보내는 것(Stream, Decoder)의 공통 인터페이스. 읽기는 Pipe 로
// 합니다(nrv_dvs/pipe.h). 데이터는 붙은 파이프마다 복사해 넣습니다.
class NRV_DVS_API Source {
  friend class Pipe;

public:
  virtual ~Source();

protected:
  Source() = default;
  Source(Source &&) noexcept = default;
  Source &operator=(Source &&) noexcept = default;

  class NRV_DVS_NO_EXPORT Impl; // 파이프 관리 (구현체가 상속)
  std::shared_ptr<Impl> impl_;  // WaitPipe 가 함께 소유
};

} // namespace delta
