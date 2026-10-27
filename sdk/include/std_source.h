// 빌드 전용(설치 안 됨): StdSource::Impl 정의. src/std_source.cpp(구현)와
// src/nrv_dvs/std_source.cpp(API)가 함께 씁니다.
#pragma once
#include "source.h"
#include <chrono>
#include <cstddef>
#include <istream>
#include <memory>
#include <nrv_dvs/std_source.h>
#include <system_error>
#include <vector>

namespace delta {

class StdSource::Impl final : public Source::Impl {
public:
  Impl(std::unique_ptr<std::istream> in, std::size_t buffer_bytes);

private:
  std::error_code
  Produce(std::vector<Block> &data, bool &discontinuous,
          std::chrono::steady_clock::time_point deadline) override;

  std::unique_ptr<std::istream> in_;
  std::size_t buffer_bytes_; // Block 의 배수
};

} // namespace delta
