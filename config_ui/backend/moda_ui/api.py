"""REST API under /api."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse, StreamingResponse

from . import applier, docker_ops, live
from . import repository as repo
from . import schemas
from .auth import COOKIE_NAME, current_user, require_admin
from .db import connect, get_db
from .security import create_session_token, hash_password, verify_password
from .settings import get_settings
from .snapshot import SnapshotError, grab_frame


router = APIRouter(prefix="/api")
MODELS_MOUNT = "/home/jetson/models"
Db = Depends(get_db)
User = Depends(current_user)
Admin = Depends(require_admin)


# --- auth ----------------------------------------------------------------------------


@router.post("/auth/login")
def login(data: schemas.LoginRequest, response: Response, conn: sqlite3.Connection = Db) -> dict:
    user = repo.get_user_by_name(conn, data.username)
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(user["id"], user["role"]),
        httponly=True,
        samesite="strict",
        max_age=get_settings().session_hours * 3600,
    )
    return {"id": user["id"], "username": user["username"], "role": user["role"]}


@router.post("/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = User) -> dict:
    return user


@router.get("/users")
def users(conn: sqlite3.Connection = Db, _: dict = Admin) -> list[dict]:
    return repo.list_users(conn)


@router.post("/users")
def create_user(data: schemas.UserCreate, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    return {"id": repo.create_user(conn, data.username, hash_password(data.password), data.role)}


@router.put("/users/{user_id}/password")
def change_password(
    user_id: int, data: schemas.PasswordChange, conn: sqlite3.Connection = Db, user: dict = User
) -> dict:
    if user["role"] != "admin" and user["id"] != user_id:
        raise HTTPException(403, "Chỉ được đổi mật khẩu của chính mình")
    repo.get_user(conn, user_id)
    repo.set_user_password(conn, user_id, hash_password(data.password))
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, conn: sqlite3.Connection = Db, user: dict = Admin) -> dict:
    if user["id"] == user_id:
        raise HTTPException(409, "Không thể tự xoá tài khoản đang đăng nhập")
    repo.delete_user(conn, user_id)
    return {"ok": True}


# --- cameras -------------------------------------------------------------------------


@router.get("/cameras")
def cameras(conn: sqlite3.Connection = Db, _: dict = User) -> list[dict]:
    return repo.list_cameras(conn)


@router.post("/cameras")
def create_camera(data: schemas.CameraIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    return repo.get_camera(conn, repo.create_camera(conn, data))


@router.put("/cameras/{camera_id}")
def update_camera(camera_id: int, data: schemas.CameraIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.update_camera(conn, camera_id, data)
    return repo.get_camera(conn, camera_id)


@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: int, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.delete_camera(conn, camera_id)
    return {"ok": True}


def _snapshot(conn: sqlite3.Connection, camera_id: int) -> tuple[bytes, int, int]:
    url = repo.get_camera_url(conn, camera_id)
    try:
        image, width, height = grab_frame(url)
    except SnapshotError as error:
        raise HTTPException(502, str(error)) from error
    repo.set_camera_resolution(conn, camera_id, width, height)
    return image, width, height


@router.get("/cameras/{camera_id}/snapshot")
def camera_snapshot(camera_id: int, conn: sqlite3.Connection = Db, _: dict = User) -> Response:
    image, width, height = _snapshot(conn, camera_id)
    return Response(
        image,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Frame-Width": str(width), "X-Frame-Height": str(height)},
    )


@router.get("/cameras/{camera_id}/live")
async def camera_live(camera_id: int, _: dict = User) -> StreamingResponse:
    """MJPEG stream for <img src>. Uses its own short-lived DB connection: the stream
    outlives the request dependencies."""
    conn = connect()
    try:
        url = repo.get_camera_url(conn, camera_id)
    finally:
        conn.close()
    try:
        stream = await live.open_stream(url)
    except live.LiveError as error:
        raise HTTPException(502, str(error)) from error
    return StreamingResponse(
        live.mjpeg(stream),
        media_type=f"multipart/x-mixed-replace; boundary={live.BOUNDARY}",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/cameras/{camera_id}/test")
def camera_test(camera_id: int, conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    _, width, height = _snapshot(conn, camera_id)
    return {"ok": True, "width": width, "height": height}


# --- reference plates ----------------------------------------------------------------


@router.get("/plate-lists")
def plate_lists(conn: sqlite3.Connection = Db, _: dict = User) -> list[dict]:
    return repo.list_plate_lists(conn)


@router.post("/plate-lists")
def create_plate_list(data: schemas.PlateListIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    return {"id": repo.create_plate_list(conn, data.name)}


@router.get("/plate-lists/{list_id}/plates")
def plates(list_id: int, conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    return {"plates": repo.get_plates(conn, list_id)}


@router.put("/plate-lists/{list_id}/plates")
def replace_plates(list_id: int, data: schemas.PlatesIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.replace_plates(conn, list_id, data.plates)
    return {"count": len(data.plates)}


@router.get("/plate-lists/{list_id}/export", response_class=PlainTextResponse)
def export_plates(list_id: int, conn: sqlite3.Connection = Db, _: dict = User) -> str:
    return "".join(f"{plate}\n" for plate in repo.get_plates(conn, list_id))


@router.delete("/plate-lists/{list_id}")
def delete_plate_list(list_id: int, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.delete_plate_list(conn, list_id)
    return {"ok": True}


# --- sites ---------------------------------------------------------------------------


@router.get("/sites")
def sites(conn: sqlite3.Connection = Db, _: dict = User) -> list[dict]:
    return repo.list_sites(conn)


@router.get("/sites/{code}")
def site(code: str, conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    return repo.get_site(conn, code)


@router.put("/sites/{code}")
def update_site(code: str, data: schemas.SiteIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.update_site(conn, code, data)
    return repo.get_site(conn, code)


@router.put("/sites/{code}/sources/{index}")
def upsert_source(code: str, index: int, data: schemas.SourceIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.upsert_source(conn, code, index, data)
    return repo.get_site(conn, code)


@router.delete("/sites/{code}/sources/{index}")
def delete_source(code: str, index: int, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.delete_source(conn, code, index)
    return repo.get_site(conn, code)


@router.put("/sites/{code}/sources/{index}/zones/{kind}")
def set_zone(
    code: str, index: int, kind: str, data: schemas.ZoneIn, conn: sqlite3.Connection = Db, _: dict = Admin
) -> dict:
    if kind not in ("capture", "inference"):
        raise HTTPException(404, "kind phải là capture hoặc inference")
    settings = get_settings()
    for x, y in data.points:
        if x > settings.stream_width or y > settings.stream_height:
            raise HTTPException(422, f"Điểm ({x},{y}) nằm ngoài khung {settings.stream_width}x{settings.stream_height}")
    repo.set_zone(conn, code, index, kind, data.points)
    return repo.get_site(conn, code)


# --- system / analyzers --------------------------------------------------------------


@router.get("/settings/system")
def system_settings(conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    return repo.get_system_settings(conn)


@router.put("/settings/system")
def update_system_settings(data: schemas.SystemSettings, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.set_system_settings(conn, data.model_dump())
    return repo.get_system_settings(conn)


def _analyzer_model(analyzer: str):
    model = schemas.ANALYZER_MODELS.get(analyzer)
    if model is None:
        raise HTTPException(404, "analyzer phải là weight hoặc cargo")
    return model


@router.get("/analyzers/{analyzer}")
def analyzer(analyzer: str, conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    _analyzer_model(analyzer)
    return {
        "settings": repo.get_analyzer_settings(conn, analyzer),
        "lanes": repo.list_analyzer_lanes(conn, analyzer),
    }


@router.put("/analyzers/{analyzer}")
def update_analyzer(analyzer: str, payload: dict, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    data = _analyzer_model(analyzer).model_validate(payload)
    repo.set_analyzer_settings(conn, analyzer, data.model_dump())
    return repo.get_analyzer_settings(conn, analyzer)


@router.put("/analyzers/weight/lanes/{trigger_code}")
def upsert_weight_lane(trigger_code: str, data: schemas.WeightLaneIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.upsert_weight_lane(conn, trigger_code, data)
    return {"lanes": repo.list_analyzer_lanes(conn, "weight")}


@router.put("/analyzers/cargo/lanes/{trigger_code}")
def upsert_cargo_lane(trigger_code: str, data: schemas.CargoLaneIn, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    repo.upsert_cargo_lane(conn, trigger_code, data)
    return {"lanes": repo.list_analyzer_lanes(conn, "cargo")}


@router.delete("/analyzers/{analyzer}/lanes/{trigger_code}")
def delete_lane(analyzer: str, trigger_code: str, conn: sqlite3.Connection = Db, _: dict = Admin) -> dict:
    _analyzer_model(analyzer)
    repo.delete_analyzer_lane(conn, analyzer, trigger_code)
    return {"lanes": repo.list_analyzer_lanes(conn, analyzer)}


# --- models --------------------------------------------------------------------------


@router.get("/models/options")
def model_options(conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    """DeepStream config files under models/, as paths seen inside the detector container."""
    models_dir = get_settings().models_dir
    found: dict[str, set[str]] = {"tracker": set(), "inference": set()}
    if models_dir.exists():
        for path in models_dir.rglob("*.txt"):
            relative = path.relative_to(models_dir).as_posix()
            if "DeepStream-Yolo" in relative or "nvdsinfer_custom_impl" in relative:
                continue
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:4000]
            except OSError:
                continue
            target = f"{MODELS_MOUNT}/{relative}"
            if "[tracker]" in head:
                found["tracker"].add(target)
            elif "[property]" in head and "gie-unique-id" in head:
                found["inference"].add(target)
    for site in repo.list_sites(conn):  # keep values already in use selectable
        found["tracker"].add(site["model_tracker"])
        for key in ("model_vehicle_detection", "model_plate_detection", "model_plate_recognition"):
            found["inference"].add(site[key])
    triton_dir = models_dir / "triton"
    triton = sorted(p.name for p in triton_dir.iterdir() if (p / "config.pbtxt").exists()) if triton_dir.exists() else []
    return {"tracker": sorted(found["tracker"]), "inference": sorted(found["inference"]), "triton": triton}


@router.get("/models/file", response_class=PlainTextResponse)
def model_file(path: str, _: dict = User) -> str:
    """Read-only view of a DeepStream config file."""
    if not path.startswith(MODELS_MOUNT + "/") or not path.endswith(".txt"):
        raise HTTPException(400, "Chỉ xem được file .txt trong models/")
    models_dir = get_settings().models_dir.resolve()
    target = (models_dir / path[len(MODELS_MOUNT) + 1 :]).resolve()
    if models_dir not in target.parents or not target.exists():
        raise HTTPException(404, "Không tìm thấy file")
    return target.read_text(encoding="utf-8", errors="replace")


# --- apply / revisions ---------------------------------------------------------------


@router.get("/apply/preview")
def apply_preview(conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    return applier.public_preview(applier.preview(conn, get_settings()))


@router.post("/apply")
def apply(data: schemas.ApplyRequest, conn: sqlite3.Connection = Db, user: dict = Admin) -> dict:
    try:
        return applier.apply(conn, get_settings(), user_id=user["id"], note=data.note)
    except applier.ApplyBusy as error:
        raise HTTPException(409, str(error)) from error
    except applier.ApplyRejected as error:
        raise HTTPException(422, {"errors": error.errors}) from error


@router.get("/apply/status")
def apply_status(_: dict = User) -> dict:
    current = applier.status
    return {
        "running": current.running,
        "step": current.step,
        "log": current.log[-200:],
        "ok": current.ok,
        "revision_id": current.revision_id,
    }


@router.get("/revisions")
def revisions(conn: sqlite3.Connection = Db, _: dict = User) -> list[dict]:
    return repo.list_revisions(conn)


@router.get("/revisions/{revision_id}")
def revision(revision_id: int, conn: sqlite3.Connection = Db, _: dict = User) -> dict:
    data = repo.get_revision(conn, revision_id)
    data.pop("snapshot")
    return data


@router.post("/revisions/{revision_id}/rollback")
def rollback(revision_id: int, conn: sqlite3.Connection = Db, user: dict = Admin) -> dict:
    try:
        return applier.rollback(conn, get_settings(), revision_id, user_id=user["id"])
    except applier.ApplyBusy as error:
        raise HTTPException(409, str(error)) from error
    except applier.ApplyRejected as error:
        raise HTTPException(422, {"errors": error.errors}) from error


# --- services ------------------------------------------------------------------------


@router.get("/services")
def services(_: dict = User) -> list[dict]:
    return docker_ops.all_services(get_settings())


@router.get("/services/{project}/{service}/logs", response_class=PlainTextResponse)
def service_logs(project: str, service: str, tail: int = 100, _: dict = User) -> str:
    settings = get_settings()
    try:
        target = docker_ops.project_of(settings, project)
    except KeyError as error:
        raise HTTPException(404, "project phải là detector hoặc analysis") from error
    try:
        return docker_ops.logs(settings, target, service, min(max(tail, 1), 1000))
    except docker_ops.DockerError as error:
        raise HTTPException(502, str(error)) from error
