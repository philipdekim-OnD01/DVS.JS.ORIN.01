#include "decoder.h" // Decoder::Impl
#include <memory>
#include <nrv_dvs/decoder.h>
#include <system_error>
#include <utility>

namespace delta {

Decoder::Decoder(std::unique_ptr<Pipe> upstream, Type type) {
  if (!upstream)
    throw std::system_error(std::make_error_code(std::errc::invalid_argument),
                            "Decoder: no upstream pipe");
  if (type == Type::kRaw2)
    impl_ = std::make_shared<Impl::Raw2>(std::move(upstream));
  else
    impl_ = std::make_shared<Impl::GroupAer>(std::move(upstream));
}

Decoder::~Decoder() = default;
Decoder::Decoder(Decoder &&) noexcept = default;
Decoder &Decoder::operator=(Decoder &&) noexcept = default;

DecodeStats Decoder::Stats() const {
  return impl_ ? static_cast<const Impl &>(*impl_).Stats() : DecodeStats();
}

GroupAerDecoder::GroupAerDecoder(std::unique_ptr<Pipe> upstream)
    : Decoder(std::move(upstream), Type::kGroupAer) {}

Raw2Decoder::Raw2Decoder(std::unique_ptr<Pipe> upstream)
    : Decoder(std::move(upstream), Type::kRaw2) {}

} // namespace delta
