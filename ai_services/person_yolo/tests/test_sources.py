import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import numpy as np
import pytest
from person_yolo.sources import SnapshotSource, SnapshotError, SyntheticSource, Raw2ZstdSource
from person_yolo.sources import snapshot
from person_yolo.representations import PolarityRGB


def test_unpack_order_edges_and_rgb(snapshot_bytes):
    data = bytearray(snapshot_bytes())
    offset = 4 + struct.unpack("!I", data[:4])[0]
    data[offset] = 0b11100100  # independent known packed-byte oracle: 0,1,2,3
    data[-1] = 0b01100000  # x958=OFF, x959=ON, y719
    frame = snapshot.parse_snapshot(bytes(data))
    assert frame.pixels.shape == (720, 960)
    assert frame.pixels[0, :4].tolist() == [0, 1, 2, 3]
    assert frame.pixels[719, 958:].tolist() == [2, 1]
    rgb = PolarityRGB().transform(frame)
    assert rgb.shape == (720, 960, 3) and rgb.dtype == np.uint8
    assert rgb.flags.c_contiguous
    assert rgb[0, :4].tolist() == [[128]*3, [255, 0, 0], [0, 0, 255], [128]*3]
    assert rgb[719, 959].tolist() == [255, 0, 0]
    assert frame.source_sha256 == hashlib.sha256(data).hexdigest()
    assert "invalid_preview_pixels:1;mapped_to_background" in frame.warnings


@pytest.mark.parametrize("alter", [lambda d: b"", lambda d: d[:3], lambda d: d[:10],
    lambda d: d[:-1], lambda d: d + b"x", lambda d: b"\0\0\0\0" + d[4:],
    lambda d: struct.pack("!I", 65537) + d[4:]])
def test_truncated_short_and_extra(snapshot_bytes, alter):
    with pytest.raises(SnapshotError):
        snapshot.parse_snapshot(alter(snapshot_bytes()))


@pytest.mark.parametrize("meta", [b"[]", b"{", b"\xff", b'{"format":"raw2","monotonic":NaN}',
    b'{"format":"group_aer"}', b'{"format":"raw2","frame":null}'])
def test_invalid_json(meta):
    with pytest.raises(SnapshotError):
        snapshot.parse_snapshot(struct.pack("!I", len(meta)) + meta + bytes(172800))


@pytest.mark.parametrize("overrides", [{"snapshot_id": -1}, {"session_pid": True},
    {"preview_us": "16666"}, {"monotonic": float("inf")}, {"last_good_age": None},
    {"has_frame": "yes"}, {"frame": {"timestamp_us": -1}}, {"width": 959},
    {"height": 719}, {"display_clock": "sensor"}])
def test_metadata_rejected(snapshot_bytes, overrides):
    with pytest.raises(SnapshotError):
        snapshot.parse_snapshot(snapshot_bytes(**overrides))


def test_sequences_are_not_receiver_drop_claims(tmp_path, snapshot_bytes):
    paths = []
    for i, (pid, seq) in enumerate([(10, 4), (10, 7), (10, 7), (11, 1), (11, 0)]):
        path = tmp_path / str(i)
        path.write_bytes(snapshot_bytes(session_pid=pid, snapshot_id=seq))
        paths.append(path)
    frames = list(SnapshotSource(paths))
    assert frames[1].unobserved_previews == 2
    assert "duplicate_preview" in frames[2].warnings
    assert frames[3].discontinuity and frames[4].discontinuity
    assert frames[3].unobserved_previews is None


def test_in_place_change_rejected(tmp_path, snapshot_bytes, monkeypatch):
    path = tmp_path / "snapshot.bin"
    path.write_bytes(snapshot_bytes())
    original = os.fstat
    calls = 0
    def changed(fd):
        nonlocal calls
        calls += 1
        st = original(fd)
        return SimpleNamespace(st_mode=st.st_mode, st_size=st.st_size,
                               st_mtime_ns=st.st_mtime_ns + (calls > 1))
    monkeypatch.setattr(snapshot.os, "fstat", changed)
    with pytest.raises(SnapshotError, match="modified in place"):
        snapshot.read_snapshot_file(path)


@pytest.mark.skipif(os.name == "nt", reason="Windows open-file sharing differs; Linux atomic-replace test")
def test_atomic_replacement_keeps_one_complete_fd(tmp_path, snapshot_bytes, monkeypatch):
    path, new = tmp_path / "snapshot.bin", tmp_path / "snapshot.tmp"
    old = snapshot_bytes(snapshot_id=1)
    path.write_bytes(old)
    new.write_bytes(snapshot_bytes(snapshot_id=2))
    original = os.fstat
    calls = 0
    def replace(fd):
        nonlocal calls
        calls += 1
        st = original(fd)
        if calls == 1:
            os.replace(new, path)
        return st
    monkeypatch.setattr(snapshot.os, "fstat", replace)
    assert snapshot.read_snapshot_file(path).source_sha256 == hashlib.sha256(old).hexdigest()


@pytest.mark.parametrize("pattern", SyntheticSource.PATTERNS)
def test_synthetic_deterministic_and_bounded(pattern):
    a, b = list(SyntheticSource(pattern, 3)), list(SyntheticSource(pattern, 3))
    assert [f.source_sha256 for f in a] == [f.source_sha256 for f in b]
    assert all(f.pixels.shape == (720, 960) for f in a)
    if pattern == "moving":
        assert a[0].source_sha256 != a[1].source_sha256
        assert a[0].pixels[250, 0] == 1 and a[1].pixels[250, 20] == 1
    elif pattern == "empty":
        assert not a[0].pixels.any()
    else:
        assert a[0].pixels[719, 959] == {"on": 1, "off": 2, "invalid": 3}[pattern]


def test_raw2_never_silently_falls_back_to_preview():
    with pytest.raises(NotImplementedError, match="NOT IMPLEMENTED / NOT TESTED"):
        iter(Raw2ZstdSource(Path("dump-0000.bin.zst")))
