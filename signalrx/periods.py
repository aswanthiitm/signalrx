"""Indian fiscal-period arithmetic (FY runs April-March; FY26 = Apr-2025..Mar-2026)."""
from __future__ import annotations

import re
from datetime import date

_FY = re.compile(r"^FY(\d{2,4})$", re.I)
_Q = re.compile(r"^Q([1-4])\s*FY(\d{2,4})$", re.I)
_H = re.compile(r"^H([12])\s*FY(\d{2,4})$", re.I)


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def parse(period: str) -> tuple[str, int, int]:
    """Return (frequency, fiscal_year, sub_index). 'FY26' -> ('A', 2026, 0); 'Q1FY27' -> ('Q', 2027, 1)."""
    p = period.replace(" ", "").upper()
    if m := _Q.match(p):
        return "Q", _year(m.group(2)), int(m.group(1))
    if m := _H.match(p):
        return "H", _year(m.group(2)), int(m.group(1))
    if m := _FY.match(p):
        return "A", _year(m.group(1)), 0
    raise ValueError(f"unrecognised period {period!r}")


def sort_key(period: str) -> tuple[int, int]:
    freq, fy, sub = parse(period)
    if freq == "A":
        return fy, 5
    if freq == "H":
        return fy, sub * 2
    return fy, sub


def frequency(period: str) -> str:
    return parse(period)[0]


def end_date(period: str) -> date:
    """Calendar date on which the period closes."""
    freq, fy, sub = parse(period)
    if freq == "A":
        return date(fy, 3, 31)
    if freq == "H":
        return date(fy - 1, 9, 30) if sub == 1 else date(fy, 3, 31)
    month_end = {1: (fy - 1, 6, 30), 2: (fy - 1, 9, 30), 3: (fy - 1, 12, 31), 4: (fy, 3, 31)}[sub]
    return date(*month_end)


def prior_year(period: str) -> str:
    freq, fy, sub = parse(period)
    yy = (fy - 1) % 100
    if freq == "A":
        return f"FY{yy:02d}"
    return f"{'Q' if freq == 'Q' else 'H'}{sub}FY{yy:02d}"


def next_quarter(period: str) -> str:
    freq, fy, sub = parse(period)
    if freq != "Q":
        raise ValueError("next_quarter needs a quarterly period")
    if sub == 4:
        return f"Q1FY{(fy + 1) % 100:02d}"
    return f"Q{sub + 1}FY{fy % 100:02d}"


def months_between(a: date, b: date) -> float:
    return (b.year - a.year) * 12 + (b.month - a.month) + (b.day - a.day) / 30.0


def parse_date(s: str) -> date:
    """Accept YYYY-MM-DD, YYYY-MM (mid-month) or YYYY (mid-year)."""
    parts = [int(x) for x in s.split("-")]
    if len(parts) == 3:
        return date(*parts)
    if len(parts) == 2:
        return date(parts[0], parts[1], 15)
    return date(parts[0], 7, 1)
