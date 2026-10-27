#pragma once
#include <istream>
#include <memory>
#include <nrv_dvs/export.h>
#include <nrv_dvs/source.h>
#include <nrv_dvs/stream.h>

namespace delta {

// std::istream 의 원본(Stream 이 내주는 버퍼의 연속, 예: 녹화 파일을 푼 것)을
// format 크기 버퍼 하나씩 차례로 내주는 Source. Stream 대신 Decoder, Compressor 의
// upstream 으로 씁니다. 읽기는 Read 를 부른 스레드에서 합니다(한 번에 하나).
// 끝(남은 바이트가 버퍼 하나보다 적음)이면 Read 가 std::errc::no_message_available,
// 읽기 실패면 std::errc::io_error 를 돌려줍니다.
class NRV_DVS_API StdSource final : public Source {
public:
  // in 이 비어 있으면 std::system_error
  StdSource(std::unique_ptr<std::istream> in, Format format);
  ~StdSource() override;
  StdSource(StdSource &&) noexcept;
  StdSource &operator=(StdSource &&) noexcept;

private:
  class NRV_DVS_NO_EXPORT Impl; // pimpl: .so 밖으로 심볼을 내보내지 않음
};

} // namespace delta
