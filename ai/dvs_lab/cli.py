import argparse
from .core import read_events
from .store import write_bins, read_summary
from .report import grounded_report

def main() -> None:
    parser = argparse.ArgumentParser(description="DVS approved-event analytics baseline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("file")
    ing.add_argument("--db", default="outputs/events.sqlite")
    rep = sub.add_parser("report")
    rep.add_argument("--db", default="outputs/events.sqlite")
    a = parser.parse_args()
    if a.cmd == "ingest":
        print(f"Stored {write_bins(a.db, read_events(a.file))} distinct time bins in {a.db}")
    else:
        print(grounded_report(read_summary(a.db)))
if __name__ == "__main__":
    main()
