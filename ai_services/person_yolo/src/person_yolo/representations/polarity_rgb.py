import numpy as np
from ..contracts import HEIGHT, WIDTH


class PolarityRGB:
    name = "polarity_rgb_v1"
    # RGB order: gray background, ON red, OFF blue; invalid is not an event.
    palette = np.array([[128, 128, 128], [255, 0, 0], [0, 0, 255], [128, 128, 128]], dtype=np.uint8)

    def transform(self, frame):
        values = frame.pixels
        if values.shape != (HEIGHT, WIDTH) or values.dtype != np.uint8 or np.any(values > 3):
            raise ValueError("expected 720x960 uint8 preview indices in 0..3")
        return np.ascontiguousarray(self.palette[values])
