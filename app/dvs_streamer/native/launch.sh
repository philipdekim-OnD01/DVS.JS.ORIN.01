#!/usr/bin/env bash
# Both GNOME autostart and the Desktop icon use the same single-window unit.
set -euo pipefail
if [[ -z ${DISPLAY:-} ]]; then
    echo 'Run this launcher from the Jetson graphical desktop.' >&2
    exit 1
fi
for name in DISPLAY XAUTHORITY XDG_CURRENT_DESKTOP; do
    if [[ -v $name ]]; then
        systemctl --user import-environment "$name"
    fi
done
systemctl --user start nrv-dvs-native.service
# Type=notify waits until the window and signal handler exist.
systemctl --user kill --kill-whom=main --signal=SIGUSR1 nrv-dvs-native.service
