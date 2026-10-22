"""Form-independent cleaning and aggregation.

Provider modules turn source files into `Report` objects. This module applies the
cleaning rules, then builds the aggregate rows. Provider-level data (CCN, name)
never leaves this module: only aggregates are returned.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from crbench import groups
from crbench.stats import percentile_bounds, summarize

MIN_PERIOD_DAYS = 300
MAX_PERIOD_DAYS = 430
PRIVACY_FLOOR = 11
COMPLETE_RATIO = 0.90

NO_FINANCIAL_DATA = "no_financial_data"
NO_COST_OR_DAYS = "no_cost_or_days"


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    unit: str  # usd, usd_per_day, usd_per_visit, ratio, count
    definition: str
    source_fields: list[str]
    valid_range: tuple[float, float] | None = None  # values outside are data errors
    screen: bool = True  # apply the 1st to 99th percentile screen
    has_pooled: bool = False  # rows carry a pooled value (sum of numerators / sum of denominators)
    pooled_only: bool = False  # only the pooled value is published (no quartiles or mean)


@dataclass
class Report:
    """One cost report, in memory only."""

    year: int
    ccn: str
    rec: int
    period_days: int | None
    exclusion: str | None  # None, NO_FINANCIAL_DATA or NO_COST_OR_DAYS
    state: str | None = None
    control: str | None = None
    size: float | None = None
    values: dict[str, float | None] = field(default_factory=dict)
    parts: dict[str, tuple[float, float]] = field(default_factory=dict)  # metric id: (numerator, denominator)
    flags: list[str] = field(default_factory=list)  # data checks, counted over kept reports
    raw_flags: list[str] = field(default_factory=list)  # data checks, counted over all reports


def clean(reports: list[Report], metrics: list[Metric]):
    """Apply the cleaning rules. Returns (kept_by_year, counts_by_year).

    Order of rules for each year: no data, partial-year, duplicates, then (on the
    kept reports) per-metric range guard and percentile screen.
    """
    by_year: dict[int, list[Report]] = defaultdict(list)
    for r in reports:
        by_year[r.year].append(r)

    kept_by_year: dict[int, list[Report]] = {}
    counts: dict[int, dict] = {}
    for year in sorted(by_year):
        rows = by_year[year]
        c = {
            "raw": len(rows),
            "excluded_no_financial_data": 0,
            "excluded_no_cost_or_days": 0,
            "excluded_partial_year": 0,
            "dropped_duplicates": 0,
            "kept": 0,
            "raw_rows_sharing_a_ccn": len(rows) - len({r.ccn for r in rows}),
        }
        raw_checks = Counter()
        for r in rows:
            raw_checks.update(r.raw_flags)

        eligible = []
        for r in rows:
            if r.exclusion == NO_FINANCIAL_DATA:
                c["excluded_no_financial_data"] += 1
            elif r.exclusion == NO_COST_OR_DAYS:
                c["excluded_no_cost_or_days"] += 1
            elif r.period_days is None or not (MIN_PERIOD_DAYS <= r.period_days <= MAX_PERIOD_DAYS):
                c["excluded_partial_year"] += 1
            else:
                eligible.append(r)

        best: dict[str, Report] = {}
        for r in eligible:
            cur = best.get(r.ccn)
            if cur is None or (r.period_days, r.rec) > (cur.period_days, cur.rec):
                best[r.ccn] = r
        c["dropped_duplicates"] = len(eligible) - len(best)
        kept = list(best.values())
        c["kept"] = len(kept)

        checks = Counter()
        for r in kept:
            checks.update(r.flags)
            if r.state is None:
                checks["no_state_group"] += 1
            if r.control is None:
                checks["no_control_group"] += 1
        c["data_checks"] = dict(sorted(checks.items()))
        c["raw_data_checks"] = dict(sorted(raw_checks.items()))
        c["metric_screening"] = apply_screens(kept, metrics)

        kept_by_year[year] = kept
        counts[year] = c

    # Completeness: kept count under 90% of the prior year's kept count.
    prev = None
    for year in sorted(counts):
        k = counts[year]["kept"]
        counts[year]["complete"] = True if prev is None else k >= COMPLETE_RATIO * prev
        prev = k
    return kept_by_year, counts


def apply_screens(kept: list[Report], metrics: list[Metric]) -> dict:
    """Blank out invalid or outlying metric values in place. Returns the counts."""
    info = {}
    for m in metrics:
        entry = {"values": 0, "range_excluded": 0, "outlier_excluded": 0}
        if not m.screen:
            entry["values"] = sum(1 for r in kept if r.values.get(m.id) is not None)
            entry["screen_not_applied"] = True
            info[m.id] = entry
            continue
        if m.valid_range is not None:
            lo, hi = m.valid_range
            for r in kept:
                v = r.values.get(m.id)
                if v is not None and not (lo <= v <= hi):
                    r.values[m.id] = None
                    entry["range_excluded"] += 1
        vals = [r.values[m.id] for r in kept if r.values.get(m.id) is not None]
        entry["values"] = len(vals)
        bounds = percentile_bounds(vals)
        if bounds is not None:
            lo, hi = bounds
            entry["p01"], entry["p99"] = round(lo, 4), round(hi, 4)
            for r in kept:
                v = r.values.get(m.id)
                if v is not None and (v < lo or v > hi):
                    r.values[m.id] = None
                    entry["outlier_excluded"] += 1
        else:
            entry["screen_skipped_fewer_than"] = 100
        info[m.id] = entry
    return info


def aggregate(kept_by_year: dict[int, list[Report]], metrics: list[Metric]) -> list[dict]:
    """Long-format rows: group_type, group, year, metric, n, p25, median, p75, mean, suppressed."""
    rows = []
    by_id = {m.id: m for m in metrics}
    for year in sorted(kept_by_year):
        cells: dict[tuple[str, str, str], list] = defaultdict(lambda: [[], 0.0, 0.0])
        for r in kept_by_year[year]:
            gs = [("national", "US")]
            if r.state:
                gs.append(("state", r.state))
            band = groups.size_band(r.size)
            if band:
                gs.append(("size", band))
            if r.control:
                gs.append(("control", r.control))
            for m in metrics:
                v = r.values.get(m.id)
                if v is None:
                    continue
                num, den = r.parts.get(m.id, (None, None))
                for g in gs:
                    cell = cells[(g[0], g[1], m.id)]
                    cell[0].append(v)
                    if num is not None:
                        cell[1] += num
                        cell[2] += den
        for (gtype, group, mid), (vals, num, den) in cells.items():
            m = by_id[mid]
            row = {"group_type": gtype, "group": group, "year": year, "metric": mid, "pooled": None}
            if len(vals) < PRIVACY_FLOOR:
                row.update(n=len(vals), p25=None, median=None, p75=None, mean=None, suppressed=True)
            else:
                row.update(summarize(vals))
                row["suppressed"] = False
                if m.has_pooled and den > 0:
                    row["pooled"] = num / den
                if m.pooled_only:
                    row.update(p25=None, median=None, p75=None, mean=None)
            rows.append(row)
    return rows
