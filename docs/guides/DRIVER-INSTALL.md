# DVS 커널 드라이버 및 CAM1 overlay 설치

[가이드 목록](README.md) · 이전: [소스 다운로드](SOURCE-AND-SSH.md) · 다음: [디코더·뷰어](DECODER-AND-VIEWER.md)

이 문서의 명령은 **Jetson의 Bash 터미널**에서 실행한다.
대상은 Orin Nano Super / R39.2.1 / 커널 `6.8.12-1021-tegra` / CAM1이다.
센서/FPC 배선 변경은 장치 전원을 완전히 끈 상태에서 한다.

## 1. 보드·BSP·헤더 확인

```bash
cd ~/DVS.JS.ORIN.01
uname -m
uname -r
cat /etc/nv_tegra_release
tr -d '\0' </proc/device-tree/model
printf '\n'
dpkg-query -W nvidia-l4t-core
ls -ld /lib/modules/$(uname -r)/build
ls -l /lib/modules/$(uname -r)/build/Module.symvers
ls -l /usr/src/nvidia/nvidia-public/Module.symvers
sha256sum /lib/modules/$(uname -r)/build/Module.symvers \
  /usr/src/nvidia/nvidia-public/Module.symvers
cat bsp/r39.2.1/manifest.env
```

필수 기준:

| 항목 | 값 |
|---|---|
| CPU | aarch64 |
| 커널 | 6.8.12-1021-tegra |
| nvidia-l4t-core | 39.2.1-20260806224157 |
| kernel Module.symvers SHA-256 | e88a0a87e66d092cebdf15e1fd0924c6e83f68beaa21551ab7b8c05bc82b4fd1 |
| NVIDIA Module.symvers SHA-256 | 240382019540e53d873725b656aafefad26a33bef27755dfb6f1f663ede6e57d |

불일치하거나 헤더가 없으면 중단한다. 다른 BSP의 파일을 섞거나 manifest 검사만 지우지 않는다.
이 가이드는 이미 맞는 헤더/BSP가 설치된 장치를 전제로 한다.

## 2. 일반 빌드 도구 준비

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  git gcc g++ make cmake ninja-build python3 curl bzip2 \
  device-tree-compiler kmod binutils libzstd-dev
