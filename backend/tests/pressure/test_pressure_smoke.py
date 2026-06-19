"""Master reconciliation: every matrix combination must equal the oracle.

Also self-checks the golden dataset's baked-in independence (so the dedup
oracle's MAX-representative assumption rests on a verified property of the data,
not on faith)."""

from collections import defaultdict

from . import golden
from .reconcile import matrix_cases, reconcile_case


def test_golden_independence_is_real():
    """`dau` must truly be constant across source+source_category for a fixed
    (date,gid,country,platform); `installs` constant across every dim per day."""
    columns, rows = golden.generate()
    idx = {c: i for i, c in enumerate(columns)}

    dau_groups = defaultdict(set)
    installs_by_day = defaultdict(set)
    for r in rows:
        k = (r[idx["event_date"]], r[idx["gid"]], r[idx["country"]], r[idx["platform"]])
        dau_groups[k].add(r[idx["dau"]])
        installs_by_day[r[idx["event_date"]]].add(r[idx["installs"]])

    assert all(len(v) == 1 for v in dau_groups.values()), "dau varies across source — independence broken"
    assert all(len(v) == 1 for v in installs_by_day.values()), "installs varies within a day"


def test_golden_is_deterministic():
    a = golden.generate()
    b = golden.generate()
    assert a == b


def test_full_matrix_reconciles(golden_chart, client):
    chart_id, columns, rows, config = golden_chart
    cases = matrix_cases()
    assert len(cases) > 200  # guard against an accidentally-empty matrix
    for case in cases:
        reconcile_case(client, chart_id, columns, rows, config, case)
