"""Measure one real CLI invocation; psutil is supplied by the YOLO extra.

Example: python benchmarks/run_benchmark.py --metrics metrics.json -- --snapshot ...
No synthetic/fake detector, output image or machine-specific input path is added.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import time
import psutil
from person_yolo.cli import main

parser = argparse.ArgumentParser()
parser.add_argument("--metrics", type=Path, required=True)
parser.add_argument("cli_args", nargs=argparse.REMAINDER)
args = parser.parse_args()
if args.metrics.exists():
    parser.error("metrics file exists; choose a new output")
argv = args.cli_args[1:] if args.cli_args[:1] == ["--"] else args.cli_args
proc = psutil.Process()
cpu_start = proc.cpu_times()
wall_start = time.perf_counter()
exit_code = main(argv)
elapsed = time.perf_counter() - wall_start
cpu_end = proc.cpu_times()
cpu_seconds = cpu_end.user + cpu_end.system - cpu_start.user - cpu_start.system
memory = proc.memory_info()
peak = getattr(memory, "peak_wset", None)
if peak is None:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() != "Darwin":
        peak *= 1024
metrics = dict(exit_code=exit_code, wall_seconds=elapsed, process_cpu_seconds=cpu_seconds,
               process_cpu_percent_one_core=100 * cpu_seconds / elapsed,
               peak_process_rss_bytes=peak, final_process_rss_bytes=memory.rss,
               scope="CLI including model load; excludes wrapper/import setup; peak RSS is process lifetime",
               platform=platform.platform(), machine=platform.machine(), processor=platform.processor(),
               logical_cpus=os.cpu_count(), python=platform.python_version())
args.metrics.parent.mkdir(parents=True, exist_ok=True)
with args.metrics.open("x", encoding="utf-8") as output:
    json.dump(metrics, output, indent=2)
    output.write("\n")
raise SystemExit(exit_code)
