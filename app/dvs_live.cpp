// 수신기. 파이프 두 개를 각자 스레드에서 읽습니다.
//   화면 (main 스레드): Stream -> DropPipe -> Decoder -> DropPipe -> stdout
//   저장 (dump 스레드, 녹화 중일 때만): Stream -> WaitPipe -> Compressor -> WaitPipe -> 파일
// 화면 = 프리뷰 간격(preview_us, 센서 시각) 동안의 센서 프레임을 합친 것(같은
// 화소는 마지막 극성, 0 이면 센서 프레임 하나씩). 간격은 제어 FIFO 의
// "preview <us>" 로 바꿉니다(웹 화면의 프리뷰 fps). 화면마다 stdout 에
// [4바이트 big-endian JSON 길이][JSON 통계][2비트 화소 172800 B] 를 씁니다.
// 화소 0 이벤트 없음 / 1 ON / 2 OFF, 바이트 i 의 비트 2j..2j+1 = 화소 4i+j
// (행 우선 960x720). JSON 의 frame 은 합친 첫 센서 프레임의 시작 시각(timestamp_us),
// 마지막 프레임의 앞 프레임부터 간격(period_us, 0 = 모름), 이벤트 수이고, merged 는
// 합친 센서 프레임 수입니다. 200 ms 동안 프레임이 없으면 모은 것(없으면 빈 화면)을 씁니다.
// Decoder 는 원본 버퍼 하나를 해석해 그 안에서 끝난 센서 프레임들을 한꺼번에
// 내놓고, 뒤 DropPipe(kScreenQueue)가 그것들을 순서대로 줍니다.
// stdout 쓰기(viewer.py)가 밀리면 그 링에서 오래된 프레임부터 버려지고(최대
// kScreenQueue 프레임만큼 늦음), 해석이 밀리면 앞 DropPipe(1)가 원본 버퍼를
// 건너뜁니다.
//
// 녹화는 제어 FIFO(세 번째 인자, viewer.py 가 씀)의 한 줄 명령으로 시작/정지합니다.
//   start<TAB>디렉터리<TAB>파일 크기(바이트, 1 MiB 의 배수, 압축된 크기)<TAB>파일 개수(0 = 돌려쓰기 없음)
//   stop
//   quit   (녹화를 마치고 종료. stdout 이 닫혀 viewer.py 도 끝남)
//   preview<TAB>간격(us, 0 = 센서 프레임 하나씩)   (화면 합치기, 녹화와 무관)
// 저장은 해석 없이 원본 버퍼(1 MiB)를 받은 순서대로 버퍼마다 zstd 프레임 하나로 압축해
// (SDK Compressor, 레벨 kDumpLevel, dump 스레드의 Read 안에서) 디렉터리의
// dump-NNNN.bin.zst 에 이어 씁니다. 다음 프레임이 파일 크기를 넘기면 다음 파일로
// 넘어가므로 파일마다 따로 풀 수 있고(zstd -d) 이어 붙여 풀면(cat dump-*.zst | zstd -d)
// 받은 스트림 그대로입니다. 파일 개수가 있으면 그만큼을 돌려 쓰며 가장 오래된 파일부터
// 덮어씁니다(없으면 정지하거나 디스크가 찰 때까지 새 파일). 압축이 버퍼 속도를 못
// 따라가면(1 스레드: Delta10 약 150, 최대 감도 약 300 MiB/s) 아래 큐가 차고 결국 VI 가
// 버립니다(starved 사건).
// WaitPipe(kDumpQueue 버퍼, 2 GiB)라 저장이
// 버퍼를 건너뛰지 않고, 디스크가 잠깐 멈춰도 화면 스레드가 Stream 을 계속
// 읽는 동안은 이 큐가 받아 둡니다. 큐가 차면 Stream 이 멈춥니다.
// 그래도 앞에서 빠진 것이 보이면(Read 의 discontinuous: VI 가 CRC 오류 등으로
// 버림, 또는 드라이버 버퍼가 바닥났었음) 그 앞뒤 kContext 버퍼를
// event-<frame>.bin.zst 로 따로 남기고(돌려 쓰기로 지워지지 않음), 사건 정보를
// JSON 의 dump.events 로 내보냅니다. status.yaml, events.yaml 은 viewer.py 가
// 이 JSON 으로 씁니다.
//
// 센서 설정은 setfile(예: config/Delta_10_2000FPS.txt)의 "20:AAAA=VV" 줄을
// 순서대로 레지스터 목록으로 넘깁니다(0x0100 스트리밍 시작은 드라이버가 함).
// 사용법: dvs_live <setfile> <device> <제어 FIFO> | viewer.py
#include <algorithm>
#include <atomic>
#include <bit>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <exception>
#include <fcntl.h>
#include <filesystem>
#include <format>
#include <fstream>
#include <memory>
#include <mutex>
#include <nrv_dvs/delta.h>
#include <span>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

