"""/api/plate-check: listing and reading the per-frame plate data saved by the detector.

Run from the repository root:
    python -m unittest discover -s config_ui/backend/tests -t config_ui/backend
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import HTTPException  # noqa: E402

from moda_ui import plate_check_api as api  # noqa: E402
from moda_ui import settings as settings_module  # noqa: E402
from moda_ui.security import generate_secret_key  # noqa: E402
from moda_ui.settings import load_settings, override_settings  # noqa: E402

LANE = "in_gate1_lane1"
USER = {"id": 1, "username": "test", "role": "viewer"}


def frame(num: int, plate: str | None, valid: bool) -> dict:
    return {
        "frame_num": num,
        "ts": 1764000000.0 + num / 25,
        "plate_label": plate,
        "plate_confidences": [0.9] * len(plate or ""),
        "valid_plate": valid,
        "in_capture_zone": num > 2,
        "vehicle_confidence": 0.8,
        "box": [10, 20, 300, 400],
    }


class PlateCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "moda-jetson"
        (self.root / "runtime").mkdir(parents=True)
        previous_env = {key: os.environ.get(key) for key in ("MODA_PROJECT_DIR", "MODA_CONFIG_SECRET", "MODA_CONFIG_DB")}
        previous_settings = settings_module._settings
        os.environ["MODA_PROJECT_DIR"] = str(self.root)
        os.environ["MODA_CONFIG_DB"] = str(self.root / "data" / "test.db")
        os.environ.setdefault("MODA_CONFIG_SECRET", generate_secret_key())
        override_settings(load_settings())

        def restore():
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            settings_module._settings = previous_settings

        self.addCleanup(restore)

    def make_vehicle(self, lane: str, frames: list[dict], summary: dict | None = None, age: float = 0) -> str:
        section = str(uuid.uuid4())
        directory = self.root / "runtime" / "vehicle" / lane / "frames" / section
        directory.mkdir(parents=True)
        lines = [json.dumps(item) for item in frames]
        (directory / "frames.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for item in frames:
            (directory / f"{item['frame_num']:07d}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        if summary is not None:
            (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        stamp = time.time() - age
        for path in [*directory.iterdir(), directory]:
            os.utime(path, (stamp, stamp))
        return section

    def test_list_keeps_five_newest_per_lane_with_counts(self):
        sections = [
            self.make_vehicle(LANE, [frame(1, "29C12345", True), frame(2, None, False)], age=100 - i)
            for i in range(7)
        ]
        self.make_vehicle("out_gate1_lane2", [frame(5, "30A11111", True)])
        (self.root / "runtime" / "vehicle" / LANE / "frames" / "not-a-uuid").mkdir()

        data = api.list_vehicles(USER)

        by_lane = {item["lane"]: item["vehicles"] for item in data["lanes"]}
        self.assertEqual({LANE, "out_gate1_lane2"}, set(by_lane))
        listed = [vehicle["section_id"] for vehicle in by_lane[LANE]]
        self.assertEqual(list(reversed(sections))[:5], listed)
        first = by_lane[LANE][0]
        self.assertEqual(2, first["frame_count"])
        self.assertEqual(1, first["valid_frame_count"])
        self.assertEqual(frame(1, None, False)["ts"], first["first_ts"])
        self.assertEqual(frame(2, None, False)["ts"], first["last_ts"])
        self.assertIsNone(first["summary"])

    def test_detail_returns_sorted_frames_with_image_paths_and_summary(self):
        summary = {
            "trigger_code": LANE,
            "plate": "29C12345",
            "raw_plate": "29C1234",
            "capture_frame": 3,
            "image_path": "/data/vehicle/in_gate1_lane1/20261125/x.jpg",
            "plate_votes": {"29C12345": 2, "29C1234": 1},
        }
        section = self.make_vehicle(LANE, [frame(3, "29C12345", True), frame(1, "29C1234", False)], summary)
        directory = self.root / "runtime" / "vehicle" / LANE / "frames" / section
        with (directory / "frames.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"frame_num": 4, "ts"')  # half-written last line is skipped

        data = api.vehicle_frames(LANE, section.upper(), USER)

        self.assertEqual(section, data["section_id"])
        self.assertEqual("29C12345", data["summary"]["plate"])
        self.assertEqual([1, 3], [item["frame_num"] for item in data["frames"]])
        self.assertEqual(f"vehicle/{LANE}/frames/{section}/0000001.jpg", data["frames"][0]["image_path"])
        # The published image does not exist here: fall back to the capture frame crop.
        self.assertEqual(f"vehicle/{LANE}/frames/{section}/0000003.jpg", data["capture_image_path"])

        published = self.root / "runtime" / "vehicle" / LANE / "20261125" / "x.jpg"
        published.parent.mkdir(parents=True)
        published.write_bytes(b"\xff\xd8\xff\xd9")
        self.assertEqual(f"vehicle/{LANE}/20261125/x.jpg", api.vehicle_frames(LANE, section, USER)["capture_image_path"])

    def test_bad_lane_or_section_is_rejected(self):
        section = self.make_vehicle(LANE, [frame(1, "29C12345", True)])
        for lane, section_id in (
            ("..", section),
            ("in_gate1_lane1/..", section),
            ("IN_GATE1", section),
            (LANE, "../../etc"),
            (LANE, "1234"),
        ):
            with self.subTest(lane=lane, section_id=section_id):
                with self.assertRaises(HTTPException) as raised:
                    api.vehicle_frames(lane, section_id, USER)
                self.assertEqual(400, raised.exception.status_code)
        with self.assertRaises(HTTPException) as raised:
            api.vehicle_frames("out_gate1_lane1", section, USER)
        self.assertEqual(404, raised.exception.status_code)

    def test_missing_runtime_dirs_give_empty_list(self):
        self.assertEqual({"lanes": []}, api.list_vehicles(USER))

    def test_routes_require_login(self):
        from moda_ui.auth import current_user

        paths = set()
        for route in api.router.routes:
            paths.add(route.path)
            calls = [dependency.call for dependency in route.dependant.dependencies]
            self.assertIn(current_user, calls, route.path)
        self.assertEqual({"/api/plate-check", "/api/plate-check/{lane}/{section_id}"}, paths)

if __name__ == "__main__":
    unittest.main()
