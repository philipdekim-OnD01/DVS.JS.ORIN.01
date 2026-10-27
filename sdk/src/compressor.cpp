#include "compressor.h"
#include <algorithm>
#include <span>
#include <utility>

namespace delta {

// raw 를 zstd 프레임 하나로 압축해 data 에 [CompressedFrame][zstd 프레임] 로 씀
static std::error_code Compress(ZSTD_CCtx *cctx, const std::vector<Block> &raw,
                                std::vector<Block> &data) {
  const std::span<const std::byte> input = std::as_bytes(std::span(raw));
  // 압축 결과가 아무리 커도 들어가게 잡고 (그러면 압축은 실패하지 않음),
  // CompressedFrame 뒤에 바로 압축한 뒤 실제 크기로 줄임 (Block 단위로 올림)
  constexpr std::size_t header = sizeof(CompressedFrame);
  data.resize((header + ZSTD_compressBound(input.size()) + sizeof(Block) - 1) / sizeof(Block));
  const std::span<std::byte> out = std::as_writable_bytes(std::span(data));
  const std::size_t bytes = ZSTD_compress2(cctx, out.data() + header, out.size() - header,
                                           input.data(), input.size());
  if (ZSTD_isError(bytes))
    return std::make_error_code(std::errc::not_enough_memory);
  const CompressedFrame frame{.bytes = static_cast<std::uint32_t>(bytes)};
  const std::span<const std::byte> frame_bytes = std::as_bytes(std::span(&frame, 1));
  std::copy(frame_bytes.begin(), frame_bytes.end(), out.begin());
  data.resize((header + bytes + sizeof(Block) - 1) / sizeof(Block));
  return {};
}

Compressor::Impl::Impl(std::unique_ptr<Pipe> upstream, ZSTD_CCtx *cctx, int level)
    : upstream_(std::move(upstream)), cctx_(cctx, &ZSTD_freeCCtx), level_(level) {}

std::error_code Compressor::Impl::Work(std::stop_token stop) {
  const std::unique_ptr<ZSTD_CCtx, decltype(&ZSTD_freeCCtx)> cctx(ZSTD_createCCtx(),
                                                                 &ZSTD_freeCCtx);
  if (!cctx)
    return std::make_error_code(std::errc::not_enough_memory);
  (void)ZSTD_CCtx_setParameter(cctx.get(), ZSTD_c_compressionLevel, level_);
  std::unique_lock lock(results_mutex_);
  ++workers_;
  std::vector<Block> raw, data;
  std::error_code ec;
  // 다른 스레드가 읽는 중이 아니고, Read 가 가져가지 않은 결과가 Work 수의 두 배가
  // 안 될 때 하나를 맡음
  // wait 는 stop 이 와도 조건이 참이면 true 를 돌려줌: 입력이 끊겨 결과가 안 쌓이면
  // 조건이 늘 참이라 stop 을 따로 봐야 끝남
  while (!stop.stop_requested() &&
         changed_.wait(lock, stop, [&] { return !taking_ && results_.size() < 2 * workers_; })) {
    taking_ = true;
    lock.unlock();
    bool discontinuous = false;
    ec = upstream_->Read(raw, discontinuous, std::chrono::milliseconds(100)); // stop 을 보려고 짧게
    lock.lock();
    taking_ = false;
    changed_.notify_all();
    if (ec == std::errc::timed_out) {
      ec = {};
      continue;
    }
    if (ec)
      break;
    const std::uint64_t index = given_ + results_.size(); // 이 결과의 순번
    results_.push_back({.data = {}, .discontinuous = discontinuous, .done = false, .ec = {}});
    if (!spare_.empty()) {
      data = std::move(spare_.back());
      spare_.pop_back();
    }
    lock.unlock();
    const std::error_code compressed = Compress(cctx.get(), raw, data);
    lock.lock();
    Result &result = results_[index - given_];
    std::swap(result.data, data);
    result.ec = compressed;
    result.done = true;
    changed_.notify_all();
  }
  --workers_;
  changed_.notify_all();
  return ec;
}

std::error_code
Compressor::Impl::Produce(std::vector<Block> &data, bool &discontinuous,
                          std::chrono::steady_clock::time_point deadline) {
  std::unique_lock lock(results_mutex_);
  // Work 가 맡은 것이 있으면 다음 순번의 결과를, Work 가 없으면 직접 압축
  if (!changed_.wait_until(lock, deadline, [&] {
        return results_.empty() ? !workers_ && !taking_ : results_.front().done;
      }))
    return std::make_error_code(std::errc::timed_out);
  if (!results_.empty()) {
    Result &result = results_.front();
    std::swap(data, result.data);
    discontinuous = result.discontinuous;
    const std::error_code ec = result.ec;
    spare_.push_back(std::move(result.data));
    results_.pop_front();
    ++given_;
    changed_.notify_all();
    return ec;
  }
  taking_ = true; // 그사이 시작한 Work 는 이것이 끝난 뒤에 맡음
  lock.unlock();
  const auto left = std::chrono::ceil<std::chrono::milliseconds>(
      deadline - std::chrono::steady_clock::now());
  std::error_code ec =
      upstream_->Read(raw_, discontinuous, std::max(left, std::chrono::milliseconds(0)));
  if (!ec)
    ec = Compress(cctx_.get(), raw_, data);
  lock.lock();
  taking_ = false;
  changed_.notify_all();
  return ec;
}

} // namespace delta
