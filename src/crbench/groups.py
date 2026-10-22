"""Grouping rules: states, type of control, size bands."""

from __future__ import annotations

# 50 states, DC and Puerto Rico. Anything else is counted in the national group only.
STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY "
    "DC PR".split()
)

FOR_PROFIT = "For-profit"
NONPROFIT = "Nonprofit"
GOVERNMENT = "Government"
OTHER = "Other"

# Type-of-control codes differ by form (PRM 15-2 chapter 43 for hospice,
# CMS-1728-20 instructions for home health). Do not share one table.
HOSPICE_CONTROL = {
    **{c: NONPROFIT for c in (1, 2)},
    **{c: FOR_PROFIT for c in (3, 4, 5, 6)},
    **{c: GOVERNMENT for c in range(7, 14)},
}
HOME_HEALTH_CONTROL = {
    **{c: NONPROFIT for c in (1, 2, 6)},
    **{c: FOR_PROFIT for c in (3, 4, 5)},
    7: OTHER,  # Governmental and Private Combined
    **{c: GOVERNMENT for c in range(8, 14)},
}

SIZE_BAND_EDGES = (5000, 20000, 50000)
SIZE_BANDS = ("Under 5,000", "5,000-19,999", "20,000-49,999", "50,000+")

CONTROL_ORDER = (FOR_PROFIT, NONPROFIT, GOVERNMENT, OTHER)


def map_control(code, table: dict[int, str]) -> str | None:
    try:
        return table.get(int(str(code).strip()))
    except (TypeError, ValueError):
        return None


def clean_state(code) -> str | None:
    s = (code or "").strip().upper()
    return s if s in STATES else None


def size_band(total) -> str | None:
    if total is None or total <= 0:
        return None
    if total < SIZE_BAND_EDGES[0]:
        return SIZE_BANDS[0]
    if total < SIZE_BAND_EDGES[1]:
        return SIZE_BANDS[1]
    if total < SIZE_BAND_EDGES[2]:
        return SIZE_BANDS[2]
    return SIZE_BANDS[3]
