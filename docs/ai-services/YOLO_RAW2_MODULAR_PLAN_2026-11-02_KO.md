# DVS RAW2 기반 모듈형 YOLO 사람 검출 PoC 및 서비스 AI 로드맵

작성 기준: 2026-11-02  
대상: Samsung S5KRC1S, Jetson Orin Nano Super, Jetson Linux R39.2.1  
기준 소스: 본 저장소 main d39b956dd88e74c0cd5eae25adff26af7a12a64c  
범위: 계획/개발 인수인계. 이 문서 자체는 AI 구현이나 추론 실측을 의미하지 않는다.

## 1. 결정 및 업무 분리

- Minsu와 Karim은 CSI-2/VI 수신 품질, RSTN 리셋, watchdog, RAW2 센서 시각, 병원 장시간 녹화의 문제를 계속 검증한다. 수신 관련 이슈를 덮거나 운영 장비의 설정을 변경하지 않는다.
- 우리는 기존 RAW2/SDK/프리뷰 결과를 읽기 전용 입력으로 사용하여 AI 서비스를 독립 모듈로 만든다.
- 우선순위: YOLO nano 사람 검출 → 시간에 따른 사람 움직임/침대 이탈 후보 → DINO 특징 추출 비교 → Qwen 기반 근거 있는 사건 설명.
- 첫 제품 시나리오는 야간 침대 주변 사람 움직임 및 침대 이탈 후보 감지이다. 초기 단계에서 낙상 확정이나 의료적 진단을 주장하지 않는다.
- 산출물은 다른 센서나 SoC에서도 Source adapter만 교체해 재사용할 수 있어야 한다.

## 2. 결정적 기술 사실: RAW2를 YOLO에 직접 넣을 수 있는가?

결론: 기존 RGB로 사전학습된 일반 Ultralytics YOLO nano는 S5KRC1S의 CSI-2/V4L2 RAW2 바이트 스트림을 그대로 입력할 수 없다. RAW2는 일반적인 RGB/RGB565 사진이 아니며, 이미지 모델이 요구하는 형상, 채널, 값의 범위와 의미가 다르다. 먼저 벤더 SDK로 이벤트를 해석하고 정해진 시간창으로 모아 이미지 또는 모델용 텐서로 변환해야 한다.

- 완전 원본 경로: 압축된 dump-*.bin.zst → 압축 해제/SDK Raw2Decoder → 센서 프레임 + EventGroup → 시간창별 Event Representation → YOLO.
- 가장 빠른 현장 Smoke Test: 이미 생성된 snapshot.bin → JSON + 172,800B의 2비트 ON/OFF 프리뷰 해석 → 3채널 8비트 테스트 영상 → YOLO nano. snapshot은 여러 센서 프레임을 합친 표시용 자료이며 수신 원본의 무손실 재현이 아니다. 여기서 검출이 되더라도 원본/시간 기반 정합성을 입증한 것이 아니다.
- 진짜 sparse-event / end-to-end 입력을 받는 Event-GNN, SNN, recurrent/변형 YOLO 등은 선행 연구가 존재하지만, 일반 YOLO nano와 동일한 코드·사전학습 가중치로 RAW2 바이트를 그대로 먹일 수 있다는 뜻은 아니다.
- 실용적인 권고: 첫 번째 버전은 ON/OFF 영상을 RGB 호환 3채널로 매핑하여 일반 YOLO nano로 실행 가능성을 확인한다. 두 번째 버전에서 시간표현의 성능을 비교한다. 세 번째 버전부터 이벤트 전용 구조 또는 재학습을 검토한다.

### 선행 사례

