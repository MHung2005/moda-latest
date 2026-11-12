"""OpenCV camera helpers with bounded connection and read attempts."""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


def open_camera(cv2, url: str, open_timeout_ms: int, read_timeout_ms: int):
    """Open an FFmpeg capture and use timeout properties when OpenCV supports them."""
    backend = cv2.CAP_FFMPEG
    parameters: list[int] = []
    open_property = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
    read_property = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
    if open_property is not None:
        parameters.extend((open_property, open_timeout_ms))
    if read_property is not None:
        parameters.extend((read_property, read_timeout_ms))

    try:
        capture = cv2.VideoCapture(url, backend, parameters)
    except Exception as error:
        logger.warning(
            "This OpenCV build does not support capture timeout parameters: %s",
            error,
        )
        capture = cv2.VideoCapture(url, backend)

    if not capture.isOpened():
        capture.release()
        raise RuntimeError("Could not open RTSP camera")
    return capture
