#include "std_source.h"
#include <utility>

namespace delta {

StdSource::Impl::Impl(std::unique_ptr<std::istream> in, std::size_t buffer_bytes)
    : in_(std::move(in)), buffer_bytes_(buffer_bytes) {}

std::error_code StdSource::Impl::Produce(std::vector<Block> &data, bool &discontinuous,
                                         std::chrono::steady_clock::time_point) {
  data.resize(buffer_bytes_ / sizeof(Block));
  const auto bytes = static_cast<std::streamsize>(buffer_bytes_);
  in_->read(reinterpret_cast<char *>(data.data()), bytes);
  if (in_->gcount() != bytes)
    return std::make_error_code(in_->bad() ? std::errc::io_error
                                           : std::errc::no_message_available);
  discontinuous = false;
  return {};
}

} // namespace delta
