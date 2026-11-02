from dataclasses import asdict, dataclass
import json
import math
import re


@dataclass(frozen=True)
class Config:
    model_name: str = "yolo11n.pt"
    model_sha256: str = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
    ultralytics_version: str = "8.3.228"
    device: str = "cpu"
    imgsz: int = 640
    confidence: float = 0.25
    iou: float = 0.7
    max_det: int = 100
    threads: int = 2

    def __post_init__(self):
        if self.model_name != "yolo11n.pt" or self.ultralytics_version != "8.3.228":
            raise ValueError("P0 pins YOLO11n and ultralytics 8.3.228")
        if not isinstance(self.model_sha256, str) or not re.fullmatch("[0-9a-f]{64}", self.model_sha256):
            raise ValueError("model_sha256 must be 64 lowercase hex characters")
        if self.device != "cpu":
            raise ValueError("P0 CPU smoke test only; CUDA/TensorRT is a separate validation")
        for key, lower, upper in (("imgsz", 32, 1280), ("max_det", 1, 1000), ("threads", 1, 8)):
            value = getattr(self, key)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"invalid {key}")
        if self.imgsz % 32:
            raise ValueError("imgsz must be a multiple of 32")
        for key in ("confidence", "iou"):
            value = getattr(self, key)
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 1:
                raise ValueError(f"invalid {key}")


def load_config(path=None):
    if path is None:
        return Config()
    with open(path, encoding="utf-8") as source:
        values = json.load(source)
    if not isinstance(values, dict) or set(values) - set(asdict(Config())):
        raise ValueError("config must be an object with only documented fields")
    return Config(**values)
