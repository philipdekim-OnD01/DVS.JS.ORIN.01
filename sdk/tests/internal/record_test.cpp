// 녹화만: recordings.h 의 파일마다 같은 시간씩 녹화 (Record). 압축 벤치
// (compression_bench)는 같은 녹화를 먼저 한 뒤 압축하므로, 이 테스트는 녹화만 따로
// 해 볼 때 씀.
#include "../recordings.h"

int main() {
  bool ok = true;
  for (const RecordedFile &file : kRecordedFiles)
    ok = Record(file) && ok;
  return ok ? 0 : 1;
}
