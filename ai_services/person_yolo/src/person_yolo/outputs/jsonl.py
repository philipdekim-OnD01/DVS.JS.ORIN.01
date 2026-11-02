import json
from pathlib import Path


class JsonlWriter:
    """Create a new result file; never overwrite an input or an earlier result."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("x", encoding="utf-8", newline="\n")
        return self

    def write(self, record):
        self.stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()

    def __exit__(self, *args):
        self.stream.close()
