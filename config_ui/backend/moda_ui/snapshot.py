"""Grab one JPEG frame from an RTSP camera for drawing ROIs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import os


os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="snapshot")


class SnapshotError(RuntimeError):
    pass


def _grab(url: str, timeout_ms: int) -> tuple[bytes, int, int]:
    import cv2  # imported lazily: the API still starts without OpenCV

    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms]
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)
    try:
        if not capture.isOpened():
            raise SnapshotError("Không mở được luồng camera")
        frame = None
        # Skip a few frames: the first decoded frames of an RTSP stream are often grey.
        for _ in range(5):
            ok, candidate = capture.read()
            if ok:
                frame = candidate
        if frame is None:
            raise SnapshotError("Không đọc được frame từ camera")
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise SnapshotError("Không mã hoá được ảnh JPEG")
        height, width = frame.shape[:2]
        return encoded.tobytes(), width, height
    finally:
        capture.release()


def grab_frame(url: str, timeout_seconds: float = 8.0) -> tuple[bytes, int, int]:
    future = _executor.submit(_grab, url, int(timeout_seconds * 1000))
    try:
        return future.result(timeout=timeout_seconds + 4)
    except FutureTimeout as error:
        raise SnapshotError("Hết thời gian chờ camera") from error
    except ImportError as error:
        raise SnapshotError("OpenCV chưa được cài trong container") from error
