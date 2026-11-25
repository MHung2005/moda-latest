import json
import tempfile
from pathlib import Path
import unittest

from traffic_core.cli import load_sites, parse_args
from traffic_core.config import SiteConfigError


ENVIRONMENT = {
    "CAMERA_A": "rtsp://a",
    "CAMERA_B": "rtsp://b",
    "MODA_KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
}


def site(name, camera_env):
    return {
        "name": name,
        "camera_url_envs": [camera_env],
        "trigger_codes": {"0": name},
        "capture_zones": {"0": [[0, 0], [10, 0], [10, 10]]},
        "inference_zones": {"0": [[0, 0], [20, 0], [20, 20]]},
        "models": {
            "tracker": "/models/tracker.txt",
            "vehicle_detection": f"/models/{name}.txt",
            "plate_detection": "/models/plate.txt",
            "plate_recognition": "/models/characters.txt",
        },
        "reference_plate_path": "reference.txt",
        "utc_offset_hours": 7,
    }


class LoadSitesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.lane1 = self.write("in_gate1_lane1", site("in-gate1-lane1", "CAMERA_A"))
        self.lane2 = self.write("in_gate1_lane2", site("in-gate1-lane2", "CAMERA_B"))

    def write(self, stem, value):
        path = self.root / f"{stem}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_site_flag_can_repeat(self):
        args = parse_args(["--site", "a.json", "--site", "b.json"])
        self.assertEqual([Path("a.json"), Path("b.json")], args.site)

    def test_single_site_keeps_its_image_root(self):
        (only,) = load_sites([self.lane1], ENVIRONMENT)
        self.assertEqual("/data/vehicle", only.vehicle_image_root)

    def test_each_site_of_a_gate_gets_its_own_image_folder(self):
        lane1, lane2 = load_sites([self.lane1, self.lane2], ENVIRONMENT)
        self.assertEqual("/data/vehicle/in_gate1_lane1", lane1.vehicle_image_root)
        self.assertEqual("/data/vehicle/in_gate1_lane2", lane2.vehicle_image_root)
        self.assertEqual(("rtsp://a",), lane1.camera_urls)
        self.assertEqual(("rtsp://b",), lane2.camera_urls)
        self.assertEqual("/models/in-gate1-lane2.txt", lane2.vehicle_detection_config_path)

    def test_duplicate_site_is_rejected(self):
        with self.assertRaises(SiteConfigError):
            load_sites([self.lane1, self.lane1], ENVIRONMENT)

    def test_missing_camera_of_any_site_is_rejected(self):
        environment = {k: v for k, v in ENVIRONMENT.items() if k != "CAMERA_B"}
        with self.assertRaises(SiteConfigError):
            load_sites([self.lane1, self.lane2], environment)


if __name__ == "__main__":
    unittest.main()
