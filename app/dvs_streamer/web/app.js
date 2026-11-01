// /frame?after=ID: ID 다음의 최신 화면 하나 [u32 BE JSON 길이][JSON][2비트 화소 172800 B], 번호는 X-Frame-Id.
// 그린 뒤에 다음 화면을 요청하므로(best-effort) 브라우저는 그릴 수 있는 만큼만 받는다.
const W = 960,
  H = 720,
  PACKED = (W * H) / 4,
  canvas = document.querySelector("#view"),
  ctx = canvas.getContext("2d"),
  image = ctx.createImageData(W, H),
  rgba = new Uint32Array(image.data.buffer);
const COLORS = new Uint32Array([0xff808080, 0xffffffff, 0xff000000, 0xff808080]);
const MODES = ["노이즈 제거 끔", "노이즈 제거 k=1", "노이즈 제거 k=2"],
  idx = new Uint8Array(W * H),
  near = new Uint8Array(W * H);
let denoise = 1,
  paused = false,
  flip = false,
  flipX = false,
  polarity = 0,
  displayRoi = [0, W - 1, 0, H - 1],
  latest = null,
  shown = 0,
  frames = 0,
  bytes = 0,
  previous = null,
  periodSum = 0,
  periodCount = 0;
// 녹화: 설정은 시작할 때만 보냄. 녹화 상태는 화면 JSON 의 dump (statsLoop 에서 표시)
const rec = (id) => document.getElementById(id);
// 녹화 설정 기본값 (viewer.py 의 defaults)
fetch("/defaults.json")
  .then((r) => r.json())
  .then((d) => {
    rec("recbase").value = d.base;
    rec("recrotate").checked = d.rotate;
    rec("recsize").value = d.file_gib;
    rec("recfiles").value = d.files;
    rec("recfiles").disabled = !d.rotate;
  });
