"""Command-line entrypoint for downstream analysis services."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import signal

from .config import load_weight_config


def _request_shutdown(_signum, _frame) -> None:
    raise KeyboardInterrupt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=("weight",))
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/post_analysis.json"),
        help="Path to the downstream analysis configuration",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s",
    )
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    signal.signal(signal.SIGTERM, _request_shutdown)
    args = parse_args(argv)
    try:
        from .weight_reader import run

        run(load_weight_config(args.config))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Analysis service stopped")
