"""Read the weighbridge display after a vehicle trigger event."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import time
from typing import Iterable

from .capture import open_camera
from .config import WeightConfig
from .contracts import build_analysis_result
from .kafka import create_producer, publish
from .models import load_yolo
from .runtime import run_worker_service


logger = logging.getLogger(__name__)
DIGIT_NAMES = tuple(str(value) for value in range(10))


@dataclass(frozen=True)
class DetectedDigit:
    x_min: int
    y_min: int
    x_max: int
    y_max: int
    value: str
    confidence: float

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x_min, self.y_min, self.x_max, self.y_max


@dataclass
class Reading:
    value: int | float
    frame: object
    index: int


SELECTION_WINDOW = 3000
# A value must be read this many times before it can be the peak, so a single misread
# (an extra digit, 1 read as 7) cannot drag the selection window away from the real weight.
MIN_PEAK_COUNT = 2
# When a newer event on the same lane cancels a read, the value read so far is published only
# if it was read this many times (~2 s at the ~14 frames/s the reader processes).
MIN_STABLE_COUNT = 30


class WeightAccumulator:
    """Pick the weight as the most frequent value within [peak - 3000, peak].

    peak = largest value read at least MIN_PEAK_COUNT times; if no value repeated yet,
    the largest value read. One rule for every case: zero (empty scale) and the values
    read while the vehicle drives on/off the scale fall outside the window. Frequency
    ties go to the latest reading.

    Frames: one (the latest) per value that is in the window or above the peak (a value
    above the peak may still become the peak). Others are discarded when the peak rises.

    drop_threshold / max_drop_count only decide when to stop reading early.
    """

    def __init__(
        self,
        drop_threshold: int,
        max_drop_count: int,
        window: int | float = SELECTION_WINDOW,
        min_peak_count: int = MIN_PEAK_COUNT,
    ) -> None:
        self._counts: Counter = Counter()
        self._latest: dict[int | float, Reading] = {}
        self._confirmed_peak: int | float | None = None
        self._raw_peak: int | float | None = None
        self._window = window
        self._min_peak_count = min_peak_count
        self._drop_threshold = drop_threshold
        self._max_drop_count = max_drop_count
        self._consecutive_drops = 0
        self.early_stopped = False
        self.reading_count = 0

    @property
    def peak(self) -> int | float | None:
        return self._confirmed_peak if self._confirmed_peak is not None else self._raw_peak

    def _keep_frame(self, value: int | float) -> bool:
        peak = self.peak
        return peak is not None and value >= peak - self._window

    def add(self, value: int | float, frame) -> None:
        index = self.reading_count
        self.reading_count += 1
        self._counts[value] += 1
        previous_peak = self.peak

        if self._raw_peak is None or value > self._raw_peak:
            self._raw_peak = value
        if self._counts[value] >= self._min_peak_count and (
            self._confirmed_peak is None or value > self._confirmed_peak
        ):
            self._confirmed_peak = value

        peak = self.peak
        if peak != previous_peak:
            self._consecutive_drops = 0
            self._latest = {v: r for v, r in self._latest.items() if self._keep_frame(v)}
        elif peak - value > self._drop_threshold:
            self._consecutive_drops += 1
        else:
            self._consecutive_drops = 0

        if self._keep_frame(value):
            self._latest[value] = Reading(value=value, frame=frame.copy(), index=index)

        self.early_stopped = self._consecutive_drops >= self._max_drop_count

    def selected(self) -> Reading | None:
        peak = self.peak
        candidates = [r for v, r in self._latest.items() if peak is not None and v <= peak]
        if not candidates:
            return None
        return max(candidates, key=lambda reading: (self._counts[reading.value], reading.index))

    def count(self, value: int | float) -> int:
        return self._counts[value]

    def stable_reading(self, min_count: int) -> Reading | None:
        """selected() if its value was read at least min_count times, else None."""
        reading = self.selected()
        if reading is None or self._counts[reading.value] < min_count:
            return None
        return reading


def calculate_iou(box1: tuple[int, int, int, int], box2: tuple[int, int, int, int]) -> float:
    x1, y1 = max(box1[0], box2[0]), max(box1[1], box2[1])
    x2, y2 = min(box1[2], box2[2]), min(box1[3], box2[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = max(0, box1[2] - box1[0]) * max(0, box1[3] - box1[1])
    area2 = max(0, box2[2] - box2[0]) * max(0, box2[3] - box2[1])
    union = area1 + area2 - intersection
    return 0.0 if union <= 0 else intersection / union


def filter_overlapping_digits(
    digits: Iterable[DetectedDigit], threshold: float = 0.5
) -> list[DetectedDigit]:
    remaining = sorted(digits, key=lambda digit: digit.confidence, reverse=True)
    selected: list[DetectedDigit] = []
    while remaining:
        best = remaining.pop(0)
        selected.append(best)
        remaining = [
            candidate
            for candidate in remaining
            if calculate_iou(best.box, candidate.box) < threshold
        ]
    return selected


def read_digits(results, confidence_threshold: float) -> int | float | None:
    digits: list[DetectedDigit] = []
    for result in results:
        for box in result.boxes:
            confidence = float(box.conf[0].item())
            if confidence < confidence_threshold:
                continue
            class_id = int(box.cls[0].item())
            if class_id not in range(len(DIGIT_NAMES)):
                continue
            x_min, y_min, x_max, y_max = box.xyxy[0].cpu().numpy().astype(int)
            digits.append(
                DetectedDigit(
                    x_min=x_min,
                    y_min=y_min,
                    x_max=x_max,
                    y_max=y_max,
                    value=DIGIT_NAMES[class_id],
                    confidence=confidence,
                )
            )

    ordered = sorted(filter_overlapping_digits(digits), key=lambda digit: digit.x_min)
    if not ordered:
        return None
    value = int("".join(digit.value for digit in ordered))
    return value / 10 if value > 100_000 else value


def _lane(event: dict[str, object]) -> str | None:
    trigger_code = str(event.get("trigger_code", ""))
    if "lane1" in trigger_code:
        return "lane1"
    if "lane2" in trigger_code:
        return "lane2"
    return None


class WeightStepError(RuntimeError):
    """Failure of one named processing step; the message starts with "[step]"."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"[{step}] {message}")
        self.step = step