rec("recrotate").onchange = (e) => {
  rec("recfiles").disabled = !e.target.checked;
};
rec("record").onclick = async () => {
  const recording = latest && latest.stats.dump.state === "recording";
  const body = recording
    ? { action: "stop" }
    : {
        action: "start",
        base: rec("recbase").value,
        rotate: rec("recrotate").checked,
        file_gib: Number(rec("recsize").value),
        files: Number(rec("recfiles").value),
      };
  put("recmsg", "");
  try {
    const r = await fetch("/record", { method: "POST", body: JSON.stringify(body) });
    if (!r.ok) put("recmsg", (await r.json()).error);
  } catch (e) {
    put("recmsg", "서버에 연결하지 못했습니다");
  }
};
rec("quit").onclick = async () => {
  if (!confirm("수신기와 웹 서버를 끝낼까요? 녹화 중이면 녹화도 끝납니다. 다시 켜려면 배포 폴더에서 python3 run.py start를 실행하세요."))
    return;
  try {
    const r = await fetch("/record", { method: "POST", body: JSON.stringify({ action: "quit" }) });
    if (!r.ok) put("recmsg", (await r.json()).error);
  } catch (e) {
    put("recmsg", "서버에 연결하지 못했습니다");
  }
};
// 프리뷰 fps: 화면 합성 간격(RAW2는host 표시시각, 0은센서 프레임 하나씩).
rec("preview").onchange = async (e) => {
  const fps = Number(e.target.value);
  try {
    const r = await fetch("/record", {
      method: "POST",
      body: JSON.stringify({ action: "preview", fps }),
    });
    if (!r.ok) put("recmsg", (await r.json()).error);
  } catch (err) {
    put("recmsg", "서버에 연결하지 못했습니다");
  }
};
document.querySelector("#pause").onclick = (e) => {
  paused = !paused;
  e.target.textContent = paused ? "화면 다시 보기" : "화면 일시정지";
};
document.querySelector("#flip").onclick = () => {
  flip = !flip;
  if (latest) latest.dirty = true;
};
document.querySelector("#denoise").onclick = (e) => {
  denoise = (denoise + 1) % MODES.length;
  e.target.textContent = MODES[denoise];
  if (!denoise) put("removed", "");
  if (latest) latest.dirty = true;
};
// Browser-only controls shared by the main toolbar and settings dialog.
// Menu semantics follow the earlier hdmi_controls.py Image / Event and Display ROI groups.
window.dvsDisplay = {
  get: () => ({ paused, flipY: flip, flipX, polarity, denoise, roi: [...displayRoi] }),
  set(options) {
    if (typeof options.paused === "boolean") paused = options.paused;
    if (typeof options.flipY === "boolean") flip = options.flipY;
    if (typeof options.flipX === "boolean") flipX = options.flipX;
    if ([0, 1, 2].includes(options.polarity)) polarity = options.polarity;
    if ([0, 1, 2].includes(options.denoise)) denoise = options.denoise;
    if (Array.isArray(options.roi) && options.roi.length === 4 && options.roi.every(Number.isInteger)) {
      const [x0, x1, y0, y1] = options.roi;
      if (x0 >= 0 && x0 <= x1 && x1 < W && y0 >= 0 && y0 <= y1 && y1 < H) displayRoi = [...options.roi];
    }
    rec("pause").textContent = paused ? "화면 다시 보기" : "화면 일시정지";
    rec("denoise").textContent = MODES[denoise];
    if (!denoise) rec("removed").textContent = "";
    if (latest) latest.dirty = true;
  },
};
const put = (id, value) => (document.getElementById(id).textContent = value);
// 노이즈 제거. k=1/2 (denoise 1/2): 이벤트 화소마다 주변 8칸의 near 를 1씩 올린 뒤 near<k 인 이벤트는 배경.
// 가장자리는 칸 밖을 배경으로 봄.
// 지터 버퍼: 받은 화면을 queue 에 쌓아 두고, 화면 갱신(requestAnimationFrame = 수직동기)마다 재생 시각
// playUs(서버표시host monotonic시각)까지의 화면 중 마지막 것을 그림. 최신 화면보다 delayUs 뒤에서 실제 시간 속도로
// 진행. 화면은 원본 버퍼(약 0.1 s)마다 몰려 오므로 delayUs = 최근 2초 동안 몰려 온 묶음 사이 간격(30 ms 넘게
// 끊긴 것)의 중앙값 x 1.1, 30~200 ms 로 제한 (실시간성 우선: 더 길게 끊기면 그동안 마지막 화면을 유지).
let lagSum = 0,
  lagMax = 0,
  lagCount = 0,
  queue = [],
  newestUs = null,
  playUs = null,
  delayUs = 150000,
  lastTick = null,
  lastArrival = 0,
  gaps = [],
  ticks = [],
  sessionPid = null;
