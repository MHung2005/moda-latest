"""Thin wrapper around `docker compose` for the two existing compose projects."""

from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess

from .settings import Settings
from .validation import compose_services


DETECTOR = "detector"
ANALYSIS = "analysis"
# Triton is never recreated by the UI: none of its settings are managed here.
UNMANAGED_SERVICES = {"triton"}


class DockerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComposeProject:
    kind: str
    compose_file: str
    env_file: str


def projects(settings: Settings) -> list[ComposeProject]:
    return [
        ComposeProject(DETECTOR, settings.detector_compose_file, settings.detector_env_file),
        ComposeProject(ANALYSIS, settings.analysis_compose_file, settings.analysis_env_file),
    ]


def _base_command(settings: Settings, project: ComposeProject) -> list[str]:
    command = [
        settings.docker_binary,
        "compose",
        "--project-directory",
        str(settings.project_dir),
        "-f",
        str(settings.project_dir / project.compose_file),
    ]
    env_path = settings.project_dir / project.env_file
    if env_path.exists():
        command += ["--env-file", str(env_path)]
    return command


def _run(command: list[str], timeout: float = 120) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as error:
        raise DockerError(f"Không tìm thấy lệnh docker: {command[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise DockerError(f"Lệnh quá thời gian: {' '.join(command[:6])} ...") from error
    if result.returncode != 0:
        raise DockerError((result.stderr or result.stdout).strip() or f"exit {result.returncode}")
    return result.stdout


def _parse_ps(output: str) -> list[dict]:
    output = output.strip()
    if not output:
        return []
    if output.startswith("["):
        return json.loads(output)
    # Newer compose versions print one JSON object per line.
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def service_states(settings: Settings, project: ComposeProject) -> dict[str, dict]:
    """{service: {"state", "status", "container", "health"}} for containers that exist."""
    output = _run(_base_command(settings, project) + ["ps", "-a", "--format", "json"], timeout=30)
    states = {}
    for item in _parse_ps(output):
        states[item.get("Service", "")] = {
            "state": item.get("State", ""),
            "status": item.get("Status", ""),
            "container": item.get("Name", ""),
            "health": item.get("Health", ""),
        }
    return states


def all_services(settings: Settings) -> list[dict]:
    """Every service of both compose files, with live state when docker is reachable."""
    result = []
    for project in projects(settings):
        defined = compose_services(settings, project.compose_file)
        try:
            states = service_states(settings, project)
            error = None
        except DockerError as exc:
            states, error = {}, str(exc)
        for name in defined:
            state = states.get(name)
            result.append(
                {
                    "project": project.kind,
                    "service": name,
                    "managed": name not in UNMANAGED_SERVICES,
                    "deployed": state is not None,
                    "state": state["state"] if state else "not created",
                    "status": state["status"] if state else "",
                    "health": state["health"] if state else "",
                    "container": state["container"] if state else "",
                    "error": error,
                }
            )
    return result


def recreate(settings: Settings, project: ComposeProject, services: list[str]) -> str:
    if not services:
        return ""
    command = _base_command(settings, project) + [
        "up",
        "-d",
        "--no-deps",
        "--no-build",
        "--force-recreate",
        *services,
    ]
    return _run(command, timeout=600)


def restart_count(settings: Settings, container: str) -> int:
    output = _run([settings.docker_binary, "inspect", "--format", "{{.RestartCount}}", container], timeout=15)
    return int(output.strip() or 0)


def logs(settings: Settings, project: ComposeProject, service: str, tail: int = 100) -> str:
    return _run(
        _base_command(settings, project) + ["logs", "--no-color", "--tail", str(tail), service], timeout=30
    )


def project_of(settings: Settings, kind: str) -> ComposeProject:
    for project in projects(settings):
        if project.kind == kind:
            return project
    raise KeyError(kind)
