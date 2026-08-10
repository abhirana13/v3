import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.connections.postgres import get_db
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
def isolated_duckdb(tmp_path, monkeypatch):
    """Point the DuckDB cache at a per-test temp file so no test can ever touch the real
    aggregate cache. Tests that seed data request this fixture for the path; tests with their
    own duckdb_path fixture override it (autouse runs first, the explicit one wins).

    Production stores ONE FILE PER CHART (`<dir>/charts/chart_<id>.duckdb`) so a backpop's
    write lock — which DuckDB holds per FILE — cannot block reads of other charts. Here that
    layout is deliberately collapsed onto a single file: `chart_db_path` is patched to ignore
    the chart id and return whatever `settings.duckdb_path` currently points at.

    Why: ~33 seeding sites across the test suite do `duckdb.connect(<the fixture path>)` and
    create a `chart_<id>_data` table in it. Those tests are about serving, backpop, formulas
    and independent-metric maths — none of which care how the files are laid out — so
    rewriting all of them would be churn for no coverage. Reading `settings.duckdb_path`
    lazily also means a test's own `duckdb_path` fixture keeps working, whichever order the
    fixtures run in.

    The layout itself is covered separately by test_chart_db_split.py, which does NOT apply
    this collapse.
    """
    from app.config import settings

    path = str(tmp_path / "isolated.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    monkeypatch.setattr(
        "app.connections.duckdb.chart_db_path", lambda chart_id: settings.duckdb_path
    )
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
