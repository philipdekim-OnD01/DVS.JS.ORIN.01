# 문제 해결 및 증거 수집

[가이드 목록](README.md) · [드라이버](DRIVER-INSTALL.md) · [디코더·뷰어](DECODER-AND-VIEWER.md)

아래는 실제 실패 로그 또는 현재 소스 동작에 근거한 진단이다.
증상이 비슷하더라도 해당 장치의 출력으로 원인을 확인한다.

## SSH 연결 실패

| 출력/상태 | 구분 및 다음 확인 |
|---|---|
| Connection timed out | 전원, 부팅, LAN 연결, 현재 IP 확인. 인증 이전 단계 |
| Permission denied (publickey,password) | SSH 서버 응답은 있으나 사용자 인증 실패. 계정·등록 공개키·에이전트 확인 |
| 호스트 키 변경 경고 | 재설치 여부 및 실제 서버 키 검증. 사용자 공개키 등록과 별개 |
| 기존 SSH 창만 접속 가능 | 새 프로세스가 기존 비밀번호 입력 상태를 공유하지 않음 |
| Set-Service 액세스 거부 | Windows 서비스 설정에는 관리자 PowerShell 필요 |

[SSH 가이드](SOURCE-AND-SSH.md)를 참조한다. 비밀번호나 토큰을 URL/스크립트에 넣지 않는다.

## 빌드 실패

| 오류 | 원인/대응 |
|---|---|
| running kernel ... != pinned ... | R36/5.15 등 지원 대상 불일치. 검사 우회 또는 다른 커널용 ko 설치 금지 |
| Module.symvers ... mismatch | 실제 커널/BSP 헤더와 manifest 불일치. 정확한 패키지와 파일 출처 확인 |
| Unrecognized "version" field | CMake가 preset schema를 지원하지 못함. 검증 환경은 3.28.3, 최소 3.25 |
| format: No such file or directory | libstdc++의 C++20 `<format>` 미지원/미설치. 검증 도구체인은 GCC/G++ 13.3 |
| Ninja 또는 zstd CONFIG 오류 | ninja-build, libzstd-dev 설치와 CMake 탐색 경로 확인 |
| cached archive fails SHA pin | 다운로드 손상 또는 다른 버전. 기존 파일을 보존해 원인을 확인하고 정식 원본 확보 |
| raw_max_sensitivity.bin 없음 | 내부 테스트 fixture 문제인지 구분. build.sh app/all의 tests=OFF 빌드에는 불필요 |

빌드 중 실패한 첫 명령과 `build/logs/`를 남긴다. 파일 크기만으로 정상 빌드를 판정하지 않는다.

## 현재 종합 검사에서 알려진 불일치

소스 기준 `04908bb`의 검사와 현재 launcher 구조 사이에 다음 차이가 있다.

1. `ac3-modules`의 `app/run.py: no sysfs parameter table; passes a setfile to dvs_live` 실패:
   wrapper 안에 `--settings` 문자열을 직접 요구한다. 현재 wrapper는 새 runner로 인자를 전달하고,
   새 runner가 setfile을 Streamer 첫 인자로 넘긴다. 실제 수신 시 레지스터 적용을 확인했다.
2. `ac5-app`: 과거 `build/dvs_live` 및 `app/dvs_live`를 기대한다. 현재 앱은
   `app/dvs_streamer/build/GCC-arm64/Release/dvs_streamer`다.
3. `ac4-compare-dt`: 기준 DTB가 없으면 스크립트는 PASS로 표시하더라도 비교는 생략된다.

가짜 실행 파일이나 링크를 만들어 통과시키지 않는다. 이 사례와 무관한 **vermagic, CRC, DT 병합,
모듈 산출물 실패는 그대로 해결해야 한다**. 전체 검사 결과를 모두 통과했다고 기록하지 않는다.

## 설치했는데 /dev/video0가 없음

```bash
cd ~/DVS.JS.ORIN.01
uname -r
scripts/boot-setup.sh status
modinfo -n nrv_dvs
cat /boot/nrv-dvs/install.state
journalctl -k -b --no-pager | grep -Ei 'nrv|DVS|tegra-capture-vi|error|timeout'
```

