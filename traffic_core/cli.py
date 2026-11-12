"""Shared command-line entrypoint for Moda camera deployments."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .config import load_site_config


logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site",
        required=True,
        type=Path,
        help="Path to one config/sites/*.json deployment file",
    )
    parser.add_argument("--gpu", type=int, default=0, help="GPU ID (default: 0)")
    return parser.parse_args(argv)


def run(site_config_path: str | Path, gpu_id: int) -> None:
    site_config = load_site_config(site_config_path)
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    # CUDA-aware modules must be imported only after the GPU has been selected.
    from .detection import VehicleDetection

    VehicleDetection(site_config).run()


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args(argv)
    try:
        run(args.site, args.gpu)
    except Exception:
        logger.exception("Traffic application failed")
        raise


if __name__ == "__main__":
    main()
