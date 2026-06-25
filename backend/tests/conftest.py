import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connections.postgres import get_db
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
def _clear_backpop_cancel():
    """The cancel registry is module-level; clear it around each test so a leftover
    run id (ids restart per in-memory DB) can't cancel an unrelated run."""
    from app.backpop import _cancel_requested
    _cancel_requested.clear()
    yield
    _cancel_requested.clear()


@pytest.fixture(autouse=True)
def isolated_duckdb(tmp_path, monkeypatch):
    """Point the DuckDB cache at a per-test temp file so no test can ever touch the
    real aggregate cache (e.g. chart deletion now drops a chart_<id>_data table).
    Tests that seed data may request this fixture for the path; tests with their own
    duckdb_path fixture override it (autouse runs first, the explicit one wins)."""
    path = str(tmp_path / "isolated.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
