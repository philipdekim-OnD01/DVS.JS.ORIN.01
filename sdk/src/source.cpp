#include "source.h"
#include <algorithm>
#include <utility>

namespace delta {

void Source::Impl::Attach(PipeQueue &pipe) {
  std::lock_guard lock(mutex_);
  pipes_.push_back(&pipe);
}

void Source::Impl::Detach(PipeQueue &pipe) {
  std::lock_guard lock(mutex_);
  std::erase(pipes_, &pipe);
  changed_.notify_all(); // 이 파이프 때문에 멈춰 있었을 수 있음
}

std::error_code Source::Impl::Read(PipeQueue &pipe, std::vector<Block> &data,
                                   bool &discontinuous,
                                   std::chrono::milliseconds timeout) {
  const auto deadline = std::chrono::steady_clock::now() + timeout;
  std::unique_lock lock(mutex_);
  for (;;) {
    // DropPipe: 기다리지 않고 만들 수 있는 것을 모두 받아 둔 뒤 (넘치면 오래된
    // 것부터 버려짐) 남은 것 중 가장 오래된 것을 읽음. 기다려서 하나를 받은
    // 뒤에도 그사이 준비된 것까지 받음.
    if (pipe.drop)
      while (!producing_ && !Full() && Ready())
        if (auto ec = ProduceOne(lock, deadline))
          return ec;
    if (!pipe.entries.empty()) {
      PipeQueue::Entry &entry = pipe.entries.front();
      std::swap(data, entry.data);
      discontinuous = entry.discontinuous;
      pipe.spare.push_back(std::move(entry.data)); // 사용자가 갖고 있던 메모리
      pipe.entries.pop_front();
      changed_.notify_all(); // WaitPipe 에 자리가 남
      return {};
    }
    // 다른 파이프가 만드는 중이거나 꽉 찬 WaitPipe 가 있으면 기다리고,
    // 아니면 이 호출이 만듦
    if (producing_ || Full()) {
      if (changed_.wait_until(lock, deadline) == std::cv_status::timeout)
        return std::make_error_code(std::errc::timed_out);
      continue;
    }
    if (auto ec = ProduceOne(lock, deadline))
      return ec;
  }
}

std::error_code
Source::Impl::ProduceOne(std::unique_lock<std::mutex> &lock,
                         std::chrono::steady_clock::time_point deadline) {
  producing_ = true;
  lock.unlock();
  bool discontinuous = false;
  const std::error_code ec = Produce(made_, discontinuous, deadline);
  lock.lock();
  producing_ = false;
  // 파이프마다 복사해 넣음. 마지막 파이프는 복사 대신 made_ 를 맞바꿈.
  // (복사는 resize + std::copy: -O0 에서도 memmove 로 감. std::ranges::copy 는 -O0
  //  에서 바이트 단위 반복이라 느림)
  for (std::size_t i = 0; !ec && i < pipes_.size(); ++i) {
    PipeQueue &pipe = *pipes_[i];
    const bool last = i + 1 == pipes_.size();
    bool dropped = false;
    if (pipe.entries.size() >= pipe.capacity) {
      // DropPipe 가 꽉 참 (WaitPipe 는 Full() 이라 여기 오지 않음): 가장
      // 오래된 것을 버리고, 그 바로 뒤 데이터에 discontinuous 표시
      pipe.spare.push_back(std::move(pipe.entries.front().data));
      pipe.entries.pop_front();
      ++pipe.dropped;
      dropped = true;
      if (!pipe.entries.empty())
        pipe.entries.front().discontinuous = true;
    }
    std::vector<Block> copy;
    if (!pipe.spare.empty()) {
      copy = std::move(pipe.spare.back());
      pipe.spare.pop_back();
    }
    if (last) {
      std::swap(copy, made_);
    } else {
      copy.resize(made_.size());
      std::copy(made_.begin(), made_.end(), copy.begin());
    }
    pipe.entries.push_back({.data = std::move(copy),
                            .discontinuous = discontinuous || (dropped && pipe.entries.empty())});
  }
  changed_.notify_all();
  return ec;
}

bool Source::Impl::Full() const noexcept {
  return std::ranges::any_of(pipes_, [](const PipeQueue *pipe) {
    return !pipe->drop && pipe->entries.size() >= pipe->capacity;
  });
}

} // namespace delta