1. Silva et al., "A recurrent YOLOv8-based framework for event-based object detection" (2025), DOI 10.3389/fnins.2024.1477979. 이벤트를 VTEI(Volume of Ternary Event Images) 시간 텐서로 변환한 뒤 recurrent YOLOv8을 적용한다. 원본 바이트 그대로 입력한 사례가 아니라 이벤트 표현 + 모델 수정의 사례다. https://pmc.ncbi.nlm.nih.gov/articles/PMC11794834/
2. Hannan et al., "Event-to-Video Conversion for Overhead Object Detection" (2024). 이벤트에서 영상으로 재구성하여 기존 객체 검출 모델의 사전학습 도메인 차이를 줄이는 접근이다. https://www.pnnl.gov/publications/event-video-conversion-overhead-object-detection
3. Hay et al., "Comparing Representations for Event Camera-based Visual Object Tracking" (ICCV Workshop 2025). Event Frame, Voxel Grid, IWE, EST 등 표현 선택의 차이가 실제 성능에 중요함을 보여준다. https://openaccess.thecvf.com/content/ICCV2025W/NeVi/html/Hay_Comparing_Representations_for_Event_Camera-based_Visual_Object_Tracking_ICCVW_2025_paper.html

위 사례는 설계 근거이지 S5KRC1S에서 YOLO 사람이 이미 검출되었다는 증거가 아니다.

## 3. 현재 저장소에서 검증된 데이터 경계

### 수신기/SDK 사실

- SDK 공개 API: sdk/include/nrv_dvs/decoder.h
  - SensorFrame: timestamp_us(센서 시각 후보), period_us(미확정 가능), groups.
  - EventGroup: x, y(8픽셀 그룹의 시작 행), mask(행별 비트), polarity(0=ON, 1=OFF).
  - Raw2Decoder와 GroupAerDecoder가 같은 출력 형식을 제공한다.
  - 센서 프레임 경계와 V4L2 버퍼 경계는 동일하지 않다.
- 수신기: app/dvs_streamer/src/main.cpp
  - RAW2 수신 원본: 약 175,680B/VI buffer. raw dump는 버퍼마다 독립 zstd frame으로 압축하여 dump-NNNN.bin.zst에 기록한다.
  - 프리뷰 출력: [4B big-endian JSON length][JSON][172,800B packed 2-bit image].
  - 프리뷰 화소: 0=없음, 1=ON, 2=OFF. 3은 유효 이벤트로 간주하지 않는다.
  - 프리뷰는 기본 16,666us 창에서 여러 센서 프레임을 합치며, 최신 극성을 보존한다. 시간/이벤트 원본의 전량 보존이 아니다.
- 프리뷰 독립 읽기: app/dvs_streamer/python/viewer.py 내 read_snapshot(directory). 함수는 snapshot.bin을 numpy로 언패킹하여 960x720 픽셀을 반환한다. 이 함수는 재사용 가능하나 웹 서버/녹화 제어에 종속시키지 말 것.
- 현재의 1,597.80 decoded frames/s, 58.71 server preview frames/s, 267.49MiB/s는 2026-11-02 약 4초 구간의 실제 측정이지 YOLO 추론 결과가 아니다: docs/deployment/FPS-2026-11-02.md.
- RAW2 센서 timestamp에는 ambiguity/order-error 카운터가 남아 있다. 센서 timestamp로 확정된 광학적인 지연·속도를 계산하지 말 것. host monotonic과 원본/해석 frame sequence를 별도로 보존하되, 녹화 파일에 호스트 시각이 프레임별로 포함된다고 추정하지 말 것.
- RAW2 959-column short-frame 진단과 RP5의 955/960 컬럼 이슈는 혼동하지 말 것. 실제 좌표 정합성이 필요한 응용에서 별도 검증하고, 디코더 출력에 잘못된 좌표를 보간하지 말 것.

### 입력 출처의 차이

