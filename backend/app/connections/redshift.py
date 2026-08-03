"""Read-only Redshift connection. SELECT only — must never write.

When BASTION_HOST is configured, every connection is opened through an SSH tunnel
to the bastion (authenticated with the private key at BASTION_KEY_PATH) which
forwards to the real Redshift endpoint. The tunnel is opened per connection and
torn down with it — keeps the code shape simple; revisit if latency matters.
"""

from contextlib import contextmanager
from pathlib import Path

import redshift_connector

from app.config import settings


def _bastion_enabled() -> bool:
    return bool(settings.bastion_host)


@contextmanager
def connect(database: str | None = None):
    """Open a read-only connection. `database` picks which database on the cluster to
    query (same host/credentials); None => the default (settings.redshift_database)."""
    db = database or settings.redshift_database

    if _bastion_enabled():
        from sshtunnel import SSHTunnelForwarder

        key_path = settings.bastion_key_path
        if not Path(key_path).exists():
            raise RuntimeError(
                f"BASTION_KEY_PATH not found on disk: {key_path}. "
                "Place the private key at that path (chmod 600) and restart."
            )

        with SSHTunnelForwarder(
            (settings.bastion_host, settings.bastion_port),
            ssh_username=settings.bastion_user,
            ssh_pkey=key_path,
            remote_bind_address=(settings.redshift_host, settings.redshift_port),
        ) as tunnel:
            # Redshift requires SSL here (cluster has require_ssl on): a non-SSL
            # connection is rejected at pg_hba with "no pg_hba.conf entry ... SSL off".
            # ssl=True uses the driver default sslmode=verify-ca, which validates the
            # server cert against the CA but NOT the hostname — so terminating at
            # 127.0.0.1 through the tunnel still verifies. (Do NOT set ssl=False: the
            # SSH hop being encrypted doesn't satisfy the cluster's SSL requirement.)
            conn = redshift_connector.connect(
                host="127.0.0.1",
                port=tunnel.local_bind_port,
                database=db,
                user=settings.redshift_user,
                password=settings.redshift_password,
                ssl=True,
            )
            try:
                yield conn
            finally:
                conn.close()
        return

    conn = redshift_connector.connect(
        host=settings.redshift_host,
        port=settings.redshift_port,
        database=db,
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
