"""Session cookie authentication and role checks."""

from __future__ import annotations

import sqlite3

from fastapi import Depends, HTTPException, Request, status

from . import repository as repo
from .db import get_db
from .security import read_session_token


COOKIE_NAME = "moda_session"


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    payload = read_session_token(token) if token else None
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Chưa đăng nhập hoặc phiên đã hết hạn")
    try:
        return repo.get_user(conn, int(payload["uid"]))
    except repo.NotFound as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Tài khoản không còn tồn tại") from error


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cần quyền admin")
    return user
