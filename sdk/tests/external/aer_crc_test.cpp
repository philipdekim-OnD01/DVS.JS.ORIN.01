// group AER 을 레인 속도 2496 Mbps, 예전 Delta10 목록 + 최대 감도 17/12/2C/01 (EvthMax, 약 320 buf/s)
// 로 받으며 CSI CRC 오류 버퍼가 kErrors 개 나올 때까지 스트리밍 (kRuns 번 반복). 링크에 데이터를
// 가득 실어야 오류가 잘 드러남: 10.13판 Delta_10_2000FPS.txt (약 255 buf/s) 는 오류가 너무 드묾.
// 캐리어 보드끼리 CRC 오류율을 비교하려고 씀: 보드만 바꿔 같은 조건으로 돌리고 오류율을 비교.
// 회차마다 오류 시각과 1분 진행을 찍고, 끝나면 회차별 표, 회차가 둘 이상이면 평균(± 표준편차),
// 전체 합계 기준 오류율과 그 95% 신뢰구간을 찍음. 정확도는 오류 총 건수로만 정해짐
// (10건: 측정값의 약 0.5-1.7배, 100건: 약 0.8-1.2배).
//   crc: VI 가 ERROR 로 돌려준 버퍼 (커널 로그 corr_err), gaps: VI sequence 공백 전체 (crc 포함)
// kStall 동안 버퍼도 오류도 안 오면 그 회차는 멈춤으로 끝내고 평균에서 뺌.
// Read 오류나 멈춘 회차가 있으면 1
#include "nrv_dvs/delta.h"
#include "../register_lists.h"
#include <chrono>
#include <cmath>
#include <cstdint>
#include <format>
#include <iostream>
#include <vector>

static constexpr const char *kSetfile = "settings/Delta_10_2000FPS_EvthMax.txt";
// setfile 의 DPHY_PLL_PMS (00/8B/F8, 2298 Mbps) 를 목록 끝에 붙여 덮어씀:
// 00/8D/00 = P4 M416 S0, 24 MHz -> 2496 Mbps/lane
static constexpr std::uint32_t kLane2496[] = {
    NRV_DVS_OP(0x30A5, 0x00),
    NRV_DVS_OP(0x30A6, 0x8D),
    NRV_DVS_OP(0x30A7, 0x00),
};
static constexpr int kRuns = 1;
static constexpr std::uint64_t kErrors = 10;
static constexpr std::chrono::seconds kStall(10);

using Clock = std::chrono::steady_clock;

struct Run {
  double minutes = 0;
  delta::StreamStats stats;
  double bits = 0; // 보낸 비트: 받은 버퍼 + VI 가 버린 버퍼 (같은 크기)
  bool stalled = false;
};

static double PerHour(const Run &r) { return static_cast<double>(r.stats.error_buffers) / (r.minutes / 60); }
static double PerBit(const Run &r) { return static_cast<double>(r.stats.error_buffers) / r.bits; }

// 오류 k 개에서 멈추므로 k 번째 오류까지의 양(시간, 비트) x 오류율 ~ Gamma(k, 1).
// Gamma(k, 1) 의 누적확률 P(t) = 1 - e^-t sum_{i<k} t^i / i! 이 p 가 되는 t 를 이분법으로 찾음
static double GammaQuantile(std::uint64_t k, double p) {
  double low = 0, high = 10 * static_cast<double>(k) + 50;
  for (int iteration = 0; iteration < 200; ++iteration) {
    const double t = (low + high) / 2;
    double term = 1, sum = 0;
    for (std::uint64_t i = 0; i < k; ++i) {
      sum += term;
      term *= t / static_cast<double>(i + 1);
    }
    if (1 - std::exp(-t) * sum < p)
      low = t;
    else
      high = t;
  }
  return (low + high) / 2;
}

static std::error_code Measure(const std::vector<std::uint32_t> &ops, int index, Run &run) {
  auto dev = delta::Device("/dev/video0");
  auto err = dev.SetRegisterList(ops);
  if (err)
    return err;
  // dev 를 넘기면 Stream 이 STREAMON (이때 위 목록이 센서에 쓰임)
  delta::Stream stream(std::move(dev), delta::Format::Compressed);
  delta::WaitPipe pipe(stream, 64);

  const auto start = Clock::now();
  auto last_report = start, last_progress = start;
  std::uint64_t buffer_bytes = 0, errors = 0, received = 0;
  std::vector<delta::Block> data;
  bool discontinuous = false;
  std::cout << std::format("\n[run {}/{}]\n", index, kRuns);
  while (errors < kErrors) {
    err = pipe.Read(data, discontinuous, std::chrono::milliseconds(500));
    if (err && err != std::errc::timed_out)
      return err;
    if (!err)
      buffer_bytes = data.size() * sizeof(delta::Block);
    run.stats = stream.Stats();
    const auto now = Clock::now();
    run.minutes = std::chrono::duration<double>(now - start).count() / 60;
    if (run.stats.buffers != received || run.stats.error_buffers != errors)
      last_progress = now;
    received = run.stats.buffers;
    if (run.stats.error_buffers > errors) {
      errors = run.stats.error_buffers;
      std::cout << std::format("  {:6.1f} min  crc {:2}/{}  buffers {}\n", run.minutes, errors, kErrors,
                               run.stats.buffers)
                << std::flush;
    }
    if (now - last_report >= std::chrono::minutes(1)) {
      std::cout << std::format("  {:6.1f} min  ...    buffers {}  ({:.1f} buf/s)  gaps {}  starved {}\n",
                               run.minutes, run.stats.buffers,
                               static_cast<double>(run.stats.buffers) / (run.minutes * 60), run.stats.dropped,
                               run.stats.starved)
                << std::flush;
      last_report = now;
    }
    if (now - last_progress >= kStall) {
      run.stalled = true;
      std::cout << std::format("  {:6.1f} min  stalled: no buffers for {} s\n", run.minutes, kStall.count());
      break;
    }
  }
  run.bits = static_cast<double>((run.stats.buffers + run.stats.dropped) * buffer_bytes) * 8;
  return {};
}

