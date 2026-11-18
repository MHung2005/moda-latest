import json
import unittest

from post_analysis.config import load_weight_config
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
}


class FakeFrame:
    def __init__(self, name):
        self.name = name

    def copy(self):
        return FakeFrame(self.name)


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


class KafkaContractTests(unittest.TestCase):
    def test_result_preserves_trigger_and_replaces_analysis_fields(self):
        result = build_analysis_result(
            {"trigger_code": "in_gate1_lane1", "analysis_output": ""},
            analysis_cam_id=1,
            analysis_code="weight_gate1_lane1",
            analysis_image="/weight.jpg",
            analysis_output=12345,
        )

        self.assertEqual(result["trigger_code"], "in_gate1_lane1")
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

    def test_accumulator_after_sustained_drop_uses_window_mode(self):
        accumulator = WeightAccumulator(drop_threshold=100, max_drop_count=2)
        accumulator.add(5_000, FakeFrame("peak"))
        accumulator.add(4_000, FakeFrame("drop-1"))
        accumulator.add(4_100, FakeFrame("drop-2"))

        self.assertTrue(accumulator.early_stopped)
        # 4_000 and 4_100 are inside [2_000, 5_000]; all seen once -> latest wins.
        self.assertEqual(accumulator.selected().value, 4_100)

    def test_accumulator_mode_within_window_of_peak(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        readings = (
            [(0, "empty")] * 10          # empty scale, most frequent overall
            + [(12_000, "ramp")] * 6     # driving on: outside [29_600, 32_600]
            + [(32_500, "stable")] * 4
            + [(32_600, "peak")] * 2
            + [(30_000, "settle")] * 3
        )
        for value, name in readings:
            accumulator.add(value, FakeFrame(name))

        selected = accumulator.selected()

        self.assertEqual(selected.value, 32_500)
        self.assertEqual(selected.frame.name, "stable")

    def test_accumulator_drops_frames_outside_window(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        for value in (0, 1_000, 10_000, 20_000, 30_000):
            accumulator.add(value, FakeFrame(str(value)))

        self.assertEqual(set(accumulator._latest), {30_000})

    def test_accumulator_ignores_single_misread_above_real_weight(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        readings = (
            [(32_500, "real")] * 3
            + [(92_500, "misread")]      # seen once: cannot become the peak
            + [(32_500, "real-last")]
        )
        for value, name in readings:
            accumulator.add(value, FakeFrame(name))

        self.assertEqual(accumulator.peak, 32_500)
        self.assertEqual(accumulator.selected().value, 32_500)
        self.assertEqual(accumulator.selected().frame.name, "real-last")

    def test_accumulator_repeated_high_value_becomes_peak(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        for value, name in [(20_000, "a")] * 3 + [(32_500, "b"), (32_500, "c")]:
            accumulator.add(value, FakeFrame(name))

        self.assertEqual(accumulator.peak, 32_500)
        self.assertEqual(accumulator.selected().value, 32_500)

    def test_accumulator_uses_raw_peak_when_nothing_repeats(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        for value in (100, 31_000, 32_000):
            accumulator.add(value, FakeFrame(str(value)))

        self.assertEqual(accumulator.peak, 32_000)
        self.assertEqual(accumulator.selected().value, 32_000)

    def test_accumulator_returns_none_without_readings(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        self.assertIsNone(accumulator.selected())


class DiagnosticsTests(unittest.TestCase):
    def test_crop_fits_detects_resolution_change(self):
        from post_analysis.config import Crop
        from post_analysis.weight_reader import crop_fits

        drawn_on_3200x1800 = Crop(x_min=2255, x_max=3119, y_min=787, y_max=1183)
        self.assertTrue(crop_fits(drawn_on_3200x1800, 3200, 1800))
        self.assertFalse(crop_fits(drawn_on_3200x1800, 1920, 1080))

    def test_read_stats_distinguishes_no_boxes_from_low_confidence(self):
        from post_analysis.weight_reader import ReadStats

        stats = ReadStats()
        stats.observe([], None)
        stats.observe([0.3, 0.2], None)
        stats.observe([0.9], 15920)
        stats.observe([0.9], 15920)

        self.assertEqual(stats.frames, 4)
        self.assertEqual(stats.frames_with_digit_boxes, 3)
        self.assertEqual(stats.frames_with_value, 2)
        self.assertIn("top_values=[15920x2]", stats.summary())
        self.assertIn("best_digit_conf=0.90", stats.summary())

    def test_failure_logs_use_exception_line_and_event_identity(self):
        from post_analysis.runtime import describe, short_reason

        reason = 'Traceback (most recent call last):\n  File "x"\nWeightStepError: [5/7 read] No valid\n'
        self.assertEqual(short_reason(reason), "WeightStepError: [5/7 read] No valid")
        item = WorkItem(
            source=SourceRecord("VehicleDetection_Gate1", 0, 812),
            event={"trigger_code": "in_gate1_lane1", "section_id": "abcdef123456", "detected_object_id": "29E90692"},
        )
        self.assertEqual(
            describe(item), "offset=812 trigger=in_gate1_lane1 section=abcdef12 plate=29E90692"
        )


if __name__ == "__main__":
    unittest.main()
