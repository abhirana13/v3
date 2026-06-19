"""Ad-hoc serving-at-scale benchmark (not part of the test suite).

Synthesizes N rows directly in DuckDB (fast bulk insert, NOT the row-by-row
writer) then times the REAL serve_data hot path for several request shapes.
Run: docker compose exec -T backend python scale_bench.py 5000000
"""
import sys
import time

import duckdb

from app.connections import duckdb as duckdb_conn
from app.models import Chart, Dimension, Metric
from app.schemas import DataRequest
from app.serving import serve_data

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
CHART_ID = 99999
TABLE = f"chart_{CHART_ID}_data"
PATH = f"/tmp/scale_{N}.duckdb"
duckdb_conn.settings.duckdb_path = PATH

con = duckdb.connect(PATH)
con.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
t0 = time.perf_counter()
con.execute(f"""
CREATE TABLE "{TABLE}" AS
SELECT
  DATE '2026-01-01' + CAST(i % 180 AS INTEGER) AS event_date,
  'g' || (i % 8)              AS gid,
  'c' || (i % 6)              AS country,
  's' || (i % 5)              AS source,
  'sc' || (i % 2)             AS source_category,
  CASE WHEN i % 2 = 0 THEN 'ios' ELSE 'android' END AS platform,
  (random() * 5)::DOUBLE      AS revenue,
  (i % 20) + 1                AS sessions,
  (i % 90) + 10               AS dau,
  (i % 50) * 10 + 500         AS installs,
  CASE WHEN i % 7 = 0 THEN NULL ELSE i % 5 END AS crashes
FROM range({N}) t(i)
""")
build = time.perf_counter() - t0
rowcount = con.execute(f'SELECT COUNT(*) FROM "{TABLE}"').fetchone()[0]
con.close()
print(f"rows={rowcount:,}  bulk_build={build:.2f}s  path={PATH}")

DIMS = ["gid", "country", "source", "source_category", "platform"]
chart = Chart(id=CHART_ID, name="bench", query="x", time_column="event_date", cache_strategy="append")
chart.dimensions = [Dimension(name=d, column_name=d) for d in DIMS]
chart.metrics = [
    Metric(name="revenue", column_name="revenue", independent_dimensions=[]),
    Metric(name="sessions", column_name="sessions", independent_dimensions=[]),
    Metric(name="crashes", column_name="crashes", independent_dimensions=[]),
    Metric(name="dau", column_name="dau", independent_dimensions=["source", "source_category"]),
    Metric(name="installs", column_name="installs", independent_dimensions=DIMS),
    Metric(name="arpu", column_name=None, formula="revenue / dau", decimals=4),
]

shapes = [
    ("no group, all metrics, day", DataRequest(granularity="day", dimensions=[], metrics=None, filters={})),
    ("group by source, all metrics, day", DataRequest(granularity="day", dimensions=["source"], metrics=None, filters={})),
    ("group by 2 dims, all metrics, day", DataRequest(granularity="day", dimensions=["country", "platform"], metrics=None, filters={})),
    ("group by all 5 dims, all metrics, day", DataRequest(granularity="day", dimensions=DIMS, metrics=None, filters={})),
    ("no group, all metrics, WEEK", DataRequest(granularity="week", dimensions=[], metrics=None, filters={})),
    ("group by source, arpu only (formula), day", DataRequest(granularity="day", dimensions=["source"], metrics=["arpu"], filters={})),
]
for label, req in shapes:
    # warm + measure (median of 3)
    times = []
    out = None
    for _ in range(3):
        t = time.perf_counter()
        out = serve_data(chart, req)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    print(f"  {times[1]:8.1f} ms   ({out['row_count']:>5} rows out)   {label}")
