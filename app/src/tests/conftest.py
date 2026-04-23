from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
import pytest

from ml_service.api import create_app
from ml_service.database import Base, make_engine, make_session_factory
from ml_service.init_db import initialize_database


class FakeTaskPublisher:
    def __init__(self, published_messages: list) -> None:
        self.published_messages = published_messages

    def publish(self, message) -> None:
        self.published_messages.append(message)


@pytest.fixture
def session_factory():
    engine = make_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(session_factory):
    with session_factory() as db_session:
        yield db_session


@pytest.fixture
def published_messages():
    return []


@pytest.fixture
def app(session_factory, published_messages):
    initialize_database(session_factory=session_factory)
    task_publisher = FakeTaskPublisher(published_messages)
    return create_app(
        session_factory=session_factory,
        initialize_on_startup=False,
        task_publisher=task_publisher,
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
