"""Debug dump of every plate-reading frame of the most recent vehicles of a lane.

For each vehicle (section_id) the detector hands over a crop of the vehicle on every frame
where it is inside the inference zone, plus what the plate model read on that frame. A
background thread draws the reading on the crop and writes:

    <root>/<section_id>/<frame_num:07d>.jpg
    <root>/<section_id>/frames.jsonl   one JSON object per saved frame
    <root>/<section_id>/summary.json   final (voted) plate etc., written when the event is published

Only the `keep_vehicles` most recent section directories are kept.

This is a side channel: submit()/finish() never block (a full queue drops the frame) and
never raise, so the DeepStream probe and the Kafka event flow are unaffected by it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import threading
import time
from typing import Any


logger = logging.getLogger(__name__)

ENV_KEEP_VEHICLES = "MODA_FRAME_DUMP_KEEP"
DEFAULT_KEEP_VEHICLES = 5
SECTION_ID_PATTERN = re.compile(r"^[0-9a-fA-F-]{8,64}$")
_STOP = object()


def keep_vehicles_from_env(environ=os.environ) -> int:
    """Number of vehicles to keep per lane; 0 (or an invalid value) disables the dump."""
    raw = environ.get(ENV_KEEP_VEHICLES, str(DEFAULT_KEEP_VEHICLES))
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; frame dump disabled", ENV_KEEP_VEHICLES, raw)
        return 0


class FrameDumpWriter:
    def __init__(
        self,
        root: str | Path,
        keep_vehicles: int,
        *,
        # ~24 vehicle crops (a few MB each) bound the extra memory of a lane.
        max_queue: int = 24,
        jpeg_quality: int = 80,
        cv2_module=None,
        start: bool = True,
    ) -> None:
        if keep_vehicles < 1:
            raise ValueError("keep_vehicles must be at least 1")
        self.root = Path(root)
        self.keep_vehicles = keep_vehicles
        self.jpeg_quality = jpeg_quality
        self._cv2 = cv2_module
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._known_sections: set[str] = set()
        self.dropped = 0
        self.written = 0
        self.errors = 0
        self._last_drop_log = 0.0
        self._thread = threading.Thread(target=self._run, name="frame-dump", daemon=True)
        if start:
            self._thread.start()

    # ---- producer side (DeepStream probe thread): never blocks, never raises ----

    def submit(self, section_id: str, crop: Any, record: dict[str, Any]) -> bool:
        return self._offer(("frame", section_id, crop, record))

    def finish(self, section_id: str, summary: dict[str, Any]) -> bool:
        return self._offer(("summary", section_id, None, summary))

    def _offer(self, item) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            self.dropped += 1
            now = time.monotonic()
            if now - self._last_drop_log >= 60:
                self._last_drop_log = now
                logger.warning("Frame dump queue full: %d item(s) dropped so far", self.dropped)
            return False
        except Exception:  # pragma: no cover - defensive, must never reach the probe
            return False

    def close(self, timeout: float = 5.0) -> None:
        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ---- writer thread ----

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self.process(item)
            except Exception:
                self.errors += 1
                logger.exception("Frame dump write failed")

    def process(self, item) -> None:
        kind, section_id, crop, data = item
        if not SECTION_ID_PATTERN.match(section_id):
            raise ValueError(f"Unexpected section_id {section_id!r}")
        directory = self.root / section_id
        if section_id not in self._known_sections:
            directory.mkdir(parents=True, exist_ok=True)
            self._known_sections.add(section_id)
            self._prune()
        else:
            directory.mkdir(parents=True, exist_ok=True)
        if kind == "frame":
            self._write_frame(directory, crop, data)
        elif kind == "summary":
            _write_json_atomic(directory / "summary.json", data)
        self.written += 1

    def _write_frame(self, directory: Path, crop, record: dict[str, Any]) -> None:
        cv2 = self._cv2
        if cv2 is None:
            import cv2 as cv2_import

            cv2 = self._cv2 = cv2_import
        image = _annotate(cv2, crop, record)
        name = f"{int(record['frame_num']):07d}.jpg"
        if not cv2.imwrite(str(directory / name), image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]):
            raise IOError(f"Could not write {directory / name}")
        line = dict(record, image=name)
        with (directory / "frames.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _prune(self) -> None:
        sections = [path for path in self.root.iterdir() if path.is_dir()]
        if len(sections) <= self.keep_vehicles:
            return
        sections.sort(key=lambda path: path.stat().st_mtime)
        for path in sections[: len(sections) - self.keep_vehicles]:
            shutil.rmtree(path, ignore_errors=True)
            self._known_sections.discard(path.name)


def _annotate(cv2, crop, record: dict[str, Any]):
    """Copy of the crop with a header band: plate read on this frame and its confidence."""
    label = record.get("plate_label") or "-"
    confidences = record.get("plate_confidences") or []
    min_conf = f"{min(confidences):.2f}" if confidences else "-"
    zone = "CAPTURE" if record.get("in_capture_zone") else ""
    text = f"#{record.get('frame_num')} {label} min={min_conf} {zone}".strip()
    height, width = crop.shape[:2]
    band = max(28, height // 18)
    image = cv2.copyMakeBorder(crop, band, 0, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    color = (0, 220, 0) if record.get("valid_plate") else (0, 0, 255)
    scale = band / 36
    cv2.putText(image, text, (6, int(band * 0.75)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 2, cv2.LINE_AA)
    return image


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