@dataclass
class ReadStats:
    """What the reading loop saw, for progress logs and failure messages."""

    frames: int = 0
    frames_with_digit_boxes: int = 0
    frames_with_value: int = 0
    best_digit_confidence: float = 0.0
    last_value: int | float | None = None
    values: Counter = field(default_factory=Counter)

    def observe(self, digit_confidences: list[float], value: int | float | None) -> None:
        self.frames += 1
        if digit_confidences:
            self.frames_with_digit_boxes += 1
            self.best_digit_confidence = max(self.best_digit_confidence, max(digit_confidences))
        if value is not None:
            self.frames_with_value += 1
            self.last_value = value
            self.values[value] += 1

    def summary(self) -> str:
        top = ", ".join(f"{value}x{count}" for value, count in self.values.most_common(3)) or "-"
        return (
            f"frames={self.frames} frames_with_digit_boxes={self.frames_with_digit_boxes}"
            f" frames_with_value={self.frames_with_value}"
            f" best_digit_conf={self.best_digit_confidence:.2f} top_values=[{top}]"
        )


def digit_confidences(results) -> list[float]:
    """Confidence of every digit box (classes 0-9) before the threshold is applied."""
    confidences = []
    for result in results:
        for box in result.boxes:
            if int(box.cls[0].item()) in range(len(DIGIT_NAMES)):
                confidences.append(float(box.conf[0].item()))
    return confidences


