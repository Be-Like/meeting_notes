import os
from dataclasses import dataclass

# Before main, and so auth, is imported: bcrypt's minimum work factor keeps every test hash under a millisecond.
os.environ.setdefault("BCRYPT_ROUNDS", "4")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth import get_runtime
from database import get_db
from main import app
from mesh import CallEnvelope, MeshRuntime, Reply, Transport, build_runtime
from models import Base


class RecordingTransport:
    """Keeps every envelope it sees, so a test can tell a cache hit from a send."""

    def __init__(self, inner: Transport) -> None:
        self.inner = inner
        self.envelopes: list[CallEnvelope] = []

    def send(self, envelope: CallEnvelope) -> Reply:
        self.envelopes.append(envelope)
        return self.inner.send(envelope)


@pytest.fixture
def db_engine():
    """Fresh in-memory SQLite with all tables, shared across threads via StaticPool."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """A session bound to db_engine for model-level tests."""
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    yield session
    session.close()


@pytest.fixture(params=["memory", "json"])
def runtime(request, db_engine) -> MeshRuntime:
    """Built under one transport with the router recorded; every test that depends on it runs once per transport."""
    factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    runtime = build_runtime({"MESH_TRANSPORT": request.param}, session_factory=factory)
    runtime.transport = RecordingTransport(runtime.transport)
    return runtime


@pytest.fixture
def client(db_engine, runtime):
    """get_db and get_runtime overridden; the lifespan is not entered."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_runtime] = lambda: runtime
    yield TestClient(app)
    app.dependency_overrides.clear()


@dataclass(frozen=True)
class Account:
    """One registered and logged-in user: the id to list as an attendee and the headers to act as them."""

    id: int
    name: str
    email: str
    headers: dict[str, str]


@pytest.fixture
def user_for(client):
    """Register and log in one user; returns them as an Account."""

    def _user_for(email: str, name: str = "Test User", password: str = "correct horse") -> Account:
        created = client.post("/users/", json={"name": name, "email": email, "password": password})
        assert created.status_code == 201, created.text
        login = client.post("/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        return Account(created.json()["id"], name, email, {"Authorization": f"Bearer {login.json()['access_token']}"})

    return _user_for


@pytest.fixture
def headers_for(user_for):
    """The headers of user_for(...)."""

    def _headers_for(email: str, name: str = "Test User", password: str = "correct horse") -> dict[str, str]:
        return user_for(email, name, password).headers

    return _headers_for
