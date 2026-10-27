// 빌드 전용(설치 안 됨): Compressor::Impl 정의. src/compressor.cpp(구현)와
// src/nrv_dvs/compressor.cpp(API)가 함께 씁니다.
#pragma once
#include "source.h"
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <nrv_dvs/compressor.h>
#include <nrv_dvs/pipe.h>
#include <stop_token>
#include <system_error>
#include <vector>
#include <zstd.h>

namespace delta {

class Compressor::Impl final : public Source::Impl {
public:
  // cctx: level 을 정한 압축 컨텍스트 (Produce 가 직접 압축할 때 씀)
  Impl(std::unique_ptr<Pipe> upstream, ZSTD_CCtx *cctx, int level);

  std::error_code Work(std::stop_token stop);

private:
  // Work 가 맡은 것 하나 (upstream 순서)
  struct Result {
    std::vector<Block> data;
    bool discontinuous;
    bool done; // 압축이 끝남
    std::error_code ec;
  };

  std::error_code
  Produce(std::vector<Block> &data, bool &discontinuous,
          std::chrono::steady_clock::time_point deadline) override;

  std::unique_ptr<Pipe> upstream_;
  std::unique_ptr<ZSTD_CCtx, decltype(&ZSTD_freeCCtx)> cctx_;
  int level_;
  std::vector<Block> raw_; // Produce 가 직접 압축할 때 upstream 에서 받은 것

  std::mutex results_mutex_;                  // 아래 상태 보호
  std::condition_variable_any changed_;       // 결과가 끝나거나 빠지거나 Work 가 끝남
  std::deque<Result> results_;                // Work 가 맡은 것 (오래된 것부터)
  std::uint64_t given_ = 0;                   // Produce 가 가져간 결과 수
  std::vector<std::vector<Block>> spare_;     // 가져간 결과의 메모리 (Work 가 다시 씀)
  std::size_t workers_ = 0;                   // 실행 중인 Work 수
  // 누군가 upstream 을 읽는 중 (읽기와 results_ 자리 잡기를 한 스레드씩 해서
  // 순서를 지킴. mutex 대신 이것을 기다리므로 Work 가 stop 을 놓치지 않음)
  bool taking_ = false;
};

} // namespace delta
