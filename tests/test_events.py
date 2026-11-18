import unittest

from traffic_core.events import build_vehicle_message
from traffic_core.kafka import publish


class FakeFuture:
    def __init__(self):
        self.timeout = None

    def get(self, timeout):
        self.timeout = timeout
        return {"offset": 7}


class FakeProducer:
    def __init__(self):
        self.future = FakeFuture()
        self.call = None

    def send(self, **kwargs):
        self.call = kwargs
        return self.future


class VehicleEventTests(unittest.TestCase):
    def test_preserves_legacy_payload_contract(self):
        message = build_vehicle_message(
            source_id=0,
            trigger_code="out_gate1_lane1",
            section_id="section-1",
            plate_number="30A12345",
            image_path="vehicle/20261012/image.jpg",
            timestamp="2026-10-12T00:00:00.000Z",
            frame_number=50,
            video_fps=10,
            raw_plate="30A12345",
            best_match="30A12345",
            match_score=1.0,
            requires_search=False,
        )

        self.assertEqual(message["times_in_video"], 5.0)
        self.assertEqual(message["trigger_cam_id"], 0)
        self.assertEqual(message["analysis_cam_id"], -1)
        self.assertEqual(message["detected_object_id"], "30A12345")

    def test_trigger_publish_waits_for_broker_acknowledgement(self):
        producer = FakeProducer()
        payload = {"trigger_code": "in_gate1_lane1"}

        metadata = publish(
            producer,
            "VehicleDetection_Gate1",
            0,
            payload,
            timeout_seconds=12,
        )

        self.assertEqual(metadata, {"offset": 7})
        self.assertEqual(producer.future.timeout, 12)
        self.assertEqual(producer.call["value"], payload)


if __name__ == "__main__":
    unittest.main()
