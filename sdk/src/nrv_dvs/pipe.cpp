#include "pipe.h" // Pipe::Impl (Wait, Drop), Source::Impl
#include <format>
#include <memory>
#include <nrv_dvs/pipe.h>
#include <system_error>

namespace delta {

// Pipe::Type 순서대로
static constexpr const char *kPipeNames[] = {"WaitPipe", "DropPipe"};

Pipe::Pipe(const Source &source, Type type, std::size_t capacity) {
  const char *name = kPipeNames[static_cast<std::size_t>(type)];
  if (!source.impl_)
    throw std::system_error(
        std::make_error_code(std::errc::bad_file_descriptor),
        std::format("{}: moved-from Source", name));
  if (!capacity)
    throw std::system_error(std::make_error_code(std::errc::invalid_argument),
                            std::format("{}: capacity 0", name));
  if (type == Type::kWait)
    impl_ = std::make_unique<Impl::Wait>(source.impl_, capacity);
  else
    impl_ = std::make_unique<Impl::Drop>(source.impl_, capacity);
}

Pipe::~Pipe() = default;
Pipe::Pipe(Pipe &&) noexcept = default;
Pipe &Pipe::operator=(Pipe &&) noexcept = default;

std::error_code Pipe::Read(std::vector<Block> &data, bool &discontinuous,
                           std::chrono::milliseconds timeout) {
  if (impl_)
    return impl_->Read(data, discontinuous, timeout);
  return std::make_error_code(std::errc::bad_file_descriptor);
}

std::error_code Pipe::Dropped(std::uint64_t &dropped) const noexcept {
  if (!impl_)
    return std::make_error_code(std::errc::bad_file_descriptor);
  dropped = impl_->Dropped();
  return {};
}

WaitPipe::WaitPipe(const Source &source, std::size_t capacity)
    : Pipe(source, Type::kWait, capacity) {}

DropPipe::DropPipe(const Source &source, std::size_t capacity)
    : Pipe(source, Type::kDrop, capacity) {}

} // namespace delta
