import json
import struct
import numpy as np
import pytest


@pytest.fixture
def snapshot_bytes():
    def make(pixels=None, **overrides):
        meta = dict(format="raw2", snapshot_id=1, session_pid=10, state="live",
                    preview_us=16666, has_frame=True, last_good_age=0.01,
                    monotonic=123.456, display_clock="host_monotonic",
                    display_timestamp_us=123456001, frame={"timestamp_us": 42})
        meta.update(overrides)
        if pixels is None:
            pixels = np.zeros((720, 960), dtype=np.uint8)
        groups = pixels.reshape(-1, 4)
        packed = groups[:, 0] | groups[:, 1] << 2 | groups[:, 2] << 4 | groups[:, 3] << 6
        payload = json.dumps(meta).encode()
        return struct.pack("!I", len(payload)) + payload + packed.tobytes()
    return make
