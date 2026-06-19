"""Reusable harness helpers: configure a chart via the real API and load rows
into its DuckDB table via the real writer."""

from datetime import date

from app.backpop.duckdb_writer import write_batch
from app.templating import DateBatch


def configure_chart(client, config, name="golden-pressure"):
    r = client.post(
        "/charts",
        json={"name": name, "query": "SELECT 1", "time_column": config.time_column},
    )
    assert r.status_code == 201, r.text
    chart_id = r.json()["id"]

    metrics_payload = [
        {"name": m.name, "column_name": m.column_name, "independent_dimensions": list(m.independent)}
        for m in config.base_metrics
    ] + [
        {"name": f.name, "formula": f.formula, "decimals": f.decimals}
        for f in config.formulas
    ]
    r = client.put(
        f"/charts/{chart_id}/dims-metrics",
        json={
            "time_column": config.time_column,
            "dimensions": [{"name": d, "column_name": d} for d in config.dims],
            "metrics": metrics_payload,
        },
    )
    assert r.status_code == 200, r.text
    return chart_id


def load_rows(chart_id, columns, rows, time_column):
    dates = [r[columns.index(time_column)] for r in rows]
    start, end = (min(dates), max(dates)) if dates else (date(2026, 1, 1), date(2026, 1, 1))
    write_batch(chart_id, columns, rows, DateBatch(start_date=start, end_date=end), "append", time_column)
