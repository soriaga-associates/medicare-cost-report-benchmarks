"""Home health agencies (Form CMS-1728-20) from the data.cms.gov CSV files."""

from __future__ import annotations

import csv
import re
from pathlib import Path

from crbench import groups
from crbench.pipeline import NO_COST_OR_DAYS, NO_FINANCIAL_DATA, Metric, Report
from crbench.stats import period_days, to_float

DISCIPLINES = [
    ("rn", "Skilled Nursing Care-RN", "skilled nursing (RN)"),
    ("lpn", "Skilled Nursing Care-LPN", "skilled nursing (LPN)"),
    ("pt", "Physical Therapy", "physical therapy"),
    ("pta", "Physical Therapy Asst", "physical therapy assistant"),
    ("ot", "Occupational Therapy", "occupational therapy"),
    ("ota", "Occupational Therapy Asst", "occupational therapy assistant"),
    ("slp", "Speech-Language-Path", "speech-language pathology"),
    ("msw", "Medical Social Service", "medical social service"),
    ("hha", "Home Health Aide", "home health aide"),
]

COL_CCN = "Provider CCN"
COL_REC = "rpt_rec_num"
COL_STATE = "State Code"
COL_CONTROL = "Type of Control"
COL_BEGIN = "Fiscal Year Begin Date"
COL_END = "Fiscal Year End Date"
COL_TOTAL_COST = "Total Cost"
COL_VISITS = "Total, Total Visits"
COL_MEDICARE_VISITS = "Total, Medicare Title XVIII Visits"
COL_NET_REV = "Net Patient Revenues (line 1 minus line 2) Total"
COL_OP_EXP = "Less Total Operating Expenses (sum of lines 4 through 16)"
COL_MEDICARE_COST = "Total HHA Medicare Program Cost"
# Medicare PPS payments: the four episode columns (Wkst D lines 10 to 13) plus the two
# separately entered outlier columns (lines 14 and 15). PRM 15-2 section 4715.2 treats the
# outlier payments as additional to lines 10 to 13 (see README).
PPS_COLUMNS = [
    "Total PPS Payment - full episodes/periods without outliers",
    "Total PPS Payment - full episodes/periods with outliers",
    "Total PPS Payment - LUPA episodes/periods",
    "Total PPS Payment - PEP episodes/periods",
    "Total PPS Outlier Payment - full episodes/periods with outliers",
    "Total PPS Outlier Payment - PEP episodes/periods",
]


def _metrics() -> list[Metric]:
    ms = [
        Metric(
            f"cost_per_visit_{key}",
            f"Cost per visit, {label}",
            "usd_per_visit",
            f"Average cost per {label} visit, only for agencies that report this discipline.",
            [f"data.cms.gov column '{col}, Avg Cost Per Visit' (Wkst C Part I col 4)"],
        )
        for key, col, label in DISCIPLINES
    ]
    ms += [
        Metric(
            "total_visits",
            "Total visits per agency",
            "count",
            "Total visits, all disciplines and payers.",
            ["data.cms.gov column 'Total, Total Visits'"],
        ),
        Metric(
            "medicare_share_visits",
            "Medicare share of visits",
            "ratio",
            "Each agency's Title XVIII (Medicare) visits divided by its total visits. Quartiles and mean are across agencies. "
            "The pooled value is total Medicare visits divided by total visits across all agencies in the group.",
            ["data.cms.gov columns 'Total, Medicare Title XVIII Visits' / 'Total, Total Visits'"],
            screen=False,
            has_pooled=True,
        ),
        Metric(
            "operating_margin",
            "All-payer operating margin",
            "ratio",
            "(Net patient revenue minus total operating expenses) divided by net patient revenue. "
            "Only where net patient revenue is above zero; margins outside -100% to +100% are excluded as data errors.",
            [
                "data.cms.gov columns 'Net Patient Revenues (line 1 minus line 2) Total' (F-1 line 3) and "
                "'Less Total Operating Expenses (sum of lines 4 through 16)' (F-1 line 17)"
            ],
            valid_range=(-1.0, 1.0),
        ),
        Metric(
            "medicare_margin_cost_based",
            "Cost-based Medicare margin",
            "ratio",
            "(Medicare PPS payments minus total HHA Medicare program cost) divided by Medicare PPS payments. "
            "Payments are the sum of the four 'Total PPS Payment' episode columns and the two 'Total PPS Outlier Payment' columns "
            "(Wkst D lines 10 to 15), not adjusted for bad debts or sequestration. Margins outside -100% to +100% are excluded as data errors.",
            ["data.cms.gov columns " + "; ".join(f"'{c}'" for c in PPS_COLUMNS) + "; 'Total HHA Medicare Program Cost'"],
            valid_range=(-1.0, 1.0),
        ),
    ]
    return ms


