import json
import unittest

from post_analysis.cargo_recognition import (
    Detection,
    classify_rock_crops,
    select_detection,
    select_rock_label,
)
from post_analysis.config import load_cargo_config, load_weight_config
from post_analysis.contracts import build_analysis_result
from post_analysis.kafka import MessageDecodeError, decode_event
from post_analysis.runtime import (
    OffsetTracker,
    SourceRecord,
    WorkItem,
    _close_finished_job,
    _receive_outcome,
    _start_job,
)
from post_analysis.weight_reader import WeightAccumulator, read_digits


CONFIG_PATH = "config/post_analysis.json"
ENVIRONMENT = {
    "MODA_KAFKA_BOOTSTRAP_SERVERS": "kafka:29092",
    "MODA_TRITON_URL": "http://triton:8000",
    "MODA_WEIGHT_CAMERA_URL": "rtsp://weight",
    "MODA_CARGO_LAND1_CAMERA_URL": "rtsp://cargo-1",
    "MODA_CARGO_LAND2_CAMERA_URL": "rtsp://cargo-2",
}


class FakeFrame:
    def __init__(self, name):
        self.name = name

    def copy(self):
        return FakeFrame(self.name)


class FakeProbabilities:
    def __init__(self, top1, confidence):
        self.top1 = top1
        self.top1conf = confidence


class FakeClassificationResult:
    def __init__(self, top1, confidence):
        self.probs = FakeProbabilities(top1, confidence)


class FakeClassifier:
    def __init__(self, predictions):
        self.predictions = iter(predictions)
        self.sources = []

    def predict(self, *, source, save):
        self.sources.append(source)
        top1, confidence = next(self.predictions)
        return [FakeClassificationResult(top1, confidence)]


def successful_worker(_config, event, _stop_event):
    if event.get("value") != 42:
        raise ValueError("unexpected event")


class PostAnalysisConfigTests(unittest.TestCase):
    def test_loads_weight_runtime_without_credentials_in_json(self):
        config = load_weight_config(CONFIG_PATH, ENVIRONMENT)

        self.assertEqual(config.camera_url, "rtsp://weight")
        self.assertEqual(config.model_url, "http://triton:8000/tram_can")
        self.assertEqual(config.kafka.dead_letter_topic, "AnalysisFailure_Gate1")
        self.assertEqual(config.worker.max_workers, 2)

    def test_loads_cargo_cameras_and_worker_limits(self):
        config = load_cargo_config(CONFIG_PATH, ENVIRONMENT)

        self.assertEqual(config.camera_urls["in_gate1_land1"], "rtsp://cargo-1")
        self.assertEqual(config.camera_urls["out_gate1_land2"], "rtsp://cargo-2")
        self.assertEqual(config.classifier_url, "http://triton:8000/rock_classify")
        self.assertEqual(config.worker.max_workers, 3)
        self.assertEqual(config.worker.event_timeout_seconds, 120)


class KafkaContractTests(unittest.TestCase):
    def test_result_preserves_trigger_and_replaces_analysis_fields(self):
        result = build_analysis_result(
            {"trigger_code": "in_gate1_land1", "analysis_output": ""},
            analysis_cam_id=1,
            analysis_code="weight_gate1_land1",
            analysis_image="/weight.jpg",
            analysis_output=12345,
        )

        self.assertEqual(result["trigger_code"], "in_gate1_land1")
        self.assertEqual(result["analysis_cam_id"], 1)
        self.assertEqual(result["analysis_output"], 12345)

    def test_decode_event_rejects_invalid_json_and_non_objects(self):
        with self.assertRaises(MessageDecodeError):
            decode_event(b"not-json")
        with self.assertRaises(MessageDecodeError):
            decode_event(json.dumps(["not", "an", "event"]).encode())

    def test_offset_tracker_commits_only_completed_prefix(self):
        tracker = OffsetTracker()
        first = SourceRecord("events", 0, 10)
        second = SourceRecord("events", 0, 11)
        tracker.register(first)
        tracker.register(second)

        tracker.complete(second)
        self.assertEqual(tracker.take_committable(), {})
        tracker.complete(first)

        self.assertEqual(tracker.take_committable(), {("events", 0): 12})

    def test_spawned_worker_reports_success(self):
        item = WorkItem(
            source=SourceRecord("events", 0, 12),
            event={"value": 42},
        )
        job = _start_job(successful_worker, None, item, None)
        try:
            self.assertTrue(job.result.poll(5), "spawned worker did not respond")
            self.assertEqual(_receive_outcome(job), (True, ""))
        finally:
            _close_finished_job(job, grace_seconds=1)


class WeightLogicTests(unittest.TestCase):
    def test_empty_detection_is_not_a_zero_weight(self):
        self.assertIsNone(read_digits([], 0.5))

    def test_accumulator_uses_latest_mode_without_retaining_every_frame(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        for value, name in ((100, "a"), (200, "b"), (100, "c"), (200, "latest")):
            accumulator.add(value, FakeFrame(name))

        selected = accumulator.selected()

        self.assertIsNotNone(selected)
        self.assertEqual(selected.value, 200)
        self.assertEqual(selected.frame.name, "latest")
        self.assertFalse(hasattr(accumulator, "readings"))

    def test_accumulator_ignores_zero_when_a_nonzero_value_was_read(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        readings = [(0, "empty")] * 5 + [(32_500, "first"), (32_480, "x"), (32_500, "loaded")] + [(0, "after")] * 4
        for value, name in readings:
            accumulator.add(value, FakeFrame(name))

        selected = accumulator.selected()

        self.assertEqual(selected.value, 32_500)
        self.assertEqual(selected.frame.name, "loaded")

    def test_accumulator_returns_zero_when_only_zero_was_read(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        for name in ("a", "b", "last"):
            accumulator.add(0, FakeFrame(name))

        self.assertEqual(accumulator.selected().value, 0)
        self.assertEqual(accumulator.selected().frame.name, "last")

    def test_accumulator_uses_peak_after_sustained_drop(self):
        accumulator = WeightAccumulator(drop_threshold=100, max_drop_count=2)
        accumulator.add(5_000, FakeFrame("peak"))
        accumulator.add(4_000, FakeFrame("drop-1"))
        accumulator.add(4_100, FakeFrame("drop-2"))

        self.assertTrue(accumulator.early_stopped)
        self.assertEqual(accumulator.selected().value, 5_000)
        self.assertEqual(accumulator.selected().frame.name, "peak")


class CargoLogicTests(unittest.TestCase):
    def test_selects_highest_confidence_detection_not_first_box(self):
        selected = select_detection(
            [
                Detection(0, 0.51, (0, 0, 10, 10)),
                Detection(1, 0.91, (10, 10, 30, 30)),
            ]
        )

        self.assertEqual(selected.class_id, 1)

    def test_preferred_base_rock_wins_above_threshold(self):
        result = select_rock_label([(2, 0.95), (0, 0.75)], 0.7)

        self.assertEqual(result, "dasetbase")

    def test_highest_confidence_rock_wins_without_preferred_base(self):
        result = select_rock_label([(2, 0.95), (0, 0.65)], 0.7)

        self.assertEqual(result, "da5x10")

    def test_fixed_batch_classifier_receives_one_crop_per_request(self):
        classifier = FakeClassifier([(2, 0.95), (0, 0.75), (4, 0.99)])

        result = classify_rock_crops(classifier, ["a", "b", "c"], 0.7)

        self.assertEqual(result, "dasetbase")
        self.assertEqual(classifier.sources, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
