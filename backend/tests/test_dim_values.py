"""Phase 8 support: GET /charts/{id}/dim-values — distinct values + date extent."""

from datetime import date

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "dv.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _seed(duckdb_path, chart_id, columns_with_types, rows):
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    cols_def = ", ".join(f'"{c}" {t}' for c, t in columns_with_types)
    conn.execute(f'CREATE TABLE "{table}" ({cols_def})')
    cols = [c for c, _ in columns_with_types]
    ph = ", ".join("?" * len(cols))
    conn.executemany(
        f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) VALUES ({ph})',
        rows,
    )
    conn.close()


def _make_chart(client):
    cid = client.post("/charts", json={"name": "dv", "query": "SELECT 1", "time_column": "event_date"}).json()["id"]
    r = client.put(
        f"/charts/{cid}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": "country", "column_name": "country"}, {"name": "source", "column_name": "source"}],
            "metrics": [{"name": "dau", "column_name": "dau"}],
        },
    )
    assert r.status_code == 200, r.text
    return cid


def test_dim_values_ordered_by_metric_descending_and_date_extent(client, duckdb_path):
    cid = _make_chart(client)
    _seed(
        duckdb_path, cid,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("source", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 1), "US", "A", 10),
            (date(2026, 6, 1), "UK", "B", 5),
            (date(2026, 6, 3), "US", "A", 12),
        ],
    )
    body = client.get(f"/charts/{cid}/dim-values").json()
    # ordered by descending total of the primary metric (dau), NOT alphabetically:
    # US = 10 + 12 = 22 > UK = 5  -> US first
    assert body["dimensions"]["country"] == ["US", "UK"]
    assert body["dimensions"]["source"] == ["A", "B"]  # A (with US) = 22 > B = 5
    assert body["date_min"] == "2026-06-01"
    assert body["date_max"] == "2026-06-03"


def test_dim_values_respects_date_range(client, duckdb_path):
    cid = _make_chart(client)
    _seed(
        duckdb_path, cid,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("source", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 1), "US", "A", 10),
            (date(2026, 6, 9), "BR", "C", 7),
        ],
    )
    body = client.get(f"/charts/{cid}/dim-values?from_date=2026-06-05&to_date=2026-06-30").json()
    assert body["dimensions"]["country"] == ["BR"]  # US (6/1) filtered out


def test_dim_values_empty_table(client, duckdb_path):
    cid = _make_chart(client)
    body = client.get(f"/charts/{cid}/dim-values").json()
    assert body["dimensions"] == {"country": [], "source": []}
    assert body["date_min"] is None