A. Jetson snapshot.bin: 즉시 실행 가능한 표시용 2비트 데이터. 첫 pretrained YOLO smoke test에 사용.
B. Jetson RAW2 .zst: 원본 버퍼의 기록. 우선 SDK 디코더를 재사용하는 오프라인 C++ 또는 Python 바인딩 어댑터로 해석하고, 복원된 프레임/이벤트를 입력으로 사용할 것. 압축 멀티프레임 구조와 버퍼 경계를 바르게 처리할 것. 실제 파일 접근은 사전 확인.
C. RP5 mipi-raw2-10s.dvsraw.gz: 기존 연구 파일. RP5 971/976 마커/좌표 경계 미해결이므로 정확한 960x720 실제 라벨 생성의 기준 진실로 사용하지 말 것. 기존 정상 decoder 출력과 오류/품질 플래그를 구분하여 비교 연구용으로만 사용할 것.
D. Synthetic: 기기 없이 구현/테스트 가능하도록 무조건 제공. 실제 사람 검출 성공으로 기록하지 말 것.

## 4. 독립 AI 모듈 계약

신규 경로 제안: ai_services/person_yolo/ 아래에 새 파일만 추가한다. 기존 drivers/, device-tree/, sdk/, app/dvs_streamer/를 고치지 않는다. 이름은 저장소 구성 확인 후 일관되게 선택한다.

1) sources/
- SnapshotSource: snapshot.bin을 읽어 packed 2비트 프리뷰와 JSON 메타데이터를 반환. 원본 파일 변경 금지, 임시 파일 교체 중 스냅샷 일관성 검증.
- Raw2ZstdSource: 오프라인 .zst에서 SDK decoder를 재사용. 프레임/이벤트 경계를 넘나드는 버퍼 처리와 읽기 에러를 명시적으로 보고. 첫 PR에서는 실제 디코더 연결이 복잡할 경우 인터페이스만 만들고 NOT IMPLEMENTED로 표시; 프리뷰 검출을 RAW2 원본 완료로 가장하지 말 것.
- SyntheticSource: 빈 프레임, ON/OFF 패턴, 잘린 입력, 비정상 값, 불연속 테스트.

2) representations/
- PolarityRGB: 회색 배경 + ON/OFF를 서로 다른 채널로 표시하는 3채널 uint8 출력. 색상 선택 자체는 네트워크 성능의 근거가 아님.
- OnOffCount: 일정 시간창에서 ON/OFF 빈도·카운트를 독립 2채널로 보존. 필요할 때 RGB 3채널로 어댑트.
- TimeSurface: 이벤트별 최근 시각으로 생성(신뢰 가능한 시계에서만). 프리뷰 단일 이미지로는 진짜 event timestamp를 복원할 수 없으므로 SnapshotSource에서 TimeSurface를 계산했다는 주장은 금지.
- TemporalVolume: 10/20/50ms 또는 5/10/20 bin 등 비교 가능한 시간창 설정과 경계 처리를 명시.

3) detectors/
- YoloNanoDetector: Ultralytics pretrained nano 모델(예: yolo11n.pt 또는 yolov8n.pt)을 고정된 버전/체크섬으로 로드, person class만 필터, 좌표/score/모델 버전 출력.
- 초기엔 CPU/macOS에서도 돌아가는 오프라인 smoke test를 우선. Jetson TensorRT export는 오프라인 테스트 후 별도 단계로 진행.
- 원본 DVS 입력과 RGB/COCO 사전학습 도메인이 달라 사람 검출 실패가 예상될 수 있다. 결과가 없으면 0 detection을 정상적이고 정직한 결과로 남긴다. 임의로 사람 박스를 만들거나 threshold를 유리하게 조정하지 말 것.

4) tracking/ (후속)
- 여러 프레임 간 동일인 추적, 침대 ROI 이탈 후보, 움직임 방향·빈도·지속시간; 최초 PR에서는 인터페이스 계획만.
- 정지 인물이 이벤트를 만들지 않는 DVS 특성 때문에 움직임 부재를 무인 상태나 안전 상태의 증거로 간주하지 말 것.

