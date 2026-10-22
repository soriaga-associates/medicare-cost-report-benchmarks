"""Hospice (Form CMS-1984-14) from the HCRIS raw files."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from crbench import groups
from crbench.pipeline import NO_COST_OR_DAYS, Metric, Report
from crbench.stats import parse_date, period_days, to_float

S1 = "S100000"
WC = "C000000"
F2 = "F200000"

# id: (label, first line of the Worksheet C block). Each block is: total cost,
# total unduplicated days, average cost per diem, Medicare days, Medicare cost.
LEVELS = {
    "chc": ("continuous home care", 1),
    "rhc": ("routine home care", 6),
    "irc": ("inpatient respite care", 11),
    "gip": ("general inpatient care", 16),
}
S1_DAY_LINES = {"chc": 30, "rhc": 31, "irc": 32, "gip": 33}

# Every (worksheet, line) we read. Keeps memory small.
NEEDED: dict[str, set[int]] = {
    S1: {30, 31, 32, 33, 34},
    WC: set(range(1, 24)),
    F2: {8, 26, 27, 41, 42},
}
PART_II_LINES = (27, 41, 42)


def _metrics() -> list[Metric]:
    ms = [
        Metric(
            "cost_per_day_total",
            "Cost per day, all levels of care",
            "usd_per_day",
            "Average cost per day, all levels of care, as filed (Worksheet C line 23 col 3). If the filed value is "
            "missing, total cost divided by total unduplicated days (lines 21 and 22).",
            ["Wkst C line 23 col 3 (fallback: line 21 col 3 / line 22 col 3)"],
        )
    ]
    for lid, (name, first) in LEVELS.items():
        ms.append(
            Metric(
                f"cost_per_day_{lid}",
                f"Cost per day, {name}",
                "usd_per_day",
                f"Average cost per day for {name} as filed (Worksheet C line {first + 2} col 3), only for agencies that "
                f"report days at this level. If the filed value is missing, cost divided by days (lines {first} and {first + 1}).",
                [f"Wkst C line {first + 2} col 3 (fallback: line {first} col 3 / line {first + 1} col 3)"],
            )
        )
    for lid, (name, _) in LEVELS.items():
        ms.append(
            Metric(
                f"share_days_{lid}",
                f"Share of days, {name}",
                "ratio",
                f"Each agency's days of {name} divided by its total days (blank days count as zero). Quartiles and mean "
                "are across agencies. The pooled value is total days at this level divided by total days across all agencies in the group.",
                [f"Wkst S-1 Part II line {S1_DAY_LINES[lid]} col 4 / line 34 col 4"],
                screen=False,
                has_pooled=True,
            )
        )
    for lid in ("chc", "irc", "gip"):
        name = LEVELS[lid][0]
        ms.append(
            Metric(
                f"agencies_providing_{lid}",
                f"Agencies providing {name}",
                "ratio",
                f"Share of agencies reporting more than zero days of {name}. Published only in the pooled field; n is the number of agencies.",
                [f"Wkst S-1 Part II line {S1_DAY_LINES[lid]} col 4 (greater than 0)"],
                screen=False,
                has_pooled=True,
                pooled_only=True,
            )
        )
    ms += [
        Metric(
            "medicare_share_days",
            "Medicare share of days",
            "ratio",
            "Each agency's Title XVIII (Medicare) days divided by its total days, all levels of care. Quartiles and mean are across "
            "agencies. The pooled value is total Medicare days divided by total days across all agencies in the group.",
            ["Wkst S-1 Part II line 34 col 1 / line 34 col 4"],
            screen=False,
            has_pooled=True,
        ),
        Metric(
            "operating_margin",
            "All-payer operating margin",
            "ratio",
            "(Net patient revenue minus total operating expenses) divided by net patient revenue. "
            "Only where net patient revenue is above zero; margins outside -100% to +100% are excluded as data errors.",
            ["Wkst F-2 line 8 col 4; Wkst F-2 line 41 col 2"],
            valid_range=(-1.0, 1.0),
        ),
        Metric(
            "total_patient_days",
            "Total patient days per agency",
            "count",
            "Total days of hospice care, all payers and levels of care.",
            ["Wkst S-1 Part II line 34 col 4"],
        ),
    ]
    return ms


METRICS = _metrics()


def open_members(zip_path: Path):
    """Return (ZipFile, {file_year: {"rpt": name, "nmrc": name, "alpha": name}})."""
    z = zipfile.ZipFile(zip_path)
    found: dict[int, dict[str, str]] = defaultdict(dict)
    for name in z.namelist():
        m = re.fullmatch(r"HOSPC14_(\d{4})_(rpt|nmrc|alpha)\.csv", name)
        if m:
            found[int(m.group(1))][m.group(2)] = name
    return z, {y: d for y, d in sorted(found.items()) if len(d) == 3}


def _text(z: zipfile.ZipFile, name: str):
    return io.TextIOWrapper(z.open(name), encoding="latin-1", newline="")


def read_numeric(z, name):
    """Return ({rec: {(wksht, line, col): value}}, subscripted_rows_ignored).

    Line and column numbers are stored as plain integers (00600 -> 6, 00300 -> 3).
    Subscripted lines such as 00601 are not used; they are counted for transparency.
    """
    out: dict[str, dict] = defaultdict(dict)
    ignored = 0
    with _text(z, name) as f:
        for raw in f:
            parts = raw.rstrip("\r\n").split(",")
            if len(parts) != 5:
                continue
            rec, wk, line, col, val = parts
            wanted = NEEDED.get(wk)
            if wanted is None:
                continue
            try:
                ln, cl = int(line), int(col)
            except ValueError:
                continue
            if ln // 100 not in wanted:
                continue
            if ln % 100 != 0:
                ignored += 1
                continue
            v = to_float(val)
            if v is not None:
                out[rec][(wk, ln // 100, cl // 100)] = v
    return out, ignored


def read_alpha(z, name):
    """{rec: {"state": ..., "control": ...}} from Worksheet S-1 alpha cells."""
    out: dict[str, dict] = defaultdict(dict)
    with _text(z, name) as f:
        for raw in f:
            if ",S100000," not in raw:
                continue
            row = next(csv.reader([raw.rstrip("\r\n")]))
            if len(row) != 5 or row[1] != S1:
                continue
            rec, _, line, col, val = row
            if line == "00300" and col == "00200":
                out[rec]["state"] = val
            elif line == "01900" and col == "00100":
                out[rec]["control"] = val
    return out


def part_ii(vals, line):
    """Worksheet F-2 Part II value and the columns present.

    Part II is stored in one column (column 2 in every report we checked). Rule: use
    column 2; if it is absent use column 4; never sum columns.
    """
    cols = [c for c in (1, 2, 3, 4) if (F2, line, c) in vals]
    if (F2, line, 2) in vals:
        return vals[(F2, line, 2)], cols
    if (F2, line, 4) in vals:
        return vals[(F2, line, 4)], cols
    return None, cols


def federal_fy(begin) -> int:
    return begin.year + 1 if begin.month >= 10 else begin.year


def build_report(rec, rpt_row, vals, alpha, file_year) -> Report:
    """Turn one report's rpt row plus its numeric and alpha cells into a Report."""
    begin = parse_date(rpt_row[5])
    year = federal_fy(begin) if begin else file_year
    g = lambda w, ln, c: vals.get((w, ln, c))  # noqa: E731
    cost, days = g(WC, 21, 3), g(WC, 22, 3)
    no_data = cost is None or days is None or cost <= 0 or days <= 0

    r = Report(
        year=year,
        ccn=rpt_row[2].strip(),
        rec=int(rec),
        period_days=period_days(rpt_row[5], rpt_row[6]),
        exclusion=NO_COST_OR_DAYS if no_data else None,
        state=groups.clean_state(alpha.get("state")),
        control=groups.map_control(alpha.get("control"), groups.HOSPICE_CONTROL),
    )
    if year != file_year:
        r.raw_flags.append("fy_differs_from_file_year")

    # Which column(s) hold Part II, and does net income tie out? All reports.
    for ln in PART_II_LINES:
        _, cols = part_ii(vals, ln)
        if cols:
            r.raw_flags.append(f"f2_line{ln}_columns_{'+'.join(map(str, cols))}")
    net_income, _ = part_ii(vals, 42)
    expenses, _ = part_ii(vals, 41)
    total_rev = g(F2, 26, 4)
    if net_income is None or expenses is None or total_rev is None:
        r.raw_flags.append("f2_net_income_check_not_possible")
    else:
        expected = total_rev - expenses
        ok = abs(net_income - expected) <= max(1.0, 0.001 * abs(expected))
        r.raw_flags.append("f2_net_income_check_pass" if ok else "f2_net_income_check_fail")

    if no_data:
        return r

    v = r.values
    # Per diems are published as filed; recomputed only when the filed value is missing.
    reported = g(WC, 23, 3)
    if reported is not None and reported > 0:
        v["cost_per_day_total"] = reported
        if abs(reported - cost / days) > max(0.05, 0.001 * cost / days):
            r.flags.append("per_diem_total_differs_from_recomputed")
    else:
        v["cost_per_day_total"] = cost / days
        r.flags.append("per_diem_total_recomputed")

    for lid, (_, first) in LEVELS.items():
        lc, ld, lp = g(WC, first, 3), g(WC, first + 1, 3), g(WC, first + 2, 3)
        if not (ld and ld > 0):
            continue
        if lp is not None and lp > 0:
            v[f"cost_per_day_{lid}"] = lp
            if lc and abs(lp - lc / ld) > max(0.05, 0.001 * lc / ld):
                r.flags.append("per_diem_level_differs_from_recomputed")
        elif lc and lc > 0:
            v[f"cost_per_day_{lid}"] = lc / ld
            r.flags.append("per_diem_level_recomputed")

    total_days = g(S1, 34, 4)
    if total_days is None or total_days <= 0:
        total_days = days  # fall back to Worksheet C line 22 col 3
        r.flags.append("total_days_from_wkst_c")
    v["total_patient_days"] = total_days
    r.size = total_days
    for lid, line in S1_DAY_LINES.items():
        level_days = g(S1, line, 4) or 0.0
        v[f"share_days_{lid}"] = level_days / total_days
        r.parts[f"share_days_{lid}"] = (level_days, total_days)
        if lid != "rhc":
            v[f"agencies_providing_{lid}"] = 1.0 if level_days > 0 else 0.0
            r.parts[f"agencies_providing_{lid}"] = (v[f"agencies_providing_{lid}"], 1.0)
    medicare_days = g(S1, 34, 1) or 0.0
    v["medicare_share_days"] = medicare_days / total_days
    r.parts["medicare_share_days"] = (medicare_days, total_days)

    revenue = g(F2, 8, 4)
    if revenue is None or revenue <= 0:
        r.flags.append("margin_no_positive_revenue")
    elif expenses is None:
        r.flags.append("margin_no_expenses")
    else:
        v["operating_margin"] = (revenue - expenses) / revenue
    return r


def load(zip_path: Path, log=print) -> list[Report]:
    z, years = open_members(zip_path)
    reports: list[Report] = []
    for fy, names in years.items():
        with _text(z, names["rpt"]) as f:
            rpt = {row[0]: row for row in csv.reader(f) if len(row) >= 7}
        vals, ignored = read_numeric(z, names["nmrc"])
        alpha = read_alpha(z, names["alpha"])
        for rec, row in rpt.items():
            try:
                r = build_report(rec, row, vals.get(rec, {}), alpha.get(rec, {}), fy)
            except ValueError:
                continue
            reports.append(r)
        log(f"  hospice HCRIS file year {fy}: {len(rpt)} reports, {ignored} subscripted rows on needed lines ignored")
    return reports
