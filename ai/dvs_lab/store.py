import sqlite3
from pathlib import Path
from .core import Event, time_bins

def write_bins(db_path: str | Path, events: list[Event]) -> int:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as con:
        con.execute("CREATE TABLE IF NOT EXISTS activity_bins (start_us INTEGER PRIMARY KEY, on_count INTEGER NOT NULL, off_count INTEGER NOT NULL)")
        bins = time_bins(events)
        con.executemany("INSERT INTO activity_bins (start_us,on_count,off_count) VALUES (?,?,?) ON CONFLICT(start_us) DO UPDATE SET on_count=excluded.on_count, off_count=excluded.off_count", bins)
        return len(bins)

def read_summary(db_path: str | Path) -> dict:
    with sqlite3.connect(db_path) as con:
        count, on_count, off_count, first_us, last_us = con.execute("SELECT COUNT(*), COALESCE(SUM(on_count),0), COALESCE(SUM(off_count),0), MIN(start_us), MAX(start_us) FROM activity_bins").fetchone()
    return {"bins":count,"on_events":on_count,"off_events":off_count,"first_bin_us":first_us,"last_bin_us":last_us}