gcc --version
g++ --version
cmake --version
ninja --version
```

검증된 도구는 GCC/G++ 13.3과 CMake 3.28.3이다. SDK는 CMake 3.25 이상 및
`<format>`을 포함한 C++20 구현이 필요하다. 전체 OS upgrade나 커널 변경은 이 단계에 포함하지 않는다.

## 3. 빌드와 로그 보존

기존 설치를 다시 빌드하는 경우 먼저 `./dvs stop`으로 수신기와 뷰어를 정상 종료하고,
`./dvs status`에서 capture/viewer가 모두 null인지 확인한다. `all`은 사용자 공간 파일도
갱신하므로 실행 중인 상태에서 재빌드하지 않는다. 최초 설치에는 이 단계가 필요 없다.

```bash
mkdir -p build/logs
set -o pipefail
JOBS=4 ./build.sh all 2>&1 | tee "build/logs/all-$(date +%Y%m%d-%H%M%S).log"
build_rc=${PIPESTATUS[0]}
printf 'build exit=%s\n' "$build_rc"
```

`build exit=0`을 확인한 뒤 다음 단계로 간다. 실패 시 설치하지 말고 로그의 첫 오류를 확인한다.
이 명령은 NVIDIA 소스를 다운로드하고 SHA-256을 검증한 뒤 드라이버, DTBO, SDK와 앱을 빌드한다.
`build.sh` 자체는 설치·재부팅·모듈 로드를 하지 않는다.

드라이버와 DTBO만 필요한 경우에는 같은 환경 검사 아래 다음 순서도 제공된다.

```bash
./build.sh fetch && ./build.sh drivers && ./build.sh dt
```

## 4. 산출물과 오프라인 검증

```bash
ls -lh build/nrv_dvs.ko build/cam1.dtbo
modinfo build/nrv_dvs.ko
python3 tests/checks.py modules "$PWD"
./tests/check-build.sh
scripts/boot-setup.sh check
scripts/boot-setup.sh install --dry-run
```

확인할 항목:

- 모듈 vermagic의 release가 실행 중 커널과 일치한다.
- module 검사에서 stock NVIDIA 카메라 모듈과 full vermagic 및 import 심볼 CRC가 일치한다.
- overlay가 현재 보드의 기본 DTB에 병합되고 `nrv_dvs@10`이 존재한다.
- `boot-setup.sh check`와 dry-run이 성공하며 실제 변경 예정 diff를 검토한다.

현재 종합 검사는 과거 `dvs_live` 경로와 이전 launcher 구조를 기대하는 항목 때문에
일부 실패할 수 있다. **전체 실패를 무시하지 말고** [알려진 검사 불일치](TROUBLESHOOTING.md)를
대조한다. 커널/CRC/DT 관련 실패는 설치 중단 사유다.

## 5. 설치

이 단계부터 `/lib/modules`, `/etc/depmod.d`, `/boot` 및 extlinux가 변경된다.
dry-run과 백업 동작을 확인한 뒤 실행한다.

```bash
set -o pipefail
sudo scripts/boot-setup.sh install 2>&1 | tee "build/logs/install-$(date +%Y%m%d-%H%M%S).log"
install_rc=${PIPESTATUS[0]}
printf 'install exit=%s\n' "$install_rc"
```

`install exit=0`과 `installed. DEFAULT=nrv-dvs`를 확인한다.

| 대상 | 설치 동작 |
|---|---|
| /lib/modules/6.8.12-1021-tegra/updates/nrv/nrv_dvs.ko | DVS 모듈 배치 |
| /etc/depmod.d/nrv-dvs.conf | DVS 모듈 검색 우선순위 등록 후 depmod |
| /boot/nrv-dvs/cam1.dtbo | overlay 배치 |
| /boot/nrv-dvs/install.state | 버전·해시·이전 기본 부팅 항목 기록 |
| /boot/nrv-dvs/backup/extlinux.conf.* | 원본 부팅 설정 백업 |
| /boot/extlinux/extlinux.conf | nrv-dvs 항목 추가 및 DEFAULT 설정, primary 유지 |

NVIDIA stock 모듈, Image, initrd, 기본 DTB는 교체하지 않는다.
다만 과거 설치의 `updates/nrv/tegra-camera.ko`가 있다면 스크립트가 제거한다.
그 파일이 있는 장치는 용도와 백업을 먼저 확인한다.

설치 직후 확인:

```bash
modinfo -n nrv_dvs
modinfo -n tegra_camera
cmp build/nrv_dvs.ko /lib/modules/$(uname -r)/updates/nrv/nrv_dvs.ko
cmp build/cam1.dtbo /boot/nrv-dvs/cam1.dtbo
cat /boot/nrv-dvs/install.state
grep -E '^(DEFAULT|LABEL)|OVERLAYS|FDT ' /boot/extlinux/extlinux.conf
```

`cmp` 성공은 출력 없이 exit 0이다. `tegra_camera`는 `updates/nrv` 아래의 custom 모듈을
가리키면 안 된다. 실행 중 NVIDIA 카메라 모듈을 강제 언로드/교체하지 않는다.

## 6. 재부팅 및 활성화 확인

설치는 자동 재부팅하지 않는다. 작업을 저장하고 재부팅 가능한 시점에:

```bash
sudo reboot
```

SSH 연결이 끊긴 뒤 다시 접속한다.

```bash
cd ~/DVS.JS.ORIN.01
scripts/boot-setup.sh status
ls -l /dev/video*
journalctl -k -b --no-pager | grep -Ei 'nrv_dvs|DVS ready|tegra-capture-vi'
```

기대 결과는 `DT overlay: applied`, `nrv_dvs: loaded = build/`, stock `tegra_camera`,
`/dev/video0`, `status: ready`다. 실제 센서 데이터 수신은 다음 [디코더 가이드](DECODER-AND-VIEWER.md)에서 확인한다.

## 7. 제거·복구가 필요한 경우

아래는 정상 설치 절차에 이어 실행하는 명령이 아니라 **제거할 때만** 쓰는 경로다.
이번 검증에서는 uninstall을 실제 실행하지 않았으며 스크립트에 제공된 동작이다.

```bash
./dvs stop
scripts/boot-setup.sh uninstall --dry-run
```

diff를 검토하고 제거를 결정했다면:

```bash
sudo scripts/boot-setup.sh uninstall
sudo reboot
```

설치한 DVS 파일과 항목을 제거하고 저장된 이전 DEFAULT를 복원하며 백업은 남긴다.
부팅이 안 되면 보존된 `primary` 항목으로 부팅할 수 있는 콘솔 경로를 사용한다.
실행 중 커널에서 강제 모듈 제거로 복구하려 하지 않는다.
