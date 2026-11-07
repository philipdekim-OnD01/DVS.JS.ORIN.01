# DVS YOLO 영상 후처리

저장된 DVS 영상을 읽어 pretrained YOLO11 nano의 person 검출을 실행하고
주석 영상, 프레임별 검출 기록, 실행 요약을 만드는 독립 데스크톱/CLI 솔루션이다.
MIPI 장치·수신기·센서 설정에 접근하지 않으며 브라우저나 웹 서버를 사용하지 않는다.

## 입력

| 입력 | 처리 | 시간 의미 |
|---|---|---|
| MP4 / AVI / MOV / MKV / WebM 한 파일 | 디코딩된 RGB 영상을 그대로 YOLO에 전달 | 파일의 nominal FPS 기준 프레임 시각 |
| 저장 snapshot `.bin` 여러 개 | 기존 2-bit preview → PolarityRGB → YOLO | 사용자가 고른 재생 FPS; 센서 시간 복원 아님 |
| RAW2 `.zst`, CSI/V4L2 bytes | 지원하지 않음 | 기존 SDK 어댑터가 필요한 별도 작업 |

GitHub에서 가져오는 것은 이 앱의 소스다. 현재 DVS.JS.ORIN.01 저장소에는 실제 MP4/AVI
영상이 포함되어 있지 않다. 사용할 DVS 영상은 권한 있는 GitHub 파일/Release나 로컬 저장소에서
PC로 받은 후 앱에서 선택한다. 사설 원본 영상과 가중치는 Git에 커밋하지 않는다.
영상에 사람 라벨이 없으므로 검출이 0이어도 성공한 정확도 평가나 무인 상태로 해석하지 않는다.

## 설치

검증 환경은 Windows 11 x86-64, Python 3.13.13, CPU다. Jetson GPU 설치·배포는 수행하지 않았다.
다음 명령은 저장소의 `ai_services/person_yolo/`에서 실행한다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.9.0 torchvision==0.24.0 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -e '.[yolo,video,test]'
.\.venv\Scripts\python.exe -m person_yolo.desktop
```

Linux에서는 별도 venv의 `python`을 사용하고 배포판의 Tk 패키지가 필요하다.
기존 센서/디코더 가상환경을 덮어쓰지 않는다. GUI 없는 환경은 아래 CLI를 사용한다.
모델 없이 입력만 검증하려면 `.[video,test]` 설치와 `--dry-run`을 사용한다.

## 모델

[공식 yolo11n.pt](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt)를
저장소 밖 캐시에 저장하고 앱의 가중치 선택창에서 선택한다.

- SHA-256: `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`
- 크기: 5,613,764 bytes. Ultralytics 8.3.228 / PyTorch 2.9.0 / torchvision 0.24.0.
- 라이선스와 공급자 AGPL/Enterprise 조건: [모델 출처 안내](README.md#모델-출처와-고정값).
- 자동 모델 다운로드가 없으며 파일 해시/라이브러리 버전이 다르면 실패한다.
- 기본 CPU 2 threads, imgsz 640, confidence 0.25, IoU 0.7, person class 0.
  앱의 confidence 변경값은 결과 summary에 기록된다. 이번 검증은 기본값을 유지했다.

## 독립 앱 사용

1. **영상 파일 선택** 또는 **프리뷰 파일 선택**을 누른다. 프리뷰는 파일명 순서로 처리한다.
2. 가중치 및 결과를 저장할 부모 폴더를 선택한다.
3. **후처리 시작**을 누르면 CPU 추론과 현재 프레임 미리보기가 시작된다.
4. 완료 후 **결과 폴더 열기**로 결과를 확인한다.

실행마다 `job-날짜-시각` 하위 폴더를 새로 만들므로 기존 결과를 덮어쓰지 않는다.
**취소** 또는 창 닫기는 현재 단계 종료 후 처리를 정리한다. 대용량 입력 해시 계산이나
모델 로딩 중에는 취소 반영까지 시간이 걸릴 수 있다. 메모리에는 현재 프레임과 최대 2개의
축소 미리보기만 유지하며 전체 영상을 RAM에 올리지 않는다.

**입력만 확인**은 검출을 실행하지 않는다. 결과에 `dry_run`, `inference_ran=false`를 기록한다.
모델 없이 가짜 검출을 만들지 않는다. 누적 박스 수는 고유 인원수와 다르며 tracking은 없다.

## CLI

```sh
python -m person_yolo.postprocess --input /data/dvs-video.mp4 \
  --weights /cache/yolo11n.pt --output /results/new-job
python -m person_yolo.postprocess --input /data/snapshot-00.bin /data/snapshot-01.bin \
  --snapshot-fps 4 --weights /cache/yolo11n.pt --output /results/new-snapshots
python -m person_yolo.postprocess --input /data/dvs-video.avi --dry-run --output /results/input-check
```

`--config configs/p0.json`으로 고정 설정을 지정할 수 있다. output 경로는 존재하지 않는
새 폴더여야 한다. source 경로에 URL·카메라 번호를 전달하지 않는다.

## 결과 파일과 제한

- `annotated.mp4`: 원본 크기의 영상에 실제 person bbox/score와 프레임 상태를 표시.
- `detections.jsonl`: 매 입력 프레임의 source SHA, frame index, nominal 시각, 표현,
  원본 좌표 bbox, confidence, 추론 실행 여부와 시간, 모델 metadata. 0검출도 기록.
- `summary.json`: completed/cancelled/failed, 처리·추론·skip 수, 누적 박스 수,
  입력/모델/출력 해시, 설정, 실행 환경, wall time.
- 취소/실패하면 `.partial.mp4`, `.partial.jsonl`과 상태 요약이 남는다. 완료 결과로 표시하지 않는다.
  출력 영상은 다시 디코딩하여 프레임 수와 크기가 일치해야 완료로 확정한다.
- 입력 파일 size/mtime 변화는 실패로 처리한다. 레지스터·수신 상태는 영상에서 추정하지 않는다.
- MP4에는 음성을 복사하지 않는다. VFR 원본은 nominal FPS의 CFR로 재생되므로 정확한
  원본 VFR 타임라인/오디오 동기화 용도가 아니다. 홀수 해상도는 조용히 자르지 않고 거절한다.
- 영상 색상은 그대로 보존해 RGB로 변환하며, 2-bit 프리뷰에만 PolarityRGB를 적용한다.
  [Ultralytics 입력 계약](https://docs.ultralytics.com/modes/predict/),
  [OpenCV VideoWriter](https://docs.opencv.org/4.x/dd/d9e/classcv_1_1VideoWriter.html).
- 프리뷰의 오래된 정상 프레임 상태/중복은 skip으로 남기고 배경 화면을 출력한다.
  영상 파일에서는 이런 센서 상태를 복원할 수 없으므로 해당 품질 정보는 미확인이다.

검증 기록: [2026-11-06 후처리 결과](benchmarks/POSTPROCESS_2026-11-06_KO.md),
[2026-11-07 실제 RP5 10초 영상](benchmarks/RP5_VIDEO_2026-11-07_KO.md).
공유 MP4는 GitHub에서 로컬로 다운로드한 뒤 **영상 파일 선택**으로 연다.
RAW2 Release의 `.dvsraw.gz`는 이 MP4 입력과 다른 형식이며 이 앱에서 직접 처리하지 않는다.
실제 RP5 영상 600프레임은 기본 설정으로 처리 완료했지만 person 검출은 0건이었다.
앱 실행 성공과 사람 검출 성능 검증을 구분해야 한다.
