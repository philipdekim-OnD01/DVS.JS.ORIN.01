# SDK·RAW2/GROUP AER 디코더·실시간 뷰어 설치

[가이드 목록](README.md) · 이전: [드라이버 설치](DRIVER-INSTALL.md) · [문제 해결](TROUBLESHOOTING.md)

이 문서의 명령은 Jetson 터미널에서 실행한다. 실제 센서 수신에는
[드라이버와 overlay 활성화](DRIVER-INSTALL.md)가 먼저 필요하다.
디코더는 `libnrv_dvs` SDK에 포함되며 `dvs_streamer`가 이를 사용한다.
별도의 이름 없는 바이너리나 다른 저장소의 SDK를 받아 교체할 필요가 없다.

## 1. 도구와 Python 의존성

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  g++ make cmake ninja-build libzstd-dev python3 \
  python3-numpy python3-yaml python3-pil psmisc util-linux curl
g++ --version
cmake --version
python3 -c 'import numpy, yaml, PIL; print("VIEWER_IMPORTS_OK")'
printf '#include <format>\nint main() {}\n' | g++ -std=c++20 -x c++ -fsyntax-only -
```

검증 환경은 Linux aarch64, GCC/G++ 13.3, CMake 3.28.3이다.
CMake 최소 요구는 3.25다. `<format>` 검사에 실패하면 CMake만 바꿔서는 해결되지 않는다.
임의 헤더를 만들어 우회하지 말고 지원되는 C++ 도구체인을 준비한다.

## 2. SDK와 Streamer 빌드

`./build.sh all`이 이미 성공했다면 이 빌드를 반복할 필요는 없다.
기존 설치를 재빌드한다면 먼저 저장소 루트에서 `./dvs stop`을 실행하고,
`./dvs status`의 capture/viewer가 모두 null인지 확인한다. 종료가 지연되면 빌드하지 말고
원인을 확인한다. 실행 중인 바이너리·공유 라이브러리를 덮어쓰지 않는다.
최초 설치에는 이 중지 단계가 필요 없다. 아래 SDK 단독 재빌드에도 같은 순서를 적용한다.

사용자 공간만 빌드하려면:

```bash
cd ~/DVS.JS.ORIN.01
mkdir -p build/logs
set -o pipefail
./build.sh app 2>&1 | tee "build/logs/app-$(date +%Y%m%d-%H%M%S).log"
app_rc=${PIPESTATUS[0]}
printf 'app build exit=%s\n' "$app_rc"
```

`app build exit=0`을 확인한다. `app` 명령은 커널/BSP preflight를 하지 않으므로
이 빌드 성공을 드라이버 호환성 증거로 사용하면 안 된다.

| 항목 | 저장소 기준 경로 |
|---|---|
| 수신 실행 파일 | app/dvs_streamer/build/GCC-arm64/Release/dvs_streamer |
| SDK 배포 레이아웃 | sdk/build/GCC-arm64/Release/ |
| SDK 공유 라이브러리 | sdk/build/GCC-arm64/Release/lib/libnrv_dvs.so.0.1.0 |
| Python runner / viewer | app/dvs_streamer/build/GCC-arm64/Release/run.py, viewer.py |
| 웹 파일·setfile | 같은 Release 폴더의 web/, settings/ |

```bash
file app/dvs_streamer/build/GCC-arm64/Release/dvs_streamer
ldd app/dvs_streamer/build/GCC-arm64/Release/dvs_streamer
ls -l sdk/build/GCC-arm64/Release/lib/
```

`ldd`에 `not found`가 없어야 한다. 이번 설치는 저장소 내 빌드 트리를 사용한다.
Streamer 하나만 다른 폴더로 옮기면 라이브러리·Python·웹·settings 경로가 끊길 수 있다.
전역 `sudo make install`은 이 실행 절차에 필요하지 않다.

### SDK만 필요한 개발자용 경로

다음은 소스 프리셋으로 확인한 별도 경로이며 실제 배포에서 실행한 경로는 위 `build.sh app`이다.

```bash
cd ~/DVS.JS.ORIN.01/sdk
cmake --preset GCC-arm64 -DNRV_DVS_BUILD_TESTS=OFF
cmake --build --preset Release-GCC-arm64 --target nrv_dvs_dist --parallel 2
```

SDK 단독 configure는 테스트가 기본 ON이므로 OFF를 명시한다.
`raw_max_sensitivity.bin`은 내부 테스트 데이터이며 이 OFF 빌드에는 필요하지 않다.
내부 벤치마크를 별도로 실행한다면 [원본 복구 기록](../IMPORT_STATUS.md)에 따라 정식 데이터를 확보한다.

## 3. 장치와 권한 확인

```bash
cd ~/DVS.JS.ORIN.01
scripts/boot-setup.sh status
id
ls -l /dev/video0
fuser /dev/video0
./dvs status
```

`/dev/video0`가 존재하고 현재 사용자에게 접근 권한이 있어야 한다.
검증 장치의 사용자는 `video` 그룹에 속했다. 해당 그룹 권한만 부족한 경우:

```bash
sudo usermod -aG video "$USER"
```

그 뒤 로그아웃·다시 로그인하여 `id`로 반영을 확인한다.
이미 다른 수신기가 장치를 사용 중이면 그 프로그램을 정상 종료한다.
`./dvs status`에 capture/viewer PID가 있으면 새 수신기를 중복 시작하지 않는다.

## 4. RAW2 라이브 수신과 화면 열기

**sudo 없이** 실행한다.

```bash
cd ~/DVS.JS.ORIN.01
./dvs start
```

기대 출력은 `정상 수신·화면 서버 준비 완료`와 주소 목록이다.
기본 setfile은 실행본 옆 `settings/Delta_10_2000FPS_RAW2.txt`다.
runner는 `/dev/video0`를 사용하고 녹화를 시작하지 않는다.

- Jetson 브라우저: <http://127.0.0.1:8080/>
- 같은 LAN의 PC: `http://<Jetson-IP>:8080/`
- 검증 장치: <http://192.168.0.121:8080/>

