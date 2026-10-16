"""Human formatting for figures (Indian conventions: crore for rupee amounts)."""
from __future__ import annotations

from datetime import date

from . import periods


def money(value: float, unit: str) -> str:
    if unit == "INR_mn":
        cr = value / 10.0
        return f"₹{cr:,.0f} cr" if cr < 100_000 else f"₹{value / 1000:,.1f} bn"
    if unit == "USD_mn":
        return f"${value / 1000:,.1f}bn" if value >= 1000 else f"${value:,.0f}M"
    if unit == "INR_k":
        return f"₹{value:,.1f}k"
    if unit == "beds":
        return f"{value:,.0f} beds"
    if unit == "%":
        return f"{value:.1f}%"
    return f"{value:,.2f} {unit}"


def pct(v: float, dp: int = 1, sign: bool = False) -> str:
    return f"{v:+.{dp}f}%" if sign else f"{v:.{dp}f}%"


def nice_date(s: str, precision: str = "day") -> str:
    d = periods.parse_date(s)
    if precision == "month" or len(s) == 7:
        return d.strftime("%b %Y")
    if precision == "quarter":
        q = (d.month - 1) // 3 + 1
        return f"Q{q} {d.year}"
    if len(s) == 4:
        return s
    return d.strftime("%d %b %Y").lstrip("0")


def days(a: str, b: str) -> int:
    return (periods.parse_date(b) - periods.parse_date(a)).days


def today() -> date:
    return date.today()
