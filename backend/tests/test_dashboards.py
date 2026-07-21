"""Phase 1 — dashboards model + CRUD + API.

Covers: dashboard CRUD (auto Main tab, number allocation, name conflicts),
tabs (add/rename/delete, last-tab guard), widgets (create/update/delete with
save-time validation against the source chart), bulk layout save, global
filters replace, replicate (deep copy), and delete cascades in both directions
(dashboard→tree, chart→widgets).
"""

import pytest

from app.dashboards.models import Dashboard, DashboardFilter, DashboardTab, Widget


# ---------- helpers ----------

def make_chart(client, name="dash-src", dims=("country", "platform"), metrics=("dau", "revenue")):
    r = client.post(
        "/charts",
        json={"name": name, "query": "SELECT 1", "time_column": "event_date"},
    )
    assert r.status_code == 201, r.text
    chart_id = r.json()["id"]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": "event_date",
            "dimensions": [{"name": d, "column_name": d} for d in dims],
            "metrics": [{"name": m, "column_name": m} for m in metrics],
        },
    )
    assert r.status_code == 200, r.text
    return chart_id


def make_dashboard(client, name="My Dashboard", **overrides):
    r = client.post("/dashboards", json={"name": name, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def chart_widget_body(chart_id, name="Daily Revenue", **config_overrides):
    config = {"metrics": [{"name": "revenue"}], **config_overrides}
    return {
        "type": "chart",
        "source_chart_id": chart_id,
        "name": name,
        "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
        "config": config,
    }


def number_widget_body(chart_id, name="Rev/Day", **config_overrides):
    config = {"metric": "revenue", **config_overrides}
    return {
        "type": "number",
        "source_chart_id": chart_id,
        "name": name,
        "layout": {"x": 6, "y": 0, "w": 3, "h": 2},
        "config": config,
    }


@pytest.fixture
def chart_id(client):
    return make_chart(client)


@pytest.fixture
def dash(client):
    return make_dashboard(client)


def main_tab_id(client, dashboard_id):
    r = client.get(f"/dashboards/{dashboard_id}")
    assert r.status_code == 200, r.text
    return r.json()["tabs"][0]["id"]


# ---------- dashboard CRUD ----------

def test_create_dashboard_auto_main_tab_and_number(client):
    d1 = make_dashboard(client, "First")
    assert d1["number"] == 100
    assert d1["enabled"] is True
    assert d1["default_date_range_days"] == 90
    assert d1["default_end_offset_days"] == 2

    tree = client.get(f"/dashboards/{d1['id']}").json()
    assert [t["name"] for t in tree["tabs"]] == ["Main"]
    assert tree["tabs"][0]["widgets"] == []
    assert tree["filters"] == []

    d2 = make_dashboard(client, "Second")
    assert d2["number"] == 101


def test_create_duplicate_name_409(client):
    make_dashboard(client, "Dupe")
    r = client.post("/dashboards", json={"name": "Dupe"})
    assert r.status_code == 409


def test_list_overview_counts(client, chart_id):
    d = make_dashboard(client, "Counted")
    tab_id = main_tab_id(client, d["id"])
    client.post(f"/dashboards/{d['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id))
    client.post(f"/dashboards/{d['id']}/tabs/{tab_id}/widgets", json=number_widget_body(chart_id))

    rows = client.get("/dashboards").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Counted"
    assert row["number"] == 100
    assert row["tab_count"] == 1
    assert row["widget_count"] == 2


def test_update_dashboard(client, dash):
    r = client.put(
        f"/dashboards/{dash['id']}",
        json={"name": "Renamed", "enabled": False, "default_end_offset_days": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Renamed"
    assert body["enabled"] is False
    assert body["default_end_offset_days"] == 3


def test_unknown_dashboard_404s(client):
    assert client.get("/dashboards/9999").status_code == 404
    assert client.put("/dashboards/9999", json={"name": "x"}).status_code == 404
    assert client.delete("/dashboards/9999").status_code == 404
    assert client.post("/dashboards/9999/replicate").status_code == 404


def test_delete_dashboard_cascades_tree(client, db_session, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    client.post(f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id))
    client.put(
        f"/dashboards/{dash['id']}/filters",
        json={"filters": [{"dimension": "country", "default_values": ["US"]}]},
    )

    assert client.delete(f"/dashboards/{dash['id']}").status_code == 204
    assert client.get(f"/dashboards/{dash['id']}").status_code == 404
    assert db_session.query(Dashboard).count() == 0
    assert db_session.query(DashboardTab).count() == 0
    assert db_session.query(Widget).count() == 0
    assert db_session.query(DashboardFilter).count() == 0
    # the source chart is untouched
    assert client.get(f"/charts/{chart_id}").status_code == 200


# ---------- tabs ----------

def test_add_rename_delete_tab(client, dash):
    did = dash["id"]
    r = client.post(f"/dashboards/{did}/tabs", json={"name": "ROAS Deepdive"})
    assert r.status_code == 201
    tab = r.json()
    assert tab["display_order"] == 1  # after Main (0)

    r = client.put(f"/dashboards/{did}/tabs/{tab['id']}", json={"name": "Engagement"})
    assert r.status_code == 200
    assert r.json()["name"] == "Engagement"

    assert client.delete(f"/dashboards/{did}/tabs/{tab['id']}").status_code == 204
    tree = client.get(f"/dashboards/{did}").json()
    assert [t["name"] for t in tree["tabs"]] == ["Main"]


def test_delete_last_tab_400(client, dash):
    tab_id = main_tab_id(client, dash["id"])
    r = client.delete(f"/dashboards/{dash['id']}/tabs/{tab_id}")
    assert r.status_code == 400
    assert "at least one tab" in r.json()["detail"]


def test_tab_of_other_dashboard_404(client):
    d1 = make_dashboard(client, "A")
    d2 = make_dashboard(client, "B")
    d2_tab = main_tab_id(client, d2["id"])
    r = client.put(f"/dashboards/{d1['id']}/tabs/{d2_tab}", json={"name": "X"})
    assert r.status_code == 404


# ---------- widgets: create + validation ----------

def test_create_chart_widget_normalizes_config(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json=chart_widget_body(chart_id, group_by=["country"], filters={"platform": ["ANDROID"]}),
    )
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["type"] == "chart"
    assert w["layout"] == {"x": 0, "y": 0, "w": 6, "h": 4}
    # defaults filled in by config normalization
    assert w["config"]["viz"] == "line"
    assert w["config"]["metrics"] == [{"name": "revenue", "y_axis": "primary"}]
    assert w["config"]["offset_mode"] == "only_on_end_date"


def test_create_number_widget_defaults(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=number_widget_body(chart_id)
    )
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["config"]["compares"] == ["previous_day", "last_week"]
    assert w["config"]["decimals"] == 0


def test_widget_unknown_chart_404(client, dash):
    tab_id = main_tab_id(client, dash["id"])
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(9999)
    )
    assert r.status_code == 404


def test_widget_metric_not_on_chart_400(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    body = chart_widget_body(chart_id)
    body["config"]["metrics"] = [{"name": "bogus_metric"}]
    r = client.post(f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=body)
    assert r.status_code == 400
    assert "bogus_metric" in r.json()["detail"]


def test_widget_group_by_dim_not_on_chart_400(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json=chart_widget_body(chart_id, group_by=["bogus_dim"]),
    )
    assert r.status_code == 400
    assert "bogus_dim" in r.json()["detail"]


def test_widget_filter_dim_not_on_chart_400(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json=number_widget_body(chart_id, filters={"bogus_dim": ["x"]}),
    )
    assert r.status_code == 400
    assert "bogus_dim" in r.json()["detail"]


def test_widget_derived_dim_accepted(client, chart_id, dash):
    """country_tier is backend-derived (from country) — valid even though the chart
    doesn't declare it as a dimension."""
    tab_id = main_tab_id(client, dash["id"])
    r = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets",
        json=chart_widget_body(chart_id, group_by=["country_tier"]),
    )
    assert r.status_code == 201, r.text


def test_widget_config_shape_422s(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    did = dash["id"]

    # >5 metrics
    body = chart_widget_body(chart_id)
    body["config"]["metrics"] = [{"name": f"m{i}"} for i in range(6)]
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422

    # duplicate metrics
    body = chart_widget_body(chart_id)
    body["config"]["metrics"] = [{"name": "revenue"}, {"name": "revenue"}]
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422

    # >5 group_by
    body = chart_widget_body(chart_id, group_by=[f"d{i}" for i in range(6)])
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422

    # number widget without a metric
    body = number_widget_body(chart_id)
    del body["config"]["metric"]
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422

    # bad viz value
    body = chart_widget_body(chart_id, viz="pie")
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422

    # unknown widget type
    body = chart_widget_body(chart_id)
    body["type"] = "table"
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422


def test_widget_layout_validation_422(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    did = dash["id"]

    body = chart_widget_body(chart_id)
    body["layout"] = {"x": 0, "y": 0, "w": 0, "h": 4}  # zero width
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422

    body = chart_widget_body(chart_id)
    body["layout"] = {"x": 8, "y": 0, "w": 6, "h": 4}  # x + w > 12
    assert client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=body).status_code == 422


# ---------- widgets: update / delete ----------

def test_update_widget_name_and_layout(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    w = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id)
    ).json()

    r = client.put(
        f"/dashboards/{dash['id']}/widgets/{w['id']}",
        json={"name": "Renamed Widget", "layout": {"x": 6, "y": 2, "w": 3, "h": 2}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed Widget"
    assert body["layout"] == {"x": 6, "y": 2, "w": 3, "h": 2}
    # config untouched
    assert body["config"]["metrics"] == [{"name": "revenue", "y_axis": "primary"}]


def test_update_widget_config_revalidated(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    w = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id)
    ).json()

    # valid change
    r = client.put(
        f"/dashboards/{dash['id']}/widgets/{w['id']}",
        json={"config": {"metrics": [{"name": "dau", "y_axis": "secondary"}], "viz": "bar"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["config"]["viz"] == "bar"

    # unknown metric -> 400
    r = client.put(
        f"/dashboards/{dash['id']}/widgets/{w['id']}",
        json={"config": {"metrics": [{"name": "nope"}]}},
    )
    assert r.status_code == 400

    # config not matching the widget's (immutable) type -> 422
    r = client.put(
        f"/dashboards/{dash['id']}/widgets/{w['id']}",
        json={"config": {"metric": "dau"}},
    )
    assert r.status_code == 422


def test_update_widget_source_chart_revalidates(client, chart_id, dash):
    other_chart = make_chart(client, name="other-src", dims=("country",), metrics=("installs",))
    tab_id = main_tab_id(client, dash["id"])
    w = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id)
    ).json()

    # moving to a chart that lacks 'revenue' -> 400
    r = client.put(
        f"/dashboards/{dash['id']}/widgets/{w['id']}", json={"source_chart_id": other_chart}
    )
    assert r.status_code == 400

    # moving with a compatible config in the same call -> 200
    r = client.put(
        f"/dashboards/{dash['id']}/widgets/{w['id']}",
        json={"source_chart_id": other_chart, "config": {"metrics": [{"name": "installs"}]}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["source_chart_id"] == other_chart


def test_delete_widget(client, chart_id, dash):
    tab_id = main_tab_id(client, dash["id"])
    w = client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id)
    ).json()
    assert client.delete(f"/dashboards/{dash['id']}/widgets/{w['id']}").status_code == 204
    tree = client.get(f"/dashboards/{dash['id']}").json()
    assert tree["tabs"][0]["widgets"] == []
    assert client.delete(f"/dashboards/{dash['id']}/widgets/{w['id']}").status_code == 404


def test_delete_source_chart_deletes_widgets(client, db_session, dash):
    """A widget is meaningless without its source chart — chart deletion removes it
    (but never the dashboard or its tabs)."""
    doomed_chart = make_chart(client, name="doomed")
    tab_id = main_tab_id(client, dash["id"])
    client.post(
        f"/dashboards/{dash['id']}/tabs/{tab_id}/widgets", json=chart_widget_body(doomed_chart)
    )
    assert db_session.query(Widget).count() == 1

    assert client.delete(f"/charts/{doomed_chart}").status_code == 204
    assert db_session.query(Widget).count() == 0
    tree = client.get(f"/dashboards/{dash['id']}").json()
    assert tree["tabs"][0]["widgets"] == []


# ---------- bulk layout ----------

def test_bulk_layout_save(client, chart_id, dash):
    did = dash["id"]
    tab_id = main_tab_id(client, did)
    w1 = client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id)).json()
    w2 = client.post(
        f"/dashboards/{did}/tabs/{tab_id}/widgets", json=number_widget_body(chart_id)
    ).json()

    r = client.put(
        f"/dashboards/{did}/tabs/{tab_id}/layout",
        json=[
            {"widget_id": w1["id"], "x": 0, "y": 0, "w": 8, "h": 5},
            {"widget_id": w2["id"], "x": 8, "y": 0, "w": 4, "h": 3},
        ],
    )
    assert r.status_code == 200, r.text
    by_id = {w["id"]: w["layout"] for w in r.json()["widgets"]}
    assert by_id[w1["id"]] == {"x": 0, "y": 0, "w": 8, "h": 5}
    assert by_id[w2["id"]] == {"x": 8, "y": 0, "w": 4, "h": 3}


def test_bulk_layout_rejects_foreign_widget(client, chart_id, dash):
    did = dash["id"]
    tab_id = main_tab_id(client, did)
    other_tab = client.post(f"/dashboards/{did}/tabs", json={"name": "T2"}).json()
    w_other = client.post(
        f"/dashboards/{did}/tabs/{other_tab['id']}/widgets", json=chart_widget_body(chart_id)
    ).json()

    r = client.put(
        f"/dashboards/{did}/tabs/{tab_id}/layout",
        json=[{"widget_id": w_other["id"], "x": 0, "y": 0, "w": 2, "h": 2}],
    )
    assert r.status_code == 400


# ---------- global filters ----------

def test_replace_filters_atomic(client, dash):
    did = dash["id"]
    r = client.put(
        f"/dashboards/{did}/filters",
        json={
            "filters": [
                {"dimension": "gid", "default_values": []},
                {"dimension": "country", "default_values": ["US", "GB"]},
            ]
        },
    )
    assert r.status_code == 200, r.text
    filters = r.json()["filters"]
    assert [f["dimension"] for f in filters] == ["gid", "country"]
    assert filters[0]["display_order"] == 0
    assert filters[1]["default_values"] == ["US", "GB"]

    # replacing with a shorter list drops the old rows (atomic replace)
    r = client.put(
        f"/dashboards/{did}/filters",
        json={"filters": [{"dimension": "platform", "default_values": [1, 2]}]},
    )
    filters = r.json()["filters"]
    assert [f["dimension"] for f in filters] == ["platform"]
    assert filters[0]["default_values"] == [1, 2]  # non-string values preserved


def test_replace_filters_duplicate_dimension_422(client, dash):
    r = client.put(
        f"/dashboards/{dash['id']}/filters",
        json={"filters": [{"dimension": "gid"}, {"dimension": "gid"}]},
    )
    assert r.status_code == 422


# ---------- replicate ----------

def test_replicate_deep_copies(client, chart_id, dash):
    did = dash["id"]
    tab_id = main_tab_id(client, did)
    client.post(f"/dashboards/{did}/tabs", json={"name": "Deepdive"})
    client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=chart_widget_body(chart_id))
    client.post(f"/dashboards/{did}/tabs/{tab_id}/widgets", json=number_widget_body(chart_id))
    client.put(
        f"/dashboards/{did}/filters",
        json={"filters": [{"dimension": "country", "default_values": ["US"]}]},
    )

    r = client.post(f"/dashboards/{did}/replicate")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["id"] != did
    assert copy["name"] == "My Dashboard (copy)"
    assert copy["number"] == 101
    assert [t["name"] for t in copy["tabs"]] == ["Main", "Deepdive"]
    assert len(copy["tabs"][0]["widgets"]) == 2
    assert [f["dimension"] for f in copy["filters"]] == ["country"]

    orig_w = client.get(f"/dashboards/{did}").json()["tabs"][0]["widgets"][0]
    copy_w = copy["tabs"][0]["widgets"][0]
    assert copy_w["id"] != orig_w["id"]
    assert copy_w["config"] == orig_w["config"]
    assert copy_w["layout"] == orig_w["layout"]
    assert copy_w["source_chart_id"] == chart_id  # same source chart, not copied

    # replicate again -> " (copy) 2"
    r = client.post(f"/dashboards/{did}/replicate")
    assert r.json()["name"] == "My Dashboard (copy) 2"
