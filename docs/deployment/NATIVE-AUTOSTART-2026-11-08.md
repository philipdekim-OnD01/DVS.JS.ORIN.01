# DVS 부팅 자동 시작 및 바탕화면 실행 — 2026-11-08

## FACT

- 사용자 요청에 따라 Jetson `192.168.0.121`, `philip` 계정에 설치했다.
- 부팅 시 `nrv-dvs-streamer.service`가 기존 RAW2 수신기를 시작한다.
- 기존 자동 로그인(`AutomaticLogin=philip`) 후 GNOME autostart가 독립 디코더 창을 연다.
- 바탕화면과 앱 목록에 **DVS 디코더** 실행 버튼을 등록했다. 바탕화면 파일은 실행 권한과 `metadata::trusted=true`를 확인했다.
- GUI는 사용자 서비스로 한 개만 실행한다. 반복 실행은 기존 창 활성화 요청으로 처리하며, 정상적으로 창을 닫으면 다시 열 때까지 닫힌 상태를 유지한다.
- 수신기/SDK/driver/sensor setfile 소스와 기존 독립 디코더 파일은 수정하지 않았다. 새 서비스·실행 wrapper·설치 스크립트만 추가했다.

## EVIDENCE

설치 명령:

```sh
python3 scripts/install-native-autostart.py --user philip --repo /home/philip/DVS.JS.ORIN.01 --dry-run
sudo python3 scripts/install-native-autostart.py --user philip --repo /home/philip/DVS.JS.ORIN.01
```

설치 결과 `0`. 기존 앱 목록 항목은 다음 디렉터리에 백업했다.
`/home/philip/.local/state/nrv-dvs/autostart-backup-20261108T205033/`

등록 및 실행 확인:

| 항목 | 확인 결과 |
|---|---|
| 시스템 수신 서비스 | enabled, active/running, PID 875062 |
| 수신 스케줄링 | SCHED_RR, priority 10 (기존 서비스 템플릿의 값) |
| 사용자 GUI 서비스 | active/running, 재실행 후 PID 875176 |
| 자동 로그인 | 기존 philip 자동 로그인 설정 유지 |
| GNOME 자동 시작 파일 | `~/.config/autostart/nrv-dvs-native.desktop` |
| 바탕화면 파일 | `/home/philip/Desktop/DVS-Decoder.desktop`, mode 755, trusted=true |
| 중복 실행 | autostart 항목 + 바탕화면 항목 반복 실행 시 GUI PID 875099 유지 |
| GUI 서비스 정지 후 바탕화면 재실행 | 새 GUI 정상 시작, 수신 PID 875062 유지 |

현재 수동 실행을 서비스로 전환하기 전 `dump.state=idle`을 확인하고 기존 수신을 정상 종료했다.
서비스가 같은 `Delta_10_2000FPS_RAW2.txt`와 `--no-web`으로 수신을 다시 시작했다.
수신 서비스는 SCHED_RR 권한을 기존 `run.py`에 제공한다. 현재 세션에서 녹화는 시작하지 않았다.

서비스 전환 후 30.009초 동안:

- 수신 버퍼 **47,900개**, **1,596.18 buffers/s**.
- 측정 구간 `dropped`, `starved`, `error_buffers` 증가량 각각 **0**.
- 네 샘플 모두 `has_frame=true`, 최신 정상 프레임 상태 확인.
- 녹화 `idle`.

이는 VI 버퍼 처리율이며 GUI 표시 FPS나 모든 센서 이벤트의 무손실 보장이 아니다.

검증: `systemd-analyze verify`, 사용자 unit verify, `desktop-file-validate`, `bash -n`,
Python syntax compile, Windows/Jetson 각각 기존 native decoder unittest **4개 통과**.
`gio launch`로 실제 Desktop Entry를 실행하여 창 생성과 중복 방지를 확인했다.
설치 중 기존 NVIDIA 서비스 2개의 오래된 syslog 출력 설정 경고가 있었으나 검증/설치는 성공했다.

- [실측 결과 JSON](evidence/2026-11-08/autostart/verification.json)
- [Jetson 설치 로그](evidence/2026-11-08/autostart/install.log)
- [Jetson 디코더 테스트](evidence/2026-11-08/autostart/native-tests.log)
- Jetson 전체 작업 증거: `/home/philip/dvs-autostart-20261108/`.
- [설치/상태 확인/해제 가이드](../guides/NATIVE-DECODER.md#부팅-자동-시작과-바탕화면-버튼)

## RISK

재부팅이나 전원 차단은 하지 않았다. 자동 시작 등록·현재 세션 실행은 검증했으며,
실제 다음 전원 인가에서의 자동 로그인/시작은 아직 현장 검증 전이다.

프로그램의 시작 실패/비정상 종료는 재시도하지만, 프로세스가 살아 있는 채로 센서 설정이
초기화되거나 MIPI 데이터가 멈추는 경우까지 자동 복구하는 watchdog은 이번 범위에 없다.
앱의 수신 상태와 증가하는 버퍼를 확인해야 한다. 정상 시작 설정과 정상 영상 수신을 구분한다.

## NEXT

다음 사용자가 계획한 전원 인가 때 수신 서비스와 디코더 창의 자동 시작을 확인한다.
창을 닫은 뒤에는 바탕화면 **DVS 디코더**로 다시 열 수 있다.
소스는 별도 feature branch에 기록하며 main에 자동 병합하지 않는다.
