"""Phase 3 pressure: OID classification matrix + config-driven new-column.

(CTE preservation, LIMIT/semicolon stripping, template substitution, and error
propagation are in tests/test_introspection.py.)"""

from unittest.mock import MagicMock, patch

import pytest

from app.introspection import classify_columns, introspect_query


def _col(name, oid):
    return (name, oid, None, None, None, None, None)


# Every OID the classifier knows about, with its expected role.
NUMERIC_OIDS = [20, 21, 23, 700, 701, 1700, 790]
STRING_OIDS = [25, 1042, 1043]
TIME_OIDS = [1082, 1114, 1184, 1083, 1266]
BOOL_OIDS = [16]


@pytest.mark.parametrize("oid", NUMERIC_OIDS)
def test_numeric_oids_become_metrics(oid):
    r = classify_columns([_col("event_date", 1082), _col("m", oid)])
    assert [m.name for m in r.metrics] == ["m"]
    assert [d.name for d in r.dimensions] == []


@pytest.mark.parametrize("oid", STRING_OIDS + BOOL_OIDS)
def test_string_and_bool_oids_become_dimensions(oid):
    r = classify_columns([_col("event_date", 1082), _col("d", oid)])
    assert [d.name for d in r.dimensions] == ["d"]
    assert [m.name for m in r.metrics] == []


@pytest.mark.parametrize("oid", TIME_OIDS)
def test_first_time_oid_is_time_column_second_is_dim(oid):
    r = classify_columns([_col("t1", oid), _col("t2", oid), _col("n", 23)])
    assert r.time_column == "t1"
    assert [d.name for d in r.dimensions] == ["t2"]
    assert [m.name for m in r.metrics] == ["n"]


def _ctx(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_adding_a_column_surfaces_a_new_dimension_with_zero_code_change():
    """Config-driven guarantee: re-introspecting a query that gained a column
    must surface the new column as a dimension automatically."""
    before = MagicMock()
    before.description = [_col("event_date", 1082), _col("country", 1043), _col("dau", 20)]
    after = MagicMock()
    after.description = [_col("event_date", 1082), _col("country", 1043),
                         _col("app_version", 1043), _col("dau", 20)]  # new column

    with patch("app.introspection.redshift_conn.connect", return_value=_ctx(before)):
        r1 = introspect_query("SELECT event_date, country, dau FROM t")
    with patch("app.introspection.redshift_conn.connect", return_value=_ctx(after)):
        r2 = introspect_query("SELECT event_date, country, app_version, dau FROM t")

    assert [d.name for d in r1.dimensions] == ["country"]
    assert [d.name for d in r2.dimensions] == ["country", "app_version"]  # new dim, no code change
    assert [m.name for m in r2.metrics] == ["dau"]
