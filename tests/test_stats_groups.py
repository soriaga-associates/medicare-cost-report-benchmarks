import pytest

from crbench import groups, stats


def test_quartiles_use_inclusive_method():
    assert stats.quartiles([1, 2, 3, 4, 5]) == (2.0, 3.0, 4.0)
    assert stats.quartiles([10]) == (10, 10, 10)


def test_summarize_has_n_quartiles_and_mean():
    s = stats.summarize([1, 2, 3, 4])
    assert s == {"n": 4, "p25": 1.75, "median": 2.5, "p75": 3.25, "mean": 2.5}


def test_percentile_bounds_need_enough_values():
    assert stats.percentile_bounds(list(range(50))) is None
    lo, hi = stats.percentile_bounds([float(i) for i in range(1, 201)])
    assert lo == pytest.approx(2.99)
    assert hi == pytest.approx(198.01)


def test_to_float_handles_blanks_and_commas():
    assert stats.to_float("1,234.5") == 1234.5
    assert stats.to_float("  ") is None
    assert stats.to_float("abc") is None
    assert stats.to_float(None) is None


def test_dates_and_period_days():
    assert stats.period_days("01/01/2023", "12/31/2023") == 365
    assert stats.period_days("2023-01-01", "2023-06-30") == 181
    assert stats.period_days("", "12/31/2023") is None
    assert stats.period_days("12/31/2023", "01/01/2023") is None


def test_control_codes_differ_by_form():
    # Code 6 is for-profit in the hospice form but nonprofit in the home health form.
    assert groups.map_control("6", groups.HOSPICE_CONTROL) == groups.FOR_PROFIT
    assert groups.map_control("6", groups.HOME_HEALTH_CONTROL) == groups.NONPROFIT
    # Code 7 is government for hospice, "governmental and private combined" (other) for home health.
    assert groups.map_control("7", groups.HOSPICE_CONTROL) == groups.GOVERNMENT
    assert groups.map_control("7", groups.HOME_HEALTH_CONTROL) == groups.OTHER


def test_hospice_control_ranges():
    t = groups.HOSPICE_CONTROL
    assert [groups.map_control(c, t) for c in (1, 2)] == [groups.NONPROFIT] * 2
    assert [groups.map_control(c, t) for c in (3, 4, 5, 6)] == [groups.FOR_PROFIT] * 4
    assert [groups.map_control(c, t) for c in range(7, 14)] == [groups.GOVERNMENT] * 7
    assert groups.map_control("14", t) is None
    assert groups.map_control("", t) is None


def test_home_health_control_ranges():
    t = groups.HOME_HEALTH_CONTROL
    assert [groups.map_control(c, t) for c in (1, 2, 6)] == [groups.NONPROFIT] * 3
    assert [groups.map_control(c, t) for c in (3, 4, 5)] == [groups.FOR_PROFIT] * 3
    assert [groups.map_control(c, t) for c in range(8, 14)] == [groups.GOVERNMENT] * 6
    assert groups.map_control(None, t) is None


def test_size_bands():
    assert groups.size_band(4999) == "Under 5,000"
    assert groups.size_band(5000) == "5,000-19,999"
    assert groups.size_band(19999) == "5,000-19,999"
    assert groups.size_band(20000) == "20,000-49,999"
    assert groups.size_band(49999) == "20,000-49,999"
    assert groups.size_band(50000) == "50,000+"
    assert groups.size_band(None) is None


def test_states_are_us_dc_pr_only():
    assert len(groups.STATES) == 52
    assert groups.clean_state(" tx ") == "TX"
    assert groups.clean_state("DC") == "DC"
    assert groups.clean_state("PR") == "PR"
    assert groups.clean_state("GU") is None
    assert groups.clean_state("") is None
