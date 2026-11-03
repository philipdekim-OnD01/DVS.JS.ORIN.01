# Orin 독립 디코더 앱

Jetson 데스크톱 터미널에서 저장소 루트의 `bash ./dvs-desktop`을 실행한다.
Tk/Pillow/NumPy 기반의 독립 창이며 브라우저, WebView, HTTP 서버/클라이언트를 사용하지 않는다.
필요 패키지: `python3-tk python3-pil.imagetk python3-numpy`.

기존 SDK 디코더가 생성하는 `/dev/shm/nrv-dvs-live/snapshot.bin`을 읽는다.
디코더가 실행 중이지 않으면 `./dvs start --no-web`으로 HTTP 없이 수신을 시작할 수 있다.
이미 수신/녹화 중인 프로세스는 앱 실행 시 재시작하지 않는다. 기존 웹 서비스가 실행 중이어도
이 앱은 웹 서비스에 접속하지 않고 로컬 snapshot만 읽는다.

표시 패널: 앱 목표 FPS(15/30/60), ON/OFF, 좌우/상하 반전, 이웃 이벤트 필터,
배경/ON/OFF 색, 표시 ROI, 일시정지, PNG 저장, F11 전체화면.
앱 목표 FPS는 센서 FPS나 수신기 합성 간격을 바꾸지 않는다.

레지스터 패널: RP5 `hdmi_controls.py`의 메뉴 구성을 참고해 MIPI/RAW2, 감도,
이벤트, 센서 ROI 주소 모음을 제공한다. 기존 Orin `sensor_settings.py`를 재사용한다.
최대 16주소, 1초 간격의 실제 I2C 읽기이며 쓰기는 제공하지 않는다.
정상 프레임이 2초 이내에 수신됐을 때만 읽기를 허용한다.
설정 파일 탭은 실행 프로세스의 setfile 경로에서 **앱이 시작할 때 읽은 파일**이다.
수신 시작 이후 파일이 편집됐는지는 확인할 수 없으므로 적용된 센서 값이라고 표시하지 않는다.

수신 상태: MIPI→VI 바이트/버퍼 증가와 SDK decoded frame 증가를 별도로 보여준다.
프로세스가 `state=live`나 preview heartbeat를 계속 출력해도 마지막 정상 프레임이 오래됐으면
수신 정지로 표시하고 이전 영상을 비운다. 화면 일시정지 중에도 수신 상태는 갱신한다.

테스트: `python3 -m unittest discover -s tests -p test_native_decoder.py -v`.
센서 프레임 시각의 ambiguity/order-error는 수신 상태 JSON에서 별도로 확인한다.
앱 종료는 센서·녹화를 정지하지 않는다.

## 2026-11-03 현장 검증

- Jetson의 실제 X11 데스크톱에 독립 앱 실행, PID 231367, 실행 로그 오류 없음.
- 앱 목록에 `DVS Decoder` 등록. 실제 화면에서 설정 패널과 수신 정지 표시를 확인했다.
- Windows 및 Jetson 각각 unittest 4개 통과: stale 판정, heartbeat와 수신량 분리,
  극성/좌표/무효 화소/반전/ROI 및 노이즈 필터.
- 재측정 3.013633초 동안 수신 bytes/buffers, decoded frames/events 증가 모두 0.
  `has_frame=false`, 마지막 정상 프레임 87094.489초 전. capture PID 67942.
- 커널 로그에 `tegra-capture-vi: uncorr_err: request timed out after 2500 ms` 반복 기록.
  이는 호스트가 MIPI→VI 데이터 버퍼를 받지 못한다는 근거이며 센서/케이블/VI 중
  어느 계층이 원인인지는 이번 읽기 전용 점검으로 확정하지 않았다.
- 수신기 상태의 `recording` 표시는 남아 있었으나 실제 데이터는 증가하지 않았다.
  기존 녹화 프로세스와 센서 설정은 변경하지 않았다.
- 정상 수신이 아니므로 새 패널의 실제 I2C 읽기 버튼은 비활성 상태다.
  이번 실행에서 하드웨어 readback을 새로 성공했다고 주장하지 않는다.
- 다음 수신 복구 단계는 기존 녹화 파일 상태 확인·종료 후 수신 재시작과
  MIPI 연결/센서 전원 및 커널 로그 재점검이다.
