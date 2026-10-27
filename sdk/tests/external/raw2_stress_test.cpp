#include "stress.h"

// RAW2, 레인 속도 2298 Mbps (DPHY_PLL_PMS 00/8B/F8). 2496 Mbps 의 CRC
// 오류율(60초에 약 0.19건)이면 25분에 약 4.6건이라, 0건이면 차이가 있다고 볼 수
// 있는 길이
int main() {
  return Stress("settings/Delta_10_2000FPS_RAW2.txt", delta::Format::Raw2,
                std::chrono::minutes(25));
}
