from unittest.mock import MagicMock, patch

import pytest


def _create_chart(client, name="Test"):
    r = client.post(
        "/charts",
        json={"name": name, "query": "SELECT date, source, dau FROM t"},
    )
    assert r.status_code == 201
    return r.json()


def _intro_ctx(description):
    cursor = MagicMock()
    cursor.description = description
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


@pytest.fixture
def chart(client):
    return _create_chart(client)


def test_introspect_returns_proposed_split(client, chart):
    desc = [
        ("date", 1082, None, None, None, None, None),
        ("source", 1043, None, None, None, None, None),
        ("dau", 20, None, None, None, None, None),
    ]
    with patch("app.introspection.redshift_conn.connect", return_value=_intro_ctx(desc)):
        r = client.post(f"/charts/{chart['id']}/introspect")
    assert r.status_code == 200
    body = r.json()
    assert body["time_column"] == "date"
    assert [d["name"] for d in body["dimensions"]] == ["source"]
    assert [m["name"] for m in body["metrics"]] == ["dau"]


def test_introspect_chart_not_found(client):
    r = client.post("/charts/9999/introspect")
    assert r.status_code == 404


def test_introspect_redshift_error_returns_400(client, chart):
    ctx = MagicMock()
    ctx.__enter__.side_effect = RuntimeError("syntax error at or near WHEN")
    ctx.__exit__.return_value = False
    with patch("app.introspection.redshift_conn.connect", return_value=ctx):
        r = client.post(f"/charts/{chart['id']}/introspect")
    assert r.status_code == 400
    assert "syntax error" in r.json()["detail"]


def test_get_dims_metrics_empty_for_new_chart(client, chart):
    r = client.get(f"/charts/{chart['id']}/dims-metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["time_column"] is None
    assert body["date_format"] == "%Y-%m-%d"
    assert body["dimensions"] == []
    assert body["metrics"] == []


def test_get_dims_metrics_chart_not_found(client):
    r = client.get("/charts/9999/dims-metrics")
    assert r.status_code == 404


def test_put_dims_metrics_saves_and_returns(client, chart):
    payload = {
        "time_column": "date",
        "date_format": "%Y-%m-%d",
        "dimensions": [
            {"name": "source", "column_name": "source", "kind": "regular"},
            {"name": "country", "column_name": "country", "kind": "regular"},
        ],
        "metrics": [
            {
                "name": "dau",
                "column_name": "dau",
                "independent_dimensions": ["source"],
                "y_axis": "primary",
                "decimals": 0,
            },
            {
                "name": "revenue",
                "column_name": "revenue",
                "y_axis": "secondary",
                "decimals": 2,
                "unit": "$",
            },
        ],
    }
    r = client.put(f"/charts/{chart['id']}/dims-metrics", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["time_column"] == "date"
    assert body["date_format"] == "%Y-%m-%d"
    assert [d["name"] for d in body["dimensions"]] == ["source", "country"]
    assert [d["display_order"] for d in body["dimensions"]] == [0, 1]
    assert [m["name"] for m in body["metrics"]] == ["dau", "revenue"]
    assert body["metrics"][0]["independent_dimensions"] == ["source"]
    assert body["metrics"][1]["unit"] == "$"

    # Round-trip via GET
    r = client.get(f"/charts/{chart['id']}/dims-metrics")
    assert r.status_code == 200
    assert r.json()["metrics"][0]["independent_dimensions"] == ["source"]


def test_put_dims_metrics_replaces_existing(client, chart):
    client.put(
        f"/charts/{chart['id']}/dims-metrics",
        json={
            "dimensions": [{"name": "a", "column_name": "a"}],
            "metrics": [{"name": "m1", "column_name": "m1"}],
        },
    )
    client.put(
        f"/charts/{chart['id']}/dims-metrics",
        json={
            "dimensions": [
                {"name": "b", "column_name": "b"},
                {"name": "c", "column_name": "c"},
            ],
            "metrics": [{"name": "m2", "column_name": "m2"}],
        },
    )
    r = client.get(f"/charts/{chart['id']}/dims-metrics")
    body = r.json()
    assert [d["name"] for d in body["dimensions"]] == ["b", "c"]
    assert [m["name"] for m in body["metrics"]] == ["m2"]


def test_put_dims_metrics_rejects_independent_dim_to_unknown(client, chart):
    payload = {
        "dimensions": [{"name": "source", "column_name": "source"}],
        "metrics": [
            {
                "name": "dau",
                "column_name": "dau",
                "independent_dimensions": ["country"],  # not in dimensions
            }
        ],
    }
    r = client.put(f"/charts/{chart['id']}/dims-metrics", json=payload)
    assert r.status_code == 422
    assert "country" in r.text


def test_put_dims_metrics_chart_not_found(client):
    r = client.put(
        "/charts/9999/dims-metrics",
        json={"dimensions": [], "metrics": []},
    )
    assert r.status_code == 404


def test_delete_chart_cascades_dims_metrics(client, chart):
    client.put(
        f"/charts/{chart['id']}/dims-metrics",
        json={
            "dimensions": [{"name": "source", "column_name": "source"}],
            "metrics": [{"name": "dau", "column_name": "dau"}],
        },
    )
    r = client.delete(f"/charts/{chart['id']}")
    assert r.status_code == 204
    r = client.get(f"/charts/{chart['id']}/dims-metrics")
    assert r.status_code == 404