def crop_fits(crop, width: int, height: int) -> bool:
    return 0 <= crop.x_min < crop.x_max <= width and 0 <= crop.y_min < crop.y_max <= height


def _local_day(config: WeightConfig) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=config.utc_offset_hours)).strftime("%Y%m%d")


def _save_debug_frame(cv2, config: WeightConfig, trigger_code: str, reason: str, frame, crop) -> str | None:
    """Full camera frame with the configured crop drawn on it, to see what the reader looked at."""
    if frame is None:
        return None
    try:
        annotated = frame.copy()
        cv2.rectangle(annotated, (crop.x_min, crop.y_min), (crop.x_max, crop.y_max), (0, 0, 255), 4)
        label = (
            f"{trigger_code} crop x {crop.x_min}-{crop.x_max} y {crop.y_min}-{crop.y_max}"
            f" frame {frame.shape[1]}x{frame.shape[0]}"
        )
        cv2.putText(annotated, label, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        folder = Path(config.output_root) / "debug" / _local_day(config)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"fail_{trigger_code}_{reason}_{time.time():.3f}.jpg"
        return str(path) if cv2.imwrite(str(path), annotated) else None
    except Exception:  # debugging aid only; never hide the real failure
        logger.exception("Could not save weight debug frame")
        return None


def process_event(config: WeightConfig, event: dict[str, object], stop_event) -> None:
    import cv2

    trigger_code = str(event.get("trigger_code", ""))
    tag = f"{trigger_code} section={str(event.get('section_id') or '')[:8] or '-'}"
    event_started = time.monotonic()

    def step(number: int, name: str, message: str, *args) -> None:
        logger.info(
            "%s [%d/7 %s] " + message + " (t=%.1fs)",
            tag, number, name, *args, time.monotonic() - event_started,
        )

    # 1. Which ROI of the weighbridge display belongs to this lane.
    crop = config.crops.get(trigger_code)
    if crop is None:
        raise WeightStepError("1/7 crop", f"No weight crop configured for trigger_code {trigger_code!r}")
    step(
        1, "crop", "crop x=%d..%d y=%d..%d (%dx%d)",
        crop.x_min, crop.x_max, crop.y_min, crop.y_max,
        crop.x_max - crop.x_min, crop.y_max - crop.y_min,
    )

    # 2. Digit model on Triton.
    try:
        model = load_yolo(
            config.model_url,
            task="detect",
            attempts=config.io.model_startup_attempts,
            delay_seconds=config.io.model_startup_delay_seconds,
        )
    except Exception as error:
        raise WeightStepError("2/7 model", f"Triton model {config.model_url} not ready: {error}") from error
    step(2, "model", "model ready: %s", config.model_url)
    if stop_event.is_set():
        raise WeightStepError("2/7 model", "Weight read was cancelled before the camera was opened")

    # 3. Weighbridge camera.
    try:
        capture = open_camera(
            cv2,
            config.camera_url,
            config.io.camera_open_timeout_ms,
            config.io.camera_read_timeout_ms,
        )
    except Exception as error:
        raise WeightStepError("3/7 camera", f"Could not open weighbridge camera: {error}") from error
    step(3, "camera", "camera opened")

    accumulator = WeightAccumulator(config.drop_threshold, config.max_drop_count)
    stats = ReadStats()
    started_at = time.monotonic()
    next_progress = started_at + 10
    last_frame = None

    try:
        # 4 + 5. Read frames until the value is stable, peaks, or max_seconds elapse.
        while not stop_event.is_set() and time.monotonic() - started_at <= config.max_seconds:
            ok, frame = capture.read()
            if not ok:
                raise WeightStepError(
                    "5/7 read",
                    f"Could not read weighbridge camera after {stats.frames} frame(s)"
                    f" ({time.monotonic() - started_at:.1f}s)",
                )
            height, width = frame.shape[:2]
            if last_frame is None:
                step(4, "frame", "first frame %dx%d", width, height)
            last_frame = frame
            if not crop_fits(crop, width, height):
                debug = _save_debug_frame(cv2, config, trigger_code, "crop_outside", frame, crop)
                raise WeightStepError(
                    "4/7 frame",
                    f"Weight crop x={crop.x_min}..{crop.x_max} y={crop.y_min}..{crop.y_max}"
                    f" exceeds frame size {width}x{height}: the camera resolution differs from"
                    f" the one the crop was drawn on; redraw the crop. Debug image: {debug}",
                )

            lcd = frame[crop.y_min : crop.y_max, crop.x_min : crop.x_max]
            results = model(lcd, verbose=False)
            value = read_digits(results, config.confidence)
            stats.observe(digit_confidences(results), value)
            if time.monotonic() >= next_progress:
                step(5, "read", "progress %s", stats.summary())
                next_progress += 10
            if value is None:
                continue
            accumulator.add(value, lcd)
            if accumulator.early_stopped:
                break

        if stop_event.is_set():
            # A newer event took this lane over. Keep the value read so far if it is stable
            # (read at least MIN_STABLE_COUNT times); a value still rising while the vehicle
            # drives on would be wrong, so anything less is a failure.
            selected = accumulator.stable_reading(MIN_STABLE_COUNT)
            if selected is None:
                raise WeightStepError(
                    "5/7 read",
                    f"Weight read was cancelled before a stable value"
                    f" (needs {MIN_STABLE_COUNT} identical readings); {stats.summary()}",
                )
            step(
                5, "read", "cancelled by a newer event; using stable value %s (x%d) %s",
                selected.value, accumulator.count(selected.value), stats.summary(),
            )
        else:
            step(
                5, "read", "finished early_stop=%s %s",
                accumulator.early_stopped, stats.summary(),
            )
            selected = accumulator.selected()
        if selected is None:
            debug = _save_debug_frame(cv2, config, trigger_code, "no_digits", last_frame, crop)
            hint = (
                "no digit box at all: the crop probably does not cover the display"
                if stats.frames_with_digit_boxes == 0
                else f"digit boxes found but none reached confidence {config.confidence}"
            )
            raise WeightStepError(
                "5/7 read",
                f"No valid weighbridge reading in {time.monotonic() - started_at:.1f}s"
                f" ({hint}); {stats.summary()}. Debug image: {debug}",
            )

        # 6. Evidence image of the selected reading.
        output_dir = Path(config.output_root) / _local_day(config)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"weight_{trigger_code}_{time.time()}.jpg"
        if not cv2.imwrite(str(image_path), selected.frame):
            raise WeightStepError("6/7 save", f"Could not write weighbridge image: {image_path}")
        step(6, "save", "value=%s image=%s", selected.value, image_path)

        # 7. Result to Kafka.
        payload = build_analysis_result(
            event,
            analysis_cam_id=1,
            analysis_code=config.analysis_codes[trigger_code],
            analysis_image=str(image_path),
            analysis_output=selected.value,
        )
        try:
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
        except Exception as error:
            raise WeightStepError(
                "7/7 publish", f"Could not publish to {config.kafka.output_topic}: {error}"
            ) from error
        step(7, "publish", "published %s=%s to %s", config.analysis_codes[trigger_code],
             selected.value, config.kafka.output_topic)
    finally:
        capture.release()


def run(config: WeightConfig) -> None:
    logger.info(
        "weight_reader config: model=%s max_seconds=%s confidence=%s crops=%s",
        config.model_url,
        config.max_seconds,
        config.confidence,
        {code: (c.x_min, c.x_max, c.y_min, c.y_max) for code, c in config.crops.items()},
    )
    run_worker_service(
        service_name="weight_reader",
        kafka=config.kafka,
        worker=config.worker,
        handler=process_event,
        handler_config=config,
        routing_key=_lane,
        supersede_same_key=True,
    )
