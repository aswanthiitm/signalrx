"""Quantitative scan: turns the numbers in the ledger into anomalies worth investigating.

The scan is deliberately dumb and exhaustive. It does not decide what the
story is; it flags every place where the numbers move enough that a story
probably exists, and records each derived figure as a Calculation so it can be
audited later.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import periods
from ..ledger import EvidenceLedger
from . import metrics as M


@dataclass
class Anomaly:
    type: str
    subject: str
    magnitude: float
    description: str
    periods: list[str] = field(default_factory=list)
    calcs: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


def _segment_tables(led: EvidenceLedger, metric: str = "revenue_segment"):
    """Yield (freq, unit, {period: {segment: Observation}}) for complete periods."""
    segs = led.dim_values(metric, "segment")
    by: dict[tuple[str, str], dict[str, dict]] = {}
    for o in led.observations:
        if o.entity != led.company or o.metric != metric or "segment" not in o.dims or len(o.dims) != 1:
            continue
        f = periods.frequency(o.period)
        by.setdefault((f, o.unit), {}).setdefault(o.period, {})[o.dims["segment"]] = o
    for (f, unit), table in by.items():
        full = {p: row for p, row in table.items() if len(row) >= max(3, len(segs) - 1)}
        if len(full) >= 2:
            yield f, unit, dict(sorted(full.items(), key=lambda kv: periods.sort_key(kv[0])))


def mix_shift(led: EvidenceLedger, threshold_pp: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    for freq, unit, table in _segment_tables(led):
        # Only compare segments present in every complete period.
        common = set.intersection(*(set(r) for r in table.values()))
        share_rows: dict[str, dict[str, tuple[float, str]]] = {}
        for p, row in table.items():
            total = sum(row[s].value for s in common)
            share_rows[p] = {}
            for s in common:
                v = M.pct(row[s].value, total)
                c = led.calc(f"{s} share of revenue, {p}", f"{s} / sum(segments) x 100", v, "%",
                             [row[x] for x in sorted(common)])
                share_rows[p][s] = (v, c.id)
        ps = list(share_rows)
        for s in common:
            seq = [share_rows[p][s][0] for p in ps]
            first, last = seq[0], seq[-1]
            peak_i = max(range(len(seq)), key=lambda i: seq[i]) if last < first else min(range(len(seq)), key=lambda i: seq[i])
            d = last - first
            d_peak = last - seq[peak_i]
            if max(abs(d), abs(d_peak)) >= threshold_pp:
                out.append(Anomaly(
                    "mix_shift", s, d_peak if abs(d_peak) > abs(d) else d,
                    f"{s} share moved {first:.1f}% ({ps[0]}) -> {seq[peak_i]:.1f}% ({ps[peak_i]}) -> {last:.1f}% ({ps[-1]}) [{freq}, {unit}]",
                    ps, [share_rows[p][s][1] for p in ps],
                    {"freq": freq, "unit": unit, "series": dict(zip(ps, seq)), "peak_period": ps[peak_i],
                     "share_calc": {p: share_rows[p][s][1] for p in ps}}))
    return out


def segment_drops(led: EvidenceLedger, threshold_pct: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    for seg in led.dim_values("revenue_segment", "segment"):
        for unit in {o.unit for o in led.series("revenue_segment", segment=seg)}:
            ser = [o for o in led.series("revenue_segment", segment=seg) if o.unit == unit]
            idx = {o.period: o for o in ser}
            for o in ser:
                prev = idx.get(periods.prior_year(o.period))
                if not prev:
                    continue
                g = M.growth(o.value, prev.value)
                c = led.calc(f"{seg} revenue growth YoY, {o.period}", f"({o.period} / {prev.period} - 1) x 100",
                             g, "%", [o, prev])
                if g <= -threshold_pct:
                    out.append(Anomaly("segment_drop", seg, g,
                                       f"{seg} revenue {g:+.1f}% YoY in {o.period} ({prev.value:,.0f} -> {o.value:,.0f} {unit})",
                                       [prev.period, o.period], [c.id], {"unit": unit}))
            # Sequential step-downs in quarterly data.
            q = [o for o in ser if periods.frequency(o.period) == "Q"]
            adjacent = [i for i in range(1, len(q)) if periods.next_quarter(q[i - 1].period) == q[i].period]
            if len(q) >= 3 and adjacent:
                i = min(adjacent, key=lambda j: M.growth(q[j].value, q[j - 1].value))
                rel = M.growth(q[i].value, q[i - 1].value)
                if rel <= -threshold_pct:
                    c = led.calc(f"{seg} sequential change, {q[i - 1].period}->{q[i].period}",
                                 "(Qt / Qt-1 - 1) x 100", rel, "%", [q[i], q[i - 1]])
                    out.append(Anomaly("segment_step_down", seg, rel,
                                       f"{seg} fell {rel:.1f}% sequentially {q[i - 1].period}->{q[i].period} and has not recovered "
                                       f"(latest {q[-1].value:,.0f} {unit})",
                                       [o.period for o in q], [c.id],
                                       {"unit": unit, "before": q[i - 1].value, "after": q[i].value, "step_period": q[i].period,
                                        "latest": q[-1].value, "latest_period": q[-1].period}))
    return out


def growth_gaps(led: EvidenceLedger, threshold_pp: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    by_period: dict[str, dict[str, object]] = {}
    for o in led.observations:
        if o.entity == led.company and o.metric == "revenue_growth_yoy" and "basis" in o.dims:
            by_period.setdefault(o.period, {})[o.dims["basis"]] = o
    for p, row in by_period.items():
        rep = row.get("reported")
        if not rep:
            continue
        for basis, o in row.items():
            if basis == "reported":
                continue
            gap = o.value - rep.value
            c = led.calc(f"Growth gap {basis} vs reported, {p}", f"growth[{basis}] - growth[reported]", gap, "pp", [o, rep])
            if abs(gap) >= threshold_pp:
                out.append(Anomaly("growth_gap", basis, gap,
                                   f"Growth {basis.replace('_', ' ')} was {o.value:.0f}% vs {rep.value:.0f}% reported in {p} (gap {gap:+.0f}pp)",
                                   [p], [c.id], {"basis": basis, "reported": rep.value, "adjusted": o.value}))
    return out


def share_shifts(led: EvidenceLedger, metric: str, dim: str, threshold_pp: float = 2.0) -> list[Anomaly]:
    out: list[Anomaly] = []
    for val in led.dim_values(metric, dim):
        ser = led.series(metric, **{dim: val})
        if len(ser) >= 2:
            d = ser[-1].value - ser[0].value
            c = led.calc(f"{val} share change {ser[0].period}->{ser[-1].period}", "share[t] - share[t0]", d, "pp", [ser[-1], ser[0]])
            if abs(d) >= threshold_pp:
                out.append(Anomaly("specialty_share_shift", val, d,
                                   f"{val} share of {metric.replace('_', ' ')} moved {ser[0].value:.0f}% -> {ser[-1].value:.0f}% "
                                   f"({ser[0].period} -> {ser[-1].period})", [ser[0].period, ser[-1].period], [c.id],
                                   {"from": ser[0].value, "to": ser[-1].value}))
    return out


def unit_price_trend(led: EvidenceLedger, metric: str = "arpob") -> list[Anomaly]:
    """Flag deceleration in a per-unit price series (ARPOB, revenue per patient)."""
    out: list[Anomaly] = []
    g_obs = led.series(f"{metric}_growth_yoy")
    if len(g_obs) >= 2:
        vals = [o.value for o in g_obs]
        low = min(range(len(vals)), key=lambda i: vals[i])
        if vals[low] <= 1.0:
            out.append(Anomaly("unit_price_stall", metric.upper(), vals[low],
                               f"{metric.upper()} growth fell to {vals[low]:+.1f}% YoY in {g_obs[low].period} "
                               f"(series: " + ", ".join(f"{o.period} {o.value:+.1f}%" for o in g_obs) + ")",
                               [o.period for o in g_obs], [], {"series": {o.period: o.value for o in g_obs}}))
    return out


def capacity_gaps(led: EvidenceLedger, accel_threshold: float) -> list[Anomaly]:
    out: list[Anomaly] = []
    ops = led.series("beds_operational")
    if len(ops) < 2:
        return out
    latest = ops[-1]
    prev_year = led.get("beds_operational", periods.prior_year(latest.period))
    if prev_year is None:
        return out
    trailing = latest.value - prev_year.value
    c_tr = led.calc(f"Net beds added in 12 months to {latest.period}", "beds[t] - beds[t-1y]", trailing, "beds", [latest, prev_year])
    for tgt in [o for o in led.observations if o.metric == "beds_target" and o.entity == led.company]:
        by = tgt.dims.get("by")
        if not by:
            continue
        months = periods.months_between(periods.end_date(latest.period), periods.end_date(by))
        need = M.required_run_rate(tgt.value, latest.value, months)
        c_need = led.calc(f"Required net beds per year to reach {tgt.value:,.0f} by {by} ({tgt.dims.get('bound', 'point')})",
                          "(target - current) x 12 / months_left", need, "beds/yr", [tgt, latest])
        accel = need / trailing if trailing > 0 else float("inf")
        c_acc = led.calc(f"Acceleration needed vs trailing run-rate ({tgt.dims.get('bound', 'point')})",
                         "required / trailing", accel, "x", [c_need, c_tr])
        if accel >= accel_threshold:
            out.append(Anomaly("capacity_gap", f"beds by {by}", accel,
                               f"Target {tgt.value:,.0f} beds by {by} needs {need:,.0f}/yr vs {trailing:,.0f} added in the last 12 months "
                               f"({accel:.1f}x acceleration)", [prev_year.period, latest.period, by],
                               [c_tr.id, c_need.id, c_acc.id],
                               {"target": tgt.value, "bound": tgt.dims.get("bound"), "need": need, "trailing": trailing,
                                "current": latest.value, "current_period": latest.period, "by": by, "months_left": months}))
    return out


def run_scan(led: EvidenceLedger, materiality: dict) -> list[Anomaly]:
    found: list[Anomaly] = []
    found += mix_shift(led, materiality["mix_shift_pp"])
    found += segment_drops(led, materiality["segment_drop_pct"])
    found += growth_gaps(led, materiality["growth_gap_pp"])
    found += share_shifts(led, "specialty_share_ip", "specialty")
    found += unit_price_trend(led)
    found += capacity_gaps(led, materiality["capacity_acceleration"])
    for a in found:
        led.log("scan", f"anomaly:{a.type}", a.description, a.calcs)
    return found
