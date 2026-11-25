"""Shared command-line entrypoint for Moda camera deployments."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .config import SiteConfig, SiteConfigError, load_site_config


logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site",
        required=True,
        type=Path,
        action="append",
        help=(
            "Path to one config/sites/*.json deployment file. Repeat it to run several "
            "sites (e.g. the two lanes of one gate) in one process."
        ),
    )
    parser.add_argument("--gpu", type=int, default=0, help="GPU ID (default: 0)")
    return parser.parse_args(argv)


def load_sites(
    site_config_paths: Sequence[str | Path],
    environment: Mapping[str, str] | None = None,
) -> list[SiteConfig]:
    """Load every site; with several sites each one saves images in its own sub-folder.

    One site keeps `vehicle_image_root` as is (compose mounts runtime/vehicle/<site> there).
    Several sites share one container, so each writes to `<vehicle_image_root>/<site>` and
    compose mounts runtime/vehicle/<site> at that path instead.
    """
    paths = [Path(path) for path in site_config_paths]
    if not paths:
        raise SiteConfigError("At least one --site is required")
    stems = [path.stem for path in paths]
    duplicates = sorted({stem for stem in stems if stems.count(stem) > 1})
    if duplicates:
        raise SiteConfigError("Duplicate --site: " + ", ".join(duplicates))

    sites = [load_site_config(path, environment) for path in paths]
    if len(sites) == 1:
        return sites
    return [
        dataclasses.replace(
            site,
            vehicle_image_root=str(PurePosixPath(site.vehicle_image_root) / stem),
        )
        for site, stem in zip(sites, stems)
    ]


def run(site_config_paths: str | Path | Sequence[str | Path], gpu_id: int) -> None:
    if isinstance(site_config_paths, (str, Path)):
        site_config_paths = [site_config_paths]
    sites = load_sites(site_config_paths)
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)

    # CUDA-aware modules must be imported only after the GPU has been selected.
    from .detection import VehicleDetection, run_detections

    if len(sites) > 1:
        logger.info(
            "Running %d sites in one process: %s",
            len(sites),
            ", ".join(site.name for site in sites),
        )
    run_detections([VehicleDetection(site) for site in sites])


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
