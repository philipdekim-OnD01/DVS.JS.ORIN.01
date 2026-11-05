"""Offline decoded-DVS video/snapshot postprocessing. No device or network input."""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import platform
import threading
import time
import cv2
import numpy as np
from .config import Config, load_config
from .detectors import YoloNanoDetector
from .sources import SnapshotSource
from .representations import PolarityRGB

VIDEO_SUFFIXES = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def inputs(paths, snapshot_fps):
    paths = [Path(p).resolve() for p in paths]
    if not paths or any(not p.is_file() for p in paths):
        raise ValueError('Choose existing local video or saved snapshot files')
    if not math.isfinite(snapshot_fps) or not 0 < snapshot_fps <= 240:
        raise ValueError('Snapshot playback FPS must be in (0, 240]')
    if all(p.suffix.lower() == '.bin' for p in paths):
        return paths, 'snapshot_preview_sequence'
    if len(paths) == 1 and paths[0].suffix.lower() in VIDEO_SUFFIXES:
        return paths, 'decoded_video'
    raise ValueError('Use one MP4/AVI/MOV/MKV/WebM or a sequence of saved snapshot .bin files. RAW2 .zst is not supported.')


def frames(paths, kind, playback_fps, provenance):
    if kind == 'snapshot_preview_sequence':
        for i, frame in enumerate(SnapshotSource(paths)):
            m = frame.metadata
            usable = (m.get('state') == 'live' and m.get('has_frame') is True
                      and m.get('last_good_age', 2) < 2 and 'duplicate_preview' not in frame.warnings)
            yield PolarityRGB().transform(frame), playback_fps, dict(
                frame_index=i, input_id=frame.input_id, source_sha256=frame.source_sha256,
                source_frame_id=m.get('snapshot_id'), timestamp_seconds=i/playback_fps,
                timestamp_quality='user_selected_playback_time_not_sensor_time',
                sensor_timestamp_candidate_us=m.get('frame', {}).get('timestamp_us'),
                decode_warnings=frame.warnings, input_discontinuity=frame.discontinuity,
                unobserved_preview_ids=frame.unobserved_previews, usable=usable,
                representation='polarity_rgb_v1')
        return
    cap = cv2.VideoCapture(str(paths[0]))
    try:
        if not cap.isOpened(): raise ValueError('Video cannot be opened by the installed codec backend')
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 0 < fps <= 240:
            raise ValueError('Video has invalid/unsupported FPS metadata; re-export a constant-frame-rate video')
        expected = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        expected = round(expected) if math.isfinite(expected) and expected > 0 else None
        provenance[0].update(reported_frames=expected, nominal_fps=fps,
                             decode_backend=cap.getBackendName())
        i = 0
        while True:
            ok, bgr = cap.read()
            if not ok: break
            yield cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), fps, dict(
                frame_index=i, input_id=paths[0].name, source_sha256=provenance[0]['sha256'],
                source_frame_id=i, timestamp_seconds=i/fps,
                timestamp_quality='nominal_video_fps_not_sensor_time',
                sensor_timestamp_candidate_us=None, decode_warnings=['decoded_video_not_raw2',
                'sensor_provenance_and_event_timestamps_not_recoverable_from_video'],
                input_discontinuity=None, unobserved_preview_ids=None, usable=True,
                representation='decoded_video_rgb_unchanged')
            i += 1
        if i == 0: raise ValueError('No decodable video frames')
        if expected is not None and i != expected:
            raise ValueError(f'Decoded {i} frames, container reported {expected}; input completeness unverified')
    finally:
        cap.release()


def annotate(rgb, detections, label):
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    for det in detections:
        x1, y1, x2, y2 = det['bbox_xyxy']
        x1, x2 = [max(0, min(w-1, round(v))) for v in (x1, x2)]
        y1, y2 = [max(0, min(h-1, round(v))) for v in (y1, y2)]
        cv2.rectangle(bgr, (x1, y1), (x2, y2), (0, 255, 130), 2)
        cv2.putText(bgr, f"person {det['score']:.2f}", (x1, max(18, y1-6)),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 130), 1)
    cv2.putText(bgr, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 220, 255), 2)
    return bgr


