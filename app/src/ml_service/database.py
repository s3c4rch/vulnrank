from functools import lru_cache
from time import sleep

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ml_service.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str, echo: bool = False, **kwargs) -> Engine:
    return create_engine(database_url, echo=echo, future=True, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return make_engine(settings.database_url, echo=settings.db_echo)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker:
    return make_session_factory(get_engine())


def create_schema(engine: Engine | None = None) -> None:
    metadata_engine = engine or get_engine()
    Base.metadata.create_all(metadata_engine)


def wait_for_database(
    engine: Engine | None = None,
    attempts: int | None = None,
    delay: float | None = None,
) -> None:
    metadata_engine = engine or get_engine()
    settings = get_settings()
    remaining_attempts = attempts or settings.db_init_attempts
    wait_delay = delay or settings.db_init_delay
    last_error: Exception | None = None

    for _ in range(remaining_attempts):
        try:
            with metadata_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception as exc:
            last_error = exc
            sleep(wait_delay)

    if last_error is not None:
        raise last_error
