#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <span>
#include <vector>
#include <nrv_dvs/decoder.h>

// 세로8화소를16비트로 유지해 event group 하나를 한 번에 합성한다.
// 0은 앞 화면값을 유지하고 ON/OFF는 마지막 극성이 이긴다. 출력할 때만 행 우선 변환.
class PreviewRaster {
public:
  PreviewRaster() : columns_(960 * 90) {}
  void Clear() { std::fill(columns_.begin(), columns_.end(), 0); }
  void Add(const delta::EventGroup &g) {
    const auto lanes = kLanes[g.mask];
    auto &word = columns_[std::size_t{g.x} * 90 + g.y / 8];
    word = static_cast<std::uint16_t>((word & ~(lanes | (lanes << 1))) |
                                     (lanes << g.polarity));
  }
  void Pack(std::span<std::uint8_t> packed) const {
    std::fill(packed.begin(), packed.end(), 0);
    for (std::size_t x = 0; x < 960; ++x)
      for (std::size_t group = 0; group < 90; ++group) {
        const unsigned word = columns_[x * 90 + group];
        if (!word) continue;
        for (unsigned bit = 0; bit < 8; ++bit) {
          const std::size_t pixel = (group * 8 + bit) * 960 + x;
          packed[pixel / 4] |= static_cast<std::uint8_t>(((word >> (2 * bit)) & 3u) << (2 * (pixel % 4)));
        }
      }
  }
private:
  inline static constexpr auto kLanes = [] {
    std::array<unsigned,256> table{};
    for (unsigned mask = 0; mask < 256; ++mask)
      for (unsigned bit = 0; bit < 8; ++bit)
        table[mask] |= ((mask >> bit) & 1u) << (2 * bit);
    return table;
  }();
  std::vector<std::uint16_t> columns_;
};
