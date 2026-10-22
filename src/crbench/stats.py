"""Small statistics helpers (standard library only)."""

from __future__ import annotations

import statistics
from datetime import date, datetime

MIN_N_FOR_SCREEN = 100


def quartiles(values: list[float]) -> tuple[float, float, float]:
    """Return (p25, median, p75) using statistics.quantiles(n=4, method="inclusive")."""
    if len(values) == 1:
        return values[0], values[0], values[0]
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[1], q[2]


def summarize(values: list[float]) -> dict:
    """n, p25, median, p75, mean for a non-empty list."""
    p25, med, p75 = quartiles(values)
    return {
        "n": len(values),
        "p25": p25,
        "median": med,
        "p75": p75,
        "mean": statistics.fmean(values),
    }


def percentile_bounds(values: list[float]) -> tuple[float, float] | None:
    """1st and 99th percentile (inclusive method), or None if too few values to screen."""
    if len(values) < MIN_N_FOR_SCREEN:
        return None
    q = statistics.quantiles(values, n=100, method="inclusive")
    return q[0], q[98]


def to_float(text) -> float | None:
    """Parse a CMS numeric field. Blank or unreadable gives None."""
    if text is None:
        return None
    s = str(text).strip().replace(",", "").replace("$", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(text) -> date | None:
    """Parse mm/dd/yyyy or yyyy-mm-dd. Anything else gives None."""
    if not text:
        return None
    s = str(text).strip()[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def period_days(begin, end) -> int | None:
    b, e = parse_date(begin), parse_date(end)
    if b is None or e is None or e < b:
        return None
    return (e - b).days + 1
