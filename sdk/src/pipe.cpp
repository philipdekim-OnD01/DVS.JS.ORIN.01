#include "pipe.h"
#include <utility>

namespace delta {

Pipe::Impl::Impl(Type type, std::size_t capacity)
    : queue_{.drop = type == Type::kDrop,
             .capacity = capacity,
             .entries = {},
             .spare = {},
             .dropped = 0} {}

Pipe::Impl::Wait::Wait(std::shared_ptr<Source::Impl> source, std::size_t capacity)
    : Impl(Type::kWait, capacity), source_(std::move(source)) {
  source_->Attach(queue_);
}

Pipe::Impl::Wait::~Wait() { source_->Detach(queue_); }

std::error_code Pipe::Impl::Wait::Read(std::vector<Block> &data, bool &discontinuous,
                                       std::chrono::milliseconds timeout) {
  return source_->Read(queue_, data, discontinuous, timeout);
}

Pipe::Impl::Drop::Drop(const std::shared_ptr<Source::Impl> &source, std::size_t capacity)
    : Impl(Type::kDrop, capacity), source_(source) {
  source->Attach(queue_);
}

Pipe::Impl::Drop::~Drop() {
  if (auto source = source_.lock())
    source->Detach(queue_);
}

std::error_code Pipe::Impl::Drop::Read(std::vector<Block> &data, bool &discontinuous,
                                       std::chrono::milliseconds timeout) {
  auto source = source_.lock();
  if (!source)
    return std::make_error_code(std::errc::broken_pipe);
  return source->Read(queue_, data, discontinuous, timeout);
}

} // namespace delta
