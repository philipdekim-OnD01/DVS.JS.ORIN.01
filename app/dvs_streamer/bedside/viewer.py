#!/usr/bin/env python3
"""Jetson의 최신 이벤트 영상만 읽는 LAN 뷰어. 센서 제어 기능은 없다.
녹화 시작/정지 버튼 추가 (record.cmd → dvs_live recorder.h): Karim <karim@nrv.kr>, NRV, 2026-10-12."""
import argparse
import json
import os
import re
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import struct
import socket
import threading
import time
import zlib
from PIL import Image

WIDTH, HEIGHT = 960, 720
# snapshot은 빈 화소/ON/OFF의 인덱스0/1/2다. 센서 원본과 별도의 표시 색이다.
GRAY_VALUES = (128, 255, 0)
GRAY_LUT = list(GRAY_VALUES) + [128] * 253
GRAY_PALETTE = [value for value in GRAY_VALUES for _ in range(3)] + [0] * 759
PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NRV DVS 실시간 보기</title><style>
body{margin:0;background:#111722;color:#e6edf7;font:16px system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:22px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px}h1{font-size:24px;margin:0}.badge{background:#243248;padding:8px 14px;border-radius:20px}
.layout{display:grid;grid-template-columns:minmax(0,960px) 210px;gap:20px;margin-top:20px}canvas{width:100%;background:#808080;image-rendering:pixelated;border:1px solid #34445d}
aside{line-height:1.7}.metric{margin-bottom:14px}.metric b{display:block;font-size:25px}small,.muted{color:#a5b5ca}button{color:#e6edf7;background:#243248;border:1px solid #4d627e;padding:8px;border-radius:6px;cursor:pointer}
.rec{background:#1a2333;border:1px solid #34445d;border-radius:8px;padding:12px;margin-bottom:16px}.rec input{width:100%;box-sizing:border-box;margin:6px 0;padding:7px;border-radius:6px;border:1px solid #4d627e;background:#0f1520;color:#e6edf7}.rec .start{background:#1f6f43;border-color:#2e9d61;width:48%}.rec .stop{background:#7a2626;border-color:#b14040;width:48%}.recstate{font-size:20px;font-weight:700}.recon{color:#5be38f}.on{color:#fff}.off{color:#bcbcbc}.bad{color:#ff9292}.foot{line-height:1.7;margin-top:14px}@media(max-width:800px){.layout{grid-template-columns:1fr}aside{display:flex;flex-wrap:wrap;gap:20px}}
</style><main><header><h1>NRV · DVS 실시간 보기</h1><span class="badge" id="status">연결 중</span></header>
<div class="layout"><section><canvas id="view" width="960" height="720"></canvas><div class="foot"><span class="on">ON · 흰색</span>　<span class="off">OFF · 검정</span>　배경 · 회색<br>
<small>센서 960 × 720 · CID 0 · 최신 수신 구간의 이벤트를 표시합니다. 같은 화소는 마지막 극성으로 표시하며, 다음 버퍼까지 영상을 유지합니다.</small></div></section>
<aside><div class="rec"><div>녹화 · Recording</div><div class="recstate" id="recstate">—</div><input id="reclabel" maxlength="32" placeholder="label (예: bed01_patientA)"><button class="start" id="recstart">● 시작 Start</button> <button class="stop" id="recstop">■ 정지 Stop</button><small id="recinfo" style="display:block;margin-top:6px;overflow-wrap:anywhere">—</small></div><div class="metric">이벤트 / 초<b id="rate">—</b></div><div class="metric">수신량<b id="bandwidth">—</b></div>
<div class="metric">정상 버퍼<b id="buffers">—</b></div><div class="metric">수신 오류<b id="errors">—</b></div>
<div class="metric">동작 시간<b id="elapsed">—</b></div><div class="metric">화면 속도<b id="displayrate">—</b><small id="fetchms">—</small></div><div><button id="quality">화질: 빠르게 480×360</button><p><button id="pause">화면 일시정지</button></p><p><button id="flip">상하 반전</button></p><small>화면 조작 중에도 Jetson 수신은 계속됩니다.</small></div></aside></div>
<p class="muted" id="detail">수신 상태를 확인하고 있습니다.</p></main><script>
const canvas=document.querySelector('#view'),ctx=canvas.getContext('2d');let paused=false,flip=false,previous=null,scale=2,lastDisplay=0;
document.querySelector('#quality').onclick=e=>{scale=scale===2?1:2;e.target.textContent=scale===2?'화질: 빠르게 480×360':'화질: 원본 960×720';};
document.querySelector('#pause').onclick=e=>{paused=!paused;e.target.textContent=paused?'화면 다시 보기':'화면 일시정지';};
document.querySelector('#flip').onclick=()=>{flip=!flip;};
async function recCmd(action){const label=document.querySelector('#reclabel').value;if(action==='stop'&&!confirm('녹화를 정지할까요? Stop recording?'))return;try{const r=await fetch('/api/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,label})});if(!r.ok)alert('명령 실패 / command failed: '+r.status);}catch(e){alert('연결 끊김 / not connected');}}
document.querySelector('#recstart').onclick=()=>recCmd('start');document.querySelector('#recstop').onclick=()=>recCmd('stop');
const fmtTime=x=>{x=Math.floor(x);return String(Math.floor(x/3600)).padStart(2,'0')+':'+String(Math.floor(x%3600/60)).padStart(2,'0')+':'+String(x%60).padStart(2,'0');};
function showRec(r){if(!r){put('recstate','녹화 기능 없음');return;}const el=document.getElementById('recstate');const on=r.state==='recording';el.textContent=on?'● 녹화 중 REC '+fmtTime(r.seconds):(r.state==='error'?'오류 ERROR':(r.state==='idle'?'정지 STOPPED':r.state));el.classList.toggle('recon',on);el.classList.toggle('bad',r.state==='error');put('recinfo',`${r.device} · ${r.session||'—'} · ${(r.bytes/1e9).toFixed(2)} GB · 여유 free ${r.free_gb.toFixed(0)} GB · 손실 dropped ${r.dropped} · 오류 버퍼 ${r.capture_errors}${r.reason?' · '+r.reason:''}`);}
const put=(id,value)=>document.getElementById(id).textContent=value;
async function imageLoop(){if(!paused){try{const start=performance.now(),r=await fetch('/frame.png?scale='+scale,{cache:'no-store'});if(r.ok){const id=r.headers.get('X-NRV-Snapshot'),b=await createImageBitmap(await r.blob());ctx.save();if(flip){ctx.translate(0,720);ctx.scale(1,-1);}ctx.imageSmoothingEnabled=false;ctx.drawImage(b,0,0,960,720);ctx.restore();b.close();const t=performance.now();put('fetchms','전송·그리기 '+(t-start).toFixed(0)+' ms');if(id!==canvas.dataset.snapshot){if(lastDisplay)put('displayrate',(1000/(t-lastDisplay)).toFixed(1)+' FPS');lastDisplay=t;canvas.dataset.snapshot=id;}}}catch(e){}}setTimeout(imageLoop,10);}
async function statsLoop(){try{const r=await fetch('/stats.json',{cache:'no-store'});if(!r.ok)throw Error();const s=await r.json();
const healthy=s.state==='live'&&s.update_age<2&&s.last_good_age<2;put('status',healthy?(paused?'수신 중 · 화면 정지':'실시간 수신 중'):(s.state==='replay'?'저장 데이터 재생':'수신 대기 / 정지'));
document.getElementById('status').classList.toggle('bad',!healthy);put('buffers',s.buffers.toLocaleString());put('errors',s.errors.toLocaleString());put('elapsed',s.seconds.toFixed(1)+' s');
if(previous&&s.seconds>previous.seconds){const dt=s.seconds-previous.seconds;put('rate',((s.events-previous.events)/dt/1e6).toFixed(2)+' M');put('bandwidth',((s.bytes-previous.bytes)/dt/1048576).toFixed(1)+' MiB/s');}
const age=s.buffer_age_ms>=0?s.buffer_age_ms.toFixed(1)+'ms':'시각 기준 미검증';
put('detail',`누적 이벤트 ${s.events.toLocaleString()} · 버퍼 번호 공백 ${s.sequence_gaps} · 센서 프레임 번호 이상 ${s.frame_gaps} · 형식 오류 ${s.invalid+s.unknown} · 재동기화 중 생략 그룹 ${s.unsynced_groups} · CID 1 이벤트 ${s.cid1_events} · 버퍼 timestamp 대비 도착: ${age} · 표시 ${canvas.dataset.snapshot||'—'} / 통계 ${s.snapshot_id}`);previous=s;showRec(s.recording);
}catch(e){put('status','연결 끊김');}setTimeout(statsLoop,1000);}imageLoop();statsLoop();
</script></html>'''.encode()

def chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

def png(pixels):
    # 네트워크는 기존 indexed PNG를 유지한다. 화소마다 RGB 세 바이트를 전송하지 않는다.
    palette = bytes(GRAY_PALETTE[:9])
    rows = b''.join(b'\0' + pixels[y*WIDTH:(y+1)*WIDTH] for y in range(HEIGHT))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', WIDTH, HEIGHT, 8, 3, 0, 0, 0))
            + chunk(b'PLTE', palette) + chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b''))

def grayscale_image(pixels):
    """인덱스 화소를 RGB 확장 없이8비트 회색조로 매핑한다."""
    return Image.frombytes('L', (WIDTH, HEIGHT), pixels).point(GRAY_LUT)

def read_snapshot(directory):
    data = (directory/'snapshot.bin').read_bytes()
    size = struct.unpack('!I', data[:4])[0]
    if len(data) != 4+size+WIDTH*HEIGHT:
        raise ValueError('잘못된 snapshot 길이')
    return json.loads(data[4:4+size]), data[4+size:]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--directory', type=Path, default=Path('/dev/shm/nrv-dvs-live'))
    ap.add_argument('--bind', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8080)
    args = ap.parse_args()
    cache, cache_lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.settimeout(15)

        def do_GET(self):
            route = self.path.split('?', 1)[0]
            snapshot_id = None
            try:
                if route == '/':
                    body, kind = PAGE, 'text/html; charset=utf-8'
                elif route == '/stats.json':
                    s, _ = read_snapshot(args.directory)
                    s['update_age'] = max(0, time.monotonic() - s['monotonic'])
                    body, kind = json.dumps(s).encode(), 'application/json'
                elif route == '/frame.png':
                    path = args.directory/'snapshot.bin'
                    scale = 2 if self.path.endswith('?scale=2') else 1
                    with cache_lock:
                        stamp = path.stat().st_mtime_ns
                        if stamp != cache.get('stamp'):
                            s, pixels = read_snapshot(args.directory)
                            cache.clear()
                            cache.update(stamp=stamp, pixels=pixels, snapshot=s['snapshot_id'])
                        key = 'image'+str(scale)
                        if key not in cache:
                            if scale == 1:
                                cache[key] = png(cache['pixels'])
                            else:
                                im=Image.frombytes('P',(WIDTH,HEIGHT),cache['pixels'])
                                im.putpalette(GRAY_PALETTE)
                                im=im.resize((WIDTH//2,HEIGHT//2),Image.Resampling.NEAREST)
                                out=io.BytesIO();im.save(out,format='PNG',bits=2,compress_level=1)
                                cache[key]=out.getvalue()
                        body = cache[key]
                        snapshot_id = cache['snapshot']
                    kind = 'image/png'
                else:
                    self.send_error(404)
                    return
            except (OSError, ValueError):
                self.send_error(503, 'Capture not ready')
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if snapshot_id is not None:
                self.send_header('X-NRV-Snapshot', str(snapshot_id))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            """Start/Stop recording: writes record.cmd for dvs_live (Karim <karim@nrv.kr>, NRV)."""
            if self.path.split('?', 1)[0] != '/api/record':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024:
                    raise ValueError('body size')
                request = json.loads(self.rfile.read(length))
                action = request.get('action')
                label = re.sub(r'[^A-Za-z0-9_-]', '', str(request.get('label') or ''))[:32]
                if action not in ('start', 'stop'):
                    raise ValueError('action')
                line = f'start {label}' if action == 'start' else 'stop'
                temp = args.directory/'record.cmd.tmp'
                temp.write_text(line + '\n')
                os.replace(temp, args.directory/'record.cmd')
            except (OSError, ValueError, AttributeError) as error:
                self.send_error(400, str(error))
                return
            body = json.dumps({'accepted': line}).encode()
            self.send_response(202)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *values):
            if values and str(values[1] if len(values)>1 else '') not in ('200',):
                super().log_message(format, *values)

    print(f'NRV 뷰어 http://{args.bind}:{args.port}', flush=True)
    ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
