// 수신 스트레스 테스트 본체 (aer_stress_test, raw2_stress_test 가 setfile 과 형식만 바꿔 씀)
#pragma once
#include "nrv_dvs/delta.h"
#include "../register_lists.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <memory>
#include <span>
#include <thread>
#include <vector>

// setfile(실행 파일 옆 settings/) 로 duration 동안 format 으로 수신하며 누락/오류 확인.
// 1초마다 받은 양(MiB/s, Gbps), 센서 fps, 누락을 찍음.
//   gaps: VI sequence 공백 (버려진 버퍼 전체). 괄호 안 crc: 그중 VI 가 ERROR 로 돌려준
//         버퍼 (CSI CRC 등, 커널 로그 corr_err)
//   fps: 해석기가 연달아 본 센서 프레임 간격(SensorFrame::period_us) 평균으로 계산
// extra: setfile 뒤에 덧붙여 쓰는 레지스터 (목록은 순서대로 쓰여 같은 주소의 setfile 값을 덮어씀)
// 버퍼 누락(gaps)이나 드라이버 고갈이 있으면 1
inline int Stress(const char *setfile, delta::Format format, std::chrono::seconds duration,
                  std::span<const std::uint32_t> extra = {}) {
  std::vector<std::uint32_t> ops;
  if (!ReadSetfile(setfile, ops))
    return 1;
  ops.insert(ops.end(), extra.begin(), extra.end());
  auto dev = delta::Device("/dev/video0");
  auto err = dev.SetRegisterList(ops);
  if (err) {
    std::cerr << "SetRegisterList: " << err.message() << '\n';
    return 1;
  }

  // dev 를 넘기면 Stream 이 STREAMON (이때 위 목록이 센서에 쓰임)
  delta::Stream stream(std::move(dev), format);
  // 이 스레드: 모든 버퍼를 받는 WaitPipe (누락/처리량 측정)
  delta::WaitPipe pipe(stream, 64);
  // 화면처럼 따라가지 못할 수 있는 소비자: DropPipe 뒤의 해석기. 밀리면 버퍼를 건너뛰고
  // WaitPipe 의 처리량에는 영향이 없어야 함. 해석한 센서 프레임 간격으로 fps 를 셈.
  std::unique_ptr<delta::Decoder> decoder;
  if (format == delta::Format::Raw2)
    decoder = std::make_unique<delta::Raw2Decoder>(std::make_unique<delta::DropPipe>(stream, 1));
  else
    decoder = std::make_unique<delta::GroupAerDecoder>(std::make_unique<delta::DropPipe>(stream, 1));
  delta::WaitPipe sensor_frames(*decoder, 512);
  std::atomic<bool> stop = false;
  std::atomic<std::uint64_t> periods = 0, period_us = 0; // 간격이 있는 프레임 수, 간격 합
  std::thread viewer([&] {
    std::vector<delta::Block> data;
    bool discontinuous = false;
    while (!stop) {
      if (sensor_frames.Read(data, discontinuous, std::chrono::milliseconds(500)))
        continue;
      delta::SensorFrame frame{.timestamp_us = 0, .period_us = 0, .groups = 0};
      const auto in = std::as_bytes(std::span(data)).first(sizeof(frame));
      std::copy(in.begin(), in.end(), std::as_writable_bytes(std::span(&frame, 1)).begin());
      if (frame.period_us) {
        period_us += frame.period_us;
        ++periods;
      }
    }
  });

  using Clock = std::chrono::steady_clock;
  const auto start = Clock::now();
  auto last_report = start;
  std::uint64_t buffers = 0, bytes = 0, timeouts = 0, window_bytes = 0;
  std::uint64_t window_periods = 0, window_period_us = 0;
  bool failed = false;

  std::cout << "stress: " << duration.count() << " s, " << setfile << '\n';
  std::vector<delta::Block> data;
  bool discontinuous = false;
  while (Clock::now() - start < duration) {
    err = pipe.Read(data, discontinuous, std::chrono::milliseconds(500));
    if (err == std::errc::timed_out) {
      ++timeouts;
    } else if (err) {
      std::cerr << "Read: " << err.message() << '\n';
      failed = true;
      break;
    } else {
      ++buffers;
      bytes += data.size() * sizeof(delta::Block);
      window_bytes += data.size() * sizeof(delta::Block);
    }

    const auto now = Clock::now();
    if (now - last_report >= std::chrono::seconds(1)) {
      const double sec = std::chrono::duration<double>(now - last_report).count();
      const double elapsed = std::chrono::duration<double>(now - start).count();
      const double rate = static_cast<double>(window_bytes) / sec;
      const std::uint64_t p = periods, us = period_us;
      const double fps = us > window_period_us ? static_cast<double>(p - window_periods) * 1e6 /
                                                     static_cast<double>(us - window_period_us)
                                               : 0;
      const delta::StreamStats s = stream.Stats();
      std::cout << std::fixed << std::setprecision(0) << std::setw(4) << elapsed << " s  "
                << std::setprecision(1) << std::setw(6) << rate / (1 << 20) << " MiB/s  "
                << std::setprecision(2) << std::setw(5) << rate * 8 / 1e9 << " Gbps  "
                << std::setprecision(0) << std::setw(5) << fps << " fps  gaps " << s.dropped
                << " (crc " << s.error_buffers << ")  starved " << s.starved << "  timeouts "
                << timeouts << "  decoded " << decoder->Stats().buffers << "/" << s.buffers
                << std::endl;
      last_report = now;
      window_bytes = 0;
      window_periods = p;
      window_period_us = us;
    }
  }
  stop = true;
  viewer.join();
  const delta::StreamStats s = stream.Stats();
  const double total = std::chrono::duration<double>(Clock::now() - start).count();
  const std::uint64_t p = periods, us = period_us;
  std::cout << "done: " << std::setprecision(1) << total << " s, " << buffers << " buffers, "
            << static_cast<double>(bytes) / (1 << 20) / total << " MiB/s, "
            << (us ? static_cast<double>(p) * 1e6 / static_cast<double>(us) : 0) << " fps, gaps "
            << s.dropped << " (crc " << s.error_buffers << "), starved " << s.starved
            << ", timeouts " << timeouts << ", decoded " << decoder->Stats().buffers << "/"
            << s.buffers << " buffers\n";
  return failed || s.dropped || s.starved ? 1 : 0;
}
