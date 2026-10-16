"""Pure calculators. No I/O, no LLM: every derived number in a report comes from here."""
from __future__ import annotations

import math
from typing import Iterable, Mapping


def pct(a: float, b: float) -> float:
    """a as a percentage of b."""
    if b == 0:
        raise ZeroDivisionError("denominator is zero")
    return 100.0 * a / b


def growth(curr: float, prev: float) -> float:
    """Percentage change from prev to curr."""
    if prev == 0:
        raise ZeroDivisionError("base is zero")
    return 100.0 * (curr - prev) / prev


def implied_prior(curr: float, growth_pct: float) -> float:
    """Back out last period's value from this period's value and its reported growth."""
    return curr / (1.0 + growth_pct / 100.0)


def cagr(first: float, last: float, years: float) -> float:
    if first <= 0 or years <= 0:
        raise ValueError("cagr needs positive first value and years")
    return 100.0 * ((last / first) ** (1.0 / years) - 1.0)


def shares(parts: Mapping[str, float]) -> dict[str, float]:
    total = sum(parts.values())
    return {k: pct(v, total) for k, v in parts.items()}


def hhi(parts: Mapping[str, float]) -> float:
    """Herfindahl index on 0-10,000 scale: concentration of a revenue mix."""
    return sum(s * s for s in shares(parts).values())


def contribution_to_growth(prev: Mapping[str, float], curr: Mapping[str, float]) -> dict[str, float]:
    """Percentage-point contribution of each part to total growth."""
    base = sum(prev.values())
    return {k: 100.0 * (curr.get(k, 0.0) - prev.get(k, 0.0)) / base for k in set(prev) | set(curr)}


def required_run_rate(target: float, current: float, months_left: float) -> float:
    """Units per year needed to go from current to target in the time left."""
    if months_left <= 0:
        return math.inf
    return (target - current) * 12.0 / months_left


def days_between(a, b) -> int:
    return (b - a).days


def receivable_days(receivables: float, revenue: float, days: int = 365) -> float:
    return receivables / revenue * days


def zscores(xs: Iterable[float]) -> list[float]:
    xs = list(xs)
    if len(xs) < 2:
        return [0.0] * len(xs)
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return [0.0 if sd == 0 else (x - m) / sd for x in xs]


def largest_step(values: list[float]) -> tuple[int, float]:
    """Index (into values[1:]) and size of the largest absolute period-on-period step."""
    if len(values) < 2:
        return -1, 0.0
    steps = [values[i] - values[i - 1] for i in range(1, len(values))]
    i = max(range(len(steps)), key=lambda j: abs(steps[j]))
    return i + 1, steps[i]
