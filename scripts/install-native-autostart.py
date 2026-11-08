#!/usr/bin/env python3
"""Install boot receiver + login decoder + desktop launcher; never reboot/start capture."""
import argparse
import datetime
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--user', required=True)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0:
        p.error('Choose a non-root desktop user')
    repo, home = args.repo.resolve(strict=True), Path(account.pw_dir)
    # These paths are embedded in systemd and Desktop Entry Exec values.
    # Reject unsupported path syntax instead of applying shell quoting to it.
    for value in (str(repo), str(home), args.user):
        if not re.fullmatch(r'[A-Za-z0-9_./-]+', value) or value.startswith('-'):
            p.error('This installer requires ASCII paths without spaces or shell metacharacters')
    build = repo / 'app/dvs_streamer/build/GCC-arm64/Release'
    settings = build / 'settings/Delta_10_2000FPS_RAW2.txt'
    for path in (build/'dvs_streamer', build/'run.py', build/'viewer.py', settings,
                 repo/'app/dvs_streamer/python/native_decoder.py',
                 repo/'app/dvs_streamer/native/session.py', repo/'app/dvs_streamer/native/launch.sh'):
        if not path.is_file():
            p.error(f'Missing required file: {path}')

    env_args = ['env', f'HOME={home}', f'XDG_RUNTIME_DIR=/run/user/{account.pw_uid}',
                f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus']
    def as_user(command, **kwargs):
        if os.geteuid() == account.pw_uid:
            return subprocess.run(env_args+command, **kwargs)
        return subprocess.run(['runuser', '-u', args.user, '--']+env_args+command, **kwargs)

    desktop_result = as_user(['xdg-user-dir', 'DESKTOP'], check=True, capture_output=True, text=True)
    desktop = Path(desktop_result.stdout.strip())
    if not desktop.is_absolute() or not desktop.is_relative_to(home):
        p.error('Desktop must be an absolute directory inside the selected user home')
    receiver = f'''[Unit]
Description=NRV DVS RAW2 receiver (native desktop)
After=systemd-udev-trigger.service
StartLimitIntervalSec=0

[Service]
Type=forking
User={args.user}
WorkingDirectory={build}
PIDFile={build}/capture.pid
ExecStart=/usr/bin/python3 {build}/run.py start --no-web --settings {settings}
ExecStop=/usr/bin/python3 {build}/run.py stop
TimeoutStartSec=60
TimeoutStopSec=45
KillMode=control-group
SendSIGKILL=no
Restart=on-failure
RestartSec=10
LimitRTPRIO=10

[Install]
WantedBy=multi-user.target
'''
    gui = f'''[Unit]
Description=Native DVS decoder and settings window
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=notify
NotifyAccess=main
WorkingDirectory={repo}
ExecStart=/usr/bin/python3 {repo}/app/dvs_streamer/native/session.py
TimeoutStartSec=30
Restart=on-failure
RestartSec=3
'''
    entry = f'''[Desktop Entry]
Type=Application
Name=DVS Decoder
Name[ko]=DVS 디코더
Comment=Live DVS decoder and sensor settings
Comment[ko]=DVS 실시간 영상과 센서 설정 패널
Exec=/bin/bash {repo}/app/dvs_streamer/native/launch.sh
Icon=camera-video
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=false
StartupWMClass=NrvDvsDecoder
'''
    files = [
        (Path('/etc/systemd/system/nrv-dvs-streamer.service'), receiver, 0o644, False),
        (home/'.config/systemd/user/nrv-dvs-native.service', gui, 0o644, True),
        (home/'.local/share/applications/nrv-dvs-native.desktop', entry, 0o644, True),
        (home/'.config/autostart/nrv-dvs-native.desktop', entry+'X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n', 0o644, True),
        (desktop/'DVS-Decoder.desktop', entry, 0o755, True),
    ]
    if args.dry_run:
        for path, content, mode, owned in files:
            print(f'=== {path} (mode {mode:o}) ===\n{content}')
        return
    if os.geteuid() != 0:
        p.error('Installation requires sudo; --dry-run is read-only')
    # Verify dependencies before replacing any configuration.
    as_user(['/usr/bin/python3', '-c', 'import tkinter,numpy; from PIL import ImageTk'], check=True)
    backup = home/'.local/state/nrv-dvs'/('autostart-backup-'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S'))

    def make_user_dirs(path):
        missing = []
        while not path.exists():
            missing.append(path)
            path = path.parent
        for directory in reversed(missing):
            directory.mkdir()
            os.chown(directory, account.pw_uid, account.pw_gid)

    make_user_dirs(backup)
    for path, content, mode, owned in files:
        if path.is_symlink():
            raise RuntimeError(f'Refusing to replace a symlink: {path}')
        if path.exists():
            saved = backup / str(path).lstrip('/')
            make_user_dirs(saved.parent)
            shutil.copy2(path, saved)
            os.chown(saved, account.pw_uid, account.pw_gid)
        if owned:
            make_user_dirs(path.parent)
        path.write_text(content, encoding='utf-8')
        os.chmod(path, mode)
        os.chown(path, account.pw_uid if owned else 0, account.pw_gid if owned else 0)
        print('INSTALLED', path)
    subprocess.run(['systemd-analyze', 'verify', str(files[0][0])], check=True)
    for path, _, _, _ in files:
        if path.suffix == '.desktop':
            subprocess.run(['desktop-file-validate', str(path)], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'nrv-dvs-streamer.service'], check=True)
    as_user(['systemctl', '--user', 'daemon-reload'], check=True)
    trusted = as_user(['gio', 'set', str(desktop/'DVS-Decoder.desktop'), 'metadata::trusted', 'true'])
    if trusted.returncode:
        print('Desktop metadata unavailable: use Allow Launching on the icon at desktop login.')
    print('BACKUP', backup)
    print('Receiver enabled for boot. Existing capture was not stopped or restarted.')
    print('GUI starts at graphical login. Reboot was not performed.')


if __name__ == '__main__':
    main()
