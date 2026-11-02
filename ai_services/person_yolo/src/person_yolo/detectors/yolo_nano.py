import hashlib
import os
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import numpy as np
from PIL import Image


class ModelUnavailable(RuntimeError):
    pass


class YoloNanoDetector:
    def __init__(self, weights, config):
        path = Path(weights)
        if not path.is_file():
            raise ModelUnavailable("model missing: supply local yolo11n.pt; no automatic download, or use --dry-run")
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(source.read()).hexdigest()
        if digest != config.model_sha256:
            raise ModelUnavailable("model SHA-256 mismatch; refusing to load weights")
        try:
            installed = version("ultralytics")
        except PackageNotFoundError as exc:
            raise ModelUnavailable("install the pinned [yolo] dependencies or use --dry-run") from exc
        if installed != config.ultralytics_version:
            raise ModelUnavailable(f"ultralytics version mismatch: {installed}")
        try:
            # P0 is local-file inference. Never auto-install runtime dependencies.
            os.environ["YOLO_OFFLINE"] = "true"
            os.environ["YOLO_AUTOINSTALL"] = "false"
            import torch
            from ultralytics import YOLO
            torch.set_num_threads(config.threads)
            self.model = YOLO(str(path.resolve()), task="detect")
        except Exception as exc:
            raise ModelUnavailable(f"model runtime load failed: {exc}") from exc
        if self.model.names.get(0) != "person":
            raise ModelUnavailable("model class 0 is not person")
        self.config = config
        self.metadata = {"model_name": config.model_name, "model_file_sha256": digest,
                         "model_version": f"ultralytics=={installed}", "torch_version": torch.__version__,
                         "device": config.device, "inference_torch_threads": None,
                         "weights_license": "AGPL-3.0 / Ultralytics Enterprise option"}
        # select_device() resets CPU threads during the lazy predictor setup.
        # The public callback runs after that setup, before batch preprocessing.
        def configure_predict_threads(predictor):
            torch.set_num_threads(config.threads)
            self.metadata["inference_torch_threads"] = torch.get_num_threads()
        self.model.add_callback("on_predict_start", configure_predict_threads)

    def detect(self, rgb):
        if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("detector requires HWC uint8 RGB")
        cfg = self.config
        # PIL explicitly carries RGB. Ultralytics interprets ndarray inputs as BGR.
        results = self.model.predict(source=Image.fromarray(rgb), device=cfg.device,
                                     imgsz=cfg.imgsz, conf=cfg.confidence, iou=cfg.iou,
                                     max_det=cfg.max_det, classes=[0], verbose=False,
                                     save=False, stream=False)
        detections = []
        for box in results[0].boxes:
            class_id = int(box.cls.item())
            if class_id != 0:
                continue
            coords = [float(v) for v in box.xyxy[0].tolist()]
            score = float(box.conf.item())
            if not np.isfinite(coords + [score]).all():
                raise ValueError("model returned non-finite detection")
            detections.append({"class_id": 0, "label": "person", "score": score,
                               "bbox_xyxy": coords})
        return detections
