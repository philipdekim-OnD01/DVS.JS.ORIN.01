#pragma once
#include <cstdint>
#include <memory>
#include <nrv_dvs/export.h>
#include <nrv_dvs/pipe.h>
#include <nrv_dvs/source.h>

namespace delta {

// 센서 화소 배열 크기 (x < kImageWidth, y < kImageHeight)
inline constexpr std::uint32_t kImageWidth = 960;
inline constexpr std::uint32_t kImageHeight = 720;

// Decoder 출력 하나 = 센서 프레임 하나. SensorFrame 바로 뒤에 EventGroup 이
// groups 개 붙어 있습니다(std::memcpy 로 꺼내 쓰세요). 마지막 EventGroup 뒤
// Block 끝까지의 바이트는 내용이 없습니다.
struct SensorFrame {
  std::uint64_t timestamp_us; // 센서 시각 후보 (2^22 ms 주기). RAW2 선택 규칙은 아래 참조.
  std::uint32_t period_us;    // 바로 앞 프레임과 시각 후보의 간격. RAW2는 물리 시각/단위 미교정.
                              // 누락 또는 시각 미확정이면 0. 0이 아니어도 광학 취득시각 보장은 아님.
  std::uint32_t groups;       // 뒤따르는 EventGroup 수
};

// 화소 (x, y + i) 중 mask 의 비트 i 가 1 인 곳에 이벤트 (CID 0)
struct EventGroup {
  std::uint16_t x;
  std::uint16_t y; // 8 의 배수
  std::uint8_t mask;
  std::uint8_t polarity; // 0 ON, 1 OFF
};

// Decoder 가 시작부터 센 값
struct DecodeStats {
  std::uint64_t buffers = 0;         // 해석한 원본 버퍼 수
  std::uint64_t frames = 0;          // 내보낸 센서 프레임 수
  std::uint64_t discontinuities = 0; // 바로 앞에서 데이터가 빠진 원본 버퍼 (해석 상태 초기화)
  std::uint64_t events = 0;          // 해석한 이벤트 (group AER: CID 0 + 1)
  std::uint64_t on = 0;
  std::uint64_t off = 0;
  std::uint64_t cid1_events = 0;     // group AER: CID 1 이벤트 (화면에 그리지 않음)
  std::uint64_t invalid = 0;         // 형식에 맞지 않는 값 (group AER: 단어 필드, RAW2: 2비트 값 3,
                                     // 머리말, 프레임의 열 수)
  std::uint64_t unknown = 0;         // group AER: 알 수 없는 종류의 단어
  std::uint64_t unsynced_groups = 0; // group AER: 동기화(컬럼/타임스탬프/epoch) 전이라 버린 이벤트 그룹
  std::uint64_t frame_gaps = 0;      // 센서 프레임 번호 이상
  std::uint64_t raw2_timestamp_fallbacks = 0; // sub 변화 없이 후보 시각으로 내보낸 RAW2 프레임
  std::uint64_t raw2_short_frames = 0; // 960열 규격보다 짧은959열로 내보낸 RAW2 프레임
  std::uint64_t raw2_timestamp_ambiguous = 0; // sub 변화 뒤 재변화 또는 끝 한 word만 변화
  std::uint64_t raw2_timestamp_order_errors = 0; // 연속 counter 후보가 동일/half-range 역순 (fallback 포함)
};

// 센서 데이터 해석기. upstream 의 원본(Stream 버퍼)을 해석해 센서 프레임 하나씩
// 내보냅니다. 형식마다 구현이 따로 있습니다: GroupAerDecoder(Format::Compressed),
// Raw2Decoder(Format::Raw2). 출력 모양은 같습니다(위 SensorFrame + EventGroup).
// 센서 프레임은 원본 버퍼 경계와 상관없이 이어지며, 시작과 끝을 모두 본
// 프레임만 내보냅니다(다른 프레임의 이벤트가 섞이지 않음). 원본 버퍼 하나를
// 해석하면 그 안에서 끝난 프레임들이 한꺼번에 나오므로, DropPipe 로 읽으면
// 그중 최근 capacity 개를 순서대로 받습니다. 속도 조절은 뒤에 붙인 파이프가
// 합니다.
// 원본이 끊기면(Read 의 discontinuous) 만들던 프레임을 버리고 다시
// 동기화합니다. 바로 앞 센서 프레임을 내보내지 못한 프레임에는
// discontinuous 를 표시합니다(period_us 도 0).
// 읽기는 Pipe 로 합니다. 보통 upstream 은 Stream 의 DropPipe(해석이 밀리면 건너뜀).
class NRV_DVS_API Decoder : public Source {
public:
  ~Decoder() override;
  Decoder(Decoder &&) noexcept;
  Decoder &operator=(Decoder &&) noexcept;