function arrive(screen) {
  const now = performance.now();
  if (sessionPid !== screen.stats.session_pid) {
    sessionPid = screen.stats.session_pid;
    queue = []; newestUs = playUs = lastTick = null;
    lastArrival = 0; gaps = []; previous = null;
    periodSum = 0; periodCount = 0;
  }
  // 세션 초기화 뒤 새 표본을 누적해 이전 실행의 FPS가 섞이지 않게 한다.
  const f = screen.stats.frame;
  if (f && f.period_us > 0) {
    periodSum += f.period_us;
    periodCount++;
  }
  // 서버절대시각과performance.now()를 빼지 않는다. 재생은 도착후 경과만 사용.
  // 센서시각0/역행도 유효한화면일수 있으므로 빈화면 sentinel로 쓰지 않는다.
  screen.ts = (screen.stats.display_timestamp_us !== undefined ? screen.stats.display_timestamp_us : Math.round(screen.stats.monotonic * 1e6));
  if (lastArrival && now - lastArrival > 30) gaps.push([now, now - lastArrival]);
  lastArrival = now;
  gaps = gaps.filter((g) => now - g[0] < 2000);
  if (gaps.length) {
    const m = gaps.map((g) => g[1]).sort((x, y) => x - y)[gaps.length >> 1];
    delayUs = Math.min(200000, Math.max(30000, 1100 * m));
  }
  queue.push(screen);
  if (queue.length > 512) queue.shift();
  newestUs = screen.ts;
}
function tick(now) {
  if (lastTick !== null) {
    ticks.push(now - lastTick);
    if (ticks.length > 120) ticks.shift();
    if (playUs !== null) playUs += (now - lastTick) * 1000;
  }
  lastTick = now;
  if (newestUs === null) return;
  if (playUs === null || newestUs - playUs > delayUs + 100000) playUs = newestUs - delayUs;
  else if (playUs > newestUs) playUs = newestUs;
  let pick = null;
  while (queue.length && queue[0].ts <= playUs) pick = queue.shift();
  // 화면 지연 = 서버에서 이미 뒤처졌던 만큼 + 브라우저에 도착한 뒤 그리기까지 (dvs_live 가 화면을 낸 뒤 기준)
  if (pick) {
    pick.dirty = true;
    latest = pick;
    const l = pick.lag / 1000 + (now - pick.arrival);
    lagSum += l;
    lagMax = Math.max(lagMax, l);
    lagCount++;
  }
}
// 노이즈 제거가 꺼져 있으면 바이트 하나(화소 4개) -> 색 4개 표(QUAD)로 바로 칠함 (장비 Firefox 부담 줄임)
const QUAD = new Uint32Array(1024);
for (let b = 0; b < 256; b++)
  for (let j = 0; j < 4; j++) QUAD[b * 4 + j] = COLORS[(b >> (2 * j)) & 3];
