import pytest

from crbench import pipeline
from crbench.pipeline import Metric, Report

M = Metric("m", "Test metric", "usd", "test", ["x"])
N = Metric("n", "Other metric", "usd", "test", ["x"])
MARGIN = Metric("margin", "Margin", "ratio", "test", ["x"], valid_range=(-1.0, 1.0))


def rep(rec, ccn="1", days=365, year=2023, exclusion=None, **values):
    return Report(year=year, ccn=ccn, rec=rec, period_days=days, exclusion=exclusion, values=dict(values))


def test_period_filter_keeps_300_to_430_days():
    rows = [rep(i, ccn=str(i), days=d) for i, d in enumerate([299, 300, 365, 430, 431, None])]
    kept, counts = pipeline.clean(rows, [M])
    assert sorted(r.period_days for r in kept[2023]) == [300, 365, 430]
    assert counts[2023]["excluded_partial_year"] == 3  # 299, 431 and unreadable dates
    assert counts[2023]["kept"] == 3


def test_dedupe_keeps_longest_period_then_highest_rec():
    rows = [
        rep(1, ccn="A", days=350),
        rep(2, ccn="A", days=365),  # longest: kept
        rep(3, ccn="B", days=365),
        rep(4, ccn="B", days=365),  # tie: highest rec kept
        rep(5, ccn="C", days=365),
    ]
    kept, counts = pipeline.clean(rows, [M])
    assert {(r.ccn, r.rec) for r in kept[2023]} == {("A", 2), ("B", 4), ("C", 5)}
    assert counts[2023]["dropped_duplicates"] == 2
    assert counts[2023]["raw"] == 5
    assert counts[2023]["raw_rows_sharing_a_ccn"] == 2


def test_dedupe_is_per_year():
    rows = [rep(1, ccn="A", year=2022), rep(2, ccn="A", year=2023)]
    kept, _ = pipeline.clean(rows, [M])
    assert len(kept[2022]) == 1 and len(kept[2023]) == 1


def test_no_data_rows_are_dropped_and_counted_by_reason():
    rows = [
        rep(1, ccn="A"),
        rep(2, ccn="B", exclusion=pipeline.NO_COST_OR_DAYS),
        rep(3, ccn="C", exclusion=pipeline.NO_FINANCIAL_DATA),
        rep(4, ccn="D", exclusion=pipeline.NO_FINANCIAL_DATA),
    ]
    kept, counts = pipeline.clean(rows, [M])
    c = counts[2023]
    assert (c["raw"], c["kept"]) == (4, 1)
    assert c["excluded_no_cost_or_days"] == 1
    assert c["excluded_no_financial_data"] == 2


def test_outlier_screen_removes_1st_and_99th_percentile_tails():
    rows = [rep(i, ccn=str(i), m=float(i)) for i in range(1, 201)]
    kept, counts = pipeline.clean(rows, [M])
    values = sorted(r.values["m"] for r in kept[2023] if r.values.get("m") is not None)
    assert values[0] == 3.0 and values[-1] == 198.0  # 1, 2, 199, 200 excluded
    info = counts[2023]["metric_screening"]["m"]
    assert info["outlier_excluded"] == 4
    assert info["values"] == 200


def test_outlier_screen_applies_to_that_metric_only():
    rows = [rep(i, ccn=str(i), m=float(i), n=5.0) for i in range(1, 201)]
    kept, _ = pipeline.clean(rows, [M, N])
    worst = [r for r in kept[2023] if r.values["m"] is None]
    assert len(worst) == 4
    assert all(r.values["n"] == 5.0 for r in worst)  # the other metric is untouched


def test_margin_guard_excludes_beyond_100_percent():
    rows = [
        rep(1, ccn="a", margin=-1.0),
        rep(2, ccn="b", margin=1.0),
        rep(3, ccn="c", margin=-1.01),
        rep(4, ccn="d", margin=1.5),
        rep(5, ccn="e", margin=0.1),
    ]
    kept, counts = pipeline.clean(rows, [MARGIN])
    got = sorted(r.values["margin"] for r in kept[2023] if r.values["margin"] is not None)
    assert got == [-1.0, 0.1, 1.0]
    assert counts[2023]["metric_screening"]["margin"]["range_excluded"] == 2


