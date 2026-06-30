from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.backpop import router as backpop_router
from app.api.charts import router as charts_router
from app.api.data import router as data_router
from app.api.dims_metrics import router as dims_metrics_router
from app.connections import duckdb as duckdb_conn
from app.connections import postgres as postgres_conn
from app.connections import redshift as redshift_conn
from app.db import ensure_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema(postgres_conn.engine)
    duckdb_conn.ensure_database()  # so read_only serving connections can always open it
    yield


app = FastAPI(title="Analytics Dashboard API", version="0.1.0", lifespan=lifespan)
app.include_router(charts_router)
app.include_router(dims_metrics_router)
app.include_router(backpop_router)
app.include_router(data_router)


@app.get("/health")
def health():
    checks = {
        "redshift": redshift_conn.check(),
        "duckdb": duckdb_conn.check(),
        "postgres": postgres_conn.check(),
    }
    all_ok = all(c["status"] == "ok" for c in checks.values())
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