METRICS = _metrics()


def build_report(row: dict, year: int) -> Report:
    total_cost = to_float(row.get(COL_TOTAL_COST))
    visits = to_float(row.get(COL_VISITS))
    if total_cost is None:
        exclusion = NO_FINANCIAL_DATA
    elif total_cost <= 0 or visits is None or visits <= 0:
        exclusion = NO_COST_OR_DAYS
    else:
        exclusion = None
    try:
        rec = int(float(row.get(COL_REC) or 0))
    except ValueError:
        rec = 0

    r = Report(
        year=year,
        ccn=(row.get(COL_CCN) or "").strip(),
        rec=rec,
        period_days=period_days(row.get(COL_BEGIN), row.get(COL_END)),
        exclusion=exclusion,
        state=groups.clean_state(row.get(COL_STATE)),
        control=groups.map_control(row.get(COL_CONTROL), groups.HOME_HEALTH_CONTROL),
    )
    if exclusion:
        return r

    v = r.values
    for key, col, _ in DISCIPLINES:
        c = to_float(row.get(f"{col}, Avg Cost Per Visit"))
        if c is not None and c > 0:
            v[f"cost_per_visit_{key}"] = c
    v["total_visits"] = visits
    r.size = visits
    mv = to_float(row.get(COL_MEDICARE_VISITS)) or 0.0
    v["medicare_share_visits"] = mv / visits
    r.parts["medicare_share_visits"] = (mv, visits)

    revenue, expenses = to_float(row.get(COL_NET_REV)), to_float(row.get(COL_OP_EXP))
    if revenue is None or revenue <= 0:
        r.flags.append("margin_no_positive_revenue")
    elif expenses is None:
        r.flags.append("margin_no_expenses")
    else:
        v["operating_margin"] = (revenue - expenses) / revenue

    pps_parts = [to_float(row.get(c)) for c in PPS_COLUMNS]
    pps = sum(p for p in pps_parts if p is not None)
    mcost = to_float(row.get(COL_MEDICARE_COST))
    if any(p is not None for p in pps_parts) and pps > 0 and mcost is not None and mcost > 0:
        v["medicare_margin_cost_based"] = (pps - mcost) / pps
    else:
        r.flags.append("medicare_margin_not_computable")
    return r


def year_from_name(path: Path) -> int | None:
    m = re.fullmatch(r"hha_(\d{4})\.csv", path.name)
    return int(m.group(1)) if m else None


def load(raw_dir: Path, log=print) -> list[Report]:
    """Read every data/raw/home-health/hha_YYYY.csv (latin-1; the year is CMS's file year)."""
    reports: list[Report] = []
    for path in sorted(raw_dir.glob("hha_*.csv")):
        year = year_from_name(path)
        if year is None:
            continue
        with open(path, encoding="latin-1", newline="") as f:
            n = 0
            for row in csv.DictReader(f):
                reports.append(build_report(row, year))
                n += 1
        log(f"  home health file year {year}: {n} rows")
    return reports
