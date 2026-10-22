"""Synthetic fixtures. Everything here is made up; no real provider data."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

FAKE_NAME = "ZZ FAKE HOSPICE OF NOWHERE"
FAKE_NAME_HHA = "ZZ FAKE HOME HEALTH CAFE"  # accented in the file, see write_hha_csv
FAKE_CCN_PREFIX = "99"  # fake CCNs look like 99xxxx


def hospice_report(
    rec,
    ccn,
    *,
    begin="01/01/2023",
    end="12/31/2023",
    state="TX",
    control="4",
    cost=1_000_000.0,
    days=10_000.0,
    levels=None,  # {"rhc": (cost, days), ...}; default all days are routine home care
    s1_days=None,  # {30: , 31: , 32: , 33: , 34:} col 4 overrides; col 1 = Medicare in medicare_days
    medicare_days=9_000.0,
    revenue=1_100_000.0,
    expenses=1_000_000.0,
    total_revenue=None,
    net_income=None,
    part2_cols=(2,),
    reported_per_diem=None,
):
    """Return (rpt_row, nmrc_rows, alpha_rows) for one fake hospice cost report."""
    levels = levels if levels is not None else {"rhc": (cost, days)}
    first = {"chc": 1, "rhc": 6, "irc": 11, "gip": 16}
    nm = []

    def add(ws, line, col, val):
        nm.append([str(rec), ws, f"{line * 100:05d}", f"{col * 100:05d}", repr(float(val)) if val is not None else ""])

    for lid, (lc, ld) in levels.items():
        f = first[lid]
        add("C000000", f, 3, lc)
        add("C000000", f + 1, 3, ld)
        add("C000000", f + 2, 3, lc / ld)
    if cost is not None:
        add("C000000", 21, 3, cost)
    if days is not None:
        add("C000000", 22, 3, days)
    if cost and days:
        add("C000000", 23, 3, reported_per_diem if reported_per_diem is not None else cost / days)
    s1 = s1_days or {}
    for line, lid in ((30, "chc"), (31, "rhc"), (32, "irc"), (33, "gip")):
        v = s1.get(line, levels[lid][1] if lid in levels else None)
        if v is not None:
            add("S100000", line, 4, v)
    add("S100000", 34, 4, s1.get(34, days))
    add("S100000", 34, 1, medicare_days)
    if revenue is not None:
        add("F200000", 8, 4, revenue)
    tr = total_revenue if total_revenue is not None else (revenue or 0.0)
    add("F200000", 26, 4, tr)
    ni = net_income if net_income is not None else tr - (expenses or 0.0)
    for col in part2_cols:
        for line, val in ((27, expenses), (41, expenses), (42, ni)):
            if val is not None:
                add("F200000", line, col, val)
    rpt = [str(rec), control, ccn, "", "1", begin, end, "05/22/2024", "N", "N", "D", "11001", "4",
           "05/21/2024", "F", "05/17/2024", "", "05/17/2024"]
    alpha = [
        [str(rec), "S100000", "00100", "00100", FAKE_NAME],
        [str(rec), "S100000", "00200", "00100", "1 FAKE STREET, SUITE 5"],  # embedded comma, quoted
        [str(rec), "S100000", "00300", "00200", state],
        [str(rec), "S100000", "01900", "00100", control],
    ]
    return rpt, nm, alpha


def write_hospice_zip(path: Path, reports_by_file_year: dict[int, list[tuple]]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for fy, reports in reports_by_file_year.items():
            for kind, idx in (("rpt", 0), ("nmrc", 1), ("alpha", 2)):
                rows = []
                for rep in reports:
                    if idx == 0:
                        rows.append(rep[0])
                    else:
                        rows.extend(rep[idx])
                lines = []
                for r in rows:
                    buf = io.StringIO()
                    csv.writer(buf, lineterminator="").writerow(r)
                    lines.append(buf.getvalue() + "\r\n")
                z.writestr(f"HOSPC14_{fy}_{kind}.csv", "".join(lines).encode("latin-1"))


HHA_COLUMNS = [
    "rpt_rec_num", "Provider CCN", "HHA Name", "State Code", "Type of Control",
    "Fiscal Year Begin Date", "Fiscal Year End Date",
    "Total, Medicare Title XVIII Visits", "Total, Total Visits", "Total Cost",
    "Skilled Nursing Care-RN, Avg Cost Per Visit", "Physical Therapy, Avg Cost Per Visit",
    "Home Health Aide, Avg Cost Per Visit",
    "Net Patient Revenues (line 1 minus line 2) Total",
    "Less Total Operating Expenses (sum of lines 4 through 16)",
    "Total HHA Medicare Program Cost",
    "Total PPS Payment - full episodes/periods without outliers",
    "Total PPS Payment - full episodes/periods with outliers",
    "Total PPS Payment - LUPA episodes/periods",
    "Total PPS Payment - PEP episodes/periods",
    "Total PPS Outlier Payment - full episodes/periods with outliers",
    "Total PPS Outlier Payment - PEP episodes/periods",
]


def hha_row(rec, ccn, **over):
    row = {
        "rpt_rec_num": str(rec), "Provider CCN": ccn, "HHA Name": "ZZ FAKE HOME HEALTH CAFÉ",
        "State Code": "TX", "Type of Control": "5",
        "Fiscal Year Begin Date": "01/01/2024", "Fiscal Year End Date": "12/31/2024",
        "Total, Medicare Title XVIII Visits": "6000", "Total, Total Visits": "10000", "Total Cost": "2000000",
        "Skilled Nursing Care-RN, Avg Cost Per Visit": "200", "Physical Therapy, Avg Cost Per Visit": "210",
        "Home Health Aide, Avg Cost Per Visit": "",
        "Net Patient Revenues (line 1 minus line 2) Total": "2200000",
        "Less Total Operating Expenses (sum of lines 4 through 16)": "2000000",
        "Total HHA Medicare Program Cost": "1200000",
        "Total PPS Payment - full episodes/periods without outliers": "1000000",
        "Total PPS Payment - full episodes/periods with outliers": "400000",
        "Total PPS Payment - LUPA episodes/periods": "50000",
        "Total PPS Payment - PEP episodes/periods": "50000",
        "Total PPS Outlier Payment - full episodes/periods with outliers": "99999",
        "Total PPS Outlier Payment - PEP episodes/periods": "1",
    }
    row.update(over)
    return row


def write_hha_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="latin-1", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HHA_COLUMNS)
        w.writeheader()
        w.writerows(rows)
