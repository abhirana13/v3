from datetime import date
from unittest.mock import MagicMock, patch

from app.backpop.scheduler import nightly_backpop_for_yesterday
from app.models import Chart


def _make_chart(id_, name, refresh="daily", default_backpop_days=7):
    c = MagicMock(spec=Chart)
    c.id = id_
    c.name = name
    c.refresh_interval = refresh
    c.default_backpop_days = default_backpop_days
    return c


def test_nightly_uses_lookback_window_per_chart():
    daily7 = _make_chart(1, "a", default_backpop_days=7)
    daily30 = _make_chart(2, "b", default_backpop_days=30)

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [daily7, daily30]

    calls = []

    def fake_run(db_arg, chart_id, from_date=None, to_date=None, **kw):
        calls.append({"chart_id": chart_id, "from": from_date, "to": to_date})
        run = MagicMock()
        run.status = "success"
        run.row_count = 42
        run.batches_completed = 1
        run.error_message = None
        return run

    with patch("app.backpop.scheduler.SessionLocal", return_value=db), \
         patch("app.backpop.scheduler.run_backpop", side_effect=fake_run):
        summary = nightly_backpop_for_yesterday(today=date(2026, 6, 18))

    by_id = {c["chart_id"]: c for c in calls}
    assert by_id[1]["from"] == date(2026, 6, 11)  # 7 days back from today
    assert by_id[1]["to"] == date(2026, 6, 17)    # yesterday
    assert by_id[2]["from"] == date(2026, 5, 19)  # 30 days back from today
    assert by_id[2]["to"] == date(2026, 6, 17)
    assert summary["date"] == "2026-06-17"


def test_nightly_continues_when_one_chart_errors():
    c1 = _make_chart(1, "good")
    c2 = _make_chart(2, "bad")
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [c1, c2]

    def fake_run(db_arg, chart_id, **kw):
        if chart_id == 2:
            raise RuntimeError("boom")
        run = MagicMock()
        run.status = "success"
        run.row_count = 10
        run.error_message = None
        return run

    with patch("app.backpop.scheduler.SessionLocal", return_value=db), \
         patch("app.backpop.scheduler.run_backpop", side_effect=fake_run):
        summary = nightly_backpop_for_yesterday(today=date(2026, 6, 18))

    by_id = {c["chart_id"]: c for c in summary["charts"]}
    assert by_id[1]["status"] == "success"
    assert by_id[2]["status"] == "error"
    assert "boom" in by_id[2]["error"]