설치 후 아직 재부팅하지 않았다면 overlay 미적용/모듈 미로드 상태일 수 있다.
재부팅 후에도 없다면 보드 DTB 선택, CAM1 배선, 전원, I2C probe 로그를 확인한다.
CAM0에 연결한 센서를 CAM1 overlay로 자동 지원한다고 가정하지 않는다.
리본 케이블을 조정하려면 먼저 정상 종료하고 전원을 끈다.

실제 검증 장치의 I2C 메시지는 `wrote=0x55 read=0x55; silicon ID not verified`였다.
이 메시지는 readback 성공이며 silicon ID 검증은 아니다.
자체 모듈 서명 키 부재로 kernel taint 메시지도 있었으나 당시 로드는 성공했다.
다른 보안 부팅 구성에서도 로드된다고 일반화하지 않는다.

## 웹 페이지는 열리는데 화면이 멈춤

```bash
./dvs status
curl -fsS http://127.0.0.1:8080/stats.json | python3 -m json.tool
tail -n 50 app/dvs_streamer/build/GCC-arm64/Release/capture.log
tail -n 50 app/dvs_streamer/build/GCC-arm64/Release/viewer.log
journalctl -k -b --no-pager | grep -Ei 'corr_err|uncorr_err|CHANSEL|CSIMUX|timeout'
```

capture/viewer PID, session_pid 일치, `buffers`와 `frames` 증가, `last_good_age`, `has_frame`을
함께 본다. snapshot과 `state=live`만으로 현재 수신을 판단하지 않는다.
`stream.error_buffers/dropped/starved`가 증가하면 수신·버퍼·부하 문제를 조사한다.
RAW2 timestamp ambiguity/order 카운터는 수신 오류와 별도로 기록한다.

센서가 정적인 장면을 보고 있으면 변화 이벤트가 적을 수 있다. 화면 일시정지 여부와
노이즈 제거 설정을 확인하고 물체를 움직인다. 포맷/장치 문제가 해결되지 않았는데
픽셀을 가짜 데이터로 채우지 않는다.

## Jetson에서는 되지만 PC에서 접속이 안 됨

Jetson에서 `curl http://127.0.0.1:8080/stats.json`이 되는지 먼저 확인한다.
PC 주소의 `127.0.0.1`은 PC 자신이다. Jetson의 LAN IP를 사용한다.
IP 변경, 같은 LAN 여부, 포트 8080 수신 여부와 네트워크 정책을 확인한다.
현재 launcher에 과거 `--bind` 옵션은 없다. 인터넷 공개나 방화벽 전체 해제는 필요하지 않다.

## 모니터가 검은 화면인데 부팅됐는지 확인

SSH로 접속해 다음을 읽는다.

```bash
uptime
systemctl status display-manager --no-pager
loginctl list-sessions
ps -ef | grep -E 'gnome-shell|Xorg|Xwayland'
```

이 장치에서는 화면이 보이지 않았던 시점에도 SSH와 데스크톱 자동 로그인은 완료돼 있었다.
모니터 입력 소스, 케이블, 데스크톱 세션 상태를 별도로 확인한다.
단일 DRM 상태값이나 GDM 경고 하나만으로 그래픽 드라이버 고장을 확정하지 않는다.

## 진단 로그를 남기는 방법

로그에는 인증 비밀, 센서 원시 영상, 불필요한 개인정보를 넣지 않는다.
Jetson 저장소 루트에서 날짜별 폴더에 보관한다.

```bash
logdir="build/logs/diagnostic-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$logdir"
uname -a > "$logdir/kernel.txt"
cat /etc/nv_tegra_release > "$logdir/l4t.txt"
scripts/boot-setup.sh status > "$logdir/driver-status.log" 2>&1
./dvs status > "$logdir/receiver-status.log" 2>&1
curl -fsS http://127.0.0.1:8080/stats.json > "$logdir/stats.json"
```

Git에 기록할 때는 `build/` 전체를 강제 추가하지 않는다. 필요한 텍스트 증거만 검토 후
`docs/deployment/evidence/`처럼 별도 경로로 옮겨 커밋한다.
드라이버 제거와 부팅 복구는 [드라이버 가이드의 복구 절차](DRIVER-INSTALL.md)를 따른다.
