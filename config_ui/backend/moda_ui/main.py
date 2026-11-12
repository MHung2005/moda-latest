"""FastAPI application: REST API plus the built React frontend."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import live
from . import repository as repo
from .api import router
from .db import connect, init_schema
from .results_api import router as results_router
from .security import SecretKeyError
from .settings import get_settings


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Moda config UI", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.on_event("startup")
    def _startup() -> None:
        connection = connect()
        try:
            init_schema(connection)
        finally:
            connection.close()
        logger.info("Project dir: %s, database: %s", settings.project_dir, settings.database_path)
        asyncio.get_running_loop().create_task(live.reaper())

    @app.exception_handler(repo.NotFound)
    def _not_found(_: Request, error: repo.NotFound) -> JSONResponse:
        return JSONResponse({"detail": f"Không tìm thấy {error}"}, status_code=404)

    @app.exception_handler(repo.Conflict)
    def _conflict(_: Request, error: repo.Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(SecretKeyError)
    def _secret(_: Request, error: SecretKeyError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=500)

    app.include_router(router)
    app.include_router(results_router)

    static_dir = settings.static_dir
    if static_dir and static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def _spa(path: str) -> FileResponse:
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and static_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app
