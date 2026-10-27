// 빌드 전용(설치 안 됨): Decoder::Impl 과 형식별 구현(GroupAer, Raw2) 정의.
// src/decoder.cpp(공통), src/group_aer_decoder.cpp, src/raw2_decoder.cpp(구현)와
// src/nrv_dvs/decoder.cpp(API)가 함께 씁니다.
#pragma once
#include "source.h"
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <nrv_dvs/decoder.h>
#include <nrv_dvs/pipe.h>
#include <span>
#include <system_error>
#include <vector>

namespace delta {

constexpr std::uint64_t kTimestampWrap = (std::uint64_t{1} << 22) * 1000; // us (기준 22비트 ms)

// 형식에 상관없는 부분: upstream 에서 원본 버퍼를 받아 Parse 에 넘기고, 구현이
// Finish 로 끝낸 센서 프레임을 하나씩 내보냄
class Decoder::Impl : public Source::Impl {
public:
  class GroupAer;
  class Raw2;

  DecodeStats Stats() const;

protected:
  explicit Impl(std::unique_ptr<Pipe> upstream);

  // 원본 버퍼 하나를 해석 (끝난 센서 프레임은 Finish 로). Produce 안에서만 불림
  virtual void Parse(std::span<const std::byte> raw) noexcept = 0;
  // 원본이 끊김: 만들던 프레임을 버리고 다시 동기화할 상태로
  virtual void Reset() noexcept = 0;
  // groups_ 의 이벤트로 센서 프레임 하나를 끝냄. period_us 0: 바로 앞 프레임을
  // 내보내지 못함 (discontinuous)
  void Finish(std::uint64_t start_us, std::uint32_t period_us) noexcept;

  // 아래는 Produce 안에서만 씀 (한 번에 하나만 실행, Ready 는 그 밖에서만 불림)
  std::vector<EventGroup> groups_; // 만드는 중인 CID 0 센서 프레임의 이벤트
  DecodeStats work_;               // Produce 안에서 세는 값

private:
  std::error_code
  Produce(std::vector<Block> &data, bool &discontinuous,
          std::chrono::steady_clock::time_point deadline) override;
  bool Ready() noexcept override;

  std::unique_ptr<Pipe> upstream_;
  std::vector<Block> raw_; // upstream 에서 받은 원본
  // 지금 원본 버퍼에서 끝난 프레임들: [SensorFrame][EventGroup...] 를 이어 붙임
  std::vector<std::byte> done_;
  struct Finished {
    std::size_t offset; // done_ 안의 프레임 시작 위치
    bool discontinuous; // 바로 앞 센서 프레임을 내보내지 못함 (period_us 0)
  };
  std::vector<Finished> finished_;
  std::size_t next_ = 0; // 다음에 내보낼 finished_ 번호

  mutable std::mutex stats_mutex_;
  DecodeStats stats_; // 원본 버퍼마다 work_ 를 복사 (Stats() 용)
};

// group AER: 4바이트 단어의 연속 (src/group_aer_decoder.cpp)
class Decoder::Impl::GroupAer final : public Decoder::Impl {
public:
  explicit GroupAer(std::unique_ptr<Pipe> upstream);

private:
  // CID 하나의 해석 상태 (기본값 = 동기화 안 됨)
  struct Context {
    int x = -1;        // 현재 컬럼 (-1: 모름)
    bool mo = false;   // 컬럼 단어의 mo: 둘째 그룹 = 첫 그룹 -/+ delta
    int frame = -1;    // 컬럼 단어의 센서 프레임 번호
    int last_end = -1; // 마지막 프레임 끝 단어의 번호
    std::uint32_t ref = 0; // 기준 타임스탬프 (ms)
    bool ref_ok = false, sub_ok = false, epoch_ready = false;
  };

  void Parse(std::span<const std::byte> raw) noexcept override;
  void Reset() noexcept override;
  void Word(const std::uint8_t *b) noexcept;

  Context cid_[2];
  // 만드는 중인 CID 0 센서 프레임 (원본 버퍼를 넘어 이어짐)
  bool building_ = false; // 시작 타임스탬프를 봤고 그 뒤로 끊기지 않음
  std::uint64_t start_us_ = 0;
  // 마지막으로 끝낸 프레임 (다음 프레임의 period_us 용, 번호 -1: 없음)
  int previous_ = -1;
  std::uint64_t previous_us_ = 0;
};

// RAW2: 180 B 열의 연속 (src/raw2_decoder.cpp)
class Decoder::Impl::Raw2 final : public Decoder::Impl {
public:
  explicit Raw2(std::unique_ptr<Pipe> upstream);

private:
  static constexpr std::size_t kColumnBytes = 180; // 720화소 x 2비트
  // .117 표본: embedded16에서는959열, embedded1에서는960열을 관측함.
  // 다음 header가 경계를 확정하며 959열 출력은 short-frame 진단으로 구분.
  // 2200FPS RAW2 설정은 데이터955열을 보냄(x 좌표 대응 미확인).
  static constexpr int kMinDataColumns = 955;
  static constexpr int kMaxDataColumns = static_cast<int>(kImageWidth);

  void Parse(std::span<const std::byte> raw) noexcept override;
  void Reset() noexcept override;
  void Column(const std::uint8_t *c) noexcept; // 열 하나 (kColumnBytes)
  void CompleteFrame() noexcept; // 다음 유효 header에서만 앞 데이터 영역을 닫음

  // 열 경계: 머리말 열의 시작(0xCC 4개)을 찾을 때까지 동기화 안 됨
  bool synced_ = false;
  int signature_ = 0; // 동기화 전: 이어진 0xCC 바이트 수
  // 원본 버퍼 경계에 걸친 열 (filled_ 바이트까지 받음)
  std::array<std::uint8_t, kColumnBytes> column_{};
  std::size_t filled_ = 0;
  // 만드는 중인 센서 프레임
  // 머리말 개수는 세지 않음. 연속된 유효 머리말 뒤 첫 데이터 열에서 시작.
  bool embedded_ = false;
  bool boundary_seen_ = false; // 동기화 이후 데이터→머리말 경계를 본 경우만 수용
  bool metadata_ok_ = false;   // 중간부터 잡은 묶음/서로 다른 frame number는 버림
  int data_ = -1;    // 받은 데이터 열 수 (-1: 머리말을 다 보지 못해 버리는 중)
  std::uint32_t number_ = 0;   // 프레임 번호
  unsigned previous_sub_ = 0;       // 첫 머리말의 sub, 시각의 물리적 의미는 미확정
  bool started_ = false;            // sub 최초 변화를 관측함 (시각 확정은 아님)
  std::uint32_t selected_word_ = 0; // 동일 원시 word의 ref/sub만 사용
  unsigned selected_repeats_ = 0;  // 2에서 포화: 후보 뒤 반복을 적어도 한 번 확인
  bool timestamp_ambiguous_ = false; // 선택 뒤 다른 word가 나오면 묶음 전체가 미확정
  std::uint64_t start_us_ = 0;
  std::uint64_t fallback_us_ = 0;    // 변화가 없으면 Legacy처럼 마지막 머리말 +16 사용
  // 마지막으로 끝낸 프레임 (다음 프레임의 period_us 용)
  bool have_previous_ = false;
  bool previous_time_selected_ = false; // fallback frame과의 간격은 미확정(0)
  std::uint32_t previous_number_ = 0;
  std::uint64_t previous_us_ = 0;
};

} // namespace delta
