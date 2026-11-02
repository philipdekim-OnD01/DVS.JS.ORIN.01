import time
from .contracts import WIDTH, HEIGHT
from .representations import PolarityRGB


def process_frame(frame, detector=None):
    start = time.perf_counter()
    rgb = PolarityRGB().transform(frame)
    represented = time.perf_counter()
    meta = frame.metadata
    usable = frame.source_kind == "synthetic" or (
        meta.get("state") == "live" and meta.get("has_frame") is True
        and meta.get("last_good_age", 2) < 2 and "duplicate_preview" not in frame.warnings)
    ran = detector is not None and usable
    detections = detector.detect(rgb) if ran else []
    end = time.perf_counter()
    host_us = meta.get("display_timestamp_us") if meta.get("display_clock") == "host_monotonic" else None
    if host_us is None and meta.get("monotonic") is not None:
        host_us = round(meta["monotonic"] * 1_000_000)
    status = "dry_run" if detector is None else "inference_completed" if ran else "skipped_input"
    return {
        "schema_version": 1, "status": status, "inference_ran": ran,
        "input_id": frame.input_id, "source_kind": frame.source_kind,
        "source_sha256": frame.source_sha256, "source_frame_id": meta.get("snapshot_id"),
        "source_session_pid": meta.get("session_pid"), "source_sensor_format": meta.get("format"),
        "raw2_direct": False, "host_monotonic_us": host_us,
        "sensor_timestamp_candidate_us": meta.get("frame", {}).get("timestamp_us"),
        "timestamp_quality": "synthetic_no_sensor_time" if frame.source_kind == "synthetic" else "preview_host_clock_sensor_candidate_uncalibrated",
        "representation": PolarityRGB.name,
        "window_ms": meta["preview_us"] / 1000 if meta.get("preview_us") else None,
        "window_semantics": "configured_composite_target_not_measured_event_window",
        "width": WIDTH, "height": HEIGHT,
        "model_file_sha256": detector.metadata["model_file_sha256"] if detector else None,
        "model_version": detector.metadata["model_version"] if detector else None,
        "model": detector.metadata if detector else None,
        "detections": detections, "person_count": len(detections),
        "processing_ms": (end - start) * 1000,
        "representation_ms": (represented - start) * 1000,
        "inference_ms": (end - represented) * 1000 if ran else None,
        "timing_scope": "representation_and_predict_excludes_file_read_and_model_load",
        "decode_warnings": frame.warnings,
        "input_discontinuity": frame.discontinuity,
        "unobserved_preview_ids": frame.unobserved_previews,
        "receiver_cumulative": {key: meta.get(key) for key in ("skipped", "raw_pipe_dropped", "screen_pipe_dropped", "discontinuities")},
        "stream_cumulative": {key: meta.get("stream", {}).get(key) for key in ("dropped", "starved", "error_buffers")},
    }