constexpr std::size_t kDumpFrameBytes = 8192 * 128; // 원본 버퍼 하나
constexpr std::size_t kDumpQueue = 2048; // 저장 WaitPipe 에 쌓아 둘 버퍼 수 (2 GiB)
constexpr std::size_t kContext = 8; // 사건 앞뒤로 따로 남길 버퍼 수
constexpr int kDumpLevel = 1;       // 저장 zstd 레벨 (Delta10 1.4배, 최대 감도 1.2배)
// 화면 DropPipe 링: 원본 버퍼 하나에서 한꺼번에 나오는 센서 프레임(Delta10 약 200개)을
// 모두 받아 프리뷰 간격대로 합치도록 그보다 크게 (밀려도 최대 512 프레임 = 약 0.26 s 늦음)
constexpr std::size_t kScreenQueue = 512;
constexpr std::uint64_t kWrapUs = (std::uint64_t{1} << 22) * 1000; // 센서 시각이 0 으로 돌아가는 주기
constexpr std::size_t kTimings = 64; // 사건 직전 몇 버퍼의 복사/쓰기 시간을 볼지

// SIGTERM(run.py stop)/SIGINT: main 루프를 끝내 dump 스레드를 정리하고 마지막
// 통계를 내보냄
static volatile std::sig_atomic_t g_stop = 0;

// 저장 중 빠진 것 하나
// 사건 정보 중 dropped/starved/starved_ms 는 앞 사건 이후 Stream 통계가 늘어난
// 만큼. Stream 은 저장보다 최대 kDumpQueue 버퍼 앞서 있으므로 사건끼리 가까우면
// 한 사건에 몰려 보일 수 있음 (합계는 정확).
struct DumpEvent {
  std::uint64_t frame;      // 저장 순번 (0 부터)
  std::uint64_t file;       // 이 버퍼가 든 dump-<file>.bin.zst
  std::uint64_t file_frame; // 그 파일을 풀었을 때 몇 번째 버퍼인지 (위치 = file_frame * 1 MiB)
  std::uint64_t dropped;    // VI 가 번호를 매기고 버린 버퍼 (CSI CRC 오류 등)
  std::uint64_t starved;    // 드라이버 버퍼가 바닥났던 횟수
  double starved_ms;        // 그동안 끊긴 시간 (VI 시각)
  std::int64_t realtime_ns; // CLOCK_REALTIME (journalctl -k 와 맞춰 보기)
  double copy_ms_max;       // 직전 kTimings 버퍼 중 가장 느린 복사
  double write_ms_max;      // 직전 kTimings 버퍼 중 가장 느린 파일 쓰기 (압축은 Read 안)
  std::uint64_t context;    // 이 사건 앞뒤 버퍼가 든 event-<context>.bin.zst
};

// 녹화 설정 (start 명령)
struct DumpSettings {
  std::string dir;
  std::uint64_t file_bytes; // 파일 하나 (kDumpFrameBytes 의 배수)
  unsigned files;           // 돌려 쓸 파일 개수, 0 = 돌려쓰기 없음
};

// dump 스레드가 쓰고 main 이 JSON 으로 내보냄. 녹화를 시작할 때마다 main 이 초기화.
struct DumpState {
  std::atomic<std::uint64_t> frames = 0;  // 받은 원본 버퍼
  std::atomic<std::uint64_t> written = 0; // 디스크에 쓴 압축 바이트
  std::atomic<std::uint64_t> file = 0;    // 지금 쓰는 dump-<file>.bin.zst
  std::atomic<bool> running = false;
  std::mutex mutex; // 아래 보호
  DumpSettings settings{.dir = "", .file_bytes = 0, .files = 0}; // dir 빈 값: 녹화한 적 없음
  std::string error;
  std::vector<DumpEvent> events; // 아직 내보내지 않은 사건
};