  DecodeStats Stats() const; // moved-from 이면 모두 0

protected:
  enum class Type { kGroupAer, kRaw2 }; // GroupAerDecoder, Raw2Decoder

  // type 의 구현을 만듦. upstream 이 비어 있으면 std::system_error
  Decoder(std::unique_ptr<Pipe> upstream, Type type);

private:
  class NRV_DVS_NO_EXPORT Impl; // pimpl: 구현은 Impl::GroupAer, Impl::Raw2 (.so 밖으로 내보내지 않음)
};

// group AER(압축 프레임, CSI 0x2A, Format::Compressed) 해석기. 센서 프레임 하나 =
// 프레임 시작 타임스탬프부터 프레임 끝 단어까지 (CID 0).
class NRV_DVS_API GroupAerDecoder final : public Decoder {
public:
  // upstream 이 비어 있으면 std::system_error
  explicit GroupAerDecoder(std::unique_ptr<Pipe> upstream);
};

// RAW2(CSI 0x2A, Format::Raw2) 해석기. 원본은 열(세로 720화소 x 2비트 = 180 B)의
// 연속이고, 머리말(프레임 번호, 타임스탬프)이 끝난 첫 데이터 열부터 해석합니다.
// Jetson MIPI pixel word는big-endian32비트, word의낮은2비트부터y를증가시킵니다.
// 머리말 반복 개수는 고정하지 않으며 VI 버퍼 경계를 넘어 이어서 읽습니다.
// 다음 header 경계에서 데이터959/960열을 확인한 뒤 내보냅니다.959열은 short-frame으로
// 별도 계수하며 센서 규격960열과 차이의 원인은 미확정입니다. EOF에서는 확정하지 않습니다.
// timestamp_us는 첫 sub 변화의 원시 word, 변화가 없으면 마지막 머리말의 첫 timestamp를
// 사용합니다. 다른 word/frame의 ref/sub를 합성하지 않습니다. 후보 이후 모든 word가
// 동일하며 적어도2회 관측된 경우에만 연속 후보의 간격을 계산합니다. 그 조건은 보수적
// 파서 정책이며 센서 사양/물리 시각 보장이 아닙니다. timestamp_us 자체는 미확정이어도
// 진단용 후보를 보존하므로 직접 빼서 화면/취득시각을 만들지 마세요.
// fallback/ambiguous frame 및 그 직후 frame의 period_us는 0(미확정)입니다.
// 연속 선택 후보가 역행/동일값이면 해당 간격과 다음 간격도0으로 재기준을 잡습니다.
// counter 누락과 Reset을 넘어 간격을 계산하지 않습니다. 따라서 discontinuous는
// 이 경우 실제 수신 누락을 뜻하지 않습니다. counter gap 검사는 별도로 유지합니다.
class NRV_DVS_API Raw2Decoder final : public Decoder {
public:
  // upstream 이 비어 있으면 std::system_error
  explicit Raw2Decoder(std::unique_ptr<Pipe> upstream);
};

} // namespace delta
