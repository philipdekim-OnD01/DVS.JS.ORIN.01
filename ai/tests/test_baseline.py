import tempfile
import unittest
from pathlib import Path
from dvs_lab.core import Event, read_events, time_bins
from dvs_lab.store import write_bins, read_summary
from dvs_lab.report import grounded_report

class TestBaseline(unittest.TestCase):
    def test_time_bins(self):
        e = [Event(0, 0, 0, "ON"), Event(10, 0, 0, "OFF"), Event(1_000_000, 1, 1, "ON")]
        self.assertEqual(time_bins(e), [(0, 1, 1), (1000000, 1, 0)])
    def test_invalid_event_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.csv"
            p.write_text("timestamp_us,x,y,polarity\n0,960,0,ON\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_events(p)
    def test_db_report(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.sqlite"
            self.assertEqual(write_bins(p, [Event(0, 0, 0, "ON")]), 1)
            s = read_summary(p)
            self.assertEqual(s["on_events"], 1)
            self.assertIn("ON 이벤트", grounded_report(s))

if __name__ == "__main__":
    unittest.main()