static double Now() {
  timespec t{.tv_sec = 0, .tv_nsec = 0};
  clock_gettime(CLOCK_MONOTONIC, &t);
  return static_cast<double>(t.tv_sec) + static_cast<double>(t.tv_nsec) / 1e9;
}

static bool WriteAll(int fd, const void *data, std::size_t size) {
  const auto *p = static_cast<const char *>(data);
  while (size) {
    const ssize_t n = ::write(fd, p, size);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      return false;
    p += n;
    size -= static_cast<std::size_t>(n);
  }
  return true;
}

// dump 스레드 본체. stop 요청 또는 오류까지 compressed 의 zstd 프레임(원본 버퍼
// 하나씩)을 모두 settings.dir 에 씀.
static void Dump(std::stop_token stop, delta::WaitPipe compressed, const DumpSettings settings,
                 const delta::Stream &stream, DumpState &state) {
  const std::string &dir = settings.dir;
  using Clock = std::chrono::steady_clock;
  // 최근 2*kContext+1 압축 프레임을 담는 링. 사건이 나면 링의 앞뒤 프레임을
  // event 파일로 남김.
  constexpr std::size_t kRing = 2 * kContext + 1;
  std::vector<std::vector<std::byte>> ring(kRing);

  // event 파일은 다른 스레드가 씀 (dump 스레드가 밀리지 않게)
  std::mutex jobs_mutex;
  std::condition_variable_any jobs_ready;
  std::deque<std::pair<std::string, std::vector<std::byte>>> jobs;
  std::jthread writer([&](std::stop_token writer_stop) {
    std::unique_lock lock(jobs_mutex);
    for (;;) {
      jobs_ready.wait(lock, writer_stop, [&] { return !jobs.empty(); });
      if (jobs.empty())
        return; // stop 요청, 남은 일 없음
      auto [path, data] = std::move(jobs.front());
      jobs.pop_front();
      lock.unlock();
      const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
      if (fd < 0 || !WriteAll(fd, data.data(), data.size()))
        std::fprintf(stderr, "dvs_live: %s: %s\n", path.c_str(), std::strerror(errno));
      if (fd >= 0)
        ::close(fd);
      lock.lock();
    }
  });

  // 압축 프레임은 크기가 제각각이라 4 KiB 정렬 버퍼에 모아 4 KiB 단위로만 O_DIRECT 로
  // 쓰고, 파일을 끝맺을 때 남은 꼬리만 O_DIRECT 를 끄고 씀
  constexpr std::size_t kAlign = 4096;
  // 남은 꼬리(4 KiB 미만) + 프레임 하나 (압축이 안 되는 데이터면 원본보다 조금 큼)
  constexpr std::size_t kStage = 2 * kDumpFrameBytes;
  const std::unique_ptr<std::byte, decltype(&std::free)> stage(
      static_cast<std::byte *>(std::aligned_alloc(kAlign, kStage)), &std::free);

  std::vector<double> copy_ms(kTimings), write_ms(kTimings);
  std::uint64_t index = 0, opened = 0, file = 0, file_out = 0, file_frame = 0;
  std::size_t staged = 0; // stage 에 모인, 아직 안 쓴 압축 바이트
  std::uint64_t context = 0; // event 파일로 남길 첫 사건 (뒤 kContext 안의 사건도 포함)
  bool context_pending = false;
  delta::StreamStats seen; // 앞 사건 때의 Stream 통계
  std::vector<delta::Block> data;
  bool discontinuous = false;
  int fd = -1;
  std::string error;
  // 지금 파일을 끝맺음: 남은 꼬리를 O_DIRECT 없이 쓰고 닫음
  const auto close_file = [&] {
    if (staged && (::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL) & ~O_DIRECT) < 0 ||
                   !WriteAll(fd, stage.get(), staged)))
      error = std::strerror(errno);
    file_out += staged;
    state.written += staged;
    staged = 0;
    ::close(fd);
    fd = -1;
  };

  state.running = true;
  while (!stop.stop_requested() && error.empty()) {
    if (const std::error_code ec =
            compressed.Read(data, discontinuous, std::chrono::milliseconds(200))) {
      if (ec != std::errc::timed_out)
        error = ec.message();
      continue;
    }
    const std::span<const std::byte> out = std::as_bytes(std::span(data));
    delta::CompressedFrame header{.bytes = 0};
    std::memcpy(&header, out.data(), sizeof(header));
    if (sizeof(header) + header.bytes > out.size()) {
      error = "bad compressed frame";
      continue;
    }
    const std::span<const std::byte> frame = out.subspan(sizeof(header), header.bytes);
    // 이 프레임까지 넣으면 파일 크기를 넘음: 지금 파일을 끝맺고 다음 파일로
    // (빈 파일에는 그래도 넣음)
    if (fd >= 0 && file_out + staged + frame.size() > settings.file_bytes) {
      close_file();
      if (!error.empty())
        continue;
    }
    if (fd < 0) { // 다음 파일 (돌려 쓰면 가장 오래된 것)
      file = settings.files ? opened % settings.files : opened;
      ++opened;
      state.file = file;
      fd = ::open(std::format("{}/dump-{:04}.bin.zst", dir, file).c_str(),
                  O_WRONLY | O_CREAT | O_TRUNC | O_DIRECT | O_CLOEXEC, 0644);
      if (fd < 0) {
        error = std::strerror(errno);
        continue;
      }
      file_out = 0;
      file_frame = 0;
    }
    const auto t0 = Clock::now();
    ring[index % kRing].assign(frame.begin(), frame.end());
    const auto t1 = Clock::now();
    std::memcpy(stage.get() + staged, frame.data(), frame.size());
    staged += frame.size();
    const std::size_t whole = staged & ~(kAlign - 1);
    if (whole) {
      if (!WriteAll(fd, stage.get(), whole)) {
        error = std::strerror(errno);
        continue;
      }
      std::memmove(stage.get(), stage.get() + whole, staged - whole);
      staged -= whole;
      file_out += whole;
      state.written += whole;
    }
    const auto t2 = Clock::now();
    copy_ms[index % kTimings] = std::chrono::duration<double, std::milli>(t1 - t0).count();
    write_ms[index % kTimings] = std::chrono::duration<double, std::milli>(t2 - t1).count();

    if (discontinuous && index > 0) {
      if (!context_pending) {
        context_pending = true;
        context = index;
      }
      timespec now{.tv_sec = 0, .tv_nsec = 0};
      clock_gettime(CLOCK_REALTIME, &now);
      const delta::StreamStats s = stream.Stats();
      std::lock_guard lock(state.mutex);
      state.events.push_back({
          .frame = index,
          .file = file,
          .file_frame = file_frame,
          .dropped = s.dropped - seen.dropped,
          .starved = s.starved - seen.starved,
          .starved_ms = static_cast<double>(s.starved_ns - seen.starved_ns) / 1e6,
          .realtime_ns = std::int64_t{now.tv_sec} * 1'000'000'000 + now.tv_nsec,
          .copy_ms_max = *std::max_element(copy_ms.begin(), copy_ms.end()),
          .write_ms_max = *std::max_element(write_ms.begin(), write_ms.end()),
          .context = context,
      });
      seen = s;
    }
    // 사건 뒤 kContext 버퍼까지 모이면 링에 남은 앞뒤 프레임을 이어 붙여 event 파일로
    // (zstd -d 로 풀면 그 원본 버퍼들)
    if (context_pending && index == context + kContext) {
      const std::uint64_t from = context >= kContext ? context - kContext : 0;
      std::vector<std::byte> around;
      for (std::uint64_t i = from; i <= index; ++i)
        around.insert(around.end(), ring[i % kRing].begin(), ring[i % kRing].end());
      {
        std::lock_guard lock(jobs_mutex);
        jobs.emplace_back(std::format("{}/event-{}.bin.zst", dir, context), std::move(around));
      }
      jobs_ready.notify_one();
      context_pending = false;
    }
    ++file_frame;
    state.frames = ++index;
  }
  if (fd >= 0 && error.empty()) // 정지: 마지막 파일의 꼬리도 씀
    close_file();
  if (fd >= 0)
    ::close(fd);
  state.running = false;
  if (!error.empty()) {
    std::fprintf(stderr, "dvs_live: dump stopped: %s\n", error.c_str());
    std::lock_guard lock(state.mutex);
    state.error = error;
  }
}

