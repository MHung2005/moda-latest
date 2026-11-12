"""Recognize cargo state and rock type after a vehicle trigger event."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import time

from .capture import open_camera
from .config import CargoConfig
from .contracts import build_analysis_result
from .kafka import create_producer, publish
from .models import load_yolo
from .runtime import run_worker_service


logger = logging.getLogger(__name__)
ROCK_LABELS = {
    0: "dasetbase",
    1: "da1x2",
    2: "da5x10",
    3: "dahoc",
    4: "damat",
    5: "xobo",
}
CARGO_LABELS = ("rong", "cohang", "unknown", "phubat")


@dataclass(frozen=True)
class Detection:
    class_id: int
    confidence: float
    box: tuple[int, int, int, int]


def select_detection(detections: list[Detection]) -> Detection | None:
    """Select the most confident cargo detection explicitly."""
    return max(detections, key=lambda item: item.confidence, default=None)


def select_rock_label(
    classifications: list[tuple[int, float]], preferred_confidence: float
) -> str:
    if not classifications:
        return "No valid detection"
    preferred = [
        item
        for item in classifications
        if item[0] == 0 and item[1] > preferred_confidence
    ]
    selected = max(preferred or classifications, key=lambda item: item[1])
    return ROCK_LABELS.get(selected[0], "unknown")


def classify_rock_crops(classifier, crops: list, preferred_confidence: float) -> str:
    """Classify fixed-batch Triton inputs one crop at a time.

    The deployed ONNX classifier has input shape ``[1, 3, 640, 640]``. Passing
    the full crop list to Ultralytics can form a larger batch which Triton
    cannot accept.
    """
    classifications: list[tuple[int, float]] = []
    for crop in crops:
        results = classifier.predict(source=crop, save=False)
        for result in results:
            if result.probs is None:
                continue
            classification = (
                int(result.probs.top1),
                float(result.probs.top1conf),
            )
            classifications.append(classification)
            if (
                classification[0] == 0
                and classification[1] > preferred_confidence
            ):
                return ROCK_LABELS[0]
    return select_rock_label(classifications, preferred_confidence)


def is_valid_frame(frame, min_height: int = 200, min_stddev: float = 5) -> bool:
    return bool(
        frame is not None
        and frame.size > 0
        and frame.shape[0] >= min_height
        and float(frame.std()) >= min_stddev
    )


def capture_frame(config: CargoConfig, camera_id: str, url: str, stop_event):
    import cv2

    for attempt in range(1, config.capture_retries + 1):
        if stop_event.is_set():
            raise RuntimeError("Cargo capture was cancelled")
        capture = open_camera(
            cv2,
            url,
            config.io.camera_open_timeout_ms,
            config.io.camera_read_timeout_ms,
        )
        try:
            frame = None
            ok = False
            for _ in range(6):
                ok, frame = capture.read()
                if not ok:
                    break
        finally:
            capture.release()
        if ok and is_valid_frame(frame):
            return frame
        logger.warning(
            "Invalid frame from %s (attempt %s/%s)",
            camera_id,
            attempt,
            config.capture_retries,
        )
        if attempt < config.capture_retries and stop_event.wait(1):
            raise RuntimeError("Cargo capture was cancelled")
    raise RuntimeError(f"No usable cargo frame for {camera_id}")


def _cargo_detections(result) -> list[Detection]:
    detections: list[Detection] = []
    boxes = result.boxes
    for index in range(int(boxes.cls.numel())):
        class_id = int(boxes.cls[index].item())
        confidence = float(boxes.conf[index].item())
        x1, y1, x2, y2 = (
            int(value) for value in boxes.xyxy[index].detach().cpu().tolist()
        )
        detections.append(
            Detection(
                class_id=class_id,
                confidence=confidence,
                box=(x1, y1, x2, y2),
            )
        )
    return detections


def _rock_crops(image, box: tuple[int, int, int, int]) -> list:
    x1, y1, x2, y2 = box
    center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
    crops = []
    seen_coordinates = set()
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            crop_x1 = max(center_x + dx * 50 - 320, 0)
            crop_y1 = max(center_y + dy * 50 - 320, 0)
            crop_x2 = min(center_x + dx * 50 + 320, image.shape[1])
            crop_y2 = min(center_y + dy * 50 + 320, image.shape[0])
            coordinates = crop_x1, crop_y1, crop_x2, crop_y2
            if coordinates in seen_coordinates:
                continue
            seen_coordinates.add(coordinates)
            crop = image[crop_y1:crop_y2, crop_x1:crop_x2]
            if crop.size:
                crops.append(crop)
    return crops


def analyze_image(config: CargoConfig, detector, classifier, image) -> str:
    results = detector.predict(
        source=image,
        save=False,
        conf=config.detection_confidence,
    )
    detections = [
        detection
        for result in results
        for detection in _cargo_detections(result)
    ]
    selected = select_detection(detections)
    if selected is None:
        return "No valid detection"
    if selected.class_id != 1:
        if 0 <= selected.class_id < len(CARGO_LABELS):
            return CARGO_LABELS[selected.class_id]
        return "unknown"

    crops = _rock_crops(image, selected.box)
    if not crops:
        return "No valid detection"
    return classify_rock_crops(
        classifier,
        crops,
        config.preferred_class_confidence,
    )


def process_event(config: CargoConfig, event: dict[str, object], stop_event) -> None:
    import cv2

    trigger_code = str(event.get("trigger_code", ""))
    camera_url = config.camera_urls.get(trigger_code)
    if camera_url is None:
        raise ValueError(f"Unsupported trigger_code: {trigger_code!r}")

    started_at = time.monotonic()
    detector = load_yolo(
        config.detector_url,
        task="detect",
        attempts=config.io.model_startup_attempts,
        delay_seconds=config.io.model_startup_delay_seconds,
    )
    classifier = load_yolo(
        config.classifier_url,
        task="classify",
        attempts=config.io.model_startup_attempts,
        delay_seconds=config.io.model_startup_delay_seconds,
    )
    remaining_delay = max(0.0, config.settle_seconds - (time.monotonic() - started_at))
    if remaining_delay and stop_event.wait(remaining_delay):
        raise RuntimeError("Cargo analysis was cancelled")

    frame = capture_frame(config, trigger_code, camera_url, stop_event)
    result = analyze_image(config, detector, classifier, frame)
    local_now = datetime.now(timezone.utc) + timedelta(hours=config.utc_offset_hours)
    output_dir = Path(config.output_root) / local_now.strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{time.time()}_{trigger_code}_{result}.jpg"
    absolute_path = output_dir / filename
    if not cv2.imwrite(str(absolute_path), frame):
        raise RuntimeError(f"Could not write cargo image: {absolute_path}")

    relative_path = f"{Path(config.output_root).name}/{local_now:%Y-%m-%d}/{filename}"
    payload = build_analysis_result(
        event,
        analysis_cam_id=2,
        analysis_code=config.analysis_codes[trigger_code],
        analysis_image=relative_path,
        analysis_output=result,
    )
    producer = create_producer(config.kafka.bootstrap_servers)
    try:
        publish(
            producer,
            config.kafka.output_topic,
            payload,
            timeout_seconds=config.kafka.publish_timeout_seconds,
        )
    finally:
        producer.close()
    logger.info("Published cargo result for %s: %s", trigger_code, result)


def run(config: CargoConfig) -> None:
    run_worker_service(
        service_name="cargo_recognition",
        kafka=config.kafka,
        worker=config.worker,
        handler=process_event,
        handler_config=config,
    )
