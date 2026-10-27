#!/usr/bin/env python3
"""Sensor (image) frame rate from a raw VI dump of nrv_dvs (8192x128 RAW8 buffers).

usage: dvs_fps.py DUMP [--buffer-fps FPS]

Word decoding is the same as app/dvs_live.c (4-byte words):
  b0 bit7 = 1        event group (masks b2, b3 -> popcount = events)
  tag = b0 & 0x7c:   12 = sensor frame end, 8-bit counter ((b1&7)<<5)|(b2>>3)
  cid = (b0 >> 1) & 1
Processed in 16 MiB chunks (memmap), so memory use stays small. Sensor frames
are counted from the frame-end counter with wrap-around; skipped counter values
are reported as 'missing'. Image fps = frames/buffer x buffer fps (pass the fps
that v4l2-ctl printed with --buffer-fps).
"""
import argparse
import numpy as np

BUF = 8192 * 128
CHUNK = 16

ap = argparse.ArgumentParser()
ap.add_argument('dump')
ap.add_argument('--buffer-fps', type=float)
a = ap.parse_args()

raw = np.memmap(a.dump, dtype=np.uint8, mode='r')
nbuf = raw.size // BUF
POP = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.int64)
events = 0
counters = {0: [], 1: []}
per_buffer = []
stamps = []
for start in range(0, nbuf, CHUNK):
    n = min(CHUNK, nbuf - start)
    w = np.asarray(raw[start * BUF:(start + n) * BUF]).reshape(-1, 4)
    b0, b1, b2, b3 = w[:, 0], w[:, 1], w[:, 2], w[:, 3]
    group = (b0 & 0x80) != 0
    events += int(POP[b2[group]].sum() + POP[b3[group]].sum())
    end = (~group) & ((b0 & 0x7c) == 12)
    idx = np.nonzero(end)[0]
    per_buffer.extend(np.bincount(idx // (BUF // 4), minlength=n).tolist())
    cid = (b0[idx] >> 1) & 1
    f = ((b1[idx].astype(np.int32) & 7) << 5) | (b2[idx].astype(np.int32) >> 3)
    for c in (0, 1):
        counters[c].append(f[cid == c])
    # ref (ms, 22 bit) / sub (us, 0..999) timestamp words, one pair per frame
    ts = (~group) & ((b0 & 0x7c) == 8)
    ti = np.nonzero(ts)[0]
    is_sub = (b1[ti] & 0x80) != 0
    tval = np.where(is_sub, ((b2[ti].astype(np.int64) & 3) << 8) | b3[ti],
                    ((b1[ti].astype(np.int64) & 0x3f) << 16) | (b2[ti].astype(np.int64) << 8) | b3[ti])
    stamps.append(np.stack([(b0[ti] >> 1) & 1, is_sub, tval], 1))
    del w

pb = np.array(per_buffer)
print(f'buffers={nbuf}  events={events}  events/buffer={events / max(nbuf, 1):.0f}')
print(f'frame_ends per buffer: mean={pb.mean():.1f} min={pb.min()} max={pb.max()}')
for c in (0, 1):
    f = np.concatenate(counters[c])
    if not f.size:
        continue
    step = np.diff(f) % 256
    frames = int(step.sum()) + 1
    missing = int((step[step != 0] - 1).sum())
    print(f'cid{c}: frame_ends seen={f.size}  sensor frames (counter)={frames}  '
          f'missing={missing}  frames/buffer={frames / nbuf:.1f}')
    st = np.concatenate(stamps)
    st = st[st[:, 0] == c]
    ref, t = None, []
    for _, sub, v in st:            # pair each sub with the preceding ref
        if not sub:
            ref = v
        elif ref is not None:
            t.append(ref * 1000 + v)
    if len(t) > 1:
        d = np.diff(np.array(t, np.int64)) % ((1 << 22) * 1000)
        span = d.sum() / 1e6
        print(f'cid{c}: sensor timestamps (ref=ms, sub=us per setfile 32B1-32B4): '
              f'{len(t)} frames in {span:.4f} s -> IMAGE FPS (timestamp) = {(len(t) - 1) / span:.1f}; '
              f'frame period median={np.median(d):.0f} us min={d.min()} max={d.max()}')
    if a.buffer_fps:
        print(f'cid{c}: IMAGE FPS = {frames / nbuf * a.buffer_fps:.0f}  '
              f'(at {a.buffer_fps:g} buffers/s = {a.buffer_fps * BUF * 8 / 1e6:.0f} Mbps, '
              f'{events / nbuf * a.buffer_fps / 1e6:.1f} M events/s)')
