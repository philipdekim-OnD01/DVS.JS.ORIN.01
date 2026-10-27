#include "std_source.h" // StdSource::Impl
#include "stream.h"     // kWidth, kHeight
#include <memory>
#include <nrv_dvs/std_source.h>
#include <system_error>
#include <utility>

namespace delta {

StdSource::StdSource(std::unique_ptr<std::istream> in, Format format) {
  if (!in)
    throw std::system_error(std::make_error_code(std::errc::invalid_argument),
                            "StdSource: no input stream");
  const auto f = static_cast<std::size_t>(format);
  impl_ = std::make_shared<Impl>(std::move(in), std::size_t{kWidth[f]} * kHeight[f]);
}

StdSource::~StdSource() = default;
StdSource::StdSource(StdSource &&) noexcept = default;
StdSource &StdSource::operator=(StdSource &&) noexcept = default;

} // namespace delta
