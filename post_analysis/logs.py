"""Logging setup shared by the service process and its spawned worker processes.

Workers are started with the multiprocessing "spawn" context, which does not inherit the
parent's logging configuration: without calling configure_logging() in the child, every
INFO record of a worker is silently dropped.
"""

from __future__ import annotations

import logging


LOG_FORMAT = "%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
    # Ultralytics prints one line per inference ("0: 640x640 ..."); the weight reader logs a
    # per-event summary instead.
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
