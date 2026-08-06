"""Shared schema bootstrap for v1.

Used by both the backend (in its FastAPI lifespan) and the worker (on startup)
so both can boot independently against a fresh or partially-migrated database.
Replace with Alembic when v1 stabilizes.
"""

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.connections.postgres import engine
from app.dashboards import models as _dashboards_models  # noqa: F401 — registers dashboard tables on Base
from app.models import Base, Chart


def ensure_schema(eng=None) -> None:
    eng = eng or engine
    Base.metadata.create_all(bind=eng)
    inspector = inspect(eng)
    if "charts" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("charts")}
    with eng.begin() as conn:
        if "variables" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE charts "
                    "ADD COLUMN variables JSON NOT NULL DEFAULT '{}'::json"
                )
            )
        if "certified" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE charts "
                    "ADD COLUMN certified BOOLEAN NOT NULL DEFAULT false"
                )
            )
        if "cache_query_hash" not in cols:
            conn.execute(text("ALTER TABLE charts ADD COLUMN cache_query_hash VARCHAR"))
        if "database" not in cols:
            conn.execute(text("ALTER TABLE charts ADD COLUMN database VARCHAR"))
        if "x_axis" not in cols:
            conn.execute(text("ALTER TABLE charts ADD COLUMN x_axis VARCHAR"))
        if "cache_latest_date" not in cols:
            conn.execute(text("ALTER TABLE charts ADD COLUMN cache_latest_date DATE"))
        if "cache_columns" not in cols:
            conn.execute(text("ALTER TABLE charts ADD COLUMN cache_columns JSON"))
        if "default_end_offset_days" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE charts "
                    "ADD COLUMN default_end_offset_days INTEGER NOT NULL DEFAULT 2"
                )
            )
        if "chart_number" not in cols:
            conn.execute(text("ALTER TABLE charts ADD COLUMN chart_number INTEGER"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_charts_chart_number "
                    "ON charts (chart_number)"
                )
            )
            # Backfill legacy charts into the uncertified (1000+) series, by id.
            conn.execute(
                text(
                    "UPDATE charts SET chart_number = sub.n FROM ("
                    "SELECT id, 1000 + (ROW_NUMBER() OVER (ORDER BY id) - 1) AS n "
                    "FROM charts WHERE chart_number IS NULL) AS sub "
                    "WHERE charts.id = sub.id"
                )
            )
        conn.execute(
            text(
                "UPDATE charts SET date_format = '%Y-%m-%d' "
                "WHERE date_format IS NULL"
            )
        )
        if "metrics" in inspector.get_table_names():
            metric_cols = {c["name"]: c for c in inspector.get_columns("metrics")}
            # formula metrics have no backing column_name
            if not metric_cols.get("column_name", {}).get("nullable", True):
                conn.execute(
                    text("ALTER TABLE metrics ALTER COLUMN column_name DROP NOT NULL")
                )
        if "metrics" in inspector.get_table_names():
            if "included" not in {c["name"] for c in inspector.get_columns("metrics")}:
                conn.execute(
                    text(
                        "ALTER TABLE metrics "
                        "ADD COLUMN included BOOLEAN NOT NULL DEFAULT true"
                    )
                )
        if "dimensions" in inspector.get_table_names():
            dim_cols = {c["name"] for c in inspector.get_columns("dimensions")}
            if "value_order" not in dim_cols:
                conn.execute(
                    text(
                        "ALTER TABLE dimensions "
                        "ADD COLUMN value_order VARCHAR NOT NULL DEFAULT 'natural'"
                    )
                )
            if "included" not in dim_cols:
                conn.execute(
                    text(
                        "ALTER TABLE dimensions "
                        "ADD COLUMN included BOOLEAN NOT NULL DEFAULT true"
                    )
                )
        if "backpop_runs" in inspector.get_table_names():
            br_cols = {c["name"] for c in inspector.get_columns("backpop_runs")}
            if "force" not in br_cols:
                conn.execute(
                    text(
                        "ALTER TABLE backpop_runs "
                        "ADD COLUMN force BOOLEAN NOT NULL DEFAULT false"
                    )
                )
            if "cancel_requested" not in br_cols:
                conn.execute(
                    text(
                        "ALTER TABLE backpop_runs "
                        "ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT false"
                    )
                )

    # Seed cache_query_hash for charts that don't have one yet, treating an existing
    # cache as built from the current query. This lets a *future* query edit be detected
    # (hash mismatch -> rebuild) without invalidating every chart's cache on upgrade.
    from app.backpop import query_hash  # local import: avoids a module-load cycle

    with Session(eng) as session:
        pending = session.query(Chart).filter(Chart.cache_query_hash.is_(None)).all()
        for c in pending:
            c.cache_query_hash = query_hash(c)
        if pending:
            session.commit()

    # Backfill the two Postgres mirrors of DuckDB state for any chart that doesn't have them
    # yet, so request paths never open the cache file (see charts_overview and
    # duckdb_writer.cache_present_columns):
    #   * cache_latest_date — freshness for the home page
    #   * cache_columns     — which backend-derived dimensions apply, for the config page
    # The worker refreshes both after every backpop; this covers charts whose cache predates
    # the columns.
    #
    # Keyed on "value still missing", NOT on "just added the column": a one-shot gate can be
    # consumed by a run where the seed doesn't complete, and then never fires again. This
    # form self-heals and costs one read-only DuckDB open at startup only while something is
    # still unseeded. Uses ONE connection for every chart, not one per chart.
    with Session(eng) as session:
        need_date = [
            (c.id, c.time_column)
            for c in session.query(Chart)
            .filter(Chart.cache_latest_date.is_(None), Chart.time_column.isnot(None))
            .all()
        ]
        need_cols = [
            c.id
            for c in session.query(Chart).filter(Chart.cache_columns.is_(None)).all()
        ]
    if not need_date and not need_cols:
        return

    from app.backpop.duckdb_writer import table_name
    from app.connections import duckdb as duckdb_conn

    try:
        conn = duckdb_conn.get_connection(read_only=True)
    except Exception as e:  # cache locked/unopenable — retried next startup, or by a backpop
        print(f"[schema] cache mirror seed skipped ({type(e).__name__}: {e})", flush=True)
        return
    try:
        present = {
            r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        found: dict[int, object] = {}
        for cid, time_col in need_date:
            if table_name(cid) not in present:
                continue  # no cache built yet — the first backpop will set it
            try:
                row = conn.execute(
                    f'SELECT MAX(CAST("{time_col}" AS DATE)) FROM "{table_name(cid)}"'
                ).fetchone()
            except Exception as e:
                print(f"[schema] chart {cid}: freshness seed failed ({e})", flush=True)
                continue
            if row and row[0] is not None:
                found[cid] = row[0]

        found_cols: dict[int, list[str]] = {}
        for cid in need_cols:
            if table_name(cid) not in present:
                continue
            try:
                rows = conn.execute(f'PRAGMA table_info("{table_name(cid)}")').fetchall()
            except Exception as e:
                print(f"[schema] chart {cid}: column seed failed ({e})", flush=True)
                continue
            found_cols[cid] = sorted(r[1] for r in rows)
    finally:
        conn.close()

    if found or found_cols:
        with Session(eng) as session:
            for c in (
                session.query(Chart)
                .filter(Chart.id.in_(set(found) | set(found_cols)))
                .all()
            ):
                if c.id in found:
                    c.cache_latest_date = found[c.id]
                if c.id in found_cols:
                    c.cache_columns = found_cols[c.id]
            session.commit()
        print(
            f"[schema] seeded freshness for {len(found)} chart(s), "
            f"cache columns for {len(found_cols)} chart(s)",
            flush=True,
        )
