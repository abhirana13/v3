from unittest.mock import MagicMock, patch

from app.introspection import (
    IntrospectionError,
    classify_columns,
    introspect_query,
)


def _col(name, type_code):
    return (name, type_code, None, None, None, None, None)


def test_classify_columns_exposes_friendly_data_types():
    desc = [
        _col("event_date", 1082),  # date
        _col("source", 1043),      # varchar
        _col("dau", 20),           # bigint
        _col("revenue", 701),      # double
    ]
    r = classify_columns(desc)
    by_name = {d.name: d.data_type for d in r.dimensions}
    assert by_name["source"] == "varchar"
    mtypes = {m.name: m.data_type for m in r.metrics}
    assert mtypes == {"dau": "bigint", "revenue": "double"}


def test_classify_columns_basic_mix():
    desc = [
        _col("date", 1082),     # date -> time_column
        _col("source", 1043),   # varchar -> dim
        _col("country", 25),    # text -> dim
        _col("dau", 20),        # int8 -> metric
        _col("revenue", 1700),  # numeric -> metric
    ]
    r = classify_columns(desc)
    assert r.time_column == "date"
    assert [d.name for d in r.dimensions] == ["source", "country"]
    assert [m.name for m in r.metrics] == ["dau", "revenue"]


def test_classify_columns_second_time_becomes_dim():
    desc = [
        _col("event_date", 1082),    # time_column
        _col("created_at", 1184),    # 2nd date -> dim
        _col("dau", 23),
    ]
    r = classify_columns(desc)
    assert r.time_column == "event_date"
    assert [d.name for d in r.dimensions] == ["created_at"]
    assert [m.name for m in r.metrics] == ["dau"]


def test_classify_columns_no_time_column():
    desc = [_col("name", 1043), _col("count", 23)]
    r = classify_columns(desc)
    assert r.time_column is None
    assert len(r.dimensions) == 1
    assert len(r.metrics) == 1


def test_classify_columns_bool_treated_as_dim():
    desc = [_col("is_paying", 16), _col("dau", 20)]
    r = classify_columns(desc)
    assert [d.name for d in r.dimensions] == ["is_paying"]
    assert [m.name for m in r.metrics] == ["dau"]


def _fake_ctx(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def test_introspect_query_appends_limit_zero_without_wrapping():
    cursor = MagicMock()
    cursor.description = [_col("d", 1082), _col("n", 23)]

    with patch(
        "app.introspection.redshift_conn.connect", return_value=_fake_ctx(cursor)
    ):
        result = introspect_query("SELECT d, n FROM tbl WHERE d > '2026-01-01'")

    sql = cursor.execute.call_args[0][0]
    assert sql.rstrip().endswith("LIMIT 0")
    assert "_intro" not in sql  # no subquery wrap
    assert result.time_column == "d"
    assert [m.name for m in result.metrics] == ["n"]


def test_introspect_query_strips_trailing_semicolon():
    cursor = MagicMock()
    cursor.description = []
    with patch(
        "app.introspection.redshift_conn.connect", return_value=_fake_ctx(cursor)
    ):
        introspect_query("SELECT 1;")
    sql = cursor.execute.call_args[0][0]
    assert ";" not in sql
    assert sql.rstrip().endswith("LIMIT 0")


def test_introspect_query_strips_trailing_limit_clause():
    cursor = MagicMock()
    cursor.description = []
    with patch(
        "app.introspection.redshift_conn.connect", return_value=_fake_ctx(cursor)
    ):
        introspect_query("SELECT * FROM t LIMIT 100")
    sql = cursor.execute.call_args[0][0]
    assert sql.upper().count("LIMIT") == 1
    assert sql.rstrip().endswith("LIMIT 0")


def test_introspect_query_preserves_top_level_with_clause():
    cursor = MagicMock()
    cursor.description = [_col("d", 1082)]
    with patch(
        "app.introspection.redshift_conn.connect", return_value=_fake_ctx(cursor)
    ):
        introspect_query(
            "WITH cte AS (SELECT 1 AS d) SELECT d FROM cte ORDER BY d;"
        )
    sql = cursor.execute.call_args[0][0]
    assert sql.lstrip().upper().startswith("WITH")
    assert "ORDER BY d" in sql
    assert sql.rstrip().endswith("LIMIT 0")


def test_introspect_substitutes_template_variables_before_sending():
    from datetime import date as _date

    cursor = MagicMock()
    cursor.description = [_col("d", 1082)]
    with patch(
        "app.introspection.redshift_conn.connect", return_value=_fake_ctx(cursor)
    ):
        introspect_query(
            "SELECT d FROM t WHERE d = DATE '{CUR_DATE_HIPHEN}' AND c IN ({CTRY})",
            static_vars={"CTRY": ["US", "UK"]},
            sample_date=_date(2026, 6, 15),
        )
    sql = cursor.execute.call_args[0][0]
    assert "2026-06-15" in sql
    assert "'US', 'UK'" in sql
    assert "{" not in sql  # all tokens substituted


def test_introspect_unresolved_template_var_returns_clean_error():
    from app.introspection import introspect_query, IntrospectionError

    try:
        introspect_query("SELECT * FROM t WHERE x = {UNKNOWN_VAR}")
    except IntrospectionError as e:
        assert "UNKNOWN_VAR" in str(e)
    else:
        raise AssertionError("expected IntrospectionError")


def test_introspect_query_propagates_db_error():
    ctx = MagicMock()
    ctx.__enter__.side_effect = RuntimeError("connection refused")
    ctx.__exit__.return_value = False

    with patch("app.introspection.redshift_conn.connect", return_value=ctx):
        try:
            introspect_query("SELECT 1")
        except IntrospectionError as e:
            assert "RuntimeError" in str(e)
            assert "connection refused" in str(e)
        else:
            raise AssertionError("expected IntrospectionError")