def process(paths, output, weights=None, config=None, snapshot_fps=4., dry_run=False,
            cancel=None, progress=None):
    """Create a new job directory. Partial outputs remain explicitly partial on failure/cancel."""
    cfg = config or Config()
    paths, kind = inputs(paths, snapshot_fps)
    output = Path(output).resolve()
    if output.exists(): raise ValueError('Output directory already exists; choose a new job directory')
    output.mkdir(parents=True, exist_ok=False)
    cancel = cancel or threading.Event()
    started = time.perf_counter()
    report = dict(schema_version=1, state='running', source_kind=kind, raw2_direct=False,
                  inference_mode='dry_run' if dry_run else 'yolo', config=asdict(cfg),
                  processed_frames=0, inferred_frames=0, skipped_frames=0,
                  detection_count=0, frames_with_person=0, unique_person_count=None,
                  accuracy_validated=False, inputs=[], model=None,
                  execution=dict(platform=platform.platform(), python=platform.python_version(),
                                 opencv=cv2.__version__),
                  audio_preserved=False, timing='CFR playback; not sensor latency')
    writer = iterator = None
    video_path = output/'annotated.partial.mp4'
    json_path = output/'detections.partial.jsonl'
    def save_report():
        tmp = output/'summary.tmp'
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        tmp.replace(output/'summary.json')
    save_report()
    try:
        for p in paths:
            if cancel.is_set(): break
            s = p.stat()
            report['inputs'].append(dict(name=p.name, bytes=s.st_size, sha256=digest(p), mtime_ns=s.st_mtime_ns))
        if cancel.is_set():
            report['state'] = 'cancelled'
            return report
        if not dry_run and weights is None: raise ValueError('Local verified YOLO weights required; use dry-run to validate input only')
        detector = None if dry_run else YoloNanoDetector(weights, cfg)
        size = None
        iterator = frames(paths, kind, snapshot_fps, report['inputs'])
        with json_path.open('x', encoding='utf-8') as log:
            for rgb, fps, info in iterator:
                if cancel.is_set(): break
                h, w = rgb.shape[:2]
                # MP4 encoders may truncate odd dimensions. Fail explicitly instead.
                if w % 2 or h % 2: raise ValueError('MP4 output requires even width and height; input is not silently cropped')
                if size is None:
                    size = (w, h)
                    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*'mp4v'), fps, size)
                    if not writer.isOpened(): raise RuntimeError('MP4 encoder unavailable')
                    report.update(width=w, height=h, output_fps=fps, encoder='mp4v')
                if size != (w, h): raise ValueError('Frame dimensions changed within the input')
                begin = time.perf_counter()
                ran = detector is not None and info['usable']
                found = detector.detect(rgb) if ran else []
                elapsed = 1000*(time.perf_counter()-begin)
                status = 'dry_run' if dry_run else 'inference_completed' if ran else 'skipped_input'
                report['model'] = detector.metadata if detector else None
                row = dict(info, source_kind=kind, raw2_direct=False, status=status,
                           inference_ran=ran, width=w, height=h, detections=found,
                           person_count=len(found), predict_ms=elapsed if ran else None,
                           model=report['model'])
                log.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n'); log.flush()
                label = f'{status} | frame {info["frame_index"]} | person boxes {len(found)}'
                if not info['usable']: rgb = np.full_like(rgb, 128)
                picture = annotate(rgb, found, label)
                writer.write(picture)
                report['processed_frames'] += 1
                report['inferred_frames'] += int(ran)
                report['skipped_frames'] += int(status == 'skipped_input')
                report['detection_count'] += len(found)
                report['frames_with_person'] += int(bool(found))
                if progress:
                    # Bound preview payload; full-resolution annotated output goes to disk.
                    scale = min(1., 800/w, 500/h)
                    thumbnail = cv2.resize(picture, (max(1, round(w*scale)), max(1, round(h*scale))))
                    progress(dict(frames=report['processed_frames'], boxes=report['detection_count'],
                                  rgb=cv2.cvtColor(thumbnail, cv2.COLOR_BGR2RGB)))
        writer.release() if writer else None
        writer = None
        if cancel.is_set(): report['state'] = 'cancelled'
        else:
            if not report['processed_frames']: raise ValueError('No frames were processed')
            for p, source in zip(paths, report['inputs']):
                s = p.stat()
                if (s.st_size, s.st_mtime_ns) != (source['bytes'], source['mtime_ns']):
                    raise ValueError('Input file changed during processing; results are incomplete')
            check = cv2.VideoCapture(str(video_path))
            try:
                count = 0
                while True:
                    if cancel.is_set(): break
                    ok, img = check.read()
                    if not ok: break
                    if (img.shape[1], img.shape[0]) != size: raise RuntimeError('Encoded video dimensions changed')
                    count += 1
            finally: check.release()
            if cancel.is_set():
                report['state'] = 'cancelled'
                return report
            if count != report['processed_frames']: raise RuntimeError('Encoded output frame count does not match JSONL')
            video_path.rename(output/'annotated.mp4'); json_path.rename(output/'detections.jsonl')
            report['state'] = 'completed'
            report['outputs'] = {name:dict(sha256=digest(output/name), bytes=(output/name).stat().st_size)
                                 for name in ('annotated.mp4', 'detections.jsonl')}
        return report
    except BaseException as exc:
        report.update(state='cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed', error=str(exc))
        raise
    finally:
        if iterator is not None: iterator.close()
        if writer is not None: writer.release()
        report['wall_seconds'] = time.perf_counter()-started
        save_report()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', nargs='+', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='New job directory')
    p.add_argument('--weights', type=Path)
    p.add_argument('--config', type=Path)
    p.add_argument('--snapshot-fps', type=float, default=4.)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args(argv)
    try:
        r = process(args.input, args.output, args.weights, load_config(args.config), args.snapshot_fps, args.dry_run)
        print(json.dumps({k:v for k,v in r.items() if k not in ('inputs', 'outputs')}, ensure_ascii=False))
        return 0 if r['state']=='completed' else 130
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, RuntimeError, cv2.error) as exc:
        print(f'ERROR: {exc}', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
