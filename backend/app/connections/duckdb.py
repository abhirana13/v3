import os

import duckdb

from app.config import settings


def get_connection():
    os.makedirs(os.path.dirname(settings.duckdb_path), exist_ok=True)
    return duckdb.connect(settings.duckdb_path)


def check() -> dict:
    try:
        conn = get_connection()
        conn.execute("CREATE TABLE IF NOT EXISTS _health (id INTEGER, v VARCHAR)")
        conn.execute("DELETE FROM _health")
        conn.execute("INSERT INTO _health VALUES (1, 'ok')")
        row = conn.execute("SELECT v FROM _health WHERE id = 1").fetchone()
        conn.close()
        return {"status": "ok", "result": row[0] if row else None}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
