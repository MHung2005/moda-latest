import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid

import numpy as np

from traffic_core.frame_dump import FrameDumpWriter, keep_vehicles_from_env


def _record(frame_num, label="29E25602", valid=True):
    return {
        "frame_num": frame_num,
        "ts": "2026-11-25T03:37:18.000Z",
        "plate_label": label,
        "plate_confidences": [0.9] * len(label),
        "valid_plate": valid,
        "in_capture_zone": False,
        "vehicle_confidence": 0.8,
        "box": [0, 0, 64, 48],
    }


def _crop():
    return np.zeros((48, 64, 3), dtype=np.uint8)


class FrameDumpTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "frames"

    def tearDown(self):
        self._tmp.cleanup()

    def _writer(self, keep=5, **kwargs):
        return FrameDumpWriter(self.root, keep, start=False, **kwargs)

    def test_writes_frames_jsonl_and_summary(self):
        writer = self._writer()
        section = str(uuid.uuid4())
        writer.process(("frame", section, _crop(), _record(12)))
        writer.process(("frame", section, _crop(), _record(13, "19813791", valid=False)))
        writer.process(("summary", section, None, {"plate": "29E25602", "plate_votes": {"29E25602": 1}}))

        directory = self.root / section
        self.assertTrue((directory / "0000012.jpg").is_file())
        self.assertTrue((directory / "0000013.jpg").is_file())
        lines = [json.loads(line) for line in (directory / "frames.jsonl").read_text().splitlines()]
        self.assertEqual([line["frame_num"] for line in lines], [12, 13])
        self.assertEqual(lines[1]["image"], "0000013.jpg")
        self.assertFalse(lines[1]["valid_plate"])
        self.assertEqual(json.loads((directory / "summary.json").read_text())["plate"], "29E25602")
        self.assertFalse((directory / "summary.json.tmp").exists())

    def test_keeps_only_most_recent_vehicles(self):
        writer = self._writer(keep=5)
        sections = [str(uuid.uuid4()) for _ in range(7)]
        for index, section in enumerate(sections):
            writer.process(("frame", section, _crop(), _record(index)))
            past = time.time() - 100 + index  # older vehicles have older mtimes
            os.utime(self.root / section, (past, past))

        remaining = sorted(path.name for path in self.root.iterdir())
        self.assertEqual(len(remaining), 5)
        self.assertEqual(remaining, sorted(sections[2:]))

    def test_full_queue_drops_instead_of_blocking(self):
        writer = self._writer(max_queue=2)  # thread not started: nothing drains
        section = str(uuid.uuid4())
        started = time.monotonic()
        results = [writer.submit(section, _crop(), _record(n)) for n in range(5)]
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(results, [True, True, False, False, False])
        self.assertEqual(writer.dropped, 3)

    def test_write_error_does_not_stop_the_thread(self):
        writer = FrameDumpWriter(self.root, 5)
        try:
            writer.submit("../escape", _crop(), _record(1))  # rejected by section_id check
            section = str(uuid.uuid4())
            writer.submit(section, _crop(), _record(2))
            deadline = time.monotonic() + 5
            while writer.written < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            writer.close()
        self.assertEqual(writer.errors, 1)
        self.assertEqual(writer.written, 1)
        self.assertFalse((Path(self._tmp.name) / "escape").exists())
        self.assertTrue((self.root / section / "0000002.jpg").is_file())

    def test_keep_vehicles_from_env(self):
        self.assertEqual(keep_vehicles_from_env({}), 5)
        self.assertEqual(keep_vehicles_from_env({"MODA_FRAME_DUMP_KEEP": "0"}), 0)
        self.assertEqual(keep_vehicles_from_env({"MODA_FRAME_DUMP_KEEP": "3"}), 3)
        self.assertEqual(keep_vehicles_from_env({"MODA_FRAME_DUMP_KEEP": "x"}), 0)
        with self.assertRaises(ValueError):
            FrameDumpWriter(self.root, 0, start=False)


if __name__ == "__main__":
    unittest.main()
