"""Backend-derived dimensions: country_tier computed from country, no query column."""

from datetime import date

import duckdb

from app.backpop.duckdb_writer import materialize_derived, table_name
from app.models import Chart


def _seed(path, chart_id, columns_with_types, rows):
    conn = duckdb.connect(path)
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
    cid = client.post("/charts", json={"name": "derived", "query": "SELECT 1", "time_column": "event_date"}).json()["id"]
    # only `country` is a real dimension — country_tier is NOT in the query/dims
    assert client.put(f"/charts/{cid}/dims-metrics", json={
        "time_column": "event_date",
        "dimensions": [{"name": "country", "column_name": "country"}],
        "metrics": [{"name": "dau", "column_name": "dau"}],
    }).status_code == 200
    return cid


def test_country_tier_is_derived_from_country(client, db_session, isolated_duckdb):
    cid = _make_chart(client)
    _seed(
        isolated_duckdb, cid,
        [("event_date", "DATE"), ("country", "VARCHAR"), ("dau", "BIGINT")],
        [
            (date(2026, 6, 1), "United States", 100),
            (date(2026, 6, 1), "Canada", 20),
            (date(2026, 6, 1), "India", 30),
        ],
    )
    # backend computes the derived column into the cache (runs inside run_backpop in prod)
    materialize_derived(db_session.get(Chart, cid))

    # 1. the cache gained a country_tier column
    con = duckdb.connect(isolated_duckdb)
    cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table_name(cid)}")').fetchall()]
    con.close()
    assert "country_tier" in cols

    # 2. dim-values exposes it with just the buckets
    dv = client.get(f"/charts/{cid}/dim-values").json()["dimensions"]
    assert set(dv["country_tier"]) == {"Tier-1", "Tier-2"}

    # 3. dims-metrics lists it, flagged derived (so the config page can hide it)
    dims = client.get(f"/charts/{cid}/dims-metrics").json()["dimensions"]
    ct = next(d for d in dims if d["name"] == "country_tier")
    assert ct["derived"] is True
    assert all(d["name"] != "country_tier" for d in dims if not d["derived"])  # not a real dim

    # 4. splitting by the derived dim maps correctly (US + Canada = Tier-1, India = Tier-2)
    rows = client.get(f"/charts/{cid}/data?group_by=country_tier&metrics=dau").json()["rows"]
    by_tier = {r["country_tier"]: r["dau"] for r in rows}
    assert by_tier == {"Tier-1": 120, "Tier-2": 30}


def test_derived_dim_is_not_persisted_as_real(client, db_session, isolated_duckdb):
    """Even if a client sends country_tier in the dims payload, it isn't saved as a real dim."""
    cid = client.post("/charts", json={"name": "derived2", "query": "SELECT 1", "time_column": "event_date"}).json()["id"]
    client.put(f"/charts/{cid}/dims-metrics", json={
        "time_column": "event_date",
        "dimensions": [
            {"name": "country", "column_name": "country"},
            {"name": "country_tier", "column_name": "country_tier"},  # should be dropped
        ],
        "metrics": [{"name": "dau", "column_name": "dau"}],
    })
    saved = [d.name for d in db_session.get(Chart, cid).dimensions]
    assert saved == ["country"]  # country_tier not persisted
