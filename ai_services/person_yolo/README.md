# Person YOLO P0 — 읽기 전용 프리뷰 smoke test

**저장 영상 후처리 앱:** MP4/AVI와 저장 snapshot을 불러오는 독립 데스크톱 앱 및
결과 MP4/JSONL 저장은 [후처리 사용 가이드](POSTPROCESS_KO.md)를 참조한다.
아래 내용은 기존 P0 입력 계약이다.

[Issue #1](https://github.com/philipdekim-OnD01/DVS.JS.ORIN.01/issues/1)의
[공식 계획서](../../docs/ai-services/YOLO_RAW2_MODULAR_PLAN_2026-11-02_KO.md)를 구현하는 독립 Python 패키지다.
**P0는 snapshot 프리뷰 입력이며 RAW2 원본 직접 처리가 아니다.**
기존 수신기·드라이버·SDK·DT·센서 setfile·Viewer·서비스를 import하거나 제어하지 않는다.
운영 서비스에 설치하는 스크립트, CSI/I2C 접근, IPC 쓰기, 실시간 polling도 없다.

| 경로 | 상태 | 의미 |
|---|---|---|
| `SnapshotSource → PolarityRGB → YOLO11n → JSONL` | 구현/실제 실행 검증 | 이미 합성된 960×720 프리뷰 |
| `SyntheticSource → PolarityRGB → YOLO11n → JSONL` | 구현/테스트 | 결정적 패턴; 사람 검출 정확도 근거 아님 |
| `Raw2ZstdSource → 기존 SDK Raw2Decoder` | **NOT IMPLEMENTED / NOT TESTED** | P1 명시적 실패 인터페이스만 제공 |
| Jetson CUDA / TensorRT, tracking | **NOT TESTED / NOT IMPLEMENTED** | 후속 단계 |

실제 실행 결과는 [2026-11-02 보고서](benchmarks/REPORT_2026-11-02_KO.md)를 참조한다.
Windows CPU에서 실제 프리뷰 5장에 추론을 실행했고 검출은 0건이었다.
실행 경로 확인과 사람 검출 성능 입증은 별개다.

## 설치와 테스트

CPU 오프라인 검증용 별도 venv를 사용한다. Python 3.10 이상 필요.
Jetson의 시스템 Python, 기존 receiver venv, NVIDIA Torch/CUDA 환경을 덮어쓰지 않는다.
아래 명령의 시작 위치는 `ai_services/person_yolo/`다.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m person_yolo --synthetic moving --frames 3 --dry-run --output outputs_local/dry-run.jsonl
```

Windows PowerShell에서는 `py -3 -m venv .venv`, `.\.venv\Scripts\Activate.ps1`을 사용한다.
최소 설치에는 PyTorch/Ultralytics가 필요 없다. `--dry-run`은 추론하지 않으며
`status=dry_run`, `inference_ran=false`, 모델 필드 `null`, 빈 검출 목록을 기록한다.
모델 없는 일반 실행은 오류 종료하며 mock 검출을 대신 생성하지 않는다.

실제 CPU 모델 실행은 별도 venv에 다음을 추가한다. 아래 CPU wheel 명령은 검증한
Windows x86-64 환경 기준이다. Jetson GPU용 설치 안내가 아니다.

```sh
python -m pip install torch==2.9.0 torchvision==0.24.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e '.[yolo,test]'
```

## 모델 출처와 고정값

- 모델: COCO pretrained `yolo11n.pt`, Ultralytics assets release `v8.3.0`.
- [공식 다운로드](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt)
- 파일 크기: **5,613,764 bytes**.
- 관측 SHA-256: `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
  공식 release API에 digest가 없어서 다운로드한 파일의 해시를 직접 계산해 고정했다.
- `ultralytics==8.3.228`, `torch==2.9.0`, `torchvision==0.24.0`.
- 외부 의존성 라이선스: [Ultralytics v8.3.228 AGPL-3.0](https://github.com/ultralytics/ultralytics/blob/v8.3.228/LICENSE),
  [공급자의 AGPL/Enterprise 안내](https://www.ultralytics.com/license).
  배포 형태에 맞는 이용 조건 검토는 제품화 전 별도 필요하다. 이 PR이 Enterprise 권한을 부여하지 않는다.

신뢰한 공식 출처에서 저장소 밖 캐시로 직접 다운로드한다. `.pt`는 Git에 넣지 않는다.
런타임은 로컬 파일의 SHA-256과 Ultralytics 버전을 확인하고, 자동 설치/온라인 모드를 끈다.
실행 전에 쓰기 가능한 전용 `YOLO_CONFIG_DIR`을 생성한다.

```sh
mkdir -p "$HOME/.cache/nrv-person-yolo/config"
curl -fL https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt \
  -o "$HOME/.cache/nrv-person-yolo/yolo11n.pt"
sha256sum "$HOME/.cache/nrv-person-yolo/yolo11n.pt"
export YOLO_CONFIG_DIR="$HOME/.cache/nrv-person-yolo/config"
export YOLO_OFFLINE=true
export YOLO_AUTOINSTALL=false
python -m person_yolo --snapshot /path/to/saved-snapshot.bin \
  --config configs/p0.json --weights "$HOME/.cache/nrv-person-yolo/yolo11n.pt" \
  --output outputs_local/snapshot-yolo.jsonl
```

PowerShell 환경 변수는 `$env:YOLO_CONFIG_DIR = 'C:/path/to/cache/config'` 형태로 설정한다.
같은 output을 다시 지정하면 덮어쓰기 오류가 난다. 다음 실행에는 새 파일명을 사용한다.
`--snapshot a.bin b.bin`은 지정 순서대로 각 파일을 한 번 읽는다.
`--raw2-zstd dump-0000.bin.zst`는 명확한 NOT IMPLEMENTED 오류와 exit 2로 종료한다.

## 입력과 표현 계약

- snapshot = `[4B big-endian JSON length][UTF-8 JSON][172800B packed 2-bit preview]`.
  JSON은 최대 65536B, payload는 정확한 크기만 허용한다. trailing/short/truncated/잘못된 JSON은 거절한다.
- row-major 960×720, 각 byte의 낮은 2비트부터 네 화소를 읽는다.
  `0→RGB(128,128,128)`, `1 ON→(255,0,0)`, `2 OFF→(0,0,255)`.
  `3`은 경고와 함께 배경으로 표시하며 이벤트로 세지 않는다.
- 출력 tensor는 contiguous HWC `uint8` 3채널이다. x=0..959, y=0..719를 그대로 유지한다.
  959/955 컬럼 문제를 보간하지 않는다. 이후 YOLO 내부 resize/letterbox는 `imgsz=640` 설정이며,
  출력 bbox는 Ultralytics가 원본 프리뷰 좌표로 환산한다.
- RGB는 `PIL.Image`로 모델에 전달한다. Ultralytics의 ndarray 입력은 BGR이므로 그대로 넘기지 않는다.
  [공식 predict 입력 계약](https://docs.ultralytics.com/modes/predict/).
- 파일 descriptor 하나로 읽어 atomic replace 중에도 동일 파일 버전을 유지한다.
  read 전후 size/mtime 변화는 거절한다. 이는 협조적인 viewer atomic replace 계약 검증이며
  악의적인 in-place writer가 timestamp까지 복원하는 경우를 방어하는 잠금 프로토콜은 아니다.
- snapshot은 여러 센서 프레임을 합성한 최신 극성이다. 반복 이벤트의 개수·순서·개별 시각은 복원할 수 없다.
  `window_ms`는 `preview_us/1000` 설정 목표이며 실제 이벤트 관측 시간창이나 광학 지연이 아니다.

## 품질, 출력과 실패 의미

실제 입력은 저장 시점의 `state=live`, `has_frame=true`, `last_good_age<2`가 있어야 추론한다.
stalled/오래된 입력, 부족한 liveness 필드, 동일 세션의 중복 preview는 `skipped_input`으로 기록한다.
저장 시각의 상태 검증이며 현재 파일이 실시간으로 갱신 중이라는 판정은 아니다.
invalid 화소와 기존 decoder quality 카운터는 경고로 유지하고 프리뷰 smoke test는 실행한다.

JSONL 한 행은 입력 하나를 나타낸다. 검출 0건도 행을 남긴다.

- 출처: `input_id`, `source_kind`, `source_sha256`, `source_frame_id`, `source_session_pid`, `raw2_direct=false`.
  snapshot SHA는 전체 파일, synthetic SHA는 row-major 720×960 화소값 byte 배열 기준이다.
- 시각: `host_monotonic_us`와 `sensor_timestamp_candidate_us`를 분리한다.
  명시적 host clock이 없는 display timestamp를 host 시각으로 단정하지 않는다.
  센서 시각은 uncalibrated로 표시하고 센서 기반 속도·지연은 계산하지 않는다.
- 모델/표현: 버전, 해시, CPU device, 실제 prediction 스레드 수, 고정 config, 크기, `PolarityRGB`.
  Ultralytics가 predictor 초기화 시 스레드 수를 바꾸므로 공개 `on_predict_start` callback에서
  요청 스레드 수를 적용하고 실제 값을 기록한다.
- `detections[]`: `class_id=0`, `label=person`, `score`, `bbox_xyxy=[x1,y1,x2,y2]`.
- 시간: `representation_ms`, `inference_ms`, `processing_ms`는 파일 읽기와 모델 로딩을 제외한다.
  첫 predict는 lazy setup/warmup 비용을 포함할 수 있다. `inference_ms`는 forward-only GPU kernel 시간이 아니다.
- 품질: `decode_warnings`, `input_discontinuity`, `unobserved_preview_ids`, 기존 receiver/stream 누적 카운터.
  복사 간격 때문에 읽지 않은 preview ID는 receiver packet/frame loss와 동일하지 않다.
- exit 0: 선택 모드 실행 완료. `frames_inferred`/행의 `status`를 함께 확인해야 실제 추론 여부를 알 수 있다.
  exit 2: 입력/설정/모델/런타임 오류. 중간 실패 시 완료된 앞부분의 JSONL은 남을 수 있으므로 exit code 확인이 필수다.

## 측정 및 모듈 교체

```sh
python benchmarks/run_benchmark.py --metrics outputs_local/metrics.json -- \
  --snapshot /path/to/saved-snapshot.bin --config configs/p0.json \
  --weights /path/to/yolo11n.pt --output outputs_local/detections.jsonl
```

측정기는 CLI wall time, process CPU time, CPU 사용률(한 코어=100%), process peak RSS를 남긴다.
CPU/GPU 전체 시스템 사용률 또는 sensor-to-display end-to-end 지연이 아니다.
패키지의 `FrameSource`/`Detector` 경계와 representation/output을 분리하여 Source 교체가 가능하다.
`tests/`의 spy는 계약 검증에만 사용되며 CLI/benchmark에서 사용하지 않는다.

P1에서는 접근 승인된 `.bin.zst`, `status.yaml`, SHA, 수신기 버전을 확보한 뒤
기존 C++ SDK `Raw2Decoder`를 재사용한다. concatenated zstd frame/VI buffer와 센서 프레임 경계를
혼동하지 않고 `SensorFrame`/8행 `EventGroup`을 보존해야 한다.
그 뒤 `OnOffCount`, 신뢰 가능한 시계의 `TimeSurface`, 10/20/50ms `TemporalVolume`을 비교한다.
현재 snapshot만으로 이 세 표현을 계산했다고 주장하지 않는다.
tracking은 동일인 추적/침대 ROI 이탈 후보 인터페이스를 후속 설계한다. DINO/Qwen/낙상 판정은 P0 범위 밖이다.
정지 인물의 이벤트 부재를 무인 또는 안전의 증거로 사용하지 않는다.

라벨과 장면 내 사람 유무가 확인되지 않은 5개 샘플로 정확도·재현율·의료 성능을 추정하지 않는다.
raw, snapshot, 환자·개인 영상, 가중치는 Git에 넣지 않고 비식별 수치/해시/로그만 기록한다.
