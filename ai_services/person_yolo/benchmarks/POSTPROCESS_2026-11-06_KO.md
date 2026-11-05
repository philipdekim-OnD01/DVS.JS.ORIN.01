# DVS YOLO 독립 후처리 검증 — 2026-11-06

## FACT

- 기존 P0 커밋 `8ff51726ad82112c2bc7ad649093ccfcb0dbe85f`를 기반으로
  `codex/dvs-yolo-postprocess-20261106` 브랜치에서 개발했다.
- 저장 영상 / 저장 프리뷰 → YOLO11n → 결과 MP4 / JSONL / summary를 처리한다.
  독립 Tk 앱과 CLI를 제공한다. 이번 작업에서 MIPI/센서/수신기에는 접근하지 않았다.
- Windows CPU에서 실제 가중치로 실행했다. source_kind와 raw2_direct=false를 모든 결과에 기록한다.
- 테스트 **60 passed, 1 skipped**. 기존 Linux atomic replace 테스트의 Windows skip이다.
- GUI에서 파일 선택 상태, 실제 worker 추론, 축소 미리보기, 완료 및 출력 폴더 표시를 확인했다.
- 최초 레이아웃에서 미리보기 때문에 하단 상태가 가려져, footer 영역 확보와 비율 유지 축소로 수정했다.

## EVIDENCE

환경: Windows 11 x86-64, Python 3.13.13, OpenCV 5.0.0,
Ultralytics 8.3.228, torch 2.9.0+cpu, YOLO11n CPU prediction threads 2.
모델 SHA `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
confidence 0.25 / IoU 0.7 / imgsz 640을 유지했다.

| 검증 입력 | 실제 추론 | person 박스 | 결과 |
|---|---:|---:|---|
| 기존 실제 Jetson snapshot 5개 | 5/5 | 0 | 960×720, 4fps 결과 MP4 + JSONL |
| 위 프리뷰 5개로 다시 만든 MP4 | 5/5 | 0 | MP4 입력 디코딩·추론·재인코딩 성공 |
| Synthetic moving ON/OFF MP4 | 12/12 | 0 | synthetic임을 provenance에 명시 |
| 독립 앱에서 프리뷰 유래 MP4 처리 | 5/5 | 0 | GUI 완료 상태와 결과 프레임 확인 |

실제 프리뷰 직접 처리 wall time 약 1.82s, 파생 MP4 CLI 약 1.52s,
GUI 처리 약 1.51s. 모델 로딩·입력 해시·출력 재검증을 포함한 작은 파일 처리 시간이며
실시간 FPS, Jetson GPU 성능 또는 sensor latency가 아니다.
synthetic은 같은 검증 프로세스의 후속 모델 실행이므로 cold-start 시간 비교에 쓰지 않는다.

실행한 명령의 portable 표기:

```sh
python -m pytest ai_services/person_yolo -q
python -m person_yolo.postprocess --input snapshot-00.bin snapshot-01.bin snapshot-02.bin snapshot-03.bin snapshot-04.bin --weights yolo11n.pt --output real-snapshots
python -m person_yolo.postprocess --input preview-derived.mp4 --weights yolo11n.pt --output preview-derived-result
python -m person_yolo.desktop
```

영상 fixture 검증기는 같은 `process()`를 직접 호출했다. 독립 GUI도 동일 backend를 사용한다.
실제 원본 프리뷰는 2026-11-02 확보분이다. 파일 해시는 아래 provenance와 P0 기록에 연결된다.

- [테스트 로그](evidence-20261106/pytest.log)
- [실제 snapshot 후처리 요약](evidence-20261106/real-snapshots-summary.json)
- [실제 snapshot 프레임별 결과](evidence-20261106/real-snapshots-detections.jsonl)
- [파생 MP4 입력 provenance](evidence-20261106/input-video-provenance.json)
- [파생 MP4 실행 요약](evidence-20261106/preview-derived-result-summary.json)
- [Synthetic 실행 요약](evidence-20261106/synthetic-moving-result-summary.json)
- [독립 GUI 실행 요약](evidence-20261106/desktop-summary.json)

출력 MP4는 다시 읽어 JSONL과 프레임 수·해상도가 일치하는지 확인했다.
테스트는 RGB/BGR 순서, nominal 시간, 입력 보존/기존 결과 덮어쓰기 거절,
취소 시 partial 유지, 모델 누락 실패, 깨진 영상 및 RAW2 거절,
stale snapshot gate, overlay의 입력 불변성, 처리 중 입력 변경 실패를 포함한다.
Git에는 영상·이미지·가중치를 포함하지 않고 수치/해시/로그만 저장했다.

## RISK

- 현재 Git 저장소에서 실제 MP4/AVI 원본을 찾지 못했다. 이번 MP4 검증 입력은
  **이미 확보한 프리뷰 5개에서 만든 파생 영상**이며 연속 원본 녹화가 아니다.
- 사람 유무/라벨 미확인, person 0건. 정확도·재현율·고유 인원수·추적 성능은 미검증이다.
- RGB/COCO 모델과 DVS 극성 표현의 도메인 차이가 남아 있다.
- 오디오 보존 및 VFR 타임라인 보존은 지원하지 않는다. nominal FPS의 CFR 결과를 생성한다.
- RAW2 직접 해석, 장치 수신, Jetson GPU 배포, 자동 녹화 제어는 범위 밖이다.
- 검증은 짧은 파일 기준이다. 장시간 파일의 처리량과 오류율은 별도 측정이 필요하다.

## NEXT

사용할 실제 DVS 영상의 GitHub 파일/Release 또는 로컬 경로를 확보해 같은 앱에서 처리한다.
라벨 있는 영상에서 person bbox와 confidence를 검토하고 precision/recall을 측정한다.
P0 PR 및 이번 후처리 PR은 리뷰 후 병합하며 main에 자동 병합하지 않는다.
