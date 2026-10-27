// 테스트가 함께 쓰는 센서 레지스터 목록과 setfile 읽기 (Device::SetRegisterList 에 넘김)
#pragma once
#include <array>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <nrv_dvs/opcode.h>
#include <string>
#include <vector>

// 최대 감도 (settings/Delta_10_2000FPS_EvthMax.txt 와 같은 값): 이벤트 임계값을 1에 가장 가깝게.
// setfile 목록 뒤에 붙여 Delta10 값(1F/0F/07/0F)을 덮어씀 (목록은 순서대로 쓰임)
inline constexpr std::array<std::uint32_t, 4> kMaxSensitivity = {
    NRV_DVS_OP(0x0157, 0x17),
    NRV_DVS_OP(0x0166, 0x12),
    NRV_DVS_OP(0x0167, 0x2C),
    NRV_DVS_OP(0x0168, 0x01),
};

// 뷰어의 최대 감도 1F/0F/7E/07 (0157/0166/0167/0168). 0167 은 6비트 칸이라 7E 의 bit6 은
// 무시되어 3E 로 쓰임
inline constexpr std::array<std::uint32_t, 4> kViewerMaxSensitivity = {
    NRV_DVS_OP(0x0157, 0x1F),
    NRV_DVS_OP(0x0166, 0x0F),
    NRV_DVS_OP(0x0167, 0x7E),
    NRV_DVS_OP(0x0168, 0x07),
};

// 최소 감도 1F/0F/02/3E (0157/0166/0167/0168)
inline constexpr std::array<std::uint32_t, 4> kMinSensitivity = {
    NRV_DVS_OP(0x0157, 0x1F),
    NRV_DVS_OP(0x0166, 0x0F),
    NRV_DVS_OP(0x0167, 0x02),
    NRV_DVS_OP(0x0168, 0x3E),
};

// setfile 의 "20:AAAA=VV" 줄을 순서대로 ops 에 (주석 줄 "//20:..." 은 건너뜀,
// 0100 스트리밍 시작/정지는 드라이버가 하므로 뺌). 못 읽으면 false
inline bool ReadSetfile(const char *path, std::vector<std::uint32_t> &ops) {
  std::ifstream file(path);
  if (!file) {
    std::fprintf(stderr, "%s: cannot open\n", path);
    return false;
  }
  for (std::string line; std::getline(file, line);) {
    unsigned addr = 0, value = 0;
    if (std::sscanf(line.c_str(), " 20:%4x=%2x", &addr, &value) == 2 && addr != 0x0100)
      ops.push_back(NRV_DVS_OP(addr, value));
  }
  return !ops.empty();
}
