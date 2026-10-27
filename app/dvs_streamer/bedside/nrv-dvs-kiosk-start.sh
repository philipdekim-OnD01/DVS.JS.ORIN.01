#!/bin/sh
# nrv-dvs-kiosk-start.sh - runs at desktop login (GNOME autostart): keep the screen on, no lock,
# no update pop-ups, then show the DVS page full screen. Restarts the window if it is closed.
# Author: Karim <karim@nrv.kr>, NRV, 2026-10-15
gsettings set org.gnome.desktop.screensaver lock-enabled false
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
gsettings set org.gnome.desktop.notifications show-banners false
gsettings set org.gnome.software download-updates false 2>/dev/null
gsettings set org.gnome.software allow-updates false 2>/dev/null
xset s off -dpms 2>/dev/null
while true; do
  /usr/local/bin/nrv-dvs-kiosk.py "${NRV_DVS_KIOSK_URL:-http://127.0.0.1:8080}"
  sleep 3
done
