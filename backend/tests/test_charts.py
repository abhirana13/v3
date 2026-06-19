def _payload(**overrides):
    base = {
        "name": "DAU by source",
        "query": "SELECT date, source, dau FROM events WHERE date = '{CUR_DATE_HIPHEN}'",
    }
    base.update(overrides)
    return base


def test_create_chart_defaults_applied(client):
    r = client.post("/charts", json=_payload())
    assert r.status_code == 201
    body = r.json()
    assert body["id"] > 0
    assert body["name"] == "DAU by source"
    assert body["source"] == "redshift"
    assert body["refresh_interval"] == "daily"
    assert body["default_backpop_days"] == 7
    assert body["backpop_batch_size"] == 30
    assert body["default_date_range_days"] == 90
    assert body["cur_date_behavior"] == "daily"
    assert body["cache_strategy"] == "append"
    assert body["date_format"] == "%Y-%m-%d"
    assert body["time_column"] is None
    assert body["certified"] is False
    assert body["chart_number"] == 1000  # uncertified series starts at 1000
    assert "created_at" in body and "updated_at" in body


def test_chart_number_series_by_certification(client):
    a = client.post("/charts", json=_payload(name="a")).json()
    b = client.post("/charts", json=_payload(name="b")).json()
    assert a["chart_number"] == 1000  # uncertified -> 1000+
    assert b["chart_number"] == 1001
    c = client.post("/charts", json=_payload(name="c", certified=True)).json()
    assert c["certified"] is True
    assert c["chart_number"] == 100  # certified -> 100+


def test_certifying_moves_chart_between_series(client):
    a = client.post("/charts", json=_payload(name="a")).json()
    assert a["chart_number"] == 1000

    up = client.put(f"/charts/{a['id']}", json={"certified": True}).json()
    assert up["certified"] is True
    assert 100 <= up["chart_number"] < 1000  # moved into the certified series

    down = client.put(f"/charts/{a['id']}", json={"certified": False}).json()
    assert down["certified"] is False
    assert down["chart_number"] >= 1000  # moved back out


def test_charts_overview_lists_charts_with_freshness(client):
    a = client.post("/charts", json=_payload(name="a")).json()
    client.post("/charts", json=_payload(name="b", certified=True))
    r = client.get("/charts/overview")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    by_name = {row["name"]: row for row in rows}
    assert by_name["a"]["chart_number"] == a["chart_number"]
    assert by_name["b"]["certified"] is True
    # no backpop yet -> empty freshness, not running
    assert by_name["a"]["latest_data_date"] is None
    assert by_name["a"]["last_backpop_status"] is None
    assert by_name["a"]["running"] is False


def test_overview_route_not_shadowed_by_chart_id(client):
    """`/charts/overview` must resolve to the list, not be parsed as chart id."""
    r = client.get("/charts/overview")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_update_without_cert_change_keeps_number(client):
    a = client.post("/charts", json=_payload(name="a")).json()
    n = a["chart_number"]
    up = client.put(f"/charts/{a['id']}", json={"name": "renamed"}).json()
    assert up["chart_number"] == n  # unrelated edits don't churn the number


def test_create_chart_custom_schedule(client):
    r = client.post(
        "/charts",
        json=_payload(
            name="Revenue daily",
            default_backpop_days=30,
            backpop_batch_size=7,
            cache_strategy="replace",
        ),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["default_backpop_days"] == 30
    assert body["backpop_batch_size"] == 7
    assert body["cache_strategy"] == "replace"


def test_create_chart_missing_required_query(client):
    r = client.post("/charts", json={"name": "no-query"})
    assert r.status_code == 422


def test_create_chart_empty_name_rejected(client):
    r = client.post("/charts", json={"name": "", "query": "SELECT 1"})
    assert r.status_code == 422


def test_create_chart_zero_batch_size_rejected(client):
    r = client.post("/charts", json=_payload(backpop_batch_size=0))
    assert r.status_code == 422


def test_create_chart_duplicate_name_conflicts(client):
    client.post("/charts", json=_payload(name="dup"))
    r = client.post("/charts", json=_payload(name="dup"))
    assert r.status_code == 409


def test_get_chart(client):
    created = client.post("/charts", json=_payload(name="A")).json()
    r = client.get(f"/charts/{created['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "A"


def test_get_chart_not_found(client):
    r = client.get("/charts/99999")
    assert r.status_code == 404


def test_list_charts_ordered_by_id(client):
    client.post("/charts", json=_payload(name="A"))
    client.post("/charts", json=_payload(name="B"))
    r = client.get("/charts")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert names == ["A", "B"]


def test_list_charts_empty(client):
    r = client.get("/charts")
    assert r.status_code == 200
    assert r.json() == []


def test_update_chart_partial(client):
    created = client.post("/charts", json=_payload(name="A")).json()
    r = client.put(f"/charts/{created['id']}", json={"backpop_batch_size": 11})
    assert r.status_code == 200
    body = r.json()
    assert body["backpop_batch_size"] == 11
    assert body["name"] == "A"
    assert body["default_backpop_days"] == 7


def test_update_chart_not_found(client):
    r = client.put("/charts/99999", json={"name": "X"})
    assert r.status_code == 404


def test_delete_chart(client):
    created = client.post("/charts", json=_payload(name="A")).json()
    r = client.delete(f"/charts/{created['id']}")
    assert r.status_code == 204
    r = client.get(f"/charts/{created['id']}")
    assert r.status_code == 404


def test_delete_chart_not_found(client):
    r = client.delete("/charts/99999")
    assert r.status_code == 404
