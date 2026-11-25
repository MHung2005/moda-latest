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


def lane_worker(_config, event, stop_event):
    """'wait' events block until superseded, then succeed only if they had a stable value."""
    if not event.get("wait"):
        return
    if not stop_event.wait(10):
        raise RuntimeError("never superseded")
    if not event.get("stable"):
        raise RuntimeError("cancelled before a stable value")


class _StopLoop(Exception):
    pass


class _FakeMessage:
    def __init__(self, offset, event):
        self.topic, self.partition, self.offset = "events", 0, offset
        self.value = json.dumps(event).encode("utf-8")


class _FakeConsumer:
    def __init__(self, messages, committed):
        self._batches = [{("events", 0): messages}]
        self._committed = committed
        self._polls = 0

    def poll(self, timeout_ms, max_records):
        import time

        self._polls += 1
        if self._batches:
            return self._batches.pop(0)
        if self._committed.get(("events", 0)) == 2 or self._polls > 400:
            raise _StopLoop
        time.sleep(0.05)
        return {}

    def close(self):
        pass


def run_two_events_same_lane(first_is_stable):
    """Offset 0 is reading lane1 when offset 1 (lane1) arrives; returns (dlq payloads, commits)."""
    from unittest import mock

    from post_analysis import runtime
    from post_analysis.config import KafkaConfig, WorkerConfig

    dlq, committed = [], {}
    messages = [
        _FakeMessage(0, {"lane": "lane1", "wait": True, "stable": first_is_stable}),
        _FakeMessage(1, {"lane": "lane1"}),
    ]
    kafka = KafkaConfig("kafka:9092", "events", "out", "dlq", "group", 5)
    worker = WorkerConfig(
        max_workers=2, event_timeout_seconds=30, max_attempts=2,
        retry_backoff_seconds=0, cancellation_grace_seconds=1,
    )
    with mock.patch.object(runtime, "create_consumer", lambda *a: _FakeConsumer(messages, committed)), \
            mock.patch.object(runtime, "create_producer", lambda *a: mock.Mock()), \
            mock.patch.object(runtime, "publish", lambda _p, _t, payload, **_k: dlq.append(payload)), \
            mock.patch.object(runtime, "commit_offsets", lambda _c, offsets: committed.update(offsets)):
        try:
            runtime.run_worker_service(
                service_name="weight_reader", kafka=kafka, worker=worker, handler=lane_worker,
                handler_config=None, routing_key=lambda event: event.get("lane"),
                supersede_same_key=True,
            )
        except _StopLoop:
            pass
    return dlq, committed


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

    def test_superseded_job_with_stable_value_finishes_instead_of_failing(self):
        dlq, committed = run_two_events_same_lane(first_is_stable=True)
        self.assertEqual(dlq, [])
        self.assertEqual(committed.get(("events", 0)), 2)

    def test_superseded_job_without_stable_value_goes_to_dlq_once(self):
        dlq, committed = run_two_events_same_lane(first_is_stable=False)
        self.assertEqual([payload["source_offset"] for payload in dlq], [0])
        self.assertTrue(dlq[0]["error"].startswith("Superseded by offset 1 for lane1: "), dlq[0]["error"])
        self.assertIn("cancelled before a stable value", dlq[0]["error"])
        self.assertEqual(dlq[0]["attempts"], 1)
        self.assertEqual(committed.get(("events", 0)), 2)


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

    def test_stable_reading_requires_min_count(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        for _ in range(29):
            accumulator.add(6040, FakeFrame("a"))
        self.assertIsNone(accumulator.stable_reading(30))
        accumulator.add(6040, FakeFrame("b"))
        reading = accumulator.stable_reading(30)
        self.assertEqual(reading.value, 6040)
        self.assertEqual(accumulator.count(6040), 30)

    def test_stable_reading_none_without_readings(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        self.assertIsNone(accumulator.stable_reading(1))

    def test_accumulator_returns_none_without_readings(self):
        accumulator = WeightAccumulator(drop_threshold=3000, max_drop_count=70)
        self.assertIsNone(accumulator.selected())


class _ScriptedCapture:
    """Fake cv2 capture: returns the given read results in order, then fails."""

    def __init__(self, results):
        self._results = list(results)
        self.released = False

    def read(self):
        return self._results.pop(0) if self._results else (False, None)

    def release(self):
        self.released = True


class CameraReconnectTests(unittest.TestCase):
    def _reconnecting(self, first, reopened, max_reconnects=3, should_continue=lambda: True):
        from post_analysis.capture import ReconnectingCapture

        opened = []

        def open_fn():
            item = reopened.pop(0)
            if isinstance(item, Exception):
                raise item
            opened.append(item)
            return item

        return ReconnectingCapture(open_fn, first, max_reconnects, should_continue), opened

    def test_read_failure_reopens_stream_and_keeps_reading(self):
        # The production stall: one frame, then the stream goes silent.
        first = _ScriptedCapture([(True, "f1"), (False, None)])
        second = _ScriptedCapture([(True, "f2"), (True, "f3")])
        capture, opened = self._reconnecting(first, [second])

        self.assertEqual([capture.read() for _ in range(3)], [(True, "f1"), (True, "f2"), (True, "f3")])
        self.assertEqual(capture.reconnects, 1)
        self.assertTrue(first.released)
        self.assertEqual(opened, [second])

    def test_failed_reopen_counts_as_reconnect_and_retries(self):
        first = _ScriptedCapture([(False, None)])
        good = _ScriptedCapture([(True, "f1")])
        capture, _ = self._reconnecting(first, [RuntimeError("Could not open RTSP camera"), good])

        self.assertEqual(capture.read(), (True, "f1"))
        self.assertEqual(capture.reconnects, 2)

    def test_gives_up_after_max_reconnects(self):
        dead = [_ScriptedCapture([]) for _ in range(3)]
        capture, opened = self._reconnecting(_ScriptedCapture([]), list(dead), max_reconnects=2)

        self.assertEqual(capture.read(), (False, None))
        self.assertEqual(capture.reconnects, 2)
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(c.released for c in opened))

    def test_does_not_reconnect_when_cancelled_or_out_of_time(self):
        capture, opened = self._reconnecting(
            _ScriptedCapture([]), [_ScriptedCapture([(True, "f")])], should_continue=lambda: False
        )

        self.assertEqual(capture.read(), (False, None))
        self.assertEqual(capture.reconnects, 0)
        self.assertEqual(opened, [])

    def test_release_closes_current_stream_once(self):
        first = _ScriptedCapture([(True, "f1")])
        capture, _ = self._reconnecting(first, [])
        capture.release()
        capture.release()
        self.assertTrue(first.released)

    def test_open_with_retry(self):
        from post_analysis.capture import open_with_retry

        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("Could not open RTSP camera")
            return "capture"

        self.assertEqual(open_with_retry(flaky, 2, lambda: True), "capture")
        calls.clear()
        with self.assertRaises(RuntimeError):
            open_with_retry(lambda: (_ for _ in ()).throw(RuntimeError("down")), 2, lambda: True)
        with self.assertRaises(RuntimeError):  # cancelled: no second attempt
            open_with_retry(flaky, 2, lambda: False)
        self.assertEqual(len(calls), 1)


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
