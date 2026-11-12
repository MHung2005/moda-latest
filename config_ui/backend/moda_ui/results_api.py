"""Read-only API for recognition results (/api/results). Any logged-in user may read."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from . import results
from .auth import current_user


router = APIRouter(prefix="/api/results")
Rdb = Depends(results.get_results_db)
User = Depends(current_user)
IMAGE_CACHE = "private, max-age=86400, immutable"


@router.get("")
def list_results(
    lane: str | None = None,
    plate: str | None = None,
    date_from: str | None = Query(None, description="ISO 8601 UTC, inclusive"),
    date_to: str | None = Query(None, description="ISO 8601 UTC, exclusive"),
    unmatched: bool = False,
    no_plate: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    conn: sqlite3.Connection = Rdb,
    _: dict = User,
) -> dict:
    return results.list_events(
        conn,
        lane=lane or None,
        plate=plate or None,
        date_from=date_from or None,
        date_to=date_to or None,
        unmatched=unmatched,
        no_plate=no_plate,
        page=page,
        page_size=page_size,
    )


@router.get("/stats")
def result_stats(
    date_from: str | None = None,
    date_to: str | None = None,
    conn: sqlite3.Connection = Rdb,
    _: dict = User,
) -> dict:
    return results.stats(conn, date_from=date_from or None, date_to=date_to or None)


@router.get("/lanes")
def result_lanes(conn: sqlite3.Connection = Rdb, _: dict = User) -> list[str]:
    return results.lanes(conn)


@router.get("/image")
def result_image(
    path: str,
    w: int | None = Query(None, ge=32, le=1600, description="Thumbnail width"),
    _: dict = User,
) -> Response:
    image = results.resolve_image(path)
    if image is None:
        raise HTTPException(404, "Không tìm thấy ảnh")
    if not w:
        return FileResponse(image, media_type="image/jpeg", headers={"Cache-Control": IMAGE_CACHE})

    import cv2

    frame = cv2.imread(str(image))
    if frame is None:
        raise HTTPException(415, "Ảnh không đọc được")
    height, width = frame.shape[:2]
    if width > w:
        frame = cv2.resize(frame, (w, max(1, round(height * w / width))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise HTTPException(500, "Không tạo được ảnh thu nhỏ")
    return Response(encoded.tobytes(), media_type="image/jpeg", headers={"Cache-Control": IMAGE_CACHE})


@router.get("/{event_id}")
def result_detail(event_id: int, conn: sqlite3.Connection = Rdb, _: dict = User) -> dict:
    event = results.get_event(conn, event_id)
    if event is None:
        raise HTTPException(404, "Không tìm thấy kết quả")
    return event
