"""Import the real config files, regenerate them, and check nothing changes in meaning.

Run from the moda-jetson directory:
    python -m unittest discover -s config_ui/backend/tests -t config_ui/backend
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))

from moda_ui import applier, repository as repo, schemas  # noqa: E402
from moda_ui.db import connect, init_schema  # noqa: E402
from moda_ui.envfile import env_values_for_runtime, update_env_text  # noqa: E402
from moda_ui.generator import build_files  # noqa: E402
from moda_ui.importer import import_from_files  # noqa: E402
from moda_ui.security import decrypt_url, generate_secret_key, mask_url  # noqa: E402
from moda_ui.settings import load_settings, override_settings  # noqa: E402
from moda_ui.validation import validate_files  # noqa: E402


class ProjectCase(unittest.TestCase):
    """A throw-away copy of the project, imported into a fresh database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "moda-jetson"
        # Copy only what the UI touches; never the real .env files with credentials.
        for name in ("config", "traffic_core", "post_analysis"):
            shutil.copytree(PROJECT / name, root / name)
        for name in ("compose.yaml", "compose.analysis.yaml", ".env.example", ".env.jetson.example"):
            shutil.copy(PROJECT / name, root / name)
        (root / "models").mkdir()
        os.environ["MODA_PROJECT_DIR"] = str(root)
        os.environ["MODA_CONFIG_DB"] = str(root / "data" / "test.db")
        os.environ["MODA_CONFIG_SECRET"] = generate_secret_key()
        self.settings = load_settings()
        override_settings(self.settings)
        self.root = root
        self.conn = connect(self.settings.database_path)
        self.addCleanup(self.conn.close)
        init_schema(self.conn)
        import_from_files(self.conn, self.settings)


class RoundTripTests(ProjectCase):
    def test_generated_json_matches_original_meaning(self):
        files = build_files(self.conn, self.settings)
        for relative, content in files.items():
            if not relative.endswith(".json"):
                continue
            original = json.loads((self.root / relative).read_text(encoding="utf-8"))
            generated = json.loads(content)
            # The generator writes defaults explicitly; the originals may omit them.
            for key, value in generated.items():
                if key in original:
                    self.assertEqual(original[key], value, f"{relative}: {key}")

    def test_generated_plate_lists_keep_all_plates(self):
        files = build_files(self.conn, self.settings)
        for name in ("standard.txt", "lane2.txt"):
            original = (self.root / "config/reference_plates" / name).read_text(encoding="utf-8").split()
            # Duplicates are dropped on import; matching against the list is unaffected.
            self.assertEqual(list(dict.fromkeys(original)), files[f"config/reference_plates/{name}"].split())

    def test_generated_files_pass_runtime_loaders(self):
        files = build_files(self.conn, self.settings)
        self.assertEqual([], validate_files(self.settings, files))

    def test_env_contains_decrypted_camera_urls(self):
        files = build_files(self.conn, self.settings)
        env = env_values_for_runtime(files[".env"])
        self.assertTrue(env["MODA_IN_GATE1_LAND1_CAMERA_URL"].startswith("rtsp://"))
        analysis = env_values_for_runtime(files[".env.jetson"])
        self.assertIn("MODA_WEIGHT_CAMERA_URL", analysis)
        self.assertIn("MODA_ANALYSIS_BASE_IMAGE", analysis)  # unrelated keys are kept

    def test_confidence_change_affects_only_that_detector(self):
        site = repo.get_site(self.conn, "in_gate1_land1")
        data = schemas.SiteIn(**{key: site[key] for key in schemas.SiteIn.model_fields})
        data.vehicle_confidence = 0.55
        repo.update_site(self.conn, "in_gate1_land1", data)
        # Write the baseline files first so only the confidence differs.
        baseline = build_files(self.conn, self.settings)
        result = applier.preview(self.conn, self.settings)
        current = {path: baseline[path] for path in baseline}
        current["config/sites/in_gate1_land1.json"] = "{}"
        services = applier.affected_services(self.conn, self.settings, baseline, current)
        self.assertEqual({"in_gate1_land1"}, services["detector"])
        self.assertEqual(set(), services["analysis"])
        self.assertEqual([], result["errors"])

    def test_camera_url_change_restarts_services_using_it(self):
        baseline = build_files(self.conn, self.settings)
        camera = next(c for c in repo.list_cameras(self.conn) if c["env_name"] == "MODA_WEIGHT_CAMERA_URL")
        repo.update_camera(
            self.conn,
            camera["id"],
            schemas.CameraIn(
                code=camera["code"], name=camera["name"], purpose="weight",
                env_name=camera["env_name"], rtsp_url="rtsp://u:p@10.0.0.9:554/new",
            ),
        )
        generated = build_files(self.conn, self.settings)
        services = applier.affected_services(self.conn, self.settings, generated, baseline)
        self.assertEqual({"weight_reader"}, services["analysis"])
        self.assertEqual(set(), services["detector"])

    def test_zone_outside_stream_is_rejected_by_validation(self):
        repo.set_zone(self.conn, "in_gate1_land1", 0, "capture", [[0, 0], [5000, 0], [10, 10]])
        errors = validate_files(self.settings, build_files(self.conn, self.settings))
        self.assertTrue(any("ngoài khung" in error for error in errors))

    def test_snapshot_roundtrip(self):
        snapshot = repo.export_snapshot(self.conn)
        repo.set_zone(self.conn, "in_gate1_land1", 0, "capture", [[1, 1], [2, 1], [2, 2]])
        repo.import_snapshot(self.conn, snapshot)
        self.assertEqual(snapshot, repo.export_snapshot(self.conn))


