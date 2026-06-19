"""Read-only Redshift connection. SELECT only — must never write."""

from contextlib import contextmanager

import redshift_connector

from app.config import settings


@contextmanager
def connect():
    conn = redshift_connector.connect(
        host=settings.redshift_host,
        port=settings.redshift_port,
        database=settings.redshift_database,
        user=settings.redshift_user,
        password=settings.redshift_password,
    )
    try:
        yield conn
    finally:
        conn.close()


def check() -> dict:
    if not settings.redshift_host or settings.redshift_host.startswith("your-cluster"):
        return {"status": "not_configured", "detail": "REDSHIFT_HOST not set in .env"}
    try:
        with connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            return {"status": "ok", "result": row[0]}
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
