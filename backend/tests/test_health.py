from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_all_ok():
    with patch("app.main.redshift_conn.check", return_value={"status": "ok", "result": 1}), \
         patch("app.main.duckdb_conn.check", return_value={"status": "ok", "result": "ok"}), \
         patch("app.main.postgres_conn.check", return_value={"status": "ok", "result": 1}):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert set(body["checks"].keys()) == {"redshift", "duckdb", "postgres"}
        assert all(c["status"] == "ok" for c in body["checks"].values())


def test_health_degraded_when_one_check_fails():
    with patch("app.main.redshift_conn.check", return_value={"status": "error", "detail": "boom"}), \
         patch("app.main.duckdb_conn.check", return_value={"status": "ok", "result": "ok"}), \
         patch("app.main.postgres_conn.check", return_value={"status": "ok", "result": 1}):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redshift"]["status"] == "error"


def test_health_degraded_when_redshift_not_configured():
    with patch("app.main.redshift_conn.check", return_value={"status": "not_configured", "detail": "REDSHIFT_HOST not set in .env"}), \
         patch("app.main.duckdb_conn.check", return_value={"status": "ok", "result": "ok"}), \
         patch("app.main.postgres_conn.check", return_value={"status": "ok", "result": 1}):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["checks"]["redshift"]["status"] == "not_configured"
