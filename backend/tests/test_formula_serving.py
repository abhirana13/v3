"""Phase 7: formula metrics end-to-end through the serving API."""

from datetime import date

import duckdb
import pytest

from app.backpop.duckdb_writer import table_name


@pytest.fixture
def duckdb_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


def _seed(duckdb_path, chart_id, columns_with_types, rows):
    conn = duckdb.connect(duckdb_path)
    table = table_name(chart_id)
    cols_def = ", ".join(f'"{c}" {t}' for c, t in columns_with_types)
    conn.execute(f'CREATE TABLE "{table}" ({cols_def})')
    cols = [c for c, _ in columns_with_types]
    placeholders = ", ".join("?" * len(cols))
    col_list = ", ".join(f'"{c}"' for c in cols)
    conn.executemany(
        f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})', rows
    )
    conn.close()


def _make_chart(client, dims, metrics, name):
    r = client.post(
        "/charts",
        json={"name": name, "query": "SELECT 1", "time_column": "event_date"},
    )
    chart_id = r.json()["id"]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": d, "column_name": d} for d in dims],
            "metrics": metrics,
        },
    )
    assert r.status_code == 200, r.text
    return chart_id


def test_formula_ratio_no_grouping(client, duckdb_path):
    """arpu = revenue / dau, both summable, no dim grouping."""
    chart_id = _make_chart(
        client,
        dims=["country"],
        metrics=[
            {"name": "revenue", "column_name": "revenue"},
            {"name": "dau", "column_name": "dau"},
            {"name": "arpu", "formula": "revenue / dau", "decimals": 2},
        ],
        name="formula-ratio",
    )
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("revenue", "DOUBLE"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 12), "US", 100.0, 40),
            (date(2026, 6, 12), "UK", 50.0, 10),  # totals: rev=150, dau=50
        ],
    )
    r = client.get(f"/charts/{chart_id}/data?group_by=")
    row = r.json()["rows"][0]
    assert row["revenue"] == pytest.approx(150.0)
    assert row["dau"] == 50
    assert row["arpu"] == pytest.approx(3.0)  # 150 / 50


def test_formula_uses_deduped_independent_metric(client, duckdb_path):
    """arpu = revenue / dau where dau is INDEPENDENT of source. The formula
    must divide by the deduped dau (not the source-multiplied sum)."""
    chart_id = _make_chart(
        client,
        dims=["source"],
        metrics=[
            {"name": "revenue", "column_name": "revenue"},
            {
                "name": "dau",
                "column_name": "dau",
                "independent_dimensions": ["source"],
            },
            {"name": "arpu", "formula": "revenue / dau", "decimals": 4},
        ],
        name="formula-indep",
    )
    # 3 sources, same dau=100 per day (true DAU=100), source-specific revenue
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("source", "VARCHAR"), ("revenue", "DOUBLE"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 12), "A", 30.0, 100),
            (date(2026, 6, 12), "B", 40.0, 100),
            (date(2026, 6, 12), "C", 50.0, 100),
        ],
    )
    # No grouping: revenue=120, dau deduped=100 → arpu=1.2 (NOT 120/300=0.4)
    r = client.get(f"/charts/{chart_id}/data?group_by=")
    row = r.json()["rows"][0]
    assert row["dau"] == 100
    assert row["revenue"] == pytest.approx(120.0)
    assert row["arpu"] == pytest.approx(1.2)


def test_formula_division_by_zero_is_null(client, duckdb_path):
    chart_id = _make_chart(
        client,
        dims=["country"],
        metrics=[
            {"name": "revenue", "column_name": "revenue"},
            {"name": "dau", "column_name": "dau"},
            {"name": "arpu", "formula": "revenue / dau", "decimals": 2},
        ],
        name="formula-divzero",
    )
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("revenue", "DOUBLE"), ("dau", "BIGINT")],
        [(date(2026, 6, 12), "US", 100.0, 0)],
    )
    r = client.get(f"/charts/{chart_id}/data?group_by=")
    assert r.json()["rows"][0]["arpu"] is None


def test_formula_computed_without_requesting_base_metrics(client, duckdb_path):
    """Requesting only the formula metric still computes its base inputs,
    but the base metrics are not emitted unless requested."""
    chart_id = _make_chart(
        client,
        dims=["country"],
        metrics=[
            {"name": "revenue", "column_name": "revenue"},
            {"name": "dau", "column_name": "dau"},
            {"name": "arpu", "formula": "revenue / dau", "decimals": 2},
        ],
        name="formula-only",
    )
    _seed(
        duckdb_path,
        chart_id,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("revenue", "DOUBLE"), ("dau", "BIGINT")],
        [(date(2026, 6, 12), "US", 100.0, 40)],
    )
    r = client.get(f"/charts/{chart_id}/data?group_by=&metrics=arpu")
    row = r.json()["rows"][0]
    assert row["arpu"] == pytest.approx(2.5)  # 100/40
    assert "revenue" not in row
    assert "dau" not in row


def test_formula_validation_rejected_at_config_time(client):
    r = client.post(
        "/charts",
        json={"name": "bad-formula", "query": "SELECT 1", "time_column": "event_date"},
    )
    chart_id = r.json()["id"]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [],
            "metrics": [
                {"name": "dau", "column_name": "dau"},
                {"name": "bad", "formula": "dau / mau"},  # mau not a base metric
            ],
        },
    )
    assert r.status_code == 422
    assert "mau" in r.text


def test_metric_requires_column_or_formula_not_both(client):
    r = client.post(
        "/charts",
        json={"name": "both-set", "query": "SELECT 1", "time_column": "event_date"},
    )
    chart_id = r.json()["id"]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [],
            "metrics": [
                {"name": "x", "column_name": "x", "formula": "y + 1"},
            ],
        },
    )
    assert r.status_code == 422
    assert "exactly one" in r.text