5) outputs/
- 공통 Detection JSONL: input_id, source_kind, source_sha256 또는 hash, source_frame_id, host_monotonic_us(있을 때), sensor_timestamp_candidate_us(있을 때), timestamp_quality, representation, window_ms, width, height, model_file_sha256, model_version, class_id, label, score, bbox_xyxy, processing_ms, decode_warnings, dropped/skipped 상태. 센서 시간과 호스트 시간을 혼합하지 말 것.
- 선택적 이미지/주석 파일. 입력 raw/환자 데이터·가중치는 Git에 커밋하지 말 것.

6) config/ + tests/ + benchmarks/
- 고정 설정 파일로 실행 재현, 모델/라이선스 메타데이터, README, pytest.
- 설치 패키지와 CLI를 소스/디바이스/모델과 느슨하게 연결하여 다른 SoC에서도 교체 가능하게 설계.

## 5. 처리 데이터 흐름

Snapshot smoke path:
existing receiver -> snapshot.bin (read-only) -> 2bit unpack -> PolarityRGB -> YOLO nano -> detections.jsonl (+ optional annotated PNG).

Raw offline path:
dump-*.bin.zst (read-only) -> zstd frames -> existing SDK Raw2Decoder -> SensorFrame/EventGroup -> Event representation (ON/OFF counts, VTEI/temporal bins) -> YOLO nano baseline/finetune -> detections.jsonl.

Realtime AI path (후속 별도 설계):
existing receiver output -> bounded read-only tap/IPC -> AI worker -> evidence-backed alert. 수신/녹화 스레드에 AI 추론을 inline으로 넣지 말 것. backpressure는 AI 입력만 버리고 receiver/recording을 막지 않아야 하며, 버린 입력·지연·스냅샷 건너뜀을 명시적으로 계측.

## 6. 첫 Codex 작업 — 반드시 실제로 실행하고 증거 남길 것

### Phase 0: read-only inventory
- main 커밋, Python/Jetson 환경, snapshot.bin 존재·형식·JSON, 실제 접근 가능한 RAW2 .zst와 SHA를 확인.
- 데이터 파일이 없으면 synthetic test는 수행하되 실제 person detection PASS라고 적지 않는다.
- SDK decoder output shape, 959/960 short frame, timestamps 경계/quality fields를 기록.

### Phase 1: independent MVP (파일·오프라인만)
- ai_services/person_yolo/ 구조를 구현한다. 초기 최소 요구: SnapshotSource, SyntheticSource, PolarityRGB, YoloNanoDetector, JSONL 저장, CLI, README, 단위 테스트.
- 기존 recv/driver/SDK/DT/boot/config/setfile/CSI/I2C/service 등은 전혀 변경하지 않는다.
- 프리뷰 영상만 YOLO에 주입한 것을 'RAW2 원본 direct'이라고 표기하지 않는다.
- 모델 다운로드가 필요하면 외부 모델 라이선스와 해시를 기록하고 별도 캐시에 저장한다. Git에는 모델 파일을 커밋하지 않는다.
- 원본 프리뷰가 없는 환경에서도 offline/synthetic test 가능.
- 모델이 없는 경우 fail clearly / dry-run mode; mock 결과를 실제 detect 결과로 표시하지 않는다.

### Phase 2: RAW2 오프라인 연결
- 이미 구현된 SDK Decoder와 저장 포맷을 참조한다. 임의 Python raw2 새 디코더 작성으로 이중 규격을 만들지 않는다.
- 실제 Jetson .zst 파일, status.yaml, 원본 SHA와 source version이 제공된 후 연결한다.
- EventGroup 8행 마스크를 개별 (x,y,polarity)로 확장하고 10/20/50ms bin을 구성한다.
- quality flag 및 frame gap/ambiguity를 분리해 출력한다. 시간 기준이 불확실하면 안전하게 해당 temporal metric을 제외하거나 host monotonic 기준임을 명시한다.

