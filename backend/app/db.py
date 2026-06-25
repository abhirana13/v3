"""Shared schema bootstrap for v1.

Used by both the backend (in its FastAPI lifespan) and the worker (on startup)
so both can boot independently against a fresh or partially-migrated database.
Replace with Alembic when v1 stabilizes.
"""

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.connections.postgres import engine
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
        if "dimensions" in inspector.get_table_names():
            dim_cols = {c["name"] for c in inspector.get_columns("dimensions")}
            if "value_order" not in dim_cols:
                conn.execute(
                    text(
                        "ALTER TABLE dimensions "
                        "ADD COLUMN value_order VARCHAR NOT NULL DEFAULT 'natural'"
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