int main(int argc, char **argv) try {
  if (argc != 4) {
    std::fprintf(stderr, "usage: dvs_live <setfile> <device> <control fifo>\n");
    return 2;
  }
  std::ifstream setfile(argv[1]);
  if (!setfile) {
    std::fprintf(stderr, "dvs_live: cannot open %s\n", argv[1]);
    return 1;
  }
  std::vector<std::uint32_t> ops;
  for (std::string line; std::getline(setfile, line);) {
    unsigned addr = 0, value = 0;
    if (std::sscanf(line.c_str(), " 20:%4x=%2x", &addr, &value) == 2 && addr != 0x0100)
      ops.push_back(NRV_DVS_OP(addr, value));
  }
  struct sigaction on_stop {};
  on_stop.sa_handler = [](int) { g_stop = 1; };
  (void)sigaction(SIGTERM, &on_stop, nullptr);
  (void)sigaction(SIGINT, &on_stop, nullptr);
  // viewer 가 끝나면 SIGPIPE 로 죽지 않고 write 실패(EPIPE)로 정상 종료
  (void)std::signal(SIGPIPE, SIG_IGN);
  delta::Device device(argv[2]);
  if (const std::error_code ec = device.SetRegisterList(ops)) {
    std::fprintf(stderr, "dvs_live: register list (%zu ops): %s\n", ops.size(),
                 ec.message().c_str());
    return 1;
  }
  delta::Stream stream(std::move(device));
  delta::GroupAerDecoder decoder(std::make_unique<delta::DropPipe>(stream, 1));
  delta::DropPipe screens(decoder, kScreenQueue);
  // 쓰는 쪽(viewer.py)이 없어도 열리고, 없을 때 read 는 0
  const int control = ::open(argv[3], O_RDONLY | O_NONBLOCK | O_CLOEXEC);
  if (control < 0) {
    std::fprintf(stderr, "dvs_live: %s: %s\n", argv[3], std::strerror(errno));
    return 1;
  }
  std::string commands; // 제어 FIFO 에서 받았지만 아직 줄이 끝나지 않은 것
  std::uint64_t preview_us = 1'000'000 / 60; // 화면에 합칠 간격 (preview 명령, 웹 처음 선택 60 fps)

  DumpState dump_state;
  std::jthread dump; // 녹화 중일 때만. main 이 끝나면 멈추고 join (파이프보다 먼저 소멸)
  // 제어 명령 한 줄 처리
  const auto command = [&](const std::string &line) {
    if (line.starts_with("preview\t")) {
      preview_us = std::strtoull(line.c_str() + 8, nullptr, 10);
      return;
    }
    if (line == "quit") {
      g_stop = 1;
      return;
    }
    if (line == "stop") {
      if (dump.joinable()) {
        dump.request_stop();
        dump.join();
      }
      return;
    }
    DumpSettings settings{.dir = "", .file_bytes = 0, .files = 0};
    const std::size_t a = line.find('\t'), b = line.find('\t', a + 1),
                      c = line.find('\t', b + 1);
    if (line.compare(0, a, "start") || a == std::string::npos || b == std::string::npos ||
        c == std::string::npos ||
        std::sscanf(line.c_str() + b + 1, "%lu\t%u", &settings.file_bytes, &settings.files) != 2 ||
        !settings.file_bytes || settings.file_bytes % kDumpFrameBytes) {
      std::fprintf(stderr, "dvs_live: bad control command: %s\n", line.c_str());
      return;
    }
    if (dump_state.running) {
      std::fprintf(stderr, "dvs_live: already recording, ignored: %s\n", line.c_str());
      return;
    }
    if (dump.joinable()) // 오류로 끝난 앞 녹화
      dump.join();
    settings.dir = line.substr(a + 1, b - a - 1);
    std::error_code ec;
    std::filesystem::create_directories(settings.dir, ec);
    {
      std::lock_guard lock(dump_state.mutex);
      dump_state.settings = settings;
      dump_state.error = ec ? std::format("{}: {}", settings.dir, ec.message()) : "";
      dump_state.events.clear();
      dump_state.frames = 0;
      dump_state.written = 0;
      dump_state.file = 0;
    }
    if (!ec) {
      dump_state.running = true; // JSON 이 스레드 시작 전에도 recording 을 보이게
      dump = std::jthread(Dump,
                          delta::WaitPipe(delta::Compressor(std::make_unique<delta::WaitPipe>(
                                                                stream, kDumpQueue),
                                                            kDumpLevel),
                                          1),
                          settings,
                          std::cref(stream), std::ref(dump_state));
    }
  };

  std::vector<std::uint8_t> packed(delta::kImageWidth * delta::kImageHeight / 4);
  // 화면에 합치는 중인 센서 프레임들
  std::uint64_t window_start = 0;            // 합친 첫 프레임의 시작 시각
  std::uint32_t merged = 0, last_period = 0; // 합친 프레임 수, 마지막 프레임의 period_us
  std::uint64_t frame_events = 0;
  const double started = Now();
  double last_good = started;
  std::uint64_t snapshot = 0;
  // 통계 JSON + 화면 하나를 stdout 으로. 읽는 쪽이 끝났으면 false.
  const auto emit = [&](const char *live_state) {
    const double now = Now();
    const delta::DecodeStats s = decoder.Stats();
    const delta::StreamStats r = stream.Stats();
    // buffers/skipped/events...: 해석한 원본 버퍼 기준, stream: 받은 버퍼 전체
    std::string json = std::format(
        "{{\"state\":\"{}\",\"session_pid\":{},\"monotonic\":{:.6f},\"seconds\":{:.3f},"
        "\"last_good_age\":{:.3f},\"snapshot_id\":{},\"buffers\":{},\"skipped\":{},"
        "\"frames\":{},\"frame\":{{\"timestamp_us\":{},\"period_us\":{},\"events\":{}}},"
        "\"merged\":{},\"preview_us\":{},"
        "\"discontinuities\":{},\"events\":{},\"on\":{},\"off\":{},"
        "\"cid1_events\":{},\"invalid\":{},\"unknown\":{},\"unsynced_groups\":{},"
        "\"frame_gaps\":{},\"stream\":{{\"buffers\":{},\"bytes\":{},\"dropped\":{},"
        "\"starved\":{},\"starved_ms\":{:.3f},\"sequence\":{}}}",
        live_state, getpid(), now, now - started, now - last_good, ++snapshot, s.buffers,
        r.buffers > s.buffers ? r.buffers - s.buffers : 0, s.frames, window_start,
        last_period, frame_events, merged, preview_us, s.discontinuities, s.events, s.on,
        s.off, s.cid1_events, s.invalid, s.unknown, s.unsynced_groups, s.frame_gaps,
        r.buffers, r.buffers * kDumpFrameBytes, r.dropped, r.starved,
        static_cast<double>(r.starved_ns) / 1e6, r.sequence);
    {
      // state: idle 녹화한 적 없음 / recording / stopped / failed
      // (dir 은 viewer.py 가 따옴표, 역슬래시, 제어 문자 없이 만든 경로)
      std::lock_guard lock(dump_state.mutex);
      const DumpSettings &d = dump_state.settings;
      json += std::format(
          ",\"dump\":{{\"state\":\"{}\",\"error\":\"{}\",\"dir\":\"{}\",\"frames\":{},"
          "\"bytes\":{},\"written\":{},\"file\":\"dump-{:04}.bin.zst\",\"file_bytes\":{},"
          "\"files\":{},\"events\":[",
          dump_state.running         ? "recording"
          : !dump_state.error.empty() ? "failed"
          : d.dir.empty()             ? "idle"
                                      : "stopped",
          dump_state.error, d.dir, dump_state.frames.load(),
          dump_state.frames.load() * kDumpFrameBytes, dump_state.written.load(),
          dump_state.file.load(), d.file_bytes, d.files);
      for (const DumpEvent &e : dump_state.events)
        json += std::format(
            "{}{{\"frame\":{},\"file\":\"dump-{:04}.bin.zst\",\"file_frame\":{},"
            "\"dropped\":{},\"starved\":{},\"starved_ms\":{:.3f},"
            "\"realtime_ns\":{},\"copy_ms_max\":{:.3f},\"write_ms_max\":{:.3f},"
            "\"context\":\"event-{}.bin.zst\"}}",
            &e == dump_state.events.data() ? "" : ",", e.frame, e.file, e.file_frame,
            e.dropped, e.starved,
            e.starved_ms, e.realtime_ns, e.copy_ms_max, e.write_ms_max, e.context);
      dump_state.events.clear();
      json += "]}";
    }
    json += "}";
    const auto length = static_cast<std::uint32_t>(json.size());
    const unsigned char header[4] = {
        static_cast<unsigned char>(length >> 24), static_cast<unsigned char>(length >> 16),
        static_cast<unsigned char>(length >> 8), static_cast<unsigned char>(length)};
    return WriteAll(STDOUT_FILENO, header, sizeof(header)) &&
           WriteAll(STDOUT_FILENO, json.data(), json.size()) &&
           WriteAll(STDOUT_FILENO, packed.data(), packed.size());
  };

  std::vector<delta::Block> screen;
  bool discontinuous = false;
  while (!g_stop) {
    char chunk[256];
    for (ssize_t n; (n = ::read(control, chunk, sizeof(chunk))) > 0;)
      commands.append(chunk, static_cast<std::size_t>(n));
    for (std::size_t end; (end = commands.find('\n')) != std::string::npos;
         commands.erase(0, end + 1))
      command(commands.substr(0, end));
    const std::error_code ec =
        screens.Read(screen, discontinuous, std::chrono::milliseconds(200));
    if (ec && ec != std::errc::timed_out) {
      std::fprintf(stderr, "dvs_live: %s\n", ec.message().c_str());
      return 1;
    }
    // 모은 화면을 내보내고 다음 화면을 비움. 읽는 쪽(viewer.py)이 끝났으면 false.
    const auto flush = [&] {
      const bool ok = emit("live");
      std::fill(packed.begin(), packed.end(), 0);
      window_start = 0;
      merged = last_period = 0;
      frame_events = 0;
      return ok;
    };
    if (ec) { // 200 ms 동안 프레임 없음
      if (!flush())
        return 1;
      continue;
    }
    last_good = Now();
    const std::byte *bytes = std::as_bytes(std::span(screen)).data();
    delta::SensorFrame frame;
    std::memcpy(&frame, bytes, sizeof(frame));
    // 이 프레임이 간격 밖에서 시작하면(앞을 건너뛴 경우 등) 모은 것부터 내보냄
    if (merged && (frame.timestamp_us + kWrapUs - window_start) % kWrapUs >= preview_us &&
        !flush())
      return 1;
    if (!merged)
      window_start = frame.timestamp_us;
    for (std::uint32_t g = 0; g < frame.groups; ++g) {
      delta::EventGroup e;
      std::memcpy(&e, bytes + sizeof(frame) + g * sizeof(e), sizeof(e));
      frame_events += static_cast<unsigned>(std::popcount(e.mask));
      const unsigned value = 1u + e.polarity; // 1 ON, 2 OFF
      for (unsigned bit = 0; bit < 8; ++bit) {
        if (!(e.mask & (1u << bit)))
          continue;
        const std::size_t pixel = std::size_t{e.y + bit} * delta::kImageWidth + e.x;
        const unsigned shift = 2 * (pixel & 3);
        std::uint8_t &byte = packed[pixel >> 2];
        byte = static_cast<std::uint8_t>((byte & ~(3u << shift)) | (value << shift));
      }
    }
    ++merged;
    last_period = frame.period_us;
    // 이 프레임까지(그 길이는 period_us 로 봄) 간격을 채웠으면 내보냄
    if ((frame.timestamp_us + frame.period_us + kWrapUs - window_start) % kWrapUs >= preview_us &&
        !flush())
      return 1;
  }
  // 저장을 마저 끝내고(마지막 사건 포함) 마지막 통계를 보냄
  if (dump.joinable()) {
    dump.request_stop();
    dump.join();
  }
  std::fill(packed.begin(), packed.end(), 0);
  (void)emit("stopped");
  return 0;
} catch (const std::exception &e) {
  std::fprintf(stderr, "dvs_live: %s\n", e.what());
  return 1;
}