static void Row(const std::string &name, const Run &r) {
  std::cout << std::format("{:>5} {:8.1f} {:>10} {:>5} {:>5} {:>7} {:>7.1f} {:>8.1f} {:>10.2e}{}\n", name, r.minutes,
                           r.stats.buffers, r.stats.error_buffers, r.stats.dropped, r.stats.starved,
                           static_cast<double>(r.stats.buffers) / (r.minutes * 60), PerHour(r), PerBit(r),
                           r.stalled ? "  stalled" : "");
}

int main() {
  std::vector<std::uint32_t> ops;
  if (!ReadSetfile(kSetfile, ops))
    return 1;
  ops.insert(ops.end(), std::begin(kLane2496), std::end(kLane2496));
  std::cout << std::format("crc: {} (17/12/2C/01) + 2496 Mbps, {} runs x {} crc buffers\n",
                           kSetfile, kRuns, kErrors);

  std::vector<Run> runs(kRuns);
  int status = 0;
  for (int i = 0; i < kRuns; ++i) {
    auto err = Measure(ops, i + 1, runs[static_cast<std::size_t>(i)]);
    if (err) {
      std::cerr << "run " << i + 1 << ": " << err.message() << '\n';
      return 1;
    }
  }

  std::cout << std::format("\n{:>5} {:>8} {:>10} {:>5} {:>5} {:>7} {:>7} {:>8} {:>10}\n", "run", "min", "buffers",
                           "crc", "gaps", "starved", "buf/s", "crc/h", "crc/bit");
  Run total;
  double hour_sum = 0, hour_sq = 0, bit_sum = 0, bit_sq = 0;
  int n = 0;
  for (std::size_t i = 0; i < runs.size(); ++i) {
    const Run &r = runs[i];
    Row(std::to_string(i + 1), r);
    if (r.stalled) {
      status = 1;
      continue;
    }
    total.minutes += r.minutes;
    total.bits += r.bits;
    total.stats.buffers += r.stats.buffers;
    total.stats.error_buffers += r.stats.error_buffers;
    total.stats.dropped += r.stats.dropped;
    total.stats.starved += r.stats.starved;
    hour_sum += PerHour(r);
    hour_sq += PerHour(r) * PerHour(r);
    bit_sum += PerBit(r);
    bit_sq += PerBit(r) * PerBit(r);
    ++n;
  }
  if (n == 0 || total.stats.error_buffers == 0)
    return 1;
  if (n > 1) {
    // 회차 평균 ± 표본 표준편차 (멈춘 회차 제외)
    const double hour_mean = hour_sum / n, bit_mean = bit_sum / n;
    const double hour_sd = std::sqrt((hour_sq - n * hour_mean * hour_mean) / (n - 1));
    const double bit_sd = std::sqrt((bit_sq - n * bit_mean * bit_mean) / (n - 1));
    std::cout << std::format("{:>5} {:>8} {:>10} {:>5} {:>5} {:>7} {:>7} {:>8.1f} {:>10.2e}\n", "mean", "", "", "", "",
                             "", "", hour_mean, bit_mean)
              << std::format("{:>5} {:>8} {:>10} {:>5} {:>5} {:>7} {:>7} {:>8.1f} {:>10.2e}\n", "sd", "", "", "", "",
                             "", "", hour_sd, bit_sd);
  }
  // 전체 합계 기준 (전체 crc / 전체 시간·비트)
  Row("total", total);
  // 전체 오류율의 95% 신뢰구간
  const double low = GammaQuantile(total.stats.error_buffers, 0.025);
  const double high = GammaQuantile(total.stats.error_buffers, 0.975);
  std::cout << std::format("95% CI ({} crc): {:.1f} - {:.1f} crc/h, {:.2e} - {:.2e} crc/bit\n",
                           total.stats.error_buffers, low / (total.minutes / 60), high / (total.minutes / 60),
                           low / total.bits, high / total.bits);
  return status;
}
