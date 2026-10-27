#include "stress.h"

// group AER 최대 출력 (예전 Delta10 목록 + 최대 감도): 데이터 레인 시험
int main() {
  return Stress("settings/Delta_10_2000FPS_EvthMax.txt",
                delta::Format::Compressed, std::chrono::minutes(25));
}
