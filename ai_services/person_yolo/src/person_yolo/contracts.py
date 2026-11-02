from dataclasses import dataclass, field
from typing import Iterator, Protocol
import numpy as np

WIDTH, HEIGHT = 960, 720
PACKED_BYTES = WIDTH * HEIGHT // 4


@dataclass
class PreviewFrame:
    input_id: str
    source_kind: str
    source_sha256: str
    pixels: np.ndarray
    metadata: dict
    warnings: list[str] = field(default_factory=list)
    unobserved_previews: int | None = None
    discontinuity: bool = False


class FrameSource(Protocol):
    def __iter__(self) -> Iterator[PreviewFrame]: ...


class Detector(Protocol):
    def detect(self, rgb: np.ndarray) -> list[dict]: ...
