# Jetson Orin DVS 설치·운영 가이드

이 가이드는 2026-11-01에 Orin Nano Super에서 확인한 설치와 RAW2 수신 절차를 기준으로 한다.
실제 작업 기록은 [배포 기록](../deployment/JETSON-2026-11-01.md), 당시 출력은
[증거 로그](../deployment/evidence/)에서 볼 수 있다.

## 처음 설치하는 순서

| 순서 | 가이드 | 완료 기준 |
|---|---|---|
| 1 | [SSH 접속 및 소스 다운로드](SOURCE-AND-SSH.md) | main 소스, Git 무결성, 실행 권한 확보 |
| 2 | [드라이버·CAM1 overlay 설치](DRIVER-INSTALL.md) | 재부팅 후 overlay/모듈 적용 및 /dev/video0 확인 |
| 3 | [SDK·디코더·뷰어 설치 및 실행](DECODER-AND-VIEWER.md) | 프레임·이벤트 증가와 웹 화면 확인 |
| 4 | [설정창·레지스터 읽기](VIEWER-SETTINGS.md) | 실제 센서 값 조회, 표시 옵션·ROI·수신 상태 확인 |
| 필요 시 | [문제 해결 및 복구](TROUBLESHOOTING.md) | 실패 계층을 구분하고 로그 확보 |

드라이버는 커널에서 센서를 V4L2 장치로 제공한다. SDK와 `dvs_streamer`는 사용자 공간에서
버퍼를 읽고 RAW2/GROUP AER를 해석한다. Python 뷰어는 디코딩 결과를 웹 화면에 표시한다.
커널 모듈 설치와 디코더 실행은 서로 다른 단계다.

## 검증된 환경

- Orin Nano Super, P3768 + P3767-0005, CAM1 연결
- Ubuntu 24.04.4 LTS / aarch64 / Jetson Linux R39.2.1
- 커널 `6.8.12-1021-tegra`, BSP 패키지 `39.2.1-20260806224157`
- GCC/G++ 13.3.0, CMake 3.28.3, Ninja
- 실제 수신 확인: RAW2, 960×720 이벤트 영상, 약 1,600 디코딩 프레임/s

정확한 BSP 및 SHA-256 기준은 [manifest](../../bsp/r39.2.1/manifest.env)에 있다.
R36.4.3 / 커널 5.15 환경에서는 이 드라이버를 그대로 설치하지 않는다.
OS 플래시·JetPack 업그레이드는 이 가이드의 범위가 아니다.

GROUP AER는 빌드 확인까지만 수행했다. RAW2 시각 후보 해석과 장시간·고부하 안정성은
추가 검증 대상이다. 자세한 제한은 [검증 기록](../deployment/JETSON-2026-11-01.md)에 있다.

## 설치가 끝난 장치에서 일상 사용

Jetson 터미널에서:

```bash
cd ~/DVS.JS.ORIN.01
./dvs status
```

capture/viewer가 실행 중이 아닐 때:

```bash
./dvs start
```

Jetson 브라우저는 <http://127.0.0.1:8080/>, 같은 LAN의 PC에서는
`http://<Jetson-IP>:8080/`를 연다. 검증 장치 주소는 <http://192.168.0.121:8080/>다.

종료는 `./dvs stop`이다. 브라우저를 닫아도 수신기는 계속 동작한다.
재부팅 후 드라이버는 부팅 항목으로 적용되지만 수신기 자동 시작은 이번 구성에 포함되지 않는다.
