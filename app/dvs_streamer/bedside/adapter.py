#!/usr/bin/env python3
"""bedside 화면(이 폴더의 viewer.py + 키오스크 창, 고치지 않고 그대로)을 dvs_streamer 수신기에
붙이는 어댑터.

  dvs_streamer | ../viewer.py --no-http  ->  /dev/shm/nrv-dvs-live/snapshot.bin (화소당 2비트)
  이 어댑터                              ->  /dev/shm/nrv-dvs-bedside/snapshot.bin (bedside 형식)
  bedside viewer.py (이 어댑터가 띄움)   ->  http://127.0.0.1:8080 (키오스크 창이 보는 주소)

- 화면: 수신기 snapshot 이 바뀔 때마다 화소당 1바이트(0 빈 화소, 1 ON, 2 OFF)로 풀고 통계를
  bedside 화면이 읽는 이름으로 바꿔 bedside snapshot.bin 을 통째로 교체한다.
- 녹화: bedside 화면의 시작/정지가 쓰는 record.cmd ("start <label>" / "stop") 를 수신기 제어
  FIFO 명령으로 바꾼다. 돌려쓰기 없이 4 GiB 파일, 여유 공간이 1 GiB 남을 때까지 기록
  (폴더: <녹화 폴더>/<날짜-시각>[_label]).
- 이어서 녹화: 녹화 중에는 <녹화 폴더>/.recording 에 label 이 있다. 정지 버튼이나 여유 공간
  부족으로 끝나면 지우고, 그 밖의 이유(전원, 재부팅, 서비스 재시작)로 수신기가 새로 뜨면
  새 폴더 <날짜-시각>_<label>_resumed 로 이어서 녹화한다.
"""
import argparse
import importlib.util
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STREAMER = Path('/dev/shm/nrv-dvs-live')   # run.py 의 STATE: snapshot.bin, 제어 FIFO
BEDSIDE = Path('/dev/shm/nrv-dvs-bedside')  # bedside viewer.py 의 --directory
FILE_BYTES = 4 << 30                        # 녹화 파일 하나 (viewer.py 기본값과 같음)
RESERVE_BYTES = 1024 << 20                  # 녹화를 끝낼 남은 공간 (viewer.py 기본값과 같음)

# 수신기 쪽 viewer.py 의 read_snapshot (이 폴더에도 같은 이름의 bedside viewer.py 가 있어 경로로 읽음)
spec = importlib.util.spec_from_file_location('streamer_viewer', HERE.parent/'viewer.py')
streamer_viewer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(streamer_viewer)


def command(line):
    """수신기 제어 FIFO 에 한 줄 (수신기가 없으면 OSError)."""
    fd = os.open(STREAMER/'control', os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, (line + '\n').encode())
    finally:
        os.close(fd)


def start(recordings, label):
    folder = recordings/(time.strftime('%Y%m%d-%H%M%S') + (f'_{label}' if label else ''))
    command(f'start\t{folder}\t{FILE_BYTES}\t0\t{RESERVE_BYTES}')
    (recordings/'.recording').write_text(label)
    print(f'녹화 시작 {folder}', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--recordings', type=Path, default=Path('/nrv/recordings'), help='녹화 폴더')
    ap.add_argument('--port', type=int, default=8080, help='bedside 화면 포트 (127.0.0.1)')
    args = ap.parse_args()
    marker = args.recordings/'.recording'
    BEDSIDE.mkdir(mode=0o755, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # finally 에서 bedside viewer.py 도 끝냄
    viewer = subprocess.Popen([sys.executable, str(HERE/'viewer.py'), '--directory', str(BEDSIDE),
                               '--port', str(args.port)])
    stamp, session, dump_dir, recording = None, None, '', False
    started, base = 0.0, {}
    free_gb, next_free = 0.0, 0.0
    try:
        while viewer.poll() is None:
            time.sleep(0.01)
            cmd = BEDSIDE/'record.cmd'
            if cmd.exists():
                words = cmd.read_text().split()
                cmd.unlink()
                try:
                    if words[:1] == ['stop']:
                        command('stop')
                        marker.unlink(missing_ok=True)
                    elif words[:1] == ['start'] and not recording:
                        start(args.recordings, words[1] if len(words) > 1 else '')
                except OSError as e:
                    print(f'수신기에 전하지 못함: {e}', flush=True)
            try:
                now_stamp = (STREAMER/'snapshot.bin').stat().st_mtime_ns
                if now_stamp == stamp:
                    continue
                s, pixels = streamer_viewer.read_snapshot(STREAMER)
            except (OSError, ValueError):
                continue
            stamp = now_stamp
            stream, dump = s['stream'], s['dump']
            recording = dump['state'] == 'recording'
            if s['session_pid'] != session:  # 수신기가 새로 뜸: 녹화 중이던 게 끊겼으면 이어서
                session = s['session_pid']
                if marker.exists() and not recording:
                    label = marker.read_text().strip()
                    try:
                        start(args.recordings, (label + '_' if label else '') + 'resumed')
                    except OSError as e:
                        print(f'이어서 녹화 못함: {e}', flush=True)
            if dump['dir'] != dump_dir:  # 새 녹화 (또는 끝난 뒤 다음 수신기)
                dump_dir, started, base = dump['dir'], time.time(), stream
            if dump['state'] == 'failed':  # 여유 공간 부족 등: 같은 디스크에 이어서 녹화하지 않음
                marker.unlink(missing_ok=True)
            if time.monotonic() >= next_free:
                free_gb = shutil.disk_usage(args.recordings).free / 1e9
                next_free = time.monotonic() + 1
            stats = {k: s[k] for k in ('state', 'session_pid', 'monotonic', 'seconds', 'last_good_age',
                                       'snapshot_id', 'events', 'invalid', 'unknown', 'unsynced_groups',
                                       'cid1_events', 'frame_gaps')}
            stats |= {'buffers': stream['buffers'], 'errors': stream['error_buffers'],
                      'bytes': stream['bytes'], 'sequence_gaps': stream['dropped'],
                      'buffer_age_ms': -1,  # 버퍼 도착 지연은 수신기가 내보내지 않음
                      'recording': {
                          'state': {'recording': 'recording', 'failed': 'error'}.get(dump['state'], 'idle'),
                          'seconds': time.time() - started if recording else 0,
                          'device': socket.gethostname(), 'session': Path(dump_dir).name,
                          'bytes': dump['written'], 'free_gb': free_gb,
                          'dropped': stream['dropped'] - base['dropped'] if dump_dir else 0,
                          'capture_errors': stream['error_buffers'] - base['error_buffers'] if dump_dir else 0,
                          'reason': dump['error']}}
            body = json.dumps(stats).encode()
            temp = BEDSIDE/'snapshot.tmp'
            temp.write_bytes(struct.pack('!I', len(body)) + body + pixels)
            temp.replace(BEDSIDE/'snapshot.bin')
    finally:
        if viewer.poll() is None:
            viewer.terminate()
            viewer.wait()


if __name__ == '__main__':
    main()
