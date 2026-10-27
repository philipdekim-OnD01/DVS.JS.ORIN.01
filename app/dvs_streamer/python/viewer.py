#!/usr/bin/env python3
"""dvs_live 출력(stdin)을 받아 최신 이벤트 영상을 보여주는 웹 뷰어. 센서 제어 기능은 없다.

사용: dvs_live ... <제어 FIFO> | viewer.py --control <제어 FIFO>
받은 화면은 --directory 의 snapshot.bin 으로도 남겨 desktop.py, run.py status 가 읽는다.
웹 화면의 녹화 시작/정지는 제어 FIFO 로 dvs_live 에 전한다. 녹화 중에는 dvs_live 의
저장 통계로 녹화 폴더의 status.yaml 을 1초마다 다시 쓰고, 빠진 사건을 events.yaml 에
덧붙이고, 녹화 동안의 커널 메시지(CSI CRC corr_err 등)를 kernel.log 에 이어 쓴다.
"""
import datetime
import argparse
import collections
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import struct
import socket
import subprocess
import sys
import threading
import time
import os
import numpy as np
import yaml
from PIL import Image

WIDTH, HEIGHT = 960, 720
PACKED = WIDTH * HEIGHT // 4  # dvs_live 화면: 화소당 2비트, 바이트 i 의 비트 2j = 화소 4i+j
# 표시host monotonic시각 기준으로 너무뒤처진 브라우저 화면을 건너뜀.
BEHIND_LIMIT_US = 150_000
# snapshot은 빈 화소/ON/OFF의 인덱스0/1/2다. 센서 원본과 별도의 표시 색이다.
GRAY_VALUES = (128, 255, 0)
GRAY_LUT = list(GRAY_VALUES) + [128] * 253
# 웹 화면 (html/css/js): 이 파일 옆의 web/. 요청마다 읽으므로 고치면 새로고침만 하면 됨
WEB = Path(__file__).resolve().parent/'web'
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8')}

def grayscale_image(pixels):
    """인덱스 화소를 RGB 확장 없이8비트 회색조로 매핑한다."""
    return Image.frombytes('L', (WIDTH, HEIGHT), pixels).point(GRAY_LUT)

def read_snapshot(directory):
    """snapshot.bin -> (통계, 화소당 1바이트 0/1/2 화면)."""
    data = (directory/'snapshot.bin').read_bytes()
    size = struct.unpack('!I', data[:4])[0]
    if len(data) != 4+size+PACKED:
        raise ValueError('잘못된 snapshot 길이')
    packed = np.frombuffer(data, np.uint8, PACKED, 4+size)
    pixels = np.empty(WIDTH*HEIGHT, np.uint8)
    for j in range(4):
        pixels[j::4] = (packed >> (2*j)) & 3
    return json.loads(data[4:4+size]), pixels.tobytes()

