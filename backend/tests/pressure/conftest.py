"""Fixtures for the pressure harness.

Each test gets a fresh tmp DuckDB (monkeypatched path) with the golden dataset
loaded through the REAL writer (app.backpop.duckdb_writer.write_batch) and a
chart configured through the REAL API (PUT /charts/{id}/dims-metrics). Nothing
touches the real cache.
"""

import pytest

from . import golden
from .helpers import configure_chart, load_rows


@pytest.fixture
def pressure_duckdb(tmp_path, monkeypatch):
    path = str(tmp_path / "pressure.duckdb")
    monkeypatch.setattr("app.connections.duckdb.settings.duckdb_path", path)
    return path


@pytest.fixture
def golden_chart(client, pressure_duckdb):
    """(chart_id, columns, rows, config) with golden loaded into a tmp DuckDB."""
    columns, rows = golden.generate()
    config = golden.CONFIG
    chart_id = configure_chart(client, config)
    load_rows(chart_id, columns, rows, config.time_column)
    return chart_id, columns, rows, config
