"""Live camera view: RTSP -> MJPEG for the browser.

One reader thread per RTSP URL, shared by every viewer of that camera. The thread starts on
the first viewer and stops shortly after the last one leaves, so nothing is decoded while
nobody is watching. Hikvision substreams (channel X02) are tried first because they are far
cheaper to decode on the Jetson than the 2688x1520 main stream used by DeepStream.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from typing import AsyncIterator


os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

logger = logging.getLogger(__name__)

LIVE_FPS = float(os.environ.get("MODA_LIVE_FPS", "15"))
LIVE_WIDTH = int(os.environ.get("MODA_LIVE_WIDTH", "960"))
USE_SUBSTREAM = os.environ.get("MODA_LIVE_SUBSTREAM", "1") not in ("0", "false", "no")
OPEN_TIMEOUT_MS = 8000
READ_TIMEOUT_MS = 8000
IDLE_STOP_SECONDS = 10.0
FIRST_FRAME_WAIT_SECONDS = 15.0
BOUNDARY = "frame"

_HIKVISION_MAIN = re.compile(r"(/Streaming/Channels/)(\d*?)01(?=$|[/?])", re.IGNORECASE)


class LiveError(RuntimeError):
    pass


def substream_url(url: str) -> str | None:
    """rtsp://.../Streaming/Channels/301 -> .../302 (Hikvision sub stream), else None."""
    if not USE_SUBSTREAM:
        return None
    candidate = _HIKVISION_MAIN.sub(lambda m: f"{m.group(1)}{m.group(2)}02", url, count=1)
    return candidate if candidate != url else None


class _Stream:
    def __init__(self, url: str) -> None:
        self.url = url
        self.viewers = 0
        self.frame: bytes | None = None
        self.seq = 0
        self.error: str | None = None
        self.source = ""
        self.width = 0
        self.height = 0
        self.last_viewer_at = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-reader", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()

    def _open(self):
        import cv2

        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS]
        candidates = [(u, label) for u, label in ((substream_url(self.url), "sub"), (self.url, "main")) if u]
        for url, label in candidates:
            if self._stop.is_set():
                return None
            capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
            if capture.isOpened():
                ok, _ = capture.read()
                if ok:
                    self.source = label
                    return capture
            capture.release()
        return None

    def _run(self) -> None:
        import cv2

        interval = 1.0 / LIVE_FPS if LIVE_FPS > 0 else 0.0
        try:
            while not self._stop.is_set():
                capture = self._open()
                if capture is None:
                    self.error = "Không kết nối được camera"
                    self._stop.wait(5)
                    continue
                self.error = None
                next_emit = 0.0
                failures = 0
                try:
                    while not self._stop.is_set():
                        # grab() every frame keeps latency low; decode only the frames we send.
                        if not capture.grab():
                            failures += 1
                            if failures >= 3:
                                self.error = "Mất kết nối camera, đang thử lại"
                                break
                            continue
                        failures = 0
                        now = time.monotonic()
                        if now < next_emit:
                            continue
                        ok, frame = capture.retrieve()
                        if not ok or frame is None:
                            continue
                        # RTSP over TCP delivers frames in bursts; a minimum gap between frames
                        # would drop half of them. Keep an average rate budget instead.
                        next_emit = max(next_emit + interval, now - interval)
                        height, width = frame.shape[:2]
                        self.width, self.height = width, height
                        if LIVE_WIDTH and width > LIVE_WIDTH:
                            frame = cv2.resize(
                                frame,
                                (LIVE_WIDTH, max(1, round(height * LIVE_WIDTH / width))),
                                interpolation=cv2.INTER_AREA,
                            )
                        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                        if ok:
                            self.frame = encoded.tobytes()
                            self.seq += 1
                finally:
                    capture.release()
        except Exception:  # never let a camera kill the web server
            logger.exception("Live reader crashed")
            self.error = "Lỗi đọc camera"


_streams: dict[str, _Stream] = {}
_lock = threading.Lock()


def _acquire(url: str) -> _Stream:
    with _lock:
        stream = _streams.get(url)
        if stream is None or not stream.alive:
            stream = _Stream(url)
            _streams[url] = stream
        stream.viewers += 1
        stream.last_viewer_at = time.monotonic()
        return stream


def _release(stream: _Stream) -> None:
    with _lock:
        stream.viewers = max(0, stream.viewers - 1)
        stream.last_viewer_at = time.monotonic()


def reap_idle() -> None:
    """Stop readers nobody has watched for IDLE_STOP_SECONDS."""
    now = time.monotonic()
    with _lock:
        for url, stream in list(_streams.items()):
            if stream.viewers == 0 and now - stream.last_viewer_at > IDLE_STOP_SECONDS:
                stream.stop()
                del _streams[url]


async def reaper() -> None:
    while True:
        await asyncio.sleep(5)
        reap_idle()


async def open_stream(url: str) -> _Stream:
    """Register a viewer and wait for the first frame (raises LiveError on failure)."""
    stream = _acquire(url)
    deadline = time.monotonic() + FIRST_FRAME_WAIT_SECONDS
    while stream.frame is None:
        if time.monotonic() > deadline:
            _release(stream)
            raise LiveError(stream.error or "Hết thời gian chờ camera")
        await asyncio.sleep(0.2)
    return stream


async def mjpeg(stream: _Stream) -> AsyncIterator[bytes]:
    """multipart/x-mixed-replace body; releases the viewer when the client disconnects."""
    last_seq = -1
    try:
        while True:
            if stream.seq != last_seq and stream.frame is not None:
                last_seq = stream.seq
                frame = stream.frame
                yield (
                    f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                    + frame
                    + b"\r\n"
                )
            await asyncio.sleep(0.02)
    finally:
        _release(stream)


def status() -> list[dict]:
    with _lock:
        return [
            {
                "viewers": stream.viewers,
                "source": stream.source,
                "width": stream.width,
                "height": stream.height,
                "error": stream.error,
                "has_frame": stream.frame is not None,
            }
            for stream in _streams.values()
        ]