function draw(now) {
  tick(now);
  const fullRoi = displayRoi[0] === 0 && displayRoi[1] === W - 1 && displayRoi[2] === 0 && displayRoi[3] === H - 1;
  if (latest && latest.dirty && !paused && !denoise && !flipX && !polarity && fullRoi) {
    const px = latest.pixels;
    for (let y = 0; y < H; y++) {
      const row = (flip ? H - 1 - y : y) * W,
        src = (y * W) / 4;
      for (let q = 0; q < W / 4; q++) {
        const b = px[src + q] * 4,
          o = row + q * 4;
        rgba[o] = QUAD[b];
        rgba[o + 1] = QUAD[b + 1];
        rgba[o + 2] = QUAD[b + 2];
        rgba[o + 3] = QUAD[b + 3];
      }
    }
    ctx.putImageData(image, 0, 0);
    latest.dirty = false;
    ++shown;
  } else if (latest && latest.dirty && !paused) {
    const px = latest.pixels;
    for (let i = 0; i < PACKED; i++) {
      const b = px[i],
        o = i * 4;
      idx[o] = b & 3;
      idx[o + 1] = (b >> 2) & 3;
      idx[o + 2] = (b >> 4) & 3;
      idx[o + 3] = b >> 6;
    }
    let active = 0,
      kept = 0;
    if (denoise) {
      near.fill(0);
      for (let y = 0; y < H; y++)
        for (let x = 0; x < W; x++) {
          const v = idx[y * W + x];
          if (!v) continue;
          ++active;
          for (let dy = -1; dy <= 1; dy++) {
            const yy = y + dy;
            if (yy < 0 || yy >= H) continue;
            for (let dx = -1; dx <= 1; dx++) {
              const xx = x + dx;
              if ((dx || dy) && xx >= 0 && xx < W) ++near[yy * W + xx];
            }
          }
        }
    }
    for (let y = 0; y < H; y++) {
      const row = (flip ? H - 1 - y : y) * W;
      for (let x = 0; x < W; x++) {
        const i = y * W + x;
        let v = idx[i];
        if (v && denoise && near[i] < denoise) v = 0;
        if ((polarity && v !== polarity) || x < displayRoi[0] || x > displayRoi[1] || y < displayRoi[2] || y > displayRoi[3]) v = 0;
        if (v) ++kept;
        rgba[row + (flipX ? W - 1 - x : x)] = COLORS[v];
      }
    }
    if (denoise)
      put("removed", `원본 대비 표시 화소 ${active ? ((kept / active) * 100).toFixed(0) : 0}%.`);
    ctx.putImageData(image, 0, 0);
    latest.dirty = false;
    ++shown;
  }
  requestAnimationFrame(draw);
}
// 화면을 하나도 건너뛰지 않고 순서대로 받아 지터 버퍼에 넣음 (서버가 이미 지나간 것은 최신으로 건너뜀)
async function streamLoop() {
  const text = new TextDecoder();
  let id = -1;
  for (;;) {
    try {
      const period = ticks.length > 30 ? [...ticks].sort((a, b) => a - b)[ticks.length >> 1] : 0;
      const r = await fetch(`/frame?after=${id}&gap=${Math.round(period * 900)}`, {
        cache: "no-store",
      });
      if (!r.ok) throw r.status;
      id = Number(r.headers.get("X-Frame-Id"));
      const buf = new Uint8Array(await r.arrayBuffer());
      bytes += buf.length;
      const n = new DataView(buf.buffer).getUint32(0);
      const screen = {
        stats: JSON.parse(text.decode(buf.subarray(4, 4 + n))),
        pixels: buf.subarray(4 + n),
        lag: Number(r.headers.get("X-Lag-Us")),
        arrival: performance.now(),
      };
      ++frames;
      arrive(screen);
    } catch (e) {
      put("status", "연결 끊김");
      await new Promise((r) => setTimeout(r, 500));
    }
  }
}
function statsLoop() {
  if (latest) {
    const s = latest.stats,
      healthy = s.state === "live" && s.last_good_age < 2;
    put(
      "status",
      healthy ? (paused ? "수신 중 · 화면 정지" : "실시간 수신 중") : "수신 대기 / 정지",
    );
    document.getElementById("status").classList.toggle("bad", !healthy);
    put("buffers", s.buffers.toLocaleString());
    put("errors", (s.raw_pipe_dropped !== undefined ? s.raw_pipe_dropped : s.skipped).toLocaleString());
    put("elapsed", s.seconds.toFixed(1) + " s");
    // 센서 프레임 속도: 지난 1초 동안 받은 화면들의 period_us(그 프레임과 바로 앞 센서 프레임의 시작 간격) 평균
    put("frameevents", s.frame ? s.frame.events.toLocaleString() : "—");
    if (periodCount) {
      const period = periodSum / periodCount;
      put("sensorfps", (1e6 / period).toFixed(0) + (s.format === "raw2" ? " FPS (후보)" : " FPS"));
      put("period", `${s.format === "raw2" ? "미교정 시각 후보 간격" : "센서 프레임 간격"} ${period.toFixed(0)} µs (화면 ${periodCount}장 평균)`);
      periodSum = 0;
      periodCount = 0;
    } else {
      put("sensorfps", "시각 미확정");
      put("period", "유효한 연속 프레임 간격 없음");
    }
    if (previous && s.seconds > previous.stats.seconds) {
      const dt = s.seconds - previous.stats.seconds;
      put(
        "period",
        document.getElementById("period").textContent.split(" · ")[0] +
          ` · 해석 ${((s.frames - previous.stats.frames) / dt).toFixed(0)} 프레임/s`,
      );
      put("rate", ((s.events - previous.stats.events) / dt / 1e6).toFixed(2) + " M");
      put(
        "bandwidth",
        ((s.stream.bytes - previous.stats.stream.bytes) / dt / 1048576).toFixed(1) + " MiB/s",
      );
    }
    put("displayrate", (shown - (previous ? previous.shown : 0)).toFixed(0) + " FPS");
    put(
      "recvrate",
      `받음 ${frames - (previous ? previous.frames : 0)} 화면/s · ${((bytes - (previous ? previous.bytes : 0)) / 1048576).toFixed(1)} MiB/s`,
    );
    put(
      "detail",
      `형식 ${s.format === "raw2" ? "RAW2" : "group AER"} · 받은 버퍼 ${s.stream.buffers.toLocaleString()} · 수신 오류 ${s.stream.error_buffers !== undefined ? s.stream.error_buffers : "—"} · 수신 순번 누락 ${s.stream.dropped} · 드라이버 버퍼 고갈 ${s.stream.starved} (${(s.stream.starved_ms / 1000).toFixed(1)} s) · 해석 초기화 ${s.discontinuities} · 프레임 번호 이상 ${s.frame_gaps} · 형식 오류 ${s.invalid + s.unknown} · 959열 ${s.raw2_short_frames !== undefined ? s.raw2_short_frames : "—"} · 시각 전이 없음 ${s.raw2_timestamp_fallbacks !== undefined ? s.raw2_timestamp_fallbacks : "—"} · 시각 재변화/단일word ${s.raw2_timestamp_ambiguous !== undefined ? s.raw2_timestamp_ambiguous : "—"} · 후보 역행/동일 ${s.raw2_timestamp_order_errors !== undefined ? s.raw2_timestamp_order_errors : "—"} · 화면용 프레임 생략 ${s.screen_pipe_dropped !== undefined ? s.screen_pipe_dropped : "—"} · 화면 ${s.snapshot_id}${s.dump ? ` · 저장 ${s.dump.state} ${s.dump.frames.toLocaleString()} 버퍼` : ""}`,
    );
    if (document.activeElement !== rec("preview"))
      rec("preview").value = String(s.preview_us ? Math.round(1e6 / s.preview_us) : 0);
    const hz = ticks.length ? 1000 / [...ticks].sort((a, b) => a - b)[ticks.length >> 1] : 0;
    put(
      "merged",
      `화면당 센서 프레임 ${s.merged}장 · 화면 지연 평균 ${lagCount ? (lagSum / lagCount).toFixed(0) : "—"} ms (최대 ${lagMax.toFixed(0)}, 고르게 하려고 기다리는 몫 ${(delayUs / 1000).toFixed(0)}) · 모니터 ${hz.toFixed(0)} Hz`,
    );
    lagSum = lagMax = lagCount = 0;
    const d = s.dump,
      recording = d.state === "recording";
    put(
      "recstate",
      { idle: "녹화 안 함", recording: "녹화 중", stopped: "정지됨", failed: "실패" }[d.state],
    );
    put(
      "recdetail",
      d.dir
        ? `${d.dir} · 받은 ${(d.bytes / 2 ** 30).toFixed(2)} GiB → 저장 ${(d.written / 2 ** 30).toFixed(2)} GiB${d.written ? ` (${(d.bytes / d.written).toFixed(2)}배)` : ""} · 파일 ${(d.file_bytes / 2 ** 30).toFixed(0)} GiB × ${d.files ? d.files + "개 돌려쓰기" : "제한 없음"}${d.error ? " · " + d.error : ""}`
        : "",
    );
    rec("record").textContent = recording ? "녹화 정지" : "녹화 시작";
    rec("record").style.color = recording ? "#ff6b6b" : "";
    rec("recstate").style.color = recording ? "#5aa9ff" : "";
    for (const i of ["recbase", "recrotate", "recsize"]) rec(i).disabled = recording;
    rec("recfiles").disabled = recording || !rec("recrotate").checked;
    previous = { stats: s, shown, frames, bytes };
  }
  setTimeout(statsLoop, 1000);
}
requestAnimationFrame(draw);
streamLoop();
statsLoop();
