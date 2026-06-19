from unittest.mock import patch


def test_create_chart_without_initial_backpop_does_not_schedule(client):
    with patch("app.api.charts._bg_initial_backpop") as mock_bg:
        r = client.post(
            "/charts",
            json={"name": "no-init", "query": "SELECT 1"},
        )
    assert r.status_code == 201
    mock_bg.assert_not_called()


def test_create_chart_with_initial_backpop_schedules_background_task(client):
    with patch("app.api.charts._bg_initial_backpop") as mock_bg:
        r = client.post(
            "/charts",
            json={
                "name": "with-init",
                "query": "SELECT event_date FROM t WHERE event_date = DATE '{CUR_DATE_HIPHEN}'",
                "initial_backpop_days": 7,
            },
        )
    assert r.status_code == 201
    chart_id = r.json()["id"]
    mock_bg.assert_called_once_with(chart_id, 7)


def test_initial_backpop_field_is_validated(client):
    r = client.post(
        "/charts",
        json={
            "name": "bad-init",
            "query": "SELECT 1",
            "initial_backpop_days": 0,
        },
    )
    assert r.status_code == 422


def test_initial_backpop_days_not_in_response(client):
    """The field is input-only and should not echo back on the Chart record."""
    # Mock the background task: this test only asserts response shape, and the
    # real task does live Redshift + DuckDB I/O (which can block on a DuckDB
    # file lock when other processes hold the cache).
    with patch("app.api.charts._bg_initial_backpop"):
        r = client.post(
            "/charts",
            json={
                "name": "leaky-init",
                "query": "SELECT 1",
                "initial_backpop_days": 5,
            },
        )
    assert r.status_code == 201
    assert "initial_backpop_days" not in r.json()
