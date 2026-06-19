"""Phase 10 support: GET /charts/{id}/freshness — latest data date + last run."""

from datetime import date, datetime

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name
from app.models import BackpopRun


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "fresh.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _make_chart(client):
    cid = client.post("/charts", json={"name": "fresh", "query": "SELECT 1", "time_column": "event_date"}).json()["id"]
    r = client.put(
        f"/charts/{cid}/dims-metrics",
        json={"time_column": "event_date", "dimensions": [], "metrics": [{"name": "dau", "column_name": "dau"}]},
    )
    assert r.status_code == 200, r.text
    return cid


def _seed(duckdb_path, cid, rows):
    conn = duckdb.connect(duckdb_path)
    t = table_name(cid)
    conn.execute(f'CREATE TABLE "{t}" ("event_date" DATE, "dau" BIGINT)')
    conn.executemany(f'INSERT INTO "{t}" VALUES (?, ?)', rows)
    conn.close()


def test_freshness_reports_latest_date_and_last_run(client, db_session, duckdb_path):
    cid = _make_chart(client)
    _seed(duckdb_path, cid, [(date(2026, 6, 1), 10), (date(2026, 6, 9), 12)])
    db_session.add(BackpopRun(
        chart_id=cid, from_date=date(2026, 6, 1), to_date=date(2026, 6, 9),
        batch_size=30, status="success", row_count=2, batches_completed=1,
        completed_at=datetime(2026, 6, 9, 3, 0, 0),
    ))
    db_session.commit()

    body = client.get(f"/charts/{cid}/freshness").json()
    assert body["latest_data_date"] == "2026-06-09"
    assert body["running"] is False
    assert body["last_run"]["status"] == "success"
    assert body["last_run"]["row_count"] == 2


def test_freshness_running_flag(client, db_session, duckdb_path):
    cid = _make_chart(client)
    db_session.add(BackpopRun(
        chart_id=cid, from_date=date(2026, 6, 1), to_date=date(2026, 6, 9),
        batch_size=30, status="running", row_count=0, batches_completed=0,
    ))
    db_session.commit()
    body = client.get(f"/charts/{cid}/freshness").json()
    assert body["running"] is True
    assert body["last_run"]["status"] == "running"


def test_freshness_empty_when_no_data_or_runs(client, duckdb_path):
    cid = _make_chart(client)
    body = client.get(f"/charts/{cid}/freshness").json()
    assert body["latest_data_date"] is None
    assert body["running"] is False
    assert body["last_run"] is None


def test_freshness_chart_not_found(client):
    assert client.get("/charts/99999/freshness").status_code == 404


def test_reap_marks_old_running_runs_failed(db_session):
    from app.backpop import reap_stale_runs

    old = BackpopRun(chart_id=1, from_date=date(2026, 1, 1), to_date=date(2026, 1, 2),
                     batch_size=30, status="running", started_at=datetime(2020, 1, 1))
    recent = BackpopRun(chart_id=1, from_date=date(2026, 1, 1), to_date=date(2026, 1, 2),
                        batch_size=30, status="running", started_at=datetime.now())
    db_session.add_all([old, recent])
    db_session.commit()

    n = reap_stale_runs(db_session, max_age_minutes=60)
    db_session.refresh(old)
    db_session.refresh(recent)
    assert n == 1
    assert old.status == "failed"
    assert "stale" in (old.error_message or "")
    assert old.completed_at is not None
    assert recent.status == "running"  # within the age window, left alone


def test_reap_noop_when_nothing_stale(db_session):
    from app.backpop import reap_stale_runs

    assert reap_stale_runs(db_session) == 0