def lan_address():
    """기본 경로 인터페이스의 로컬망 IPv4 주소 ([주소], 네트워크가 없으면 [])."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(('192.0.2.1', 9))  # UDP connect 는 패킷을 보내지 않고 출발 주소만 정함
        except OSError:
            return []
        return [probe.getsockname()[0]]

def local_time(unix_seconds):
    return datetime.datetime.fromtimestamp(unix_seconds).astimezone().isoformat(timespec='milliseconds')


def record(dump, stream, base, started, events, cursor):
    """dvs_live JSON 의 dump/stream 통계로 녹화 폴더의 events.yaml 에 덧붙이고 status.yaml 을
    교체한다. base: 녹화를 시작할 때의 stream 통계 (dropped/starved 는 그 뒤로 늘어난 수).
    녹화 시작 뒤 아직 안 옮긴 커널 메시지를 kernel.log 에 덧붙인다 (cursor: journalctl 이
    어디까지 옮겼는지 적어 두는 파일)."""
    dump_dir = Path(dump['dir'])
    with (dump_dir/'kernel.log').open('ab') as log:
        subprocess.run(['journalctl', '-k', '-q', '-o', 'short-iso-precise', '--since', f'@{started:.0f}',
                        '--cursor-file', str(cursor)], stdout=log, stderr=subprocess.DEVNULL)
    if dump['events']:
        items = []
        for e in dump['events']:
            items.append({'time': local_time(e['realtime_ns'] / 1e9),
                          'kind': '+'.join(k for k in ('dropped', 'starved') if e[k]) or 'counted_earlier',
                          'dropped': e['dropped'], 'starved': e['starved'],
                          'starved_ms': e['starved_ms'],
                          'copy_ms_max': e['copy_ms_max'], 'write_ms_max': e['write_ms_max'],
                          'frame': e['frame'], 'file': e['file'], 'offset': e['file_frame'] * stream['buffer_bytes'],
                          'context': e['context']})
        with (dump_dir/'events.yaml').open('a') as out:
            yaml.safe_dump(items, out, sort_keys=False, allow_unicode=True)
        events += len(items)
    now = time.time()
    status = {'state': dump['state'], 'error': dump['error'],
              'started': local_time(started), 'updated': local_time(now),
              'file_bytes': dump['file_bytes'], 'files': dump['files'], 'current_file': dump['file'],
              'bytes_recorded': dump['bytes'], 'bytes_written': dump['written'],
              'frames_recorded': dump['frames'],
              'sequence': stream['sequence'],
              'dropped_frames': stream['dropped'] - base['dropped'],
              'crc_errors': stream['error_buffers'] - base['error_buffers'],
              'driver_starved': stream['starved'] - base['starved'],
              'starved_ms': stream['starved_ms'] - base['starved_ms'], 'events': events}
    if dump['state'] != 'recording':
        status['ended'] = local_time(now)
    seconds = now - started
    status['duration_s'] = round(seconds, 1)
    status['input_mib_s'] = round(dump['bytes'] / seconds / 2**20, 1)
    status['written_mib_s'] = round(dump['written'] / seconds / 2**20, 1)
    status['ratio'] = round(dump['bytes'] / dump['written'], 2) if dump['written'] else 0
    temp = dump_dir/'status.yaml.tmp'
    temp.write_text('# bytes_recorded: 받은 원본, bytes_written: zstd 로 압축해 디스크에 쓴 크기\n'
                    '# 사건의 file/offset: 그 압축 파일을 풀었을 때의 위치 (zstd -d)\n'
                    '# dropped_frames: VI 가 번호를 매긴 뒤 버린 버퍼 수 (sequence 공백, CSI CRC 오류 등)\n'
                    '# crc_errors: 그중 VI 가 ERROR 로 돌려준 버퍼 (CSI CRC 등, kernel.log 의 corr_err)\n'
                    '# driver_starved: 드라이버 버퍼가 바닥난 횟수 (그 사이 번호 없이 버려진 프레임은 셀 수 없음)\n'
                    '# starved_ms: 바닥났던 동안 끊긴 시간 합, sequence: 마지막으로 받은 버퍼의 VI 번호\n'
                    '# 사건별 자세한 내용은 events.yaml (dropped/starved 는 앞 사건 이후 늘어난 수,\n'
                    '# counted_earlier = 앞 사건에 이미 셈), 사건 앞뒤 원본은 event-<frame>.bin.zst (zstd -d)\n'
                    '# 녹화 동안의 커널 메시지는 kernel.log (사건의 time 으로 찾아 봄)\n'
                    '# ended: 녹화가 끝난 시각 (녹화 중엔 없음), duration_s: started 부터 ended (녹화 중엔 updated) 까지\n'
                    '# input_mib_s/written_mib_s: 그동안 평균 받은/쓴 속도, ratio: bytes_recorded / bytes_written\n'
                    + yaml.safe_dump(status, sort_keys=False, allow_unicode=True))
    temp.replace(dump_dir/'status.yaml')
    return events


def receive(directory, latest):
    """stdin 에서 [길이][JSON][화소] 를 받아 latest 를 갱신하고 snapshot.bin 을 통째로
    교체한다. 녹화 폴더가 있으면 status.yaml/events.yaml 도 쓴다. EOF 면 반환."""
    source = sys.stdin.buffer
    current, state, next_status = '', '', 0  # 지금 녹화 폴더와 마지막으로 기록한 상태
    while True:
        header = source.read(4)
        if len(header) < 4:
            return
        size = struct.unpack('!I', header)[0] + PACKED
        body = source.read(size)
        if len(body) < size:
            return
        stats = json.loads(body[:struct.unpack('!I', header)[0]])
        # 센서 후보시각과 독립적인 표시시각. 역행/센서wrap을 전송 큐에 적용하지 않는다.
        ts = stats.get('display_timestamp_us', round(stats['monotonic'] * 1_000_000))
        with latest:
            latest.frame = header + body
            latest.id += 1
            latest.ts = ts
            latest.recent.append((latest.id, ts, latest.frame))
            latest.notify_all()
        temp = directory/'snapshot.tmp'
        temp.write_bytes(header + body)
        temp.replace(directory/'snapshot.bin')
        dump = stats['dump']
        if dump['dir'] != current:  # 새 녹화
            current, state = dump['dir'], ''
            started, events, base = time.time(), 0, stats['stream']
            (directory/'kernel.cursor').unlink(missing_ok=True)
        if current and (dump['events'] or dump['state'] != state
                        or (state == 'recording' and time.monotonic() >= next_status)):
            events = record(dump, stats['stream'], base, started, events, directory/'kernel.cursor')
            state, next_status = dump['state'], time.monotonic() + 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--directory', type=Path, default=Path('/dev/shm/nrv-dvs-live'))
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--no-http', action='store_true', help='화면 서버 없이 snapshot.bin 만 갱신')
    ap.add_argument('--control', type=Path, required=True, help='dvs_live 제어 FIFO (녹화 시작/정지)')
    ap.add_argument('--recordings', type=Path, default=Path(__file__).resolve().parent/'recordings',
                    help='녹화 저장 폴더 기본값 (웹 화면에서 바꿀 수 있음)')
    args = ap.parse_args()
    # 녹화 설정 기본값: 웹 화면의 처음 값이자, 값을 빼고 보낸 start 요청(./dvs 자동 녹화)에 씀
    # reserve_mib: 녹화가 남길 파일 시스템 여유 (root 예약 5% 와 별개로 사용자 프로그램 몫)
    defaults = {'base': str(args.recordings), 'rotate': False, 'file_gib': 4, 'files': 16,
                'reserve_mib': 1024}
    latest = threading.Condition()  # 최신 화면 (.frame, .id, .ts 센서 시각), 최근 화면들 (.recent: (id, ts, 화면))
    latest.frame, latest.id, latest.ts = b'', 0, 0
    latest.recent = collections.deque(maxlen=128)  # 브라우저가 건너뛰지 않고 차례로 받게 남겨 둠

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.settimeout(15)

        def do_GET(self):
            route = self.path.split('?', 1)[0]
            if route == '/frame':
                self.frame()
                return
            if route in STATIC:
                name, kind = STATIC[route]
                body = (WEB/name).read_bytes()
            elif route == '/defaults.json':
                body, kind = json.dumps(defaults).encode(), 'application/json'
            elif route == '/stats.json':
                with latest:
                    frame = latest.frame
                if not frame:
                    self.send_error(503, 'Capture not ready')
                    return
                s = json.loads(frame[4:4+struct.unpack('!I', frame[:4])[0]])
                s['update_age'] = max(0, time.monotonic() - s['monotonic'])
                body, kind = json.dumps(s).encode(), 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            """/record: {"action": "stop"}, {"action": "quit"} (수신기와 이 서버 종료),
            {"action": "preview", "fps"} (화면 1장에 합칠 간격, fps 0 = 센서 프레임 하나씩) 또는
            {"action": "start", "base", "rotate", "file_gib", "files", "reserve_mib"} (뺀 값은 기본값) 를
            dvs_live 제어 FIFO 의 한 줄 명령으로 바꿔 보낸다."""
            if self.path.split('?', 1)[0] != '/record':
                self.send_error(404)
                return
            status, reply = 200, {}
            try:
                request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                with latest:
                    frame = latest.frame
                recording = frame and json.loads(frame[4:4+struct.unpack('!I', frame[:4])[0]])['dump']['state'] == 'recording'
                if request['action'] in ('stop', 'quit'):
                    line = request['action']
                elif request['action'] == 'preview':
                    fps = int(request['fps'])
                    if fps < 0:
                        raise ValueError('프리뷰 fps 는 0 이상이어야 합니다')
                    line = f'preview\t{round(1e6 / fps) if fps else 0}'
                elif recording:
                    raise ValueError('이미 녹화 중입니다')
                else:
                    request = defaults | request
                    base = Path(str(request['base']).strip()).expanduser()
                    # dvs_live 가 JSON 에 그대로 넣고 탭으로 필드를 나누므로 이런 문자는 받지 않음
                    if not base.is_absolute() or any(c in '"\\' or ord(c) < 32 for c in str(base)):
                        raise ValueError('저장 폴더는 따옴표, 역슬래시, 제어 문자 없는 절대 경로로 쓰세요')
                    size, files = int(request['file_gib']), int(request['files']) if request['rotate'] else 0
                    reserve = int(request['reserve_mib'])
                    if size < 1 or (request['rotate'] and files < 1):
                        raise ValueError('파일 크기와 파일 개수는 1 이상이어야 합니다')
                    if reserve < 0:
                        raise ValueError('남길 여유 공간은 0 이상이어야 합니다')
                    folder = base/datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
                    line = f'start\t{folder}\t{size << 30}\t{files}\t{reserve << 20}'
                    reply['dir'] = str(folder)
                fd = os.open(args.control, os.O_WRONLY | os.O_NONBLOCK)
                try:
                    os.write(fd, (line + '\n').encode())
                finally:
                    os.close(fd)
            except (KeyError, ValueError, TypeError) as e:
                status, reply = 400, {'error': str(e)}
            except OSError as e:
                status, reply = 503, {'error': f'수신기에 전하지 못했습니다: {e.strerror}'}
            body = json.dumps(reply, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def frame(self):
            """after=<번호> 다음 화면을 보낸다 (1초 안에 없으면 지금 화면). 브라우저가 차례로
            받아 쌓아 두고 고르게 그림. gap=<us>: after 화면보다 표시 시각으로 이만큼 이상 뒤인
            화면만 (브라우저가 모니터 주기를 넘겨 받지 않게, 프리뷰 fps > 모니터 Hz 일 때 솎음).
            최신 화면보다 BEHIND_LIMIT_US 넘게 뒤처진 화면(브라우저가 못 따라옴)은 건너뛰고,
            고를 화면이 없거나 after < 0 (처음) 이면 최신 화면. X-Lag-Us: 보낸 화면이 최신
            화면보다 뒤처진 표시 시각 (센서 취득 지연과 다른 값)."""
            query = parse_qs(urlsplit(self.path).query)
            after = int(query.get('after', ['-1'])[0])
            gap = int(query.get('gap', ['0'])[0])
            with latest:
                latest.wait_for(lambda: latest.id != after, timeout=1)
                frame, ident, ts = latest.frame, latest.id, latest.ts
                if after >= 0:
                    base = next((t for i, t, _ in latest.recent if i == after), None)
                    for i, t, f in latest.recent:
                        if i <= after or (base is not None and t - base < gap):
                            continue
                        if latest.ts - t <= BEHIND_LIMIT_US:
                            frame, ident, ts = f, i, t
                            break
                lag = max(0, latest.ts - ts)
            if not frame:
                self.send_error(503, 'Capture not ready')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(frame)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Id', str(ident))
            self.send_header('X-Lag-Us', str(lag))
            self.end_headers()
            try:
                self.wfile.write(frame)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format, *values):
            if values and str(values[1] if len(values)>1 else '') not in ('200',):
                super().log_message(format, *values)

    if not args.no_http:
        # 이 기기와 로컬망에서만 접속 (0.0.0.0 처럼 모든 인터페이스에 열지 않음)
        for host in ['127.0.0.1'] + lan_address():
            server = ThreadingHTTPServer((host, args.port), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f'NRV 뷰어 http://{host}:{args.port}', flush=True)
    receive(args.directory, latest)  # dvs_live 가 끝나면(EOF) 뷰어도 끝남

if __name__ == '__main__':
    main()