Jetson의 **로컬 데스크톱 터미널**에서는 다음으로 열 수 있다.

```bash
xdg-open http://127.0.0.1:8080/
```

일반 SSH 셸에는 데스크톱 DISPLAY/인증 환경이 없을 수 있다.
그 경우 PC 브라우저로 접속하거나 Jetson 로컬 브라우저에서 주소를 입력한다.
웹 서버는 loopback과 장치 LAN 주소를 사용한다. 외부 포트 포워딩은 이 절차에 포함하지 않는다.

## 5. 실제 수신·디코딩 검증

```bash
./dvs status
curl -fsS http://127.0.0.1:8080/stats.json | python3 -m json.tool
```

몇 초 간격으로 다시 확인한다.

| 필드 | 확인할 내용 |
|---|---|
| capture/viewer PID | 둘 다 실행 중이며 session_pid가 capture와 일치 |
| format | 기본 설정에서 raw2 |
| buffers / frames / events | 시간에 따라 증가, 정적인 장면에서 이벤트 증가량은 작을 수 있음 |
| has_frame / last_good_age | true 및 작은 최근 프레임 나이 |
| stream.dropped / starved / error_buffers | 증가 여부 관찰 |
| invalid / raw2_short_frames | 형식·길이 오류 관찰 |
| raw2_timestamp_ambiguous / raw2_timestamp_order_errors | 시각 후보 해석 한계 별도 관찰 |
| dump.state | 녹화하지 않을 때 idle |

`state=live`, HTTP 200, snapshot_id 증가만으로 정상 수신이라고 판단하지 않는다.
수신 타임아웃 중에도 상태가 생성될 수 있고, 종료 뒤 이전 snapshot이 남을 수 있다.

화면은 960×720 이벤트 영상이다. 정지한 물체의 RGB 모습 대신 밝기 변화가 표시된다.
흰색 ON·검정 OFF·회색 배경이며, 센서 앞에서 물체를 움직여 변화를 확인한다.
프리뷰 FPS와 센서 디코딩 FPS는 다르다. 약 1,600fps 수신을 약 60fps 화면으로 합쳐 표시할 수 있다.
UI의 센서 시각 기반 FPS는 미교정 후보값이다.

## 6. 정상 종료와 다음 실행

```bash
./dvs stop
./dvs status
```

다시 사용할 때 `./dvs start`를 실행한다. 브라우저 종료나 SSH 종료만으로 수신을 중지하지 않는다.
현재 구성은 재부팅 후 드라이버를 적용하지만 수신기·브라우저 자동 시작 서비스는 등록하지 않는다.

웹 없이 snapshot 처리만 필요하면 `./dvs start --no-web`을 사용한다.
이때도 viewer 프로세스는 snapshot 처리를 위해 실행된다.

## 7. GROUP AER와 다른 setfile

현재 SDK에는 RAW2/GROUP AER 디코더가 모두 포함된다. 실제 센서 검증은 RAW2로 수행했다.
GROUP AER 또는 다른 설정은 하드웨어와 출력 형식에 맞는 검증된 setfile을 확보한 뒤 별도로 시험한다.
runner의 `--settings`로 지정할 수 있지만 임의 레지스터 값을 추천하거나 형식 호환성을 보장하지 않는다.
실행 중 설정 변경을 위해 두 수신기를 동시에 띄우지 않는다.

## 로그 위치

- 실행 로그: `app/dvs_streamer/build/GCC-arm64/Release/capture.log`, `viewer.log`
- PID 파일: 같은 Release 폴더의 `capture.pid`, `viewer.pid`
- 최신 snapshot: `/dev/shm/nrv-dvs-live/snapshot.bin`
- 빌드 로그: `build/logs/app-configure.log`, `build/logs/app.log`
- 커널 로그: `journalctl -k -b`

실시간 우선순위 권한 경고는 일반 우선순위 실행을 뜻한다.
검증 구간에는 버퍼 고갈이 없었지만 장시간·고부하에서는 starved 등을 다시 측정해야 한다.