### Phase 3: ground-truth and benchmark
- 사람(움직임/부분가림/밤/정지), 빈 장면, 흔들림, Hot Pixel 조건을 포함한 데이터 수집·annotation 설계.
- 단일 이미지 사람 검출은 수치가 없으면 PoC 미확정. 준비된 레이블에서 precision/recall/F1, AP50, false alerts/hour, latency p50/p95, CPU/GPU/memory, skipped inputs를 측정.
- 최소 비교: PolarityRGB/ON-OFF count/temporal (동일 장면·동일 레이블·동일 모델/조건). 정지 인체 인식 한계 기록.
- YOLO nano가 실패하면 컬러 매핑부터 무한 튜닝하지 말고 도메인 차이와 데이터/라벨의 충분성을 점검. 필요하면 event-to-video reconstruction baseline 또는 fine-tuning.

## 7. 인수 기준 (첫 PR)

- [ ] 기존 main 실행 파일, sensor setfile, nrv_dvs.ko, dtbo, 서비스, 기존 Viewer/Recorder에 diff 0.
- [ ] 파일 구조·CLI·설치·모델 라이선스·데이터 출처 문서화.
- [ ] synthetic empty/moving-pattern/invalid/short/truncated 테스트 PASS. ON/OFF 극성·좌표·960×720 형상 보존.
- [ ] snapshot.bin 파싱 정확성(4B length/JSON/172800B), 경계 이상·화소값 3 안전 처리.
- [ ] pretrained YOLO nano를 실제로 실행했다면 명령/실행기기/모델 hash/입력 hash/결과(0건 가능)·CPU/RAM/latency 로그 게시.
- [ ] 예제 images/results는 비식별 공개 가능 자료만 커밋. 환자·개인·비공개 원본/민수·Karim 소스의 무단 복사 금지.
- [ ] 실제 RAW2 .zst 직접 처리 여부는 YES/NO/NOT TESTED로 명확히 구분.
- [ ] 독립 feature branch + PR을 사용하고, 리뷰·테스트 전 main 자동 병합 금지.
- [ ] PR에 FACT / EVIDENCE / RISK / NEXT 보고, 원본 수신기 unmodified 선언과 commit SHA 기록.

## 8. 연구·서비스 범위 구분

- 기술 데모: 사전학습 YOLO가 DVS 프리뷰에서 person bbox를 생성할 수 있는지.
- 제품 검증: 침대 이탈 후보 탐지, 사생활 보호, 지연·오경보율, 센서별 편차, 야간 현장 데이터.
- 의료 실증: 안전과 알림을 의료진이 검증하는 보조 기능. 병원 수집 데이터·공개/비공개 권한·인증 절차 준수.
- 후속 DINO는 오프라인 특징 추출 비교, Qwen은 규칙/검증된 이벤트에 근거한 설명만. 두 모델은 최초 YOLO PR에 넣지 않는다.
- 수신 안정성: Minsu/Karim의 CSI CRC, RSTN, 5초 stall, 실제 timestamp 정합성 및 64h 수신·녹화는 독립 추적. 그 결과를 이 서비스의 입력 품질 게이트에 반영한다.

## 9. 참고 링크

- Jetson FPS: ../deployment/FPS-2026-11-02.md
- C++ SDK 출력 계약: ../../sdk/include/nrv_dvs/decoder.h
- RAW2 decoder: ../../sdk/src/raw2_decoder.cpp
- Streamer snapshot/recording contract: ../../app/dvs_streamer/src/main.cpp
- Viewer snapshot reader: ../../app/dvs_streamer/python/viewer.py
- ReYOLOv8 (2025): https://pmc.ncbi.nlm.nih.gov/articles/PMC11794834/
- PNNL Event-to-Video (2024): https://www.pnnl.gov/publications/event-video-conversion-overhead-object-detection
- ICCVW Event Representations (2025): https://openaccess.thecvf.com/content/ICCV2025W/NeVi/html/Hay_Comparing_Representations_for_Event_Camera-based_Visual_Object_Tracking_ICCVW_2025_paper.html

이 문서를 Codex 구현 Issue의 canonical plan으로 사용한다. 완료된 검증을 반복하거나, 수신기 성능 계측값을 AI 추론 결과로 혼동하지 않는다.
