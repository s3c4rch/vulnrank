from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from ml_service.database import get_engine, wait_for_database
from ml_service.init_db import initialize_database


def create_lifespan(initialize_on_startup: bool = True):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if initialize_on_startup:
            session_factory = getattr(app.state, "session_factory", None)
            engine = session_factory.kw.get("bind") if session_factory is not None else get_engine()
            wait_for_database(engine)
            initialize_database(engine=engine, session_factory=session_factory)
        yield

    return lifespan
