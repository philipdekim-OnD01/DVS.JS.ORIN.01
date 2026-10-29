#pragma once
#include <cstdint>
#include <nrv_dvs/device.h>
#include <nrv_dvs/export.h>
#include <nrv_dvs/source.h>

namespace delta {

// Stream 이 시작부터 센 값
struct StreamStats {
  std::uint64_t buffers = 0;   // 받은 드라이버 버퍼 (CSI 오류 버퍼 제외)
  std::uint64_t dropped = 0;   // VI 가 번호를 매기고 버린 버퍼 (sequence 공백: CSI CRC 오류 등)
  std::uint64_t starved = 0;   // 드라이버 버퍼가 바닥났던 횟수 (그동안 VI 가 번호 없이 버린 버퍼는 셀 수 없음)
  std::int64_t starved_ns = 0; // 바닥났던 앞뒤 버퍼의 VI 시각 차 합 (끊긴 시간)
  std::uint32_t sequence = 0;  // 마지막으로 받은 버퍼의 VI sequence
  std::uint64_t error_buffers = 0; // V4L2 ERROR flag를 실제 관측한 버퍼 수
};

// VI 버퍼 형식. 레지스터 목록이 센서에 내보내게 한 프레임 크기와 같아야 합니다.
enum class Format : std::uint8_t {
  Compressed, // 8192 x 128 바이트 (압축 프레임 출력, Delta_10_2000FPS.txt 등)
  Raw2,       // 현재 Jetson 수신 컨테이너 2880x61 B. 센서 논리 frame 경계와 다름.
};

// 캡처 스트림. 데이터 = VI 가 채운 버퍼 하나(Format 크기).
// RAW2 논리 frame/embedded 경계는 이 버퍼에 고정 정렬되지 않을 수 있으며
// decoder가 연속 byte stream에서 재조립한다.
// 그대로입니다(복사 없음). SDK 메모리를 드라이버에 넘겨 VI 가 직접 채우게 하고
// (V4L2 USERPTR), 채워진 메모리는 그대로 내보내고 Read 로 돌려받은 메모리를 다시
// 빈 버퍼로 넘깁니다. 데이터는 Pipe 로 읽습니다(nrv_dvs/pipe.h).
class NRV_DVS_API Stream final : public Source {
public:
  // dev 를 넘겨받아 캡처를 시작(STREAMON). 이때 드라이버가 저장된 레지스터
  // 목록을 센서에 씁니다. 실패하면 std::system_error.
  explicit Stream(Device dev, Format format = Format::Compressed);
  // 캡처는 Stream 객체와 WaitPipe 가 모두 사라질 때 멈춤(STREAMOFF)
  ~Stream() override;
  Stream(Stream &&) noexcept;
  Stream &operator=(Stream &&) noexcept;

  StreamStats Stats() const; // moved-from 이면 모두 0

private:
  class NRV_DVS_NO_EXPORT Impl; // pimpl: .so 밖으로 심볼을 내보내지 않음
};

} // namespace delta
