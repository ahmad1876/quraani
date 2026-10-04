"""Living-being detector used to keep backgrounds free of people and animals.

Uses a small YOLOv8n ONNX model through OpenCV DNN. If the model or OpenCV is
unavailable the check is skipped (keyword filtering still applies upstream).
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = "https://huggingface.co/Kalray/yolov8/resolve/main/yolov8n.onnx"
# COCO ids for people and animals
LIVING = {0: "person", 14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep",
          19: "cow", 20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe"}

_net = None


def _model_path() -> Path:
    root = Path(os.environ.get("QURAANI_CACHE", Path(__file__).resolve().parent.parent / ".cache"))
    p = root / "models" / "yolov8n.onnx"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.rename(p)
    return p


def _get_net():
    global _net
    if _net is None:
        import cv2
        _net = cv2.dnn.readNetFromONNX(str(_model_path()))
    return _net


def find_living(image_bgr: np.ndarray, threshold: float = 0.30) -> list[tuple[str, float]]:
    """Return [(label, score)] for people/animals detected in a BGR image."""
    import cv2
    h, w = image_bgr.shape[:2]
    size = 640
    scale = size / max(h, w)
    nh, nw = int(round(h * scale)), int(round(w * scale))
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = cv2.resize(image_bgr, (nw, nh))
    blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, (size, size), swapRB=True, crop=False)
    net = _get_net()
    net.setInput(blob)
    out = net.forward()  # (1, 84, 8400)
    out = np.squeeze(out)
    if out.shape[0] != 84:
        out = out.T
    scores = out[4:, :]  # (80, N)
    found: dict[str, float] = {}
    for cls, label in LIVING.items():
        best = float(scores[cls].max())
        if best >= threshold:
            found[label] = max(found.get(label, 0.0), best)
    return sorted(found.items(), key=lambda x: -x[1])


def frames_have_living(paths: list[str], threshold: float = 0.30) -> list[tuple[str, float]]:
    import cv2
    hits: dict[str, float] = {}
    for p in paths:
        img = cv2.imread(p)
        if img is None:
            continue
        for label, s in find_living(img, threshold):
            hits[label] = max(hits.get(label, 0.0), s)
    return sorted(hits.items(), key=lambda x: -x[1])
