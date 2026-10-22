import pytest

from crbench import homehealth, pipeline
from fakes import hha_row, write_hha_csv


def load_one(tmp_path, **over):
    write_hha_csv(tmp_path / "hha_2024.csv", [hha_row(1, "990001", **over)])
    (r,) = homehealth.load(tmp_path, log=lambda *_: None)
    return r


def test_basic_mapping_and_year_from_file_name(tmp_path):
    r = load_one(tmp_path)
    v = r.values
    assert r.year == 2024
    assert v["cost_per_visit_rn"] == 200 and v["cost_per_visit_pt"] == 210
    assert "cost_per_visit_hha" not in v  # blank means not reported
    assert v["total_visits"] == 10_000
    assert v["medicare_share_visits"] == pytest.approx(0.6)
    assert v["operating_margin"] == pytest.approx(200_000 / 2_200_000)
    assert r.period_days == 366
    assert r.size == 10_000


def test_medicare_payments_are_the_sum_of_all_six_columns(tmp_path):
    r = load_one(tmp_path)
    # Four episode columns 1,500,000 plus outlier columns 99,999 and 1 = 1,600,000.
    assert r.values["medicare_margin_cost_based"] == pytest.approx((1_600_000 - 1_200_000) / 1_600_000)


def test_medicare_share_of_visits_has_pooled_parts(tmp_path):
    r = load_one(tmp_path)
    assert r.parts["medicare_share_visits"] == (6000.0, 10000.0)


def test_medicare_margin_not_computed_without_payments_or_cost(tmp_path):
    r = load_one(tmp_path, **{"Total HHA Medicare Program Cost": ""})
    assert "medicare_margin_cost_based" not in r.values
    assert "medicare_margin_not_computable" in r.flags


def test_blank_total_cost_is_no_financial_data(tmp_path):
    assert load_one(tmp_path, **{"Total Cost": ""}).exclusion == pipeline.NO_FINANCIAL_DATA


def test_zero_cost_or_visits_is_no_cost_or_days(tmp_path):
    assert load_one(tmp_path, **{"Total Cost": "0"}).exclusion == pipeline.NO_COST_OR_DAYS
    assert load_one(tmp_path, **{"Total, Total Visits": ""}).exclusion == pipeline.NO_COST_OR_DAYS
    assert load_one(tmp_path, **{"Total, Total Visits": "-4"}).exclusion == pipeline.NO_COST_OR_DAYS


def test_margin_needs_positive_net_patient_revenue(tmp_path):
    r = load_one(tmp_path, **{"Net Patient Revenues (line 1 minus line 2) Total": "0"})
    assert "operating_margin" not in r.values
    assert "margin_no_positive_revenue" in r.flags


def test_margin_guard_excludes_beyond_100_percent(tmp_path):
    wild = load_one(tmp_path, **{"Less Total Operating Expenses (sum of lines 4 through 16)": "9000000"})
    kept, counts = pipeline.clean([wild], homehealth.METRICS)
    assert kept[2024][0].values["operating_margin"] is None
    assert counts[2024]["metric_screening"]["operating_margin"]["range_excluded"] == 1


def test_home_health_control_codes(tmp_path):
    assert load_one(tmp_path, **{"Type of Control": "6"}).control == "Nonprofit"
    assert load_one(tmp_path, **{"Type of Control": "7"}).control == "Other"
    assert load_one(tmp_path, **{"Type of Control": "5"}).control == "For-profit"
    assert load_one(tmp_path, **{"Type of Control": "12"}).control == "Government"
    assert load_one(tmp_path, **{"Type of Control": ""}).control is None


def test_other_control_group_is_suppressed_under_11(tmp_path):
    rows = [hha_row(i, f"99{i:04d}", **{"Type of Control": "7"}) for i in range(5)]
    rows += [hha_row(100 + i, f"98{i:04d}", **{"Type of Control": "5"}) for i in range(12)]
    write_hha_csv(tmp_path / "hha_2024.csv", rows)
    reports = homehealth.load(tmp_path, log=lambda *_: None)
    kept, _ = pipeline.clean(reports, homehealth.METRICS)
    out = pipeline.aggregate(kept, homehealth.METRICS)
    other = [x for x in out if x["group_type"] == "control" and x["group"] == "Other" and x["metric"] == "total_visits"]
    assert other and other[0]["suppressed"] is True and other[0]["n"] == 5


def test_dates_other_formats_and_partial_year(tmp_path):
    r = load_one(tmp_path, **{"Fiscal Year Begin Date": "2024-10-13", "Fiscal Year End Date": "2024-12-31"})
    assert r.period_days == 80
    kept, counts = pipeline.clean([r], homehealth.METRICS)
    assert counts[2024]["excluded_partial_year"] == 1 and 2024 not in {y for y, k in kept.items() if k}


def test_non_state_code_is_national_only(tmp_path):
    assert load_one(tmp_path, **{"State Code": "GU"}).state is None
