#pragma once
#include <cstdint>
#include <memory>
#include <nrv_dvs/export.h>
#include <nrv_dvs/pipe.h>
#include <nrv_dvs/source.h>
#include <stop_token>
#include <system_error>

namespace delta {

// Compressor 출력 하나 = upstream 데이터 하나를 zstd 프레임 하나로 압축한 것.
// CompressedFrame 바로 뒤에 zstd 프레임이 bytes 바이트 붙어 있습니다(std::memcpy 로
// 꺼내 쓰세요). 그 뒤 Block 끝까지의 바이트는 내용이 없습니다.
// 프레임마다 따로 풀 수 있고(ZSTD_decompress, 원래 크기는 프레임 헤더에 있음),
// 이어 붙이면 zstd -d 로 한꺼번에 풀립니다.
struct CompressedFrame {
  std::uint32_t bytes; // 뒤따르는 zstd 프레임 크기
};

// 압축기. upstream 데이터(보통 Stream 의 원본 버퍼)를 하나씩 zstd 로 압축해
// 내보냅니다. 프레임마다 독립이라 뒤에 DropPipe 를 붙여 건너뛰어도 남은 것은
// 그대로 풀립니다. discontinuous 는 upstream 의 것을 그대로 넘깁니다.
// 압축은 Work 를 부르는 스레드들이 나눠 하고(스레드 수는 앱이 정함), Read 는
// 결과를 upstream 순서대로 줍니다. Work 를 부르는 스레드가 없으면 Read 를 부른
// 스레드가 직접 압축합니다(한 번에 하나). 보통 upstream 은 Stream 의
// WaitPipe(녹화: 하나도 버리지 않음).
class NRV_DVS_API Compressor final : public Source {
public:
  // level: zstd 압축 레벨 (클수록 작고 느림, 범위 밖이면 가까운 끝으로).
  // upstream 이 비어 있거나 압축 컨텍스트를 만들지 못하면 std::system_error
  Compressor(std::unique_ptr<Pipe> upstream, int level);
  ~Compressor() override;
  Compressor(Compressor &&) noexcept;
  Compressor &operator=(Compressor &&) noexcept;

  // 압축 스레드 본체. stop 요청이나 upstream 오류까지 upstream 데이터를 하나씩
  // 가져가 압축해 둡니다. Read 가 가져가지 않은 결과가 Work 스레드 수의 두 배가
  // 되면 기다립니다. 반환: stop 이면 {}, upstream 오류(이후 Read 도 같은 오류를
  // 받음), 압축 컨텍스트를 못 만들면 not_enough_memory, 옮겨진 객체면
  // bad_file_descriptor
  std::error_code Work(std::stop_token stop);

private:
  class NRV_DVS_NO_EXPORT Impl; // pimpl: .so 밖으로 심볼을 내보내지 않음
};

} // namespace delta
