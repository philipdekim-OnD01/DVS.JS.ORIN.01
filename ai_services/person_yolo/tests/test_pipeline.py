import json
from types import SimpleNamespace
import numpy as np
import pytest
from PIL import Image
from person_yolo.cli import main
from person_yolo.config import Config, load_config
from person_yolo.detectors import YoloNanoDetector, ModelUnavailable
from person_yolo.pipeline import process_frame
from person_yolo.sources import SyntheticSource
from person_yolo.sources.snapshot import parse_snapshot


class UnitTestDetector:
    """Contract spy only; never used by CLI or published model benchmarks."""
    metadata = {"model_file_sha256": "unit-test-only", "model_version": "unit-test-only"}
    def __init__(self):
        self.calls = 0
    def detect(self, rgb):
        self.calls += 1
        return []


def test_jsonl_dry_run_does_not_claim_inference(tmp_path, capsys):
    output = tmp_path / "output.jsonl"
    assert main(["--synthetic", "moving", "--frames", "2", "--dry-run", "--output", str(output)]) == 0
    rows = [json.loads(s) for s in output.read_text().splitlines()]
    assert len(rows) == 2
    for row in rows:
        assert row["status"] == "dry_run" and not row["inference_ran"]
        assert row["model_file_sha256"] is None and row["detections"] == []
        assert row["raw2_direct"] is False and row["source_kind"] == "synthetic"
    summary = json.loads(capsys.readouterr().out)
    assert summary["frames_inferred"] == 0


@pytest.mark.parametrize("changes", [{"state": "stalled"}, {"has_frame": False}, {"last_good_age": 2}])
def test_unusable_frame_skips_model(snapshot_bytes, changes):
    detector = UnitTestDetector()
    row = process_frame(parse_snapshot(snapshot_bytes(**changes)), detector)
    assert not detector.calls and row["status"] == "skipped_input"
    assert row["inference_ms"] is None


def test_missing_liveness_and_duplicate_skips(snapshot_bytes):
    frame = parse_snapshot(snapshot_bytes())
    detector = UnitTestDetector()
    del frame.metadata["last_good_age"]
    assert process_frame(frame, detector)["status"] == "skipped_input"
    frame.metadata["last_good_age"] = 0.01
    frame.warnings.append("duplicate_preview")
    assert process_frame(frame, detector)["status"] == "skipped_input"
    assert not detector.calls


def test_zero_detection_and_two_clocks_preserved(snapshot_bytes):
    detector = UnitTestDetector()
    row = process_frame(parse_snapshot(snapshot_bytes()), detector)
    assert detector.calls == 1 and row["status"] == "inference_completed"
    assert row["detections"] == [] and row["person_count"] == 0
    assert row["host_monotonic_us"] == 123456001
    assert row["sensor_timestamp_candidate_us"] == 42
    assert "uncalibrated" in row["timestamp_quality"]
    assert row["window_ms"] == 16.666
    assert row["window_semantics"] == "configured_composite_target_not_measured_event_window"


def test_unknown_display_clock_not_assumed(snapshot_bytes):
    frame = parse_snapshot(snapshot_bytes())
    del frame.metadata["display_clock"]
    assert process_frame(frame)["host_monotonic_us"] == 123456000


@pytest.mark.parametrize("args", [["--synthetic", "empty"], ["--raw2-zstd", "absent.bin.zst"]])
def test_missing_model_or_raw_adapter_fails(tmp_path, args, capsys):
    out = tmp_path / "out.jsonl"
    assert main(args + ["--output", str(out)]) == 2
    assert "ERROR" in capsys.readouterr().err
    assert not out.exists()


def test_never_overwrite_input_or_existing_output(tmp_path, snapshot_bytes):
    path = tmp_path / "snapshot.bin"
    original = snapshot_bytes()
    path.write_bytes(original)
    assert main(["--snapshot", str(path), "--output", str(path), "--dry-run"]) == 2
    assert path.read_bytes() == original


def test_model_missing_and_hash_checked_before_runtime_import(tmp_path):
    with pytest.raises(ModelUnavailable, match="model missing"):
        YoloNanoDetector(tmp_path / "absent.pt", Config())
    path = tmp_path / "wrong.pt"
    path.write_bytes(b"not weights")
    with pytest.raises(ModelUnavailable, match="SHA-256 mismatch"):
        YoloNanoDetector(path, Config())


@pytest.mark.parametrize("values", [{"imgsz": 33}, {"device": "cuda:0"}, {"confidence": 0},
    {"iou": float("nan")}, {"threads": True}, {"model_sha256": "bad"}, {"max_det": 0}])
def test_config_bounds(values):
    with pytest.raises(ValueError):
        Config(**values)


def test_unknown_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"fake_detections": true}')
    with pytest.raises(ValueError):
        load_config(path)


def test_detector_rgb_bridge_and_person_filter():
    detector = object.__new__(YoloNanoDetector)
    detector.config = Config()
    def box(cls, score, xyxy):
        return SimpleNamespace(cls=np.array([cls]), conf=np.array([score]), xyxy=np.array([xyxy]))
    class PredictSpy:
        def predict(self, **kwargs):
            assert isinstance(kwargs["source"], Image.Image)
            assert kwargs["source"].getpixel((0, 0)) == (255, 0, 0)
            assert kwargs["classes"] == [0] and kwargs["conf"] == 0.25
            return [SimpleNamespace(boxes=[box(1, .9, [1, 2, 3, 4]), box(0, .5, [10, 20, 30, 40])])]
    detector.model = PredictSpy()
    rgb = np.zeros((720, 960, 3), dtype=np.uint8)
    rgb[0, 0] = [255, 0, 0]
    assert detector.detect(rgb) == [{"class_id": 0, "label": "person", "score": .5,
                                     "bbox_xyxy": [10., 20., 30., 40.]}]
