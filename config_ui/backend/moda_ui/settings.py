"""Runtime settings of the configuration UI, read from environment variables."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # Absolute path of the moda-jetson checkout. Inside the container it must be the
    # same path as on the host so that relative paths in compose files stay valid.
    project_dir: Path
    database_path: Path
    secret_key: str
    static_dir: Path | None
    session_hours: int
    health_wait_seconds: float
    docker_binary: str
    detector_compose_file: str
    detector_env_file: str
    analysis_compose_file: str
    analysis_env_file: str
    # Streammux resolution of traffic_core/deepstream.py; ROI coordinates live here.
    stream_width: int = 2688
    stream_height: int = 1520

    @property
    def config_dir(self) -> Path:
        return self.project_dir / "config"

    @property
    def models_dir(self) -> Path:
        return self.project_dir / "models"

    @property
    def backup_dir(self) -> Path:
        return self.database_path.parent / "backups"

    @property
    def runtime_dir(self) -> Path:
        # Images written by detectors and analyzers (compose volumes ./runtime/...).
        return self.project_dir / "runtime"

    @property
    def results_db_path(self) -> Path:
        # Recognition results live in their own file so config backups/rollback never touch them.
        override = os.environ.get("MODA_RESULTS_DB")
        return Path(override).resolve() if override else self.database_path.parent / "moda_results.db"


def load_settings() -> Settings:
    project_dir = Path(os.environ.get("MODA_PROJECT_DIR", Path.cwd())).resolve()
    database_path = Path(
        os.environ.get(
            "MODA_CONFIG_DB", project_dir / "config_ui" / "data" / "moda_config.db"
        )
    ).resolve()
    static = os.environ.get("MODA_UI_STATIC_DIR")
    return Settings(
        project_dir=project_dir,
        database_path=database_path,
        secret_key=os.environ.get("MODA_CONFIG_SECRET", ""),
        static_dir=Path(static).resolve() if static else None,
        session_hours=int(os.environ.get("MODA_UI_SESSION_HOURS", "12")),
        health_wait_seconds=float(os.environ.get("MODA_UI_HEALTH_WAIT_SECONDS", "20")),
        docker_binary=os.environ.get("MODA_DOCKER_BINARY", "docker"),
        detector_compose_file=os.environ.get("MODA_DETECTOR_COMPOSE", "compose.yaml"),
        detector_env_file=os.environ.get("MODA_DETECTOR_ENV", ".env"),
        analysis_compose_file=os.environ.get(
            "MODA_ANALYSIS_COMPOSE", "compose.analysis.yaml"
        ),
        analysis_env_file=os.environ.get("MODA_ANALYSIS_ENV", ".env.jetson"),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def override_settings(settings: Settings) -> None:
    """Used by tests and the CLI."""
    global _settings
    _settings = settings
