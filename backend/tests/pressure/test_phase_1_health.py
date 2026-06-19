"""Phase 1 pressure: the Redshift health check must be strictly read-only.

(Independent-check / degraded behaviour is covered by tests/test_health.py.)"""

from unittest.mock import MagicMock, patch

from app.connections import redshift as redshift_conn

_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "COPY", "MERGE")


def test_redshift_check_issues_only_select_1_and_never_writes():
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    conn = MagicMock()
    conn.cursor.return_value = cursor

    with patch("app.connections.redshift.redshift_connector") as rc, \
         patch("app.connections.redshift.settings") as s:
        s.redshift_host = "real-cluster.example.com"
        rc.connect.return_value = conn
        result = redshift_conn.check()

    assert result == {"status": "ok", "result": 1}
    executed = [c.args[0] for c in cursor.execute.call_args_list]
    assert executed == ["SELECT 1"]
    joined = " ".join(executed).upper()
    assert not any(k in joined for k in _WRITE_KEYWORDS)


def test_redshift_check_reports_not_configured_without_connecting():
    with patch("app.connections.redshift.redshift_connector") as rc, \
         patch("app.connections.redshift.settings") as s:
        s.redshift_host = "your-cluster-here"  # placeholder => not configured
        result = redshift_conn.check()
    assert result["status"] == "not_configured"
    rc.connect.assert_not_called()  # never opens a socket when unconfigured