def test_completeness_flag_uses_90_percent_of_prior_year():
    rows = [rep(i, ccn=f"a{i}", year=2023) for i in range(100)]
    rows += [rep(1000 + i, ccn=f"b{i}", year=2024) for i in range(90)]  # exactly 90%: complete
    rows += [rep(2000 + i, ccn=f"c{i}", year=2025) for i in range(80)]  # 88.9% of 2024: incomplete
    _, counts = pipeline.clean(rows, [M])
    assert counts[2023]["complete"] is True
    assert counts[2024]["complete"] is True
    assert counts[2025]["complete"] is False


def test_privacy_floor_suppresses_cells_under_11():
    def r(i, state):
        x = rep(i, ccn=str(i), m=float(i))
        x.state = state
        return x

    rows = [r(i, "TX") for i in range(11)] + [r(100 + i, "NM") for i in range(10)]
    kept, _ = pipeline.clean(rows, [M])
    out = pipeline.aggregate(kept, [M])
    by = {(x["group_type"], x["group"]): x for x in out}
    assert by[("state", "TX")]["suppressed"] is False and by[("state", "TX")]["n"] == 11
    nm = by[("state", "NM")]
    assert nm["suppressed"] is True and nm["n"] == 10
    assert nm["median"] is None and nm["p25"] is None and nm["p75"] is None and nm["mean"] is None
    assert by[("national", "US")]["n"] == 21 and by[("national", "US")]["suppressed"] is False


def test_aggregate_groups_by_size_and_control():
    def r(i, size, control):
        x = rep(i, ccn=str(i), m=float(i))
        x.size, x.control = size, control
        return x

    rows = [r(i, 1000, "Nonprofit") for i in range(12)] + [r(50 + i, 60000, "For-profit") for i in range(12)]
    kept, _ = pipeline.clean(rows, [M])
    out = pipeline.aggregate(kept, [M])
    keys = {(x["group_type"], x["group"]) for x in out}
    assert ("size", "Under 5,000") in keys and ("size", "50,000+") in keys
    assert ("control", "Nonprofit") in keys and ("control", "For-profit") in keys


def test_pooled_value_is_sum_over_sum_and_respects_suppression():
    P = Metric("p", "Share", "ratio", "t", ["x"], screen=False, has_pooled=True)
    A = Metric("a", "Providing", "ratio", "t", ["x"], screen=False, has_pooled=True, pooled_only=True)

    def r(i, num, den, state):
        x = rep(i, ccn=str(i), p=num / den, a=1.0 if num else 0.0)
        x.state = state
        x.parts = {"p": (num, den), "a": (1.0 if num else 0.0, 1.0)}
        return x

    # 11 agencies: ten with 0 of 100, one with 90 of 100 -> pooled 90 / 1100; median 0.
    rows = [r(i, 0, 100, "TX") for i in range(10)] + [r(50, 90, 100, "TX")]
    kept, _ = pipeline.clean(rows, [P, A])
    out = {(x["group_type"], x["group"], x["metric"]): x for x in pipeline.aggregate(kept, [P, A])}
    tx = out[("state", "TX", "p")]
    assert tx["pooled"] == pytest.approx(90 / 1100) and tx["median"] == 0.0
    ap = out[("state", "TX", "a")]
    assert ap["pooled"] == pytest.approx(1 / 11) and ap["n"] == 11 and ap["median"] is None and ap["mean"] is None
    # Below the privacy floor nothing is published.
    kept, _ = pipeline.clean(rows[:5], [P, A])
    small = {x["metric"]: x for x in pipeline.aggregate(kept, [P, A]) if x["group_type"] == "national"}
    assert small["p"]["suppressed"] and small["p"]["pooled"] is None
