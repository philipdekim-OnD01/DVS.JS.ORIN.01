from __future__ import annotations
from dataclasses import dataclass
import csv
from pathlib import Path

WIDTH, HEIGHT = 960, 720

@dataclass(frozen=True)
class Event:
    timestamp_us: int
    x: int
    y: int
    polarity: str

def read_events(path: str | Path) -> list[Event]:
    result = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["timestamp_us", "x", "y", "polarity"]:
            raise ValueError("CSV header must be timestamp_us,x,y,polarity")
        last = -1
        for line, row in enumerate(reader, 2):
            try:
                e = Event(int(row["timestamp_us"]), int(row["x"]), int(row["y"]), row["polarity"])
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid event row {line}") from exc
            if not (e.timestamp_us >= last >= -1 and 0 <= e.x < WIDTH and 0 <= e.y < HEIGHT and e.polarity in ("ON", "OFF")):
                raise ValueError(f"Out-of-range or out-of-order event at line {line}")
            last = e.timestamp_us
            result.append(e)
    return result

def time_bins(events: list[Event], width_us: int = 1_000_000) -> list[tuple[int, int, int]]:
    if width_us <= 0:
        raise ValueError("width_us must be positive")
    buckets: dict[int, list[int]] = {}
    for e in events:
        group = buckets.setdefault(e.timestamp_us // width_us, [0, 0])
        group[0 if e.polarity == "ON" else 1] += 1
    return [(key * width_us, *counts) for key, counts in sorted(buckets.items())]