class ApplyTests(ProjectCase):
    """apply() with docker replaced by fakes."""

    def _fake_docker(self, *, healthy: bool):
        from unittest import mock

        from moda_ui import docker_ops

        deployed = {"in_gate1_land1", "weight_reader"}
        calls: list[list[str]] = []

        def states(_settings, project):
            return {
                name: {"state": "running" if healthy else "exited", "status": "", "container": name, "health": ""}
                for name in deployed
                if (name.endswith(("_reader", "_recognition")) == (project.kind == docker_ops.ANALYSIS))
            }

        patches = [
            mock.patch.object(docker_ops, "service_states", side_effect=states),
            mock.patch.object(docker_ops, "recreate", side_effect=lambda _s, _p, names: calls.append(list(names))),
            mock.patch.object(docker_ops, "restart_count", return_value=0),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.settings = self.settings.__class__(**{**self.settings.__dict__, "health_wait_seconds": 0})
        override_settings(self.settings)
        return calls

    def _write_baseline(self):
        for relative, content in build_files(self.conn, self.settings).items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def _change_confidence(self):
        site = repo.get_site(self.conn, "in_gate1_land1")
        data = schemas.SiteIn(**{key: site[key] for key in schemas.SiteIn.model_fields})
        data.vehicle_confidence = 0.42
        repo.update_site(self.conn, "in_gate1_land1", data)

    def test_apply_writes_file_and_recreates_only_affected_deployed_service(self):
        self._write_baseline()
        calls = self._fake_docker(healthy=True)
        self._change_confidence()
        result = applier.apply(self.conn, self.settings, user_id=None, note="t")
        self.assertTrue(result["ok"], result)
        self.assertEqual([["in_gate1_land1"]], calls)
        written = json.loads((self.root / "config/sites/in_gate1_land1.json").read_text(encoding="utf-8"))
        self.assertEqual(0.42, written["vehicle_confidence"])
        self.assertEqual("applied", repo.list_revisions(self.conn)[0]["status"])

    def test_failed_health_check_restores_previous_files(self):
        self._write_baseline()
        before = (self.root / "config/sites/in_gate1_land1.json").read_text(encoding="utf-8")
        calls = self._fake_docker(healthy=False)
        self._change_confidence()
        result = applier.apply(self.conn, self.settings, user_id=None, note="t")
        self.assertFalse(result["ok"])
        self.assertEqual(before, (self.root / "config/sites/in_gate1_land1.json").read_text(encoding="utf-8"))
        self.assertEqual([["in_gate1_land1"], ["in_gate1_land1"]], calls)  # new config, then old again
        self.assertEqual("failed", repo.list_revisions(self.conn)[0]["status"])

    def test_invalid_config_is_rejected_before_writing(self):
        self._write_baseline()
        self._fake_docker(healthy=True)
        repo.set_zone(self.conn, "in_gate1_land1", 0, "capture", [[0, 0], [9000, 0], [10, 10]])
        with self.assertRaises(applier.ApplyRejected):
            applier.apply(self.conn, self.settings, user_id=None, note="t")
        written = json.loads((self.root / "config/sites/in_gate1_land1.json").read_text(encoding="utf-8"))
        self.assertNotIn([9000, 0], written["capture_zones"]["0"])

    def test_rollback_restores_previous_revision(self):
        self._write_baseline()
        self._fake_docker(healthy=True)
        baseline = repo.create_revision(
            self.conn, user_id=None, note="baseline", snapshot=repo.export_snapshot(self.conn),
            files_diff="", services=[], status="applied", log="",
        )
        self._change_confidence()
        changed = applier.apply(self.conn, self.settings, user_id=None, note="change")
        self.assertTrue(changed["ok"])

        result = applier.rollback(self.conn, self.settings, baseline, user_id=None)
        self.assertTrue(result["ok"], result)
        written = json.loads((self.root / "config/sites/in_gate1_land1.json").read_text(encoding="utf-8"))
        self.assertEqual(0.7, written["vehicle_confidence"])
        self.assertEqual(0.7, repo.get_site(self.conn, "in_gate1_land1")["vehicle_confidence"])
        statuses = {r["id"]: r["status"] for r in repo.list_revisions(self.conn)}
        self.assertEqual("rolled_back", statuses[changed["revision_id"]])
        self.assertEqual("applied", statuses[result["revision_id"]])


class SmallUnitTests(unittest.TestCase):
    def test_mask_url(self):
        self.assertEqual("rtsp://admin:***@10.0.0.1:554/s", mask_url("rtsp://admin:pw@10.0.0.1:554/s"))

    def test_env_update_keeps_comments_and_escapes_dollar(self):
        text = "# comment\nA=1\nB=2\n"
        updated = update_env_text(text, {"B": "pa$s", "C": "3"})
        self.assertIn("# comment", updated)
        self.assertIn("B=pa$$s", updated)
        self.assertEqual("pa$s", env_values_for_runtime(updated)["B"])
        self.assertEqual("3", env_values_for_runtime(updated)["C"])

    def test_encryption_roundtrip(self):
        os.environ["MODA_CONFIG_SECRET"] = generate_secret_key()
        override_settings(load_settings())
        from moda_ui.security import encrypt_url

        self.assertEqual("rtsp://x", decrypt_url(encrypt_url("rtsp://x")))


if __name__ == "__main__":
    unittest.main()
