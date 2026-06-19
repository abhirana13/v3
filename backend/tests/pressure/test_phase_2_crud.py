"""Phase 2 pressure: chart CRUD boundaries.

(Happy-path CRUD + duplicate-name + cascade delete are in tests/test_charts.py.)"""


def test_name_at_length_boundary(client):
    assert client.post("/charts", json={"name": "x" * 255, "query": "SELECT 1"}).status_code == 201
    assert client.post("/charts", json={"name": "y" * 256, "query": "SELECT 1"}).status_code == 422


def test_unicode_name_accepted(client):
    r = client.post("/charts", json={"name": "日本語 chart ✓ café", "query": "SELECT 1"})
    assert r.status_code == 201
    assert r.json()["name"] == "日本語 chart ✓ café"


def test_create_delete_recreate_same_name(client):
    r = client.post("/charts", json={"name": "reuse-me", "query": "SELECT 1"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert client.delete(f"/charts/{cid}").status_code == 204
    # name is free again after delete
    assert client.post("/charts", json={"name": "reuse-me", "query": "SELECT 1"}).status_code == 201


def test_partial_update_preserves_untouched_fields_and_flips_strategy(client):
    r = client.post(
        "/charts",
        json={"name": "flip", "query": "SELECT 1", "cache_strategy": "append",
              "cur_date_behavior": "batched", "default_backpop_days": 7},
    )
    cid = r.json()["id"]
    r = client.put(f"/charts/{cid}", json={"cache_strategy": "replace"})
    assert r.status_code == 200
    body = r.json()
    assert body["cache_strategy"] == "replace"      # changed
    assert body["cur_date_behavior"] == "batched"   # untouched
    assert body["default_backpop_days"] == 7        # untouched
    assert body["query"] == "SELECT 1"              # untouched


def test_invalid_schedule_fields_rejected(client):
    for bad in ({"backpop_batch_size": 0}, {"default_backpop_days": -1}, {"default_date_range_days": 0}):
        payload = {"name": f"bad-{list(bad)[0]}", "query": "SELECT 1", **bad}
        assert client.post("/charts", json=payload).status_code == 422
