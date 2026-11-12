"""Validate generated files with the runtime's own loaders, before anything is written.

traffic_core.config and post_analysis.config only use the standard library, so they
can be imported here without DeepStream, Kafka or Triton."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import re
import sys
import tempfile
from typing import Mapping

import yaml

from .envfile import env_values_for_runtime
from .generator import POST_ANALYSIS_FILE, SITE_DIR
from .settings import Settings


_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")

def _loaders(project_dir: Path):
    if str(project_dir) not in sys.path:
        sys.path.insert(0, str(project_dir))
    traffic = importlib.import_module("traffic_core.config")
    analysis = importlib.import_module("post_analysis.config")
    return traffic, analysis


def compose_services(settings: Settings, compose_file: str) -> dict[str, dict]:
    path = settings.project_dir / compose_file
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("services", {}) or {}


def service_environment_names(service: dict) -> set[str]:
    """Variable names a compose service receives or interpolates."""
    environment = service.get("environment") or {}
    names: set[str] = set()
    if isinstance(environment, dict):
        items = environment.items()
    else:
        items = (item.split("=", 1) if "=" in item else (item, "") for item in environment)
    for key, value in items:
        names.add(str(key))
        names.update(_INTERPOLATION.findall(str(value or "")))
    return names


def validate_files(settings: Settings, files: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    try:
        traffic, analysis = _loaders(settings.project_dir)
    except ImportError as error:
        return [f"Không import được loader của runtime: {error}"]

    detector_env = env_values_for_runtime(files.get(settings.detector_env_file, ""))
    analysis_env = env_values_for_runtime(files.get(settings.analysis_env_file, ""))
    detector_services = compose_services(settings, settings.detector_compose_file)

    with tempfile.TemporaryDirectory(prefix="moda-validate-") as directory:
        root = Path(directory)
        for relative, content in files.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        for relative in sorted(files):
            if not relative.startswith(SITE_DIR + "/"):
                continue
            code = Path(relative).stem
            try:
                site = traffic.load_site_config(root / relative, detector_env)
            except ValueError as error:
                errors.append(f"{relative}: {error}")
                continue
            for zone_name, zones in (("capture", site.capture_zones), ("inference", site.inference_zones)):
                for source_id, polygon in zones.items():
                    for x, y in polygon:
                        if x > settings.stream_width or y > settings.stream_height:
                            errors.append(
                                f"{relative}: vùng {zone_name} nguồn {source_id} có điểm ({x},{y})"
                                f" nằm ngoài khung {settings.stream_width}x{settings.stream_height}"
                            )
                            break
            if detector_services:
                service = detector_services.get(code)
                if service is None:
                    errors.append(f"{relative}: không có service '{code}' trong {settings.detector_compose_file}")
                else:
                    env_names = json.loads(files[relative])["camera_url_envs"]
                    passed = service_environment_names(service)
                    missing = [name for name in env_names if name not in passed]
                    if missing:
                        errors.append(
                            f"{relative}: service '{code}' trong {settings.detector_compose_file}"
                            f" chưa truyền biến {', '.join(missing)} vào container"
                        )

        analysis_path = root / POST_ANALYSIS_FILE
        if analysis_path.exists():
            for name, loader in (("weight", analysis.load_weight_config),):
                try:
                    loader(analysis_path, analysis_env)
                except ValueError as error:
                    errors.append(f"{POST_ANALYSIS_FILE} ({name}): {error}")
    return errors
