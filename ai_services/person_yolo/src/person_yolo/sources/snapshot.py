"""Standalone preview reader, matching viewer.py's documented packed layout.

This is not another RAW2 decoder. One open fd pins the old inode if the viewer
atomically replaces snapshot.bin; in-place writes are rejected by fstat checks.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import numpy as np
from ..contracts import HEIGHT, WIDTH, PACKED_BYTES, PreviewFrame

MAX_JSON_BYTES = 65536
MAX_FILE_BYTES = 4 + MAX_JSON_BYTES + PACKED_BYTES


class SnapshotError(ValueError):
    pass


def _nonfinite(value):
    raise SnapshotError(f"non-finite JSON value: {value}")


def _validate_metadata(meta):
    if not isinstance(meta, dict) or meta.get("format") != "raw2":
        raise SnapshotError("expected JSON object with format=raw2; input is a snapshot preview")
    for key in ("snapshot_id", "session_pid", "preview_us", "display_timestamp_us"):
        value = meta.get(key)
        if key in meta and (type(value) is not int or value < 0):
            raise SnapshotError(f"{key} must be a nonnegative integer")
    for key in ("monotonic", "last_good_age"):
        value = meta.get(key)
        if key in meta and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise SnapshotError(f"{key} must be a finite nonnegative number")
    for key in ("frame", "stream"):
        if key in meta and not isinstance(meta[key], dict):
            raise SnapshotError(f"{key} must be an object")
    candidate = meta.get("frame", {}).get("timestamp_us")
    if candidate is not None and (type(candidate) is not int or candidate < 0):
        raise SnapshotError("frame.timestamp_us must be a nonnegative integer")
    if "has_frame" in meta and type(meta["has_frame"]) is not bool:
        raise SnapshotError("has_frame must be boolean")
    if "display_clock" in meta and meta["display_clock"] != "host_monotonic":
        raise SnapshotError("unsupported display clock; cannot label it host monotonic")
    if meta.get("width", WIDTH) != WIDTH or meta.get("height", HEIGHT) != HEIGHT:
        raise SnapshotError("only 960x720 previews are supported")


def parse_snapshot(data: bytes, input_id: str = "snapshot") -> PreviewFrame:
    if len(data) < 4:
        raise SnapshotError("truncated 4-byte big-endian header")
    size = struct.unpack("!I", data[:4])[0]
    if not 1 <= size <= MAX_JSON_BYTES:
        raise SnapshotError("JSON length outside 1..65536 bytes")
    if len(data) != 4 + size + PACKED_BYTES:
        raise SnapshotError("snapshot length mismatch: expected JSON + 172800 packed bytes")
    try:
        meta = json.loads(data[4:4 + size].decode("utf-8"), parse_constant=_nonfinite)
    except (ValueError, UnicodeError) as exc:
        raise SnapshotError(f"invalid snapshot JSON: {exc}") from exc
    _validate_metadata(meta)
    packed = np.frombuffer(data, dtype=np.uint8, count=PACKED_BYTES, offset=4 + size)
    pixels = np.empty(WIDTH * HEIGHT, dtype=np.uint8)
    for shift in range(4):
        pixels[shift::4] = (packed >> (2 * shift)) & 3
    warnings = ["composited_preview_not_raw2_bytes", "sensor_timestamp_uncalibrated"]
    invalid = int(np.count_nonzero(pixels == 3))
    if invalid:
        warnings.append(f"invalid_preview_pixels:{invalid};mapped_to_background")
    if meta.get("state") != "live":
        warnings.append("receiver_not_live")
    if meta.get("has_frame") is False or meta.get("last_good_age", 0) >= 2:
        warnings.append("no_recent_sensor_frame")
    if "has_frame" not in meta or "last_good_age" not in meta:
        warnings.append("input_liveness_metadata_missing")
    for key in ("raw2_short_frames", "raw2_timestamp_fallbacks", "raw2_timestamp_ambiguous",
                "raw2_timestamp_order_errors", "invalid", "unknown", "frame_gaps", "discontinuities"):
        if meta.get(key):
            warnings.append(f"receiver_cumulative_{key}:{meta[key]}")
    return PreviewFrame(input_id, "snapshot_preview", hashlib.sha256(data).hexdigest(),
                        pixels.reshape(HEIGHT, WIDTH), meta, warnings)


def read_snapshot_file(path: Path) -> PreviewFrame:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SnapshotError("snapshot must be a regular file")
        data = source.read(MAX_FILE_BYTES + 1)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SnapshotError("snapshot was modified in place during read; retry")
    return parse_snapshot(data, path.name)


class SnapshotSource:
    """Read each supplied file once. No polling, receiver imports or IPC writes."""
    def __init__(self, paths):
        self.paths = [Path(path) for path in paths]

    def __iter__(self):
        previous = None
        for path in self.paths:
            frame = read_snapshot_file(path)
            current = (frame.metadata.get("session_pid"), frame.metadata.get("snapshot_id"))
            if previous and None not in current and None not in previous:
                if current[0] != previous[0] or current[1] < previous[1]:
                    frame.discontinuity = True
                    frame.warnings.append("preview_session_or_sequence_reset")
                elif current[1] == previous[1]:
                    frame.warnings.append("duplicate_preview")
                    frame.unobserved_previews = 0
                else:
                    frame.unobserved_previews = current[1] - previous[1] - 1
            previous = current
            yield frame
