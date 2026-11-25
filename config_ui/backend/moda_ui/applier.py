"""Preview, apply and roll back configuration changes.

Apply = generate files from SQLite -> validate with runtime loaders -> back up the
current files -> write atomically -> recreate only the affected, deployed services ->
check they stay up. On failure the backup is restored and the services recreated again."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime as dt
import difflib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time

from . import docker_ops
from . import repository as repo
from .envfile import env_values_for_runtime
from .generator import POST_ANALYSIS_FILE, PLATE_DIR, SITE_DIR, GenerationError, build_files, read_current_files
from .settings import Settings
from .validation import compose_services, service_environment_names, site_service_names


SECRET_ENV_HINTS = ("URL",)
BACKUPS_TO_KEEP = 30


class ApplyBusy(RuntimeError):
    pass


class ApplyRejected(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# --- progress shared with GET /apply/status -----------------------------------------


@dataclass
class ApplyStatus:
    running: bool = False
    step: str = "idle"
    log: list[str] = field(default_factory=list)
    started_at: float | None = None
    revision_id: int | None = None
    ok: bool | None = None

    def say(self, message: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log.append(f"{stamp} {message}")


_lock = threading.Lock()
status = ApplyStatus()


# --- diffing -------------------------------------------------------------------------


def _mask_env(text: str) -> str:
    """Hide credentials of camera URLs in diffs shown to the browser."""
    from .security import mask_url

    lines = []
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if any(hint in key for hint in SECRET_ENV_HINTS):
                line = f"{key}={mask_url(value.strip(chr(39)).replace('$$', '$'))}"
        lines.append(line)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _is_env(settings: Settings, relative: str) -> bool:
    return relative in (settings.detector_env_file, settings.analysis_env_file)


def file_diffs(settings: Settings, generated: dict[str, str], current: dict[str, str | None]) -> list[dict]:
    diffs = []
    for relative in sorted(generated):
        new = generated[relative]
        old = current.get(relative)
        if old == new:
            continue
        shown_old, shown_new = old or "", new
        if _is_env(settings, relative):
            shown_old, shown_new = _mask_env(shown_old), _mask_env(shown_new)
        diff = "".join(
            difflib.unified_diff(
                shown_old.splitlines(keepends=True),
                shown_new.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            )
        )
        diffs.append({"path": relative, "created": old is None, "diff": diff})
    return diffs


# --- which services must be recreated -----------------------------------------------


def _changed_keys(old: str | None, new: str) -> set[str]:
    before = env_values_for_runtime(old or "")
    after = env_values_for_runtime(new)
    return {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}


def affected_services(
    conn: sqlite3.Connection, settings: Settings, generated: dict[str, str], current: dict[str, str | None]
) -> dict[str, set[str]]:
    changed = {path for path in generated if generated[path] != current.get(path)}
    result: dict[str, set[str]] = {docker_ops.DETECTOR: set(), docker_ops.ANALYSIS: set()}

    plate_users: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT s.code, l.file_name FROM sites s JOIN reference_plate_lists l ON l.id = s.reference_plate_list_id"
    ):
        plate_users.setdefault(row["file_name"], set()).add(row["code"])

    # A site runs in the service that lists it with --site (one container per gate).
    site_services = site_service_names(compose_services(settings, settings.detector_compose_file))
    for path in changed:
        if path.startswith(SITE_DIR + "/"):
            code = Path(path).stem
            result[docker_ops.DETECTOR].add(site_services.get(code, code))
        elif path.startswith(PLATE_DIR + "/"):
            result[docker_ops.DETECTOR] |= {
                site_services.get(code, code) for code in plate_users.get(Path(path).name, set())
            }

    if POST_ANALYSIS_FILE in changed:
        old = json.loads(current.get(POST_ANALYSIS_FILE) or "{}")
        new = json.loads(generated[POST_ANALYSIS_FILE])
        common_old = {k: v for k, v in old.items() if k not in ("weight", "cargo")}
        common_new = {k: v for k, v in new.items() if k not in ("weight", "cargo")}
        if common_old != common_new or old.get("weight") != new.get("weight"):
            result[docker_ops.ANALYSIS].add("weight_reader")

    for project in docker_ops.projects(settings):
        if project.env_file not in changed:
            continue
        keys = _changed_keys(current.get(project.env_file), generated[project.env_file])
        for name, service in compose_services(settings, project.compose_file).items():
            if name in docker_ops.UNMANAGED_SERVICES:
                continue
            if keys & service_environment_names(service):
                result[project.kind].add(name)
    return result


# --- preview -------------------------------------------------------------------------


def preview(conn: sqlite3.Connection, settings: Settings) -> dict:
    from .validation import validate_files

    try:
        generated = build_files(conn, settings)
    except GenerationError as error:
        return {"files": [], "errors": [str(error)], "services": {}, "pending": 0, "generated": None}
    current = read_current_files(settings, generated)
    errors = validate_files(settings, generated)
    services = affected_services(conn, settings, generated, current)
    diffs = file_diffs(settings, generated, current)
    return {
        "files": diffs,
        "errors": errors,
        "services": {kind: sorted(names) for kind, names in services.items()},
        "pending": len(diffs),
        "generated": generated,
        "current": current,
    }


def public_preview(result: dict) -> dict:
    return {key: value for key, value in result.items() if key not in ("generated", "current")}


# --- writing -------------------------------------------------------------------------


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    if path.exists():
        shutil.copymode(path, temporary)
    os.replace(temporary, path)


def _backup(settings: Settings, current: dict[str, str | None]) -> Path:
    folder = settings.backup_dir / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for relative, content in current.items():
        if content is None:
            continue
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (folder / "manifest.json").write_text(
        json.dumps({path: content is not None for path, content in current.items()}, indent=2),
        encoding="utf-8",
    )
    folders = sorted(path for path in settings.backup_dir.iterdir() if path.is_dir())
    for old in folders[:-BACKUPS_TO_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return folder


def _restore(settings: Settings, current: dict[str, str | None]) -> None:
    for relative, content in current.items():
        path = settings.project_dir / relative
        if content is None:
            if path.exists():
                path.unlink()
        else:
            _atomic_write(path, content)


def _recreate_and_check(
    settings: Settings, services: dict[str, set[str]], recreated: list[str]
) -> list[str]:
    """Recreate deployed services and return failures. `recreated` is filled as we go,
    so the caller knows what was touched even if a docker command raises."""
    failures: list[str] = []
    before: dict[str, int] = {}
    for project in docker_ops.projects(settings):
        wanted = services.get(project.kind, set())
        if not wanted:
            continue
        deployed = docker_ops.service_states(settings, project)
        targets = sorted(name for name in wanted if name in deployed)
        skipped = sorted(name for name in wanted if name not in deployed)
        if skipped:
            status.say(f"Bỏ qua (chưa chạy trên máy này): {', '.join(skipped)}")
        if not targets:
            continue
        status.step = f"Recreate {', '.join(targets)}"
        status.say(status.step)
        recreated += targets
        docker_ops.recreate(settings, project, targets)

    if not recreated:
        return failures

    status.step = f"Chờ {int(settings.health_wait_seconds)} giây để kiểm tra service"
    status.say(status.step)
    for project in docker_ops.projects(settings):
        for name, state in docker_ops.service_states(settings, project).items():
            if name in recreated:
                before[name] = docker_ops.restart_count(settings, state["container"])
    time.sleep(settings.health_wait_seconds)
    for project in docker_ops.projects(settings):
        states = docker_ops.service_states(settings, project)
        for name in recreated:
            if name not in states:
                continue
            state = states[name]
            restarts = docker_ops.restart_count(settings, state["container"])
            if state["state"] != "running" or restarts > before.get(name, 0):
                failures.append(f"{name}: state={state['state']} ({state['status']}), restarts={restarts}")
            else:
                status.say(f"{name}: running")
    return failures


def apply(conn: sqlite3.Connection, settings: Settings, *, user_id: int | None, note: str) -> dict:
    if not _lock.acquire(blocking=False):
        raise ApplyBusy("Đang có một lần áp dụng khác")
    try:
        status.__init__()  # reset
        status.running = True
        status.started_at = time.time()
        status.step = "Sinh và kiểm tra cấu hình"
        status.say(status.step)

        result = preview(conn, settings)
        if result["errors"]:
            status.ok = False
            raise ApplyRejected(result["errors"])
        generated, current = result["generated"], result["current"]
        services = {kind: set(names) for kind, names in result["services"].items()}
        snapshot = repo.export_snapshot(conn)
        diff_text = "\n".join(item["diff"] for item in result["files"])

        if not result["files"]:
            status.say("Không có thay đổi nào")
            status.ok = True
            return {"ok": True, "revision_id": None, "services": [], "log": status.log}

        status.step = "Sao lưu file hiện tại"
        backup = _backup(settings, current)
        status.say(f"Đã sao lưu vào {backup}")

        status.step = "Ghi file cấu hình"
        for relative, content in generated.items():
            if current.get(relative) != content:
                _atomic_write(settings.project_dir / relative, content)
                status.say(f"Ghi {relative}")

        recreated: list[str] = []
        try:
            failures = _recreate_and_check(settings, services, recreated)
        except docker_ops.DockerError as error:
            failures = [str(error)]

        if failures:
            status.say("Lỗi: " + "; ".join(failures))
            status.step = "Khôi phục cấu hình cũ"
            status.say(status.step)
            _restore(settings, current)
            # Only services that already received the new config need to go back.
            touched = {kind: names & set(recreated) for kind, names in services.items()}
            if recreated:
                try:
                    _recreate_and_check(settings, touched, [])
                except docker_ops.DockerError as error:
                    status.say(f"Khôi phục service lỗi: {error}")
            status.say("Đã khôi phục file cấu hình cũ")
            revision_id = repo.create_revision(
                conn,
                user_id=user_id,
                note=note,
                snapshot=snapshot,
                files_diff=diff_text,
                services=recreated,
                status="failed",
                log="\n".join(status.log),
            )
            status.ok = False
            status.revision_id = revision_id
            return {"ok": False, "revision_id": revision_id, "services": recreated, "log": status.log, "errors": failures}

        status.say("Hoàn tất")
        revision_id = repo.create_revision(
            conn,
            user_id=user_id,
            note=note,
            snapshot=snapshot,
            files_diff=diff_text,
            services=recreated,
            status="applied",
            log="\n".join(status.log),
        )
        status.ok = True
        status.revision_id = revision_id
        return {"ok": True, "revision_id": revision_id, "services": recreated, "log": status.log}
    finally:
        status.running = False
        status.step = "idle"
        _lock.release()


def rollback(conn: sqlite3.Connection, settings: Settings, revision_id: int, *, user_id: int | None) -> dict:
    revision = repo.get_revision(conn, revision_id)
    current_snapshot = repo.export_snapshot(conn)
    repo.import_snapshot(conn, revision["snapshot"])
    try:
        outcome = apply(conn, settings, user_id=user_id, note=f"Rollback về revision #{revision_id}")
    except Exception:
        repo.import_snapshot(conn, current_snapshot)
        raise
    if outcome["ok"]:
        repo.mark_rolled_back_after(conn, revision_id)
        # The new revision itself must stay "applied".
        if outcome["revision_id"]:
            conn.execute(
                "UPDATE config_revisions SET status = 'applied' WHERE id = ?", (outcome["revision_id"],)
            )
            conn.commit()
    else:
        repo.import_snapshot(conn, current_snapshot)
    return outcome
