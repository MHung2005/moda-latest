"""Read and update dotenv files while keeping comments and unrelated keys."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env(path.read_text(encoding="utf-8"))


def _format_value(value: str) -> str:
    # Compose interpolates "$"; double it so passwords containing "$" survive.
    escaped = value.replace("$", "$$")
    if any(character in escaped for character in " #\"'"):
        return "'" + escaped.replace("'", "") + "'"
    return escaped


def _unformat_value(value: str) -> str:
    return value.replace("$$", "$")


def update_env_text(text: str, updates: Mapping[str, str]) -> str:
    """Replace values of existing keys in place and append missing keys."""
    remaining = dict(updates)
    lines = text.splitlines()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={_format_value(remaining.pop(key))}")
                continue
        output.append(line)
    if remaining:
        if output and output[-1].strip():
            output.append("")
        output.append("# Managed by moda config UI")
        for key, value in remaining.items():
            output.append(f"{key}={_format_value(value)}")
    return "\n".join(output) + "\n"


def env_values_for_runtime(text: str) -> dict[str, str]:
    """Values as Compose will pass them to the container."""
    return {key: _unformat_value(value) for key, value in parse_env(text).items()}
