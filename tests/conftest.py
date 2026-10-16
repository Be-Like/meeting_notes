import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import get_db
from main import app
from models import Base


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


@pytest.fixture
def client(db_engine):
    """TestClient with get_db overridden to a per-request session on db_engine. Lifespan is not entered."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
