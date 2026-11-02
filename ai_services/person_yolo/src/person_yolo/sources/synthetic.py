"""Deterministic patterns for contract tests, never person-detection evidence."""
import hashlib
import numpy as np
from ..contracts import HEIGHT, WIDTH, PreviewFrame


class SyntheticSource:
    PATTERNS = ("empty", "on", "off", "moving", "invalid")

    def __init__(self, pattern="moving", frames=3):
        if pattern not in self.PATTERNS or type(frames) is not int or not 1 <= frames <= 10000:
            raise ValueError("unsupported pattern or frame count (1..10000)")
        self.pattern, self.frames = pattern, frames

    def __iter__(self):
        for i in range(self.frames):
            pixels = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
            if self.pattern in ("on", "off", "invalid"):
                pixels[0, 0] = {"on": 1, "off": 2, "invalid": 3}[self.pattern]
                pixels[-1, -1] = pixels[0, 0]
            elif self.pattern == "moving":
                x = (i * 20) % (WIDTH - 80)
                pixels[250:450, x:x + 40] = 1
                pixels[250:450, x + 40:x + 80] = 2
            meta = {"format": "raw2", "snapshot_id": i, "session_pid": 0,
                    "state": "synthetic", "preview_us": 16666, "has_frame": True}
            warnings = ["synthetic_not_sensor_data", "no_ground_truth_person_labels"]
            if self.pattern == "invalid":
                warnings.append("invalid_preview_pixels:2;mapped_to_background")
            yield PreviewFrame(f"synthetic:{self.pattern}:{i}", "synthetic",
                               hashlib.sha256(pixels.tobytes()).hexdigest(), pixels, meta,
                               warnings, 0 if i else None)
