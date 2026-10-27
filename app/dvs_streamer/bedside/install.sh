#!/bin/bash
# install.sh - 부팅하면 수신(nrv-dvs-streamer)과 bedside 화면(nrv-dvs-bedside)이 뜨고, 로그인 없이
# (nrv 자동 로그인) 터치스크린에 bedside 화면을 전체 화면으로 띄운다.
#   sudo ./install.sh            설치 (다음 부팅부터; 바로 띄우려면 sudo systemctl start ...)
#   sudo ./install.sh --uninstall
# 미리: app/dvs_streamer 를 Release 로 빌드 (build/GCC-arm64/Release/bedside/ 가 있어야 함)
# 자동 로그인과 키오스크 창 부분은 Karim <karim@nrv.kr> 의 bed-setup install.sh 5단계와 같다.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
RELEASE=/home/nrv/nrv-dvs-jetson/app/dvs_streamer/build/GCC-arm64/Release
AUTOSTART=/home/nrv/.config/autostart/nrv-dvs-kiosk.desktop
LIMITS=/etc/security/limits.d/90-nrv-dvs.conf
[[ $(id -u) -eq 0 ]] || { echo "run with sudo"; exit 1; }

if [[ ${1:-} == --uninstall ]]; then
  systemctl disable --now nrv-dvs-bedside.service nrv-dvs-streamer.service 2>/dev/null || true
  rm -f /etc/systemd/system/nrv-dvs-bedside.service /etc/systemd/system/nrv-dvs-streamer.service \
        "$AUTOSTART" "$LIMITS" /usr/local/bin/nrv-dvs-kiosk.py /usr/local/bin/nrv-dvs-kiosk-start.sh
  systemctl daemon-reload
  echo "removed (auto-login stays in /etc/gdm3/custom.conf, original saved as /etc/gdm3/custom.conf.orig)"
  exit 0
fi

[[ -f $RELEASE/bedside/adapter.py ]] || { echo "not built: $RELEASE/bedside/adapter.py"; exit 1; }

echo "== 1. services: nrv-dvs-streamer (receiver), nrv-dvs-bedside (screen), /nrv/recordings"
install -d -o nrv -g nrv /nrv/recordings
install -m 644 "$here/../nrv-dvs-streamer.service" "$here/nrv-dvs-bedside.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable nrv-dvs-streamer.service nrv-dvs-bedside.service
# 수동 run.py start 도 dvs_streamer 를 실시간(SCHED_RR 10)으로 띄울 수 있게 (다시 로그인한 세션부터)
echo 'nrv - rtprio 10' > "$LIMITS"

echo "== 2. touchscreen: auto-login as nrv, bedside page full screen at login"
[[ -f /etc/gdm3/custom.conf.orig ]] || cp /etc/gdm3/custom.conf /etc/gdm3/custom.conf.orig
if ! grep -q '^AutomaticLogin=nrv' /etc/gdm3/custom.conf; then
  sed -i '/^AutomaticLogin/d' /etc/gdm3/custom.conf
  sed -i '/^\[daemon\]/a AutomaticLoginEnable=true\nAutomaticLogin=nrv' /etc/gdm3/custom.conf
fi
install -m 755 "$here/nrv-dvs-kiosk.py" /usr/local/bin/nrv-dvs-kiosk.py
install -m 755 "$here/nrv-dvs-kiosk-start.sh" /usr/local/bin/nrv-dvs-kiosk-start.sh
install -d -o nrv -g nrv /home/nrv/.config /home/nrv/.config/autostart
install -m 644 -o nrv -g nrv "$here/nrv-dvs-kiosk.desktop" "$AUTOSTART"
python3 -c "import gi; gi.require_version('WebKit2','4.1')" 2>/dev/null \
  || echo "   warning: WebKit2 4.1 missing: sudo apt-get install --no-install-recommends python3-gi gir1.2-webkit2-4.1"
echo "done. Reboot, or now: sudo systemctl start nrv-dvs-streamer nrv-dvs-bedside"
