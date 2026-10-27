#include "decoder.h"
#include <algorithm>
#include <span>
#include <utility>

namespace delta {

Decoder::Impl::Impl(std::unique_ptr<Pipe> upstream)
    : upstream_(std::move(upstream)) {}

DecodeStats Decoder::Impl::Stats() const {
  std::lock_guard lock(stats_mutex_);
  return stats_;
}

bool Decoder::Impl::Ready() noexcept { return next_ < finished_.size(); }

std::error_code
Decoder::Impl::Produce(std::vector<Block> &data, bool &discontinuous,
                       std::chrono::steady_clock::time_point deadline) {
  // 내보낼 프레임이 없으면 원본 버퍼를 받아 해석 (프레임이 하나라도 끝날 때까지)
  while (next_ == finished_.size()) {
    done_.clear();
    finished_.clear();
    next_ = 0;
    const auto left = std::chrono::ceil<std::chrono::milliseconds>(
        deadline - std::chrono::steady_clock::now());
    bool skipped = false;
    if (auto ec = upstream_->Read(raw_, skipped,
                                  std::max(left, std::chrono::milliseconds(0))))
      return ec;
    if (skipped) {
      ++work_.discontinuities;
      Reset();
    }
    Parse(std::as_bytes(std::span(raw_)));
    ++work_.buffers;
    std::lock_guard lock(stats_mutex_);
    stats_ = work_;
  }
  const Finished &frame = finished_[next_];
  const std::size_t end = ++next_ < finished_.size() ? finished_[next_].offset : done_.size();
  const std::span<const std::byte> bytes =
      std::span(done_).subspan(frame.offset, end - frame.offset);
  // Block 단위로 올림 (끝의 남는 바이트는 내용 없음)
  data.resize((bytes.size() + sizeof(Block) - 1) / sizeof(Block));
  std::copy(bytes.begin(), bytes.end(), std::as_writable_bytes(std::span(data)).begin());
  discontinuous = frame.discontinuous;
  return {};
}

void Decoder::Impl::Finish(std::uint64_t start_us, std::uint32_t period_us) noexcept {
  const SensorFrame frame{.timestamp_us = start_us,
                          .period_us = period_us,
                          .groups = static_cast<std::uint32_t>(groups_.size())};
  // [SensorFrame][EventGroup...] 의 바이트 표현 (크기는 타입에서 정해짐)
  const std::span<const std::byte> header = std::as_bytes(std::span(&frame, 1));
  const std::span<const std::byte> events = std::as_bytes(std::span(groups_));
  const std::size_t at = done_.size();
  finished_.push_back({.offset = at, .discontinuous = period_us == 0});
  done_.resize(at + header.size() + events.size());
  auto out = done_.begin() + static_cast<std::ptrdiff_t>(at);
  out = std::copy(header.begin(), header.end(), out);
  std::copy(events.begin(), events.end(), out);
  ++work_.frames;
}

} // namespace delta
