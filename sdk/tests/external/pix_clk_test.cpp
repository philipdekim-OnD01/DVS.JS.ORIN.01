#include "stress.h"
#include <fstream>
#include <string>

// DT pix_clk_hz (VI/NVCSI 클럭·대역폭 요청과 NVCSI 설정에 쓰이는 값) 를 바꿔 가며 같은 조건으로
// 돌려 비교하는 시험. 지금 DT(1.25 GHz) 와 낮춘 값(627 MHz) 에서 각각 실행.
// group AER 최대 출력(17/12/2C/01) 을 1분 받아 수신 속도(buf/s) 를 봄.
// pix_clk_hz 는 NVCSI 가 기대하는 레인 속도(= pix_clk_hz x 8 / 4 lanes) 도 정하므로, 낮춘 DT 에선
// 센서도 같은 속도로 내려야 받을 수 있음 (2298 Mbps 그대로면 VI 가 모든 프레임을 버림).
// 결과가 어느 DT 에서 나온 것인지 남도록 시작할 때 live DT 의 pix_clk_hz 를 찍음.
// 버퍼 누락(gaps)이나 드라이버 고갈이 있으면 1
static constexpr const char *kModes = "/proc/device-tree/bus@0/i2c@3180000/nrv_dvs@10/";
// DPHY_PLL_PMS 00/86/88 = P4 M209 S0, 24 MHz -> 1254 Mbps/lane (pix_clk_hz 627 MHz)
static constexpr std::uint32_t kLane1254[] = {
    NRV_DVS_OP(0x30A5, 0x00),
    NRV_DVS_OP(0x30A6, 0x86),
    NRV_DVS_OP(0x30A7, 0x88),
};

int main() {
  std::string hz;
  for (const char *mode : {"mode0", "mode1"}) {
    std::ifstream in(std::string(kModes) + mode + "/pix_clk_hz");
    std::getline(in, hz, '\0');
    std::cout << "DT " << mode << " pix_clk_hz " << hz << '\n';
  }
  if (hz == "627000000") {
    std::cout << "lane 1254 Mbps (PMS 00/86/88)\n";
    return Stress("settings/Delta_10_2000FPS_EvthMax.txt", delta::Format::Compressed,
                  std::chrono::minutes(1), kLane1254);
  }
  return Stress("settings/Delta_10_2000FPS_EvthMax.txt", delta::Format::Compressed,
                std::chrono::minutes(1));
}
