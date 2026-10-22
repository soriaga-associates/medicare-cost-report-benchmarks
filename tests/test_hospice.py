import pytest

from crbench import hospice, pipeline
from fakes import hospice_report, write_hospice_zip


def build(tmp_path, **kw):
    """Run one fake report through the real zip reader and return the Report."""
    rep = hospice_report(1, "990001", **kw)
    zp = tmp_path / "h.zip"
    write_hospice_zip(zp, {2023: [rep]})
    reports = hospice.load(zp, log=lambda *_: None)
    assert len(reports) == 1
    return reports[0]


def test_worksheet_mapping(tmp_path):
    r = build(
        tmp_path,
        cost=1_300_021.0,
        days=8_738.0,
        levels={"rhc": (1_300_021.0, 8_738.0)},
        medicare_days=8_000.0,
        revenue=1_500_000.0,
        expenses=1_200_000.0,
    )
    v = r.values
    assert v["cost_per_day_total"] == pytest.approx(1_300_021 / 8_738)  # line 21 / line 22
    assert v["cost_per_day_total"] == pytest.approx(148.78, abs=0.01)
    assert v["cost_per_day_rhc"] == pytest.approx(148.78, abs=0.01)  # line 6 / line 7, per diem on line 8
    assert "cost_per_day_chc" not in v  # level not reported
    assert v["share_days_rhc"] == pytest.approx(1.0)
    assert v["share_days_gip"] == 0.0
    assert v["medicare_share_days"] == pytest.approx(8_000 / 8_738)
    assert v["total_patient_days"] == 8_738
    assert v["operating_margin"] == pytest.approx((1_500_000 - 1_200_000) / 1_500_000)
    assert (r.year, r.state, r.control, r.period_days, r.size) == (2023, "TX", "For-profit", 365, 8_738)


def test_level_shares_and_costs(tmp_path):
    r = build(
        tmp_path,
        cost=1_000_000.0,
        days=1_000.0,
        levels={"rhc": (500_000.0, 900.0), "gip": (500_000.0, 100.0)},
        s1_days={34: 1_000.0},
    )
    assert r.values["cost_per_day_rhc"] == pytest.approx(500_000 / 900)
    assert r.values["cost_per_day_gip"] == pytest.approx(5_000)
    assert r.values["share_days_rhc"] == pytest.approx(0.9)
    assert r.values["share_days_gip"] == pytest.approx(0.1)


def test_reported_per_diem_is_published_as_filed(tmp_path):
    ok = build(tmp_path)
    assert "per_diem_total_differs_from_recomputed" not in ok.flags
    filed = build(tmp_path, reported_per_diem=250.0)
    assert filed.values["cost_per_day_total"] == 250.0  # as filed, not 100.0
    assert "per_diem_total_differs_from_recomputed" in filed.flags


def test_per_diem_falls_back_to_recomputation_when_missing(tmp_path):
    r = build(tmp_path, reported_per_diem=0.0)
    assert r.values["cost_per_day_total"] == pytest.approx(100.0)
    assert "per_diem_total_recomputed" in r.flags


def test_pooled_parts_and_provider_indicators(tmp_path):
    r = build(
        tmp_path,
        cost=1_000_000.0,
        days=1_000.0,
        levels={"rhc": (500_000.0, 900.0), "gip": (500_000.0, 100.0)},
        s1_days={34: 1_000.0},
        medicare_days=800.0,
    )
    assert r.parts["share_days_gip"] == (100.0, 1_000.0)
    assert r.parts["medicare_share_days"] == (800.0, 1_000.0)
    assert r.values["agencies_providing_gip"] == 1.0
    assert r.values["agencies_providing_chc"] == 0.0
    assert r.values["agencies_providing_irc"] == 0.0


def test_part_ii_uses_column_2_and_records_where_it_found_it(tmp_path):
    r = build(tmp_path, part2_cols=(2,))
    assert "f2_line41_columns_2" in r.raw_flags
    assert r.values["operating_margin"] == pytest.approx((1_100_000 - 1_000_000) / 1_100_000)


def test_part_ii_falls_back_to_column_4_and_never_sums(tmp_path):
    r = build(tmp_path, part2_cols=(4,))
    assert "f2_line41_columns_4" in r.raw_flags
    assert r.values["operating_margin"] == pytest.approx(100_000 / 1_100_000)
    both = build(tmp_path, part2_cols=(2, 4))
    assert "f2_line41_columns_2+4" in both.raw_flags
    # Column 2 wins; the two columns are not added together.
    assert both.values["operating_margin"] == pytest.approx(100_000 / 1_100_000)


def test_net_income_consistency_check(tmp_path):
    assert "f2_net_income_check_pass" in build(tmp_path).raw_flags
    bad = build(tmp_path, net_income=5_000_000.0)
    assert "f2_net_income_check_fail" in bad.raw_flags
    # Within $1 passes.
    near = build(tmp_path, total_revenue=1_100_000.0, net_income=100_000.5)
    assert "f2_net_income_check_pass" in near.raw_flags


def test_margin_needs_positive_net_patient_revenue(tmp_path):
    r = build(tmp_path, revenue=0.0)
    assert "operating_margin" not in r.values
    assert "margin_no_positive_revenue" in r.flags
    r = build(tmp_path, revenue=-5.0)
    assert "operating_margin" not in r.values


def test_margin_guard_is_applied_by_the_pipeline(tmp_path):
    ok = build(tmp_path, revenue=1_000_000.0, expenses=1_900_000.0)  # -90%
    wild = build(tmp_path, revenue=1_000_000.0, expenses=2_500_000.0)  # -150%
    wild.ccn = "990002"
    kept, counts = pipeline.clean([ok, wild], hospice.METRICS)
    margins = [r.values["operating_margin"] for r in kept[2023]]
    assert margins.count(None) == 1
    assert counts[2023]["metric_screening"]["operating_margin"]["range_excluded"] == 1


def test_no_cost_or_days_reports_are_dropped(tmp_path):
    for kw in ({"cost": None}, {"days": None}, {"cost": 0.0}, {"days": -1.0}):
        r = build(tmp_path, **kw, levels={})
        assert r.exclusion == pipeline.NO_COST_OR_DAYS, kw
    assert build(tmp_path).exclusion is None


def test_federal_fiscal_year_comes_from_begin_date(tmp_path):
    assert build(tmp_path, begin="10/01/2022", end="09/30/2023").year == 2023
    assert build(tmp_path, begin="09/30/2022", end="09/29/2023").year == 2022
    assert build(tmp_path, begin="01/01/2023", end="12/31/2023").year == 2023


def test_hospice_control_uses_hospice_codes(tmp_path):
    assert build(tmp_path, control="6").control == "For-profit"
    assert build(tmp_path, control="7").control == "Government"
    assert build(tmp_path, control="2").control == "Nonprofit"


def test_unknown_state_is_kept_for_national_only(tmp_path):
    assert build(tmp_path, state="GU").state is None


def test_subscripted_lines_are_ignored(tmp_path):
    rep = hospice_report(1, "990001")
    rep[1].append(["1", "C000000", "02101", "00300", "123"])  # subscripted line 21.01, not line 21
    zp = tmp_path / "h.zip"
    write_hospice_zip(zp, {2023: [rep]})
    (r,) = hospice.load(zp, log=lambda *_: None)
    assert r.values["cost_per_day_total"] == pytest.approx(100.0)
