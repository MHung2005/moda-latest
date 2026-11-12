import json
import tempfile
from pathlib import Path
import unittest

from traffic_core.config import SiteConfigError, load_site_config


class SiteConfigTests(unittest.TestCase):
    def write_config(self, value):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "site.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def valid_config(self):
        return {
            "name": "test-site",
            "camera_url_envs": ["TEST_CAMERA_URL"],
            "trigger_codes": {"0": "in_gate"},
            "capture_zones": {"0": [[0, 0], [10, 0], [10, 10]]},
            "inference_zones": {"0": [[0, 0], [20, 0], [20, 20]]},
            "models": {
                "tracker": "/models/tracker.txt",
                "vehicle_detection": "/models/vehicle.txt",
                "plate_detection": "/models/plate.txt",
                "plate_recognition": "/models/characters.txt",
            },
            "reference_plate_path": "reference.txt",
            "vehicle_confidence": 0.8,
        }

    def test_loads_and_resolves_camera_from_environment(self):
        config = load_site_config(
            self.write_config(self.valid_config()),
            {
                "TEST_CAMERA_URL": "rtsp://camera",
                "MODA_KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
            },
        )

        self.assertEqual(config.camera_urls, ("rtsp://camera",))
        self.assertEqual(config.kafka_bootstrap_servers, "kafka:9092")
        self.assertEqual(config.trigger_codes[0], "in_gate")

    def test_missing_camera_environment_is_explicit(self):
        with self.assertRaisesRegex(SiteConfigError, "TEST_CAMERA_URL"):
            load_site_config(self.write_config(self.valid_config()), {})

    def test_rejects_zone_keys_that_do_not_match_sources(self):
        raw = self.valid_config()
        raw["capture_zones"] = {"1": [[0, 0], [10, 0], [10, 10]]}

        with self.assertRaisesRegex(SiteConfigError, "capture_zones keys"):
            load_site_config(
                self.write_config(raw),
                {
                    "TEST_CAMERA_URL": "rtsp://camera",
                    "MODA_KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
                },
            )

    def test_repository_site_configs_are_valid(self):
        repository = Path(__file__).resolve().parents[1]
        paths = sorted((repository / "config" / "sites").glob("*.json"))
        configs = []
        for path in paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            environment = {
                raw["camera_url_envs"][0]: "rtsp://camera",
                "MODA_KAFKA_BOOTSTRAP_SERVERS": "kafka:9092",
            }
            configs.append(load_site_config(path, environment))

        self.assertEqual(len({config.name for config in configs}), 4)

    def test_detector_and_post_analysis_contracts_are_connected(self):
        repository = Path(__file__).resolve().parents[1]
        post_raw = json.loads(
            (repository / "config" / "post_analysis.json").read_text(
                encoding="utf-8"
            )
        )
        detector_topics = set()
        detector_triggers = set()
        for path in sorted((repository / "config" / "sites").glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            detector_topics.add(raw.get("output_topic", "VehicleDetection_Gate1"))
            detector_triggers.update(raw["trigger_codes"].values())

        self.assertEqual(detector_topics, {post_raw["input_topic"]})
        self.assertEqual(detector_triggers, set(post_raw["weight"]["crops"]))
        self.assertEqual(
            detector_triggers,
            set(post_raw["weight"]["analysis_codes"]),
        )

        model_root = repository / "models" / "triton"
        model_names = {post_raw["weight"]["model"]}
        for model_name in model_names:
            self.assertTrue(
                (model_root / model_name / "1" / "model.onnx").is_file(),
                f"missing Triton model: {model_name}",
            )

    def test_active_deepstream_model_assets_and_engine_cache_are_connected(self):
        repository = Path(__file__).resolve().parents[1]
        cache_targets = set()
        checked_configs = set()

        for site_path in sorted((repository / "config" / "sites").glob("*.json")):
            site = json.loads(site_path.read_text(encoding="utf-8"))
            for container_config in site["models"].values():
                prefix = "/home/jetson/"
                self.assertTrue(container_config.startswith(prefix))
                config_path = repository / container_config.removeprefix(prefix)
                self.assertTrue(config_path.is_file(), f"missing config: {config_path}")
                if config_path in checked_configs:
                    continue
                checked_configs.add(config_path)

                for raw_line in config_path.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key == "model-engine-file" and value.startswith(
                        "/cache/engines/"
                    ):
                        cache_targets.add(value)
                        continue
                    if key not in {
                        "onnx-file",
                        "model-engine-file",
                        "labelfile-path",
                        "custom-lib-path",
                        "ll-config-file",
                        "ll-lib-file",
                    }:
                        continue
                    if value.startswith("/opt/nvidia/"):
                        continue
                    if value.startswith("/opt/moda/"):
                        # Built into the detector image (DeepStream 7.1 YOLO parser).
                        dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")
                        self.assertIn(value, dockerfile, f"{config_path}: {key}={value}")
                        continue
                    if value.startswith(prefix):
                        dependency = repository / value.removeprefix(prefix)
                    else:
                        dependency = config_path.parent / value
                    self.assertTrue(
                        dependency.exists(),
                        f"{config_path}: missing {key}={value}",
                    )

        # Four vehicle engines (one per lane) plus the shared plate and character engines.
        self.assertEqual(len(cache_targets), 6)
        compose = (repository / "compose.yaml").read_text(encoding="utf-8")
        self.assertEqual(
            compose.count("./runtime/engines:/cache/engines"),
            4,
        )


if __name__ == "__main__":
    unittest.main()
