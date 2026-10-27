#pragma once
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <nrv_dvs/export.h>
#include <nrv_dvs/source.h>
#include <system_error>
#include <vector>

namespace delta {

// 파이프가 주고받는 데이터의 단위 (std::vector<Block>). Stream 은 드라이버가
// 채운 메모리를 복사 없이 내보내고 받은 메모리를 다시 드라이버에 넘기는데,
// VI 는 64 바이트 정렬 주소에만 쓰므로 데이터 메모리를 늘 64 바이트 정렬로
// 둡니다. 바이트로는 std::as_bytes(std::span(data)) 로 읽으세요.
struct alignas(64) Block {
  std::array<std::byte, 64> bytes;
};

// Source 를 읽는 통로. Source 가 데이터(버퍼 하나)를 만들면 붙은 파이프마다
// 복사본을 넣어 두고, Read 가 그것을 꺼내 줍니다.
// Read 는 사용자가 원하는 스레드에서 부르면 되고, 꺼낼 데이터가 없으면 그
// 호출이 직접 만들어 옵니다(Stream: 드라이버에서 받음, Decoder: 해석).
// 파이프 하나는 한 스레드에서만 읽으세요.
// 파이프마다 capacity 개짜리 링 버퍼가 있고, 꽉 찼을 때 하는 일만 다릅니다.
// 구현: WaitPipe(하나도 버리지 않음, 꽉 차면 Source 를 멈춤),
//       DropPipe(꽉 차면 가장 오래된 것을 버림, Source 를 멈추지 않음)
class NRV_DVS_API Pipe {
public:
  Pipe(Pipe &&) noexcept;
  Pipe &operator=(Pipe &&) noexcept;
  virtual ~Pipe();

  // 다음 데이터를 data 에 담음. data 가 갖고 있던 메모리는 파이프가 받아서
  // 다음 데이터에 다시 씀.
  // discontinuous: 이 데이터 바로 앞에서 데이터가 빠졌음 (Stream: CSI 오류로
  //   버린 버퍼 또는 드라이버 버퍼 고갈, Decoder: 바로 앞 센서 프레임을
  //   내보내지 못함, DropPipe: 앞을 건너뜀)
  //   timed_out : timeout 안에 데이터 없음
  std::error_code Read(std::vector<Block> &data, bool &discontinuous,
                       std::chrono::milliseconds timeout);
  // 건너뛴 데이터 수를 dropped 에 담음 (WaitPipe 는 항상 0).
  // moved-from 이면 bad_file_descriptor (dropped 는 그대로)
  std::error_code Dropped(std::uint64_t &dropped) const noexcept;

protected:
  enum class Type { kWait, kDrop }; // WaitPipe, DropPipe

  // 생성자 공통 검사 후 type 의 구현을 만듦. type 은 에러 문자열의 이름도 정함.
  // moved-from Source 이거나 capacity 가 0 이면 std::system_error
  Pipe(const Source &source, Type type, std::size_t capacity);

private:
  class NRV_DVS_NO_EXPORT Impl; // pimpl: 구현은 Impl::Wait, Impl::Drop (.so 밖으로 내보내지 않음)
  std::unique_ptr<Impl> impl_;
};

// Source 를 shared_ptr 로 공유. 데이터를 하나도 버리지 않습니다. 읽지 않은
// 데이터가 capacity 개 쌓이면 Source 는 이 파이프가 읽을 때까지 다음 데이터를
// 만들지 않습니다(그 Source 의 다른 파이프도 함께 기다림). Stream 이 그렇게
// 오래 멈추면 드라이버 버퍼가 바닥나 VI 가 버림(discontinuous 로 표시).
// Source 객체가 사라져도 이 파이프가 있으면 계속 읽힙니다.
class NRV_DVS_API WaitPipe final : public Pipe {
public:
  // 생성 이후 들어오는 데이터부터 읽음. moved-from Source 이거나 capacity 가
  // 0 이면 std::system_error
  WaitPipe(const Source &source, std::size_t capacity);
};

// Source 를 weak_ptr 로 참조. 읽지 않은 데이터를 최근 capacity 개까지
// 남기고, 넘치면 가장 오래된 것부터 버립니다(Source 를 멈추지 않음). 버린
// 바로 뒤의 데이터에는 discontinuous 를 표시합니다. Read 는 먼저 기다리지
// 않고 받을 수 있는 것을 모두 받은 뒤(Stream: 드라이버에 이미 채워진 버퍼,
// Decoder: 해석해 둔 센서 프레임) 남은 것 중 가장 오래된 것부터 순서대로
// 줍니다. 그래서 밀려도 최대 capacity 개만큼만 늦고, 건너뛴 지점 뒤로는
// 빠짐없이 이어집니다. 새 데이터가 없으면 다음 것을 기다립니다. Source 를
// 소유한 것(Source 객체, WaitPipe)이 모두 사라지면 broken_pipe.
class NRV_DVS_API DropPipe final : public Pipe {
public:
  // 생성 이후 들어오는 데이터부터 읽음. moved-from Source 이거나 capacity 가
  // 0 이면 std::system_error
  DropPipe(const Source &source, std::size_t capacity);
};

} // namespace delta
