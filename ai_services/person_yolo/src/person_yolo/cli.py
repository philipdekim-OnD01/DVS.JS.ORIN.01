import argparse
from dataclasses import asdict
import json
from pathlib import Path
import platform
import sys
import time
from .config import load_config
from .detectors import ModelUnavailable, YoloNanoDetector
from .outputs import JsonlWriter
from .pipeline import process_frame
from .sources import SnapshotSource, SyntheticSource, Raw2ZstdSource


def main(argv=None):
    parser = argparse.ArgumentParser(description="P0 file/synthetic preview smoke test; NOT RAW2 CSI decoding")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, nargs="+", help="saved snapshot(s), read once in given order")
    source.add_argument("--synthetic", choices=SyntheticSource.PATTERNS)
    source.add_argument("--raw2-zstd", type=Path, help="P1 interface only: fails NOT IMPLEMENTED")
    parser.add_argument("--frames", type=int, default=3, help="synthetic frame count")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="validate representation; NO inference/mock detections")
    parser.add_argument("--output", type=Path, required=True, help="new JSONL path; existing files refused")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.raw2_zstd:
            iter(Raw2ZstdSource(args.raw2_zstd))
        inputs = SnapshotSource(args.snapshot) if args.snapshot else SyntheticSource(args.synthetic, args.frames)
        if args.output.exists():
            raise ValueError("output already exists; choose a new file (inputs are never overwritten)")
        if args.snapshot and args.output.resolve() in [path.resolve() for path in args.snapshot]:
            raise ValueError("output must not alias an input")
        if not args.dry_run and args.weights is None:
            raise ModelUnavailable("--weights is required for real inference; use --dry-run without a model")
        started = time.perf_counter()
        load_start = time.perf_counter()
        detector = None if args.dry_run else YoloNanoDetector(args.weights, config)
        load_ms = (time.perf_counter() - load_start) * 1000
        count, persons, inferred, skipped = 0, 0, 0, 0
        with JsonlWriter(args.output) as output:
            for frame in inputs:
                record = process_frame(frame, detector)
                record["inference_config"] = asdict(config)
                record["execution"] = {"platform": platform.system(), "machine": platform.machine(),
                                       "python": platform.python_version()}
                output.write(record)
                count += 1
                persons += record["person_count"]
                inferred += int(record["inference_ran"])
                skipped += int(record["status"] == "skipped_input")
        print(json.dumps({"frames_processed": count, "person_detections": persons,
                          "frames_inferred": inferred, "frames_skipped": skipped,
                          "mode": "dry_run_no_inference" if args.dry_run else "real_model",
                          "model_load_ms": load_ms, "total_wall_ms": (time.perf_counter() - started) * 1000,
                          "config": asdict(config)}))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
