from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


WEB_STATIC_DIR = Path(__file__).resolve().parent / "web_static"


def register_web_routes(app: FastAPI) -> None:
    app.mount("/assets", StaticFiles(directory=str(WEB_STATIC_DIR)), name="assets")

    @app.get("/", include_in_schema=False)
    def web_index() -> FileResponse:
        return FileResponse(WEB_STATIC_DIR / "index.html")
