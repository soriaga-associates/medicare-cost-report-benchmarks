"""Write the JSON, CSV and SUMMARY.md outputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from crbench import groups

ROUND_BY_UNIT = {"usd": 2, "usd_per_day": 2, "usd_per_visit": 2, "ratio": 4, "count": 2}
GROUP_TYPE_ORDER = {"national": 0, "state": 1, "size": 2, "control": 3}
STAT_KEYS = ("p25", "median", "p75", "mean", "pooled")
CSV_COLUMNS = ["group_type", "group", "year", "metric", "n", "p25", "median", "p75", "mean", "pooled", "suppressed"]


def _group_sort_key(row):
    t, g = row["group_type"], row["group"]
    if t == "size":
        pos = groups.SIZE_BANDS.index(g) if g in groups.SIZE_BANDS else 99
    elif t == "control":
        pos = groups.CONTROL_ORDER.index(g) if g in groups.CONTROL_ORDER else 99
    else:
        pos = 0
    return GROUP_TYPE_ORDER[t], pos, g


def finalize_rows(rows: list[dict], metrics) -> list[dict]:
    """Round, order and return the rows."""
    unit = {m.id: m.unit for m in metrics}
    order = {m.id: i for i, m in enumerate(metrics)}
    out = []
    for r in rows:
        r = dict(r)
        digits = ROUND_BY_UNIT[unit[r["metric"]]]
        for k in STAT_KEYS:
            if r.get(k) is not None:
                r[k] = round(r[k], digits)
        out.append({c: r[c] for c in CSV_COLUMNS})
    out.sort(key=lambda r: (_group_sort_key(r), r["year"], order[r["metric"]]))
    return out


def write_json(path: Path, meta: dict, metrics, rows: list[dict]) -> None:
    doc = {
        "meta": meta,
        "metrics": [
            {"id": m.id, "label": m.label, "unit": m.unit, "definition": m.definition, "source_fields": m.source_fields,
             "has_pooled": m.has_pooled, "pooled_only": m.pooled_only}
            for m in metrics
        ],
        "rows": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def write_csv(path: Path, comment: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(comment + "\n")
        w = csv.writer(f, lineterminator="\n")
        w.writerow(CSV_COLUMNS)
        for r in rows:
            cells = []
            for c in CSV_COLUMNS:
                v = r[c]
                cells.append("" if v is None else ("true" if v is True else "false" if v is False else v))
            w.writerow(cells)


def fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "count":
        return f"{value:,.0f}"
    return f"${value:,.2f}"


def summary_section(title: str, meta: dict, metrics, rows: list[dict], year_label: str) -> list[str]:
    latest = meta["latest_complete_year"]
    lines = [f"## {title}", ""]
    if latest is None:
        return lines + ["No complete year available.", ""]
    nat = {(r["year"], r["metric"]): r for r in rows if r["group_type"] == "national"}
    lines += [
        f"{year_label} {latest} is the latest complete year. National figures, all agencies in the cleaned sample.",
        "",
        f"### National benchmarks, {latest}",
        "",
        "Share and provider metrics also show a pooled value: total numerator divided by total denominator across all agencies.",
        "",
        "| Metric | n | 25th percentile | Median | 75th percentile | Mean | Pooled |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for m in metrics:
        r = nat.get((latest, m.id))
        if r is None:
            continue
        lines.append(
            f"| {m.label} | {r['n']:,} | {fmt(r['p25'], m.unit)} | {fmt(r['median'], m.unit)} | "
            f"{fmt(r['p75'], m.unit)} | {fmt(r['mean'], m.unit)} | {fmt(r.get('pooled'), m.unit) if m.has_pooled else 'n/a'} |"
        )
    complete = [y for y in meta["years"] if meta["counts"][str(y)]["complete"]][-6:]
    lines += ["", "### National medians over time (pooled value for share and provider metrics)", "", "| Metric | " + " | ".join(str(y) for y in complete) + " |"]
    lines.append("| --- | " + " | ".join("---:" for _ in complete) + " |")
    for m in metrics:
        key = "pooled" if m.has_pooled else "median"
        cells = [fmt((nat.get((y, m.id)) or {}).get(key), m.unit) for y in complete]
        lines.append(f"| {m.label} | " + " | ".join(cells) + " |")
    lines += ["", "### Reports by year", "", "| Year | Raw reports | Kept | Complete |", "| --- | ---: | ---: | --- |"]
    for y in meta["years"]:
        c = meta["counts"][str(y)]
        lines.append(f"| {y} | {c['raw']:,} | {c['kept']:,} | {'yes' if c['complete'] else 'no (still filling in)'} |")
    lines.append("")
    return lines


def write_summary(path: Path, sections: list[list[str]], generated_at: str, version: str) -> None:
    lines = [
        "# Medicare cost report benchmarks: summary",
        "",
        "Generated file. National medians for the latest complete year, and the trend.",
        f"Built {generated_at} with medicare-cost-report-benchmarks {version}.",
        "",
        "Credit: Soriaga & Associates, LLC. Licensed CC BY 4.0 (credit required). Underlying CMS data is public domain.",
        "General information from public CMS filings, not an audit, valuation, or advice for any agency.",
        "Not affiliated with or endorsed by CMS or HHS.",
        "",
    ]
    for s in sections:
        lines += s
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
