#!/usr/bin/env python3
"""dvs_live 출력(stdin)을 받아 최신 이벤트 영상을 보여주는 웹 뷰어. 센서 제어 기능은 없다.

사용: dvs_live ... <제어 FIFO> | viewer.py --control <제어 FIFO>
받은 화면은 --directory 의 snapshot.bin 으로도 남겨 desktop.py, run.py status 가 읽는다.
웹 화면의 녹화 시작/정지는 제어 FIFO 로 dvs_live 에 전한다. 녹화 중에는 dvs_live 의
저장 통계로 녹화 폴더의 status.yaml 을 1초마다 다시 쓰고, 빠진 사건을 events.yaml 에
덧붙인다.
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
import sys
import threading
import time
import os
import numpy as np
import yaml
from PIL import Image

WIDTH, HEIGHT = 960, 720
PACKED = WIDTH * HEIGHT // 4  # dvs_live 화면: 화소당 2비트, 바이트 i 의 비트 2j = 화소 4i+j
WRAP_US = (1 << 22) * 1000  # 센서 시각은 2^22 ms 마다 0 으로 돌아감
# 브라우저가 받는 화면이 최신 화면보다 이만큼(센서 시각) 넘게 뒤처지면 이 범위 안으로 건너뜀
BEHIND_LIMIT_US = 150_000
# snapshot은 빈 화소/ON/OFF의 인덱스0/1/2다. 센서 원본과 별도의 표시 색이다.
GRAY_VALUES = (128, 255, 0)
GRAY_LUT = list(GRAY_VALUES) + [128] * 253
PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NRV DVS 실시간 보기</title><style>
body{margin:0;background:#111722;color:#e6edf7;font:14px system-ui,sans-serif;overflow:hidden}
/* 창 높이에 딱 맞춤: 페이지는 스크롤되지 않고, 넘치면 오른쪽 칸만 스크롤 */
main{height:100vh;box-sizing:border-box;padding:10px 14px;display:flex;flex-direction:column}
header{display:flex;align-items:center;justify-content:space-between;gap:12px}h1{font-size:20px;margin:0}.badge{background:#243248;padding:5px 12px;border-radius:16px}
.layout{flex:1;min-height:0;display:flex;gap:16px;margin-top:8px}
/* 화면 크기는 창 높이로 정함 (아래 상태 줄 몫을 뺌). section 폭 = 화면 폭, 상태 줄은 그 폭에서 줄바꿈 */
section{flex:0 0 auto;width:min-content}canvas{display:block;height:min(720px,calc(100vh - 230px));width:auto;max-width:calc(100vw - 300px);background:#808080;image-rendering:pixelated;border:1px solid #34445d}
aside{flex:1 1 0;min-width:240px;overflow-y:auto;line-height:1.5;padding-left:14px;border-left:1px solid #34445d}.metric{margin-bottom:10px}.metric b{display:block;font-size:24px}small,.muted{color:#a5b5ca}button{color:#e6edf7;background:#243248;border:1px solid #4d627e;padding:6px 8px;border-radius:6px;cursor:pointer}
.rec{display:flex;flex-direction:column;gap:5px;margin-bottom:12px}.rec input{color:#e6edf7;background:#1b2536;border:1px solid #4d627e;border-radius:4px;padding:2px}.rec input[type=number]{width:4.5em}select{color:#e6edf7;background:#1b2536;border:1px solid #4d627e;border-radius:4px;padding:5px}.rec #recbase{width:100%;box-sizing:border-box}
.tools{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}.foot{font-size:12px;line-height:1.6;margin-top:6px;color:#a5b5ca}.foot b{color:#e6edf7;font-weight:600}
.on{color:#fff}.off{color:#bcbcbc}.bad{color:#ff9292}
</style><main><header><h1>NRV · DVS 실시간 보기</h1><span class="badge" id="status">연결 중</span></header>
<div class="layout"><section><canvas id="view" width="960" height="720"></canvas><div class="foot"><span class="on">ON · 흰색</span>　<span class="off">OFF · 검정</span>　배경 · 회색 · 센서 960 × 720 · CID 0 · 화면 1장 = 프리뷰 간격(센서 시각) 동안의 센서 프레임을 합친 것(같은 화소는 마지막 극성)<br>
화면 프레임의 이벤트 <b id="frameevents">—</b> · 해석한 이벤트 <b id="rate">—</b>/s · 수신량 <b id="bandwidth">—</b> · 해석한 버퍼 <b id="buffers">—</b> · 건너뛴 버퍼 <b id="errors">—</b> · 동작 시간 <b id="elapsed">—</b> · 화면 <b id="displayrate">—</b> (<span id="recvrate">—</span>)<br>
<span id="detail">수신 상태를 확인하고 있습니다.</span></div></section>
<aside><div class="metric">센서 프레임 속도<b id="sensorfps">—</b><small id="period">—</small><br><small id="merged">—</small></div><div class="metric">녹화<b id="recstate">—</b><small id="recdetail"></small></div>
<div class="rec"><label>저장 폴더 (이 안에 녹화마다 날짜-시각 폴더)<input id="recbase"></label><label><input type="checkbox" id="recrotate"> 돌려쓰기 (가장 오래된 파일부터 덮어씀)</label>
<label>파일 크기 <input id="recsize" type="number" min="1"> GiB · 개수 <input id="recfiles" type="number" min="1"></label>
<button id="record">녹화 시작</button><small class="bad" id="recmsg"></small></div>
<div class="tools"><label>프리뷰 <select id="preview"><option value="0">센서 프레임 1장</option><option value="120">120 fps</option><option value="60">60 fps</option><option value="30">30 fps</option><option value="15">15 fps</option></select></label><button id="pause">화면 일시정지</button><button id="flip">상하 반전</button><button id="denoise">노이즈 제거 끔</button></div>
<small>노이즈 제거(화면에만 적용, 저장 원본은 그대로): k=1/2 는 주변 8칸에 이벤트가 k개 이상인 화소만 표시합니다. <span id="removed"></span></small>
<p><button id="quit">수신·서버 종료</button></p></aside></div></main><script>
// /frame?after=ID: ID 다음의 최신 화면 하나 [u32 BE JSON 길이][JSON][2비트 화소 172800 B], 번호는 X-Frame-Id.
// 그린 뒤에 다음 화면을 요청하므로(best-effort) 브라우저는 그릴 수 있는 만큼만 받는다.
const W=960,H=720,PACKED=W*H/4,canvas=document.querySelector('#view'),ctx=canvas.getContext('2d'),image=ctx.createImageData(W,H),rgba=new Uint32Array(image.data.buffer);
const COLORS=new Uint32Array([0xff808080,0xffffffff,0xff000000,0xff808080]);const MODES=['노이즈 제거 끔','노이즈 제거 k=1','노이즈 제거 k=2'],idx=new Uint8Array(W*H),near=new Uint8Array(W*H);let denoise=0,paused=false,flip=false,latest=null,shown=0,frames=0,bytes=0,previous=null,periodSum=0,periodCount=0;
// 녹화: 설정은 시작할 때만 보냄. 녹화 상태는 화면 JSON 의 dump (statsLoop 에서 표시)
const DEFAULTS=@DEFAULTS@,rec=id=>document.getElementById(id);rec('recbase').value=DEFAULTS.base;rec('recrotate').checked=DEFAULTS.rotate;
rec('recsize').value=DEFAULTS.file_gib;rec('recfiles').value=DEFAULTS.files;rec('recfiles').disabled=!DEFAULTS.rotate;
rec('recrotate').onchange=e=>{rec('recfiles').disabled=!e.target.checked;};
rec('record').onclick=async()=>{const recording=latest&&latest.stats.dump.state==='recording';
const body=recording?{action:'stop'}:{action:'start',base:rec('recbase').value,rotate:rec('recrotate').checked,file_gib:Number(rec('recsize').value),files:Number(rec('recfiles').value)};
put('recmsg','');try{const r=await fetch('/record',{method:'POST',body:JSON.stringify(body)});if(!r.ok)put('recmsg',(await r.json()).error);}catch(e){put('recmsg','서버에 연결하지 못했습니다');}};
rec('quit').onclick=async()=>{if(!confirm('수신기와 웹 서버를 끝낼까요? (녹화 중이면 녹화도 끝납니다. 다시 켜려면 ./dvs)'))return;
try{const r=await fetch('/record',{method:'POST',body:JSON.stringify({action:'quit'})});if(!r.ok)put('recmsg',(await r.json()).error);}catch(e){put('recmsg','서버에 연결하지 못했습니다');}};
// 프리뷰 fps: 화면 1장에 합칠 센서 시각 간격 (dvs_live 가 합침, 0 = 센서 프레임 하나씩)
rec('preview').onchange=async e=>{const fps=Number(e.target.value);
try{const r=await fetch('/record',{method:'POST',body:JSON.stringify({action:'preview',fps})});if(!r.ok)put('recmsg',(await r.json()).error);}catch(err){put('recmsg','서버에 연결하지 못했습니다');}};
document.querySelector('#pause').onclick=e=>{paused=!paused;e.target.textContent=paused?'화면 다시 보기':'화면 일시정지';};
document.querySelector('#flip').onclick=()=>{flip=!flip;if(latest)latest.dirty=true;};
document.querySelector('#denoise').onclick=e=>{denoise=(denoise+1)%MODES.length;e.target.textContent=MODES[denoise];if(!denoise)put('removed','');if(latest)latest.dirty=true;};
const put=(id,value)=>document.getElementById(id).textContent=value;
// 노이즈 제거. k=1/2 (denoise 1/2): 이벤트 화소마다 주변 8칸의 near 를 1씩 올린 뒤 near<k 인 이벤트는 배경.
// 가장자리는 칸 밖을 배경으로 봄.
// 지터 버퍼: 받은 화면을 queue 에 쌓아 두고, 화면 갱신(requestAnimationFrame = 수직동기)마다 재생 시각
// playUs(센서 시각)까지의 화면 중 마지막 것을 그림. playUs 는 최신 화면보다 delayUs 뒤에서 실제 시간 속도로
// 진행. 화면은 원본 버퍼(약 0.1 s)마다 몰려 오므로 delayUs = 최근 2초 동안 몰려 온 묶음 사이 간격(30 ms 넘게
// 끊긴 것)의 중앙값 x 1.1, 30~200 ms 로 제한 (실시간성 우선: 더 길게 끊기면 그동안 마지막 화면을 유지).
const WRAP_US=2**22*1000;let lagSum=0,lagMax=0,lagCount=0,queue=[],newestUs=null,playUs=null,delayUs=150000,lastTick=null,lastArrival=0,gaps=[],ticks=[],rawPrev=0,wrapAdd=0;
function arrive(screen){const now=performance.now(),raw=screen.stats.frame.timestamp_us;
if(!raw){if(!queue.length){latest=screen;}return;}  // 빈 화면 (200 ms 동안 프레임 없음)
if(raw<rawPrev-WRAP_US/2)wrapAdd+=WRAP_US;rawPrev=raw;screen.ts=raw+wrapAdd;
if(lastArrival&&now-lastArrival>30)gaps.push([now,now-lastArrival]);lastArrival=now;gaps=gaps.filter(g=>now-g[0]<2000);
if(gaps.length){const m=gaps.map(g=>g[1]).sort((x,y)=>x-y)[gaps.length>>1];delayUs=Math.min(200000,Math.max(30000,1100*m));}
queue.push(screen);if(queue.length>512)queue.shift();newestUs=screen.ts;}
function tick(now){if(lastTick!==null){ticks.push(now-lastTick);if(ticks.length>120)ticks.shift();if(playUs!==null)playUs+=(now-lastTick)*1000;}lastTick=now;
if(newestUs===null)return;if(playUs===null||newestUs-playUs>delayUs+100000)playUs=newestUs-delayUs;else if(playUs>newestUs)playUs=newestUs;
let pick=null;while(queue.length&&queue[0].ts<=playUs)pick=queue.shift();
// 화면 지연 = 서버에서 이미 뒤처졌던 만큼 + 브라우저에 도착한 뒤 그리기까지 (dvs_live 가 화면을 낸 뒤 기준)
if(pick){pick.dirty=true;latest=pick;const l=pick.lag/1000+(now-pick.arrival);lagSum+=l;lagMax=Math.max(lagMax,l);lagCount++;}}
// 노이즈 제거가 꺼져 있으면 바이트 하나(화소 4개) -> 색 4개 표(QUAD)로 바로 칠함 (장비 Firefox 부담 줄임)
const QUAD=new Uint32Array(1024);for(let b=0;b<256;b++)for(let j=0;j<4;j++)QUAD[b*4+j]=COLORS[(b>>(2*j))&3];
function draw(now){tick(now);if(latest&&latest.dirty&&!paused&&!denoise){const px=latest.pixels;
for(let y=0;y<H;y++){const row=(flip?H-1-y:y)*W,src=y*W/4;for(let q=0;q<W/4;q++){const b=px[src+q]*4,o=row+q*4;rgba[o]=QUAD[b];rgba[o+1]=QUAD[b+1];rgba[o+2]=QUAD[b+2];rgba[o+3]=QUAD[b+3];}}
ctx.putImageData(image,0,0);latest.dirty=false;++shown;}
else if(latest&&latest.dirty&&!paused){const px=latest.pixels;for(let i=0;i<PACKED;i++){const b=px[i],o=i*4;idx[o]=b&3;idx[o+1]=(b>>2)&3;idx[o+2]=(b>>4)&3;idx[o+3]=b>>6;}
let active=0,kept=0;if(denoise){near.fill(0);for(let y=0;y<H;y++)for(let x=0;x<W;x++){const v=idx[y*W+x];if(!v)continue;++active;
for(let dy=-1;dy<=1;dy++){const yy=y+dy;if(yy<0||yy>=H)continue;for(let dx=-1;dx<=1;dx++){const xx=x+dx;if((dx||dy)&&xx>=0&&xx<W)++near[yy*W+xx];}}}}
for(let y=0;y<H;y++){const row=(flip?H-1-y:y)*W;for(let x=0;x<W;x++){const i=y*W+x;let v=idx[i];if(v&&denoise&&near[i]<denoise)v=0;if(v)++kept;rgba[row+x]=COLORS[v];}}
if(denoise)put('removed',`원본 대비 표시 화소 ${active?(kept/active*100).toFixed(0):0}%.`);ctx.putImageData(image,0,0);latest.dirty=false;++shown;}requestAnimationFrame(draw);}
// 화면을 하나도 건너뛰지 않고 순서대로 받아 지터 버퍼에 넣음 (서버가 이미 지나간 것은 최신으로 건너뜀)
async function streamLoop(){const text=new TextDecoder();let id=-1;for(;;){try{const period=ticks.length>30?[...ticks].sort((a,b)=>a-b)[ticks.length>>1]:0;
const r=await fetch(`/frame?after=${id}&gap=${Math.round(period*900)}`,{cache:'no-store'});if(!r.ok)throw r.status;
id=Number(r.headers.get('X-Frame-Id'));const buf=new Uint8Array(await r.arrayBuffer());bytes+=buf.length;const n=new DataView(buf.buffer).getUint32(0);
const screen={stats:JSON.parse(text.decode(buf.subarray(4,4+n))),pixels:buf.subarray(4+n),lag:Number(r.headers.get('X-Lag-Us')),arrival:performance.now()};++frames;
const f=screen.stats.frame;if(f&&f.period_us>0){periodSum+=f.period_us;periodCount++;}arrive(screen);}catch(e){put('status','연결 끊김');await new Promise(r=>setTimeout(r,500));}}}
function statsLoop(){if(latest){const s=latest.stats,healthy=s.state==='live'&&s.last_good_age<2;put('status',healthy?(paused?'수신 중 · 화면 정지':'실시간 수신 중'):'수신 대기 / 정지');
document.getElementById('status').classList.toggle('bad',!healthy);put('buffers',s.buffers.toLocaleString());put('errors',s.skipped.toLocaleString());put('elapsed',s.seconds.toFixed(1)+' s');
// 센서 프레임 속도: 지난 1초 동안 받은 화면들의 period_us(그 프레임과 바로 앞 센서 프레임의 시작 간격) 평균
put('frameevents',s.frame?s.frame.events.toLocaleString():'—');if(periodCount){const period=periodSum/periodCount;put('sensorfps',(1e6/period).toFixed(0)+' FPS');put('period',`센서 프레임 간격 ${period.toFixed(0)} µs (화면 ${periodCount}장 평균)`);periodSum=0;periodCount=0;}
if(previous&&s.seconds>previous.stats.seconds){const dt=s.seconds-previous.stats.seconds;put('period',document.getElementById('period').textContent.split(' · ')[0]+` · 해석 ${((s.frames-previous.stats.frames)/dt).toFixed(0)} 프레임/s`);put('rate',((s.events-previous.stats.events)/dt/1e6).toFixed(2)+' M');put('bandwidth',((s.stream.bytes-previous.stats.stream.bytes)/dt/1048576).toFixed(1)+' MiB/s');}
put('displayrate',(shown-(previous?previous.shown:0)).toFixed(0)+' FPS');put('recvrate',`받음 ${frames-(previous?previous.frames:0)} 화면/s · ${((bytes-(previous?previous.bytes:0))/1048576).toFixed(1)} MiB/s`);
put('detail',`받은 버퍼 ${s.stream.buffers.toLocaleString()} · CSI 오류로 버린 버퍼 ${s.stream.dropped} · 드라이버 버퍼 고갈 ${s.stream.starved} (${(s.stream.starved_ms/1000).toFixed(1)} s) · 해석한 이벤트 ${s.events.toLocaleString()} · 해석 초기화 ${s.discontinuities} · 센서 프레임 번호 이상 ${s.frame_gaps} · 형식 오류 ${s.invalid+s.unknown} · 재동기화 중 생략 그룹 ${s.unsynced_groups} · CID 1 이벤트 ${s.cid1_events} · 화면 ${s.snapshot_id}${s.dump?` · 저장 ${s.dump.state} ${s.dump.frames.toLocaleString()} 버퍼`:''}`);
if(document.activeElement!==rec('preview'))rec('preview').value=String(s.preview_us?Math.round(1e6/s.preview_us):0);
const hz=ticks.length?1000/[...ticks].sort((a,b)=>a-b)[ticks.length>>1]:0;
put('merged',`화면당 센서 프레임 ${s.merged}장 · 화면 지연 평균 ${lagCount?(lagSum/lagCount).toFixed(0):'—'} ms (최대 ${lagMax.toFixed(0)}, 고르게 하려고 기다리는 몫 ${(delayUs/1000).toFixed(0)}) · 모니터 ${hz.toFixed(0)} Hz`);lagSum=lagMax=lagCount=0;
const d=s.dump,recording=d.state==='recording';put('recstate',{idle:'녹화 안 함',recording:'녹화 중',stopped:'정지됨',failed:'실패'}[d.state]);
put('recdetail',d.dir?`${d.dir} · 받은 ${(d.bytes/2**30).toFixed(2)} GiB → 저장 ${(d.written/2**30).toFixed(2)} GiB${d.written?` (${(d.bytes/d.written).toFixed(2)}배)`:''} · 파일 ${(d.file_bytes/2**30).toFixed(0)} GiB × ${d.files?d.files+'개 돌려쓰기':'제한 없음'}${d.error?' · '+d.error:''}`:'');
rec('record').textContent=recording?'녹화 정지':'녹화 시작';rec('record').style.color=recording?'#ff6b6b':'';rec('recstate').style.color=recording?'#5aa9ff':'';for(const i of ['recbase','recrotate','recsize'])rec(i).disabled=recording;rec('recfiles').disabled=recording||!rec('recrotate').checked;
previous={stats:s,shown,frames,bytes};}setTimeout(statsLoop,1000);}
requestAnimationFrame(draw);streamLoop();statsLoop();
</script></html>'''.encode()

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

def local_time(unix_seconds):
    return datetime.datetime.fromtimestamp(unix_seconds).astimezone().isoformat(timespec='milliseconds')


def record(dump, stream, base, started, events):
    """dvs_live JSON 의 dump/stream 통계로 녹화 폴더의 events.yaml 에 덧붙이고 status.yaml 을
    교체한다. base: 녹화를 시작할 때의 stream 통계 (dropped/starved 는 그 뒤로 늘어난 수)."""
    dump_dir = Path(dump['dir'])
    if dump['events']:
        items = []
        for e in dump['events']:
            items.append({'time': local_time(e['realtime_ns'] / 1e9),
                          'kind': '+'.join(k for k in ('dropped', 'starved') if e[k]) or 'counted_earlier',
                          'dropped': e['dropped'], 'starved': e['starved'],
                          'starved_ms': e['starved_ms'],
                          'copy_ms_max': e['copy_ms_max'], 'write_ms_max': e['write_ms_max'],
                          'frame': e['frame'], 'file': e['file'], 'offset': e['file_frame'] * 1048576,
                          'context': e['context']})
        with (dump_dir/'events.yaml').open('a') as out:
            yaml.safe_dump(items, out, sort_keys=False, allow_unicode=True)
        events += len(items)
    status = {'state': dump['state'], 'error': dump['error'],
              'started': local_time(started), 'updated': local_time(time.time()),
              'file_bytes': dump['file_bytes'], 'files': dump['files'], 'current_file': dump['file'],
              'bytes_recorded': dump['bytes'], 'bytes_written': dump['written'],
              'frames_recorded': dump['frames'],
              'sequence': stream['sequence'],
              'dropped_frames': stream['dropped'] - base['dropped'],
              'driver_starved': stream['starved'] - base['starved'],
              'starved_ms': stream['starved_ms'] - base['starved_ms'], 'events': events}
    temp = dump_dir/'status.yaml.tmp'
    temp.write_text('# bytes_recorded: 받은 원본, bytes_written: zstd 로 압축해 디스크에 쓴 크기\n'
                    '# 사건의 file/offset: 그 압축 파일을 풀었을 때의 위치 (zstd -d)\n'
                    '# dropped_frames: VI 가 번호를 매긴 뒤 버린 버퍼 수 (sequence 공백, CSI CRC 오류 등)\n'
                    '# driver_starved: 드라이버 버퍼가 바닥난 횟수 (그 사이 번호 없이 버려진 프레임은 셀 수 없음)\n'
                    '# starved_ms: 바닥났던 동안 끊긴 시간 합, sequence: 마지막으로 받은 버퍼의 VI 번호\n'
                    '# 사건별 자세한 내용은 events.yaml (dropped/starved 는 앞 사건 이후 늘어난 수,\n'
                    '# counted_earlier = 앞 사건에 이미 셈), 사건 앞뒤 원본은 event-<frame>.bin.zst (zstd -d)\n'
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
        ts = stats['frame']['timestamp_us']  # 0: 빈 화면 (200 ms 동안 프레임 없음)
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
        if current and (dump['events'] or dump['state'] != state
                        or (state == 'recording' and time.monotonic() >= next_status)):
            events = record(dump, stats['stream'], base, started, events)
            state, next_status = dump['state'], time.monotonic() + 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--directory', type=Path, default=Path('/dev/shm/nrv-dvs-live'))
    ap.add_argument('--bind', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--no-http', action='store_true', help='화면 서버 없이 snapshot.bin 만 갱신')
    ap.add_argument('--control', type=Path, required=True, help='dvs_live 제어 FIFO (녹화 시작/정지)')
    ap.add_argument('--recordings', type=Path, default=Path.home()/'recordings',
                    help='녹화 저장 폴더 기본값')
    args = ap.parse_args()
    # 녹화 설정 기본값: 웹 화면의 처음 값이자, 값을 빼고 보낸 start 요청(./dvs 자동 녹화)에 씀
    defaults = {'base': str(args.recordings), 'rotate': False, 'file_gib': 4, 'files': 16}
    page = PAGE.replace(b'@DEFAULTS@', json.dumps(defaults).encode())
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
            if route == '/':
                body, kind = page, 'text/html; charset=utf-8'
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
            {"action": "start", "base", "rotate", "file_gib", "files"} (뺀 값은 기본값) 를
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
                    if size < 1 or (request['rotate'] and files < 1):
                        raise ValueError('파일 크기와 파일 개수는 1 이상이어야 합니다')
                    folder = base/datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
                    line = f'start\t{folder}\t{size << 30}\t{files}'
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
            받아 쌓아 두고 고르게 그림. gap=<us>: after 화면보다 센서 시각으로 이만큼 이상 뒤인
            화면만 (브라우저가 모니터 주기를 넘겨 받지 않게, 프리뷰 fps > 모니터 Hz 일 때 솎음).
            최신 화면보다 BEHIND_LIMIT_US 넘게 뒤처진 화면(브라우저가 못 따라옴)은 건너뛰고,
            고를 화면이 없거나 after < 0 (처음) 이면 최신 화면. X-Lag-Us: 보낸 화면이 최신
            화면보다 뒤처진 센서 시각 (브라우저가 화면 지연을 계산)."""
            query = parse_qs(urlsplit(self.path).query)
            after = int(query.get('after', ['-1'])[0])
            gap = int(query.get('gap', ['0'])[0])
            with latest:
                latest.wait_for(lambda: latest.id != after, timeout=1)
                frame, ident, ts = latest.frame, latest.id, latest.ts
                if after >= 0:
                    base = next((t for i, t, _ in latest.recent if i == after), 0)
                    for i, t, f in latest.recent:
                        if i <= after or (t and base and (t - base) % WRAP_US < gap):
                            continue
                        if not t or not latest.ts or (latest.ts - t) % WRAP_US <= BEHIND_LIMIT_US:
                            frame, ident, ts = f, i, t
                            break
                lag = (latest.ts - ts) % WRAP_US if ts and latest.ts else 0
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
        server = ThreadingHTTPServer((args.bind, args.port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f'NRV 뷰어 http://{args.bind}:{args.port}', flush=True)
    receive(args.directory, latest)  # dvs_live 가 끝나면(EOF) 뷰어도 끝남

if __name__ == '__main__':
    main()
