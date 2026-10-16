"""Inline-SVG charts. No JS libraries: the dashboard must work offline and print.

Colours are CSS custom properties (see the template), so light/dark mode swap
in one place. Every mark carries a <title> for native hover, plus data-tip for
the page's tooltip layer; every chart ships a data table.
"""
from __future__ import annotations

import html
import math

from .. import fmt, periods

SERIES = [f"var(--s{i})" for i in range(1, 9)]


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _nice_max(v: float) -> float:
    if v <= 0:
        return 1
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if v <= m * e:
            return m * e
    return 10 * e


def _table(headers: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<details class="viz-table"><summary>Data table</summary><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></details>'


def _legend(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span class="key"><i style="background:{c}"></i>{_esc(n)}</span>' for n, c in items) + "</div>"


def mix_bars(led, decliner: str, title: str) -> str:
    """100% stacked horizontal bars: revenue mix by period, decliner first."""
    segs = led.dim_values("revenue_segment", "segment")
    segs = [decliner] + [s for s in segs if s != decliner]
    rows = {}
    unit = "INR_mn"
    for o in led.observations:
        if o.entity == led.company and o.metric == "revenue_segment" and o.dims.get("segment") in segs:
            rows.setdefault(o.period, {})[o.dims["segment"]] = o.value
            unit = o.unit
    complete = sorted([p for p, r in rows.items() if all(s in r for s in segs)], key=periods.sort_key)
    # annual first, then quarters
    complete = [p for p in complete if periods.frequency(p) == "A"] + [p for p in complete if periods.frequency(p) == "Q"]
    W, row_h, bar_h, left, right = 1040, 36, 22, 72, 16
    H = 28 + row_h * len(complete)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}" class="chart">']
    for x in (0, 25, 50, 75, 100):
        px = left + (W - left - right) * x / 100
        out.append(f'<line x1="{px:.1f}" y1="14" x2="{px:.1f}" y2="{H - 6}" class="grid"/>'
                   f'<text x="{px:.1f}" y="10" class="tick" text-anchor="middle">{x}%</text>')
    table = []
    for i, p in enumerate(complete):
        y = 22 + i * row_h
        tot = sum(rows[p][s] for s in segs)
        x = left
        out.append(f'<text x="{left - 8}" y="{y + bar_h / 2 + 4}" class="lab" text-anchor="end">{_esc(p)}</text>')
        for j, s in enumerate(segs):
            share = 100 * rows[p][s] / tot
            w = (W - left - right) * share / 100
            gap = 2 if j < len(segs) - 1 else 0
            tip = f"{s} · {p}: {share:.1f}% ({fmt.money(rows[p][s], unit)})"
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(0, w - gap):.1f}" height="{bar_h}" rx="{4 if j in (0, len(segs) - 1) else 0}" '
                       f'fill="{SERIES[j]}" data-tip="{_esc(tip)}"><title>{_esc(tip)}</title></rect>')
            if j == 0 and w > 44:
                out.append(f'<text x="{x + 6:.1f}" y="{y + bar_h / 2 + 4}" class="inlab">{share:.1f}%</text>')
            x += w
        table.append([p] + [f"{100 * rows[p][s] / tot:.1f}%" for s in segs])
        if i == len([q for q in complete if periods.frequency(q) == "A"]) - 1 and i < len(complete) - 1:
            out.append(f'<line x1="8" y1="{y + row_h - 8}" x2="{W - 8}" y2="{y + row_h - 8}" class="sep"/>')
    out.append("</svg>")
    return (f'<figure><figcaption>{_esc(title)}</figcaption>{_legend([(s, SERIES[j]) for j, s in enumerate(segs)])}'
            + "".join(out) + _table(["Period"] + segs, table) + "</figure>")


def quarter_columns_with_events(led, segment: str, events: list[dict], title: str) -> str:
    """Quarterly columns for one segment with dated event markers on the same time axis."""
    q = led.series("revenue_segment", segment=segment, freq="Q")
    if not q:
        return ""
    unit = q[0].unit
    t0 = periods.end_date(q[0].period).toordinal() - 92
    t1 = max([periods.end_date(q[-1].period).toordinal()] + [periods.parse_date(e["date"]).toordinal() for e in events]) + 20
    W, H, left, right, top, base = 1040, 320, 64, 16, 76, 270
    sx = lambda t: left + (W - left - right) * (t - t0) / (t1 - t0)
    vmax = _nice_max(max(o.value for o in q) * 1.1)
    sy = lambda v: base - (base - top) * v / vmax
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}" class="chart">']
    for k in range(0, 5):
        v = vmax * k / 4
        out.append(f'<line x1="{left}" y1="{sy(v):.1f}" x2="{W - right}" y2="{sy(v):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{sy(v) + 4:.1f}" class="tick" text-anchor="end">{fmt.money(v, unit) if unit != "INR_mn" else f"{v / 10:,.0f}"}</text>')
    out.append(f'<line x1="{left}" y1="{base}" x2="{W - right}" y2="{base}" class="axis"/>')
    rows = []
    for o in q:
        end = periods.end_date(o.period).toordinal()
        x0, x1 = sx(end - 91), sx(end)
        cx = (x0 + x1) / 2
        bw = min(24, (x1 - x0) * 0.6)
        y = sy(o.value)
        tip = f"{segment} {o.period}: {fmt.money(o.value, unit)}" + (" (derived)" if o.derived else "")
        out.append(f'<path d="M{cx - bw / 2:.1f},{base} V{y + 4:.1f} q0,-4 4,-4 H{cx + bw / 2 - 4:.1f} q4,0 4,4 V{base} Z" '
                   f'fill="var(--s1)" data-tip="{_esc(tip)}"><title>{_esc(tip)}</title></path>')
        out.append(f'<text x="{cx:.1f}" y="{y - 6:.1f}" class="val" text-anchor="middle">{fmt.money(o.value, unit)}</text>')
        out.append(f'<text x="{cx:.1f}" y="{base + 16}" class="tick" text-anchor="middle">{_esc(o.period)}</text>')
        rows.append([o.period, fmt.money(o.value, unit), "derived" if o.derived else "reported"])
    lanes = [top - 50, top - 32, top - 14]
    for i, e in enumerate(events):
        x = sx(periods.parse_date(e["date"]).toordinal())
        ly = lanes[i % 3]
        out.append(f'<line x1="{x:.1f}" y1="{ly + 6}" x2="{x:.1f}" y2="{base}" class="evline"/>'
                   f'<circle cx="{x:.1f}" cy="{ly}" r="7" class="evdot" data-tip="{_esc(e["label"] + ": " + e["title"])}">'
                   f'<title>{_esc(e["label"] + ": " + e["title"])}</title></circle>'
                   f'<text x="{x:.1f}" y="{ly + 4}" class="evnum" text-anchor="middle">{i + 1}</text>')
    out.append("</svg>")
    key = '<ol class="evkey">' + "".join(f"<li><b>{_esc(e['label'])}</b> {_esc(e['title'])}</li>" for e in events) + "</ol>"
    return f'<figure><figcaption>{_esc(title)}</figcaption>' + "".join(out) + key + _table(["Quarter", segment, "basis"], rows) + "</figure>"


def growth_columns(series: dict[str, float], split_last: tuple[float, float] | None, title: str, peer: tuple[str, float] | None) -> str:
    """YoY unit-price growth by quarter; the last column optionally split into core + policy benefit."""
    items = list(series.items())
    W, H, left, right, top, base = 1040, 260, 48, 16, 28, 220
    vmax = _nice_max(max([v for _, v in items] + ([peer[1]] if peer else [])) * 1.15)
    sy = lambda v: base - (base - top) * v / vmax
    n = len(items) + (1 if peer else 0)
    slot = (W - left - right) / n
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}" class="chart">']
    for k in range(0, 5):
        v = vmax * k / 4
        out.append(f'<line x1="{left}" y1="{sy(v):.1f}" x2="{W - right}" y2="{sy(v):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{sy(v) + 4:.1f}" class="tick" text-anchor="end">{v:g}%</text>')
    out.append(f'<line x1="{left}" y1="{base}" x2="{W - right}" y2="{base}" class="axis"/>')
    rows = []
    for i, (p, v) in enumerate(items):
        cx = left + slot * (i + 0.5)
        bw = 28
        last = i == len(items) - 1 and split_last
        if last:
            core, ben = split_last
            yc, yt = sy(core), sy(core + ben)
            out.append(f'<rect x="{cx - bw / 2:.1f}" y="{yc:.1f}" width="{bw}" height="{base - yc:.1f}" fill="var(--s1)" '
                       f'data-tip="{_esc(f"{p} core: {core:+.1f}%")}"><title>{_esc(f"{p} core: {core:+.1f}%")}</title></rect>'
                       f'<path d="M{cx - bw / 2:.1f},{yc - 2:.1f} V{yt + 4:.1f} q0,-4 4,-4 H{cx + bw / 2 - 4:.1f} q4,0 4,4 V{yc - 2:.1f} Z" fill="var(--s2)" '
                       f'data-tip="{_esc(f"{p} policy tariff benefit: +{ben:.1f}pp")}"><title>{_esc(f"{p} policy tariff benefit: +{ben:.1f}pp")}</title></path>')
            rows.append([p, f"{v:+.1f}%", f"core {core:+.1f}% + benefit {ben:.1f}pp"])
        else:
            y = sy(max(v, 0))
            h = max(1.5, base - y)
            out.append(f'<path d="M{cx - bw / 2:.1f},{base} V{base - h + (4 if h > 6 else 0):.1f} {"q0,-4 4,-4" if h > 6 else ""} '
                       f'H{cx + bw / 2 - (4 if h > 6 else 0):.1f} {"q4,0 4,4" if h > 6 else ""} V{base} Z" fill="var(--s1)" '
                       f'data-tip="{_esc(f"{p}: {v:+.1f}%")}"><title>{_esc(f"{p}: {v:+.1f}%")}</title></path>')
            rows.append([p, f"{v:+.1f}%", ""])
        out.append(f'<text x="{cx:.1f}" y="{sy(v) - 6:.1f}" class="val" text-anchor="middle">{v:+.1f}%</text>'
                   f'<text x="{cx:.1f}" y="{base + 16}" class="tick" text-anchor="middle">{_esc(p)}</text>')
    if peer:
        cx = left + slot * (len(items) + 0.5)
        y = sy(peer[1])
        out.append(f'<path d="M{cx - 12:.1f},{base} V{y + 4:.1f} q0,-4 4,-4 H{cx + 8:.1f} q4,0 4,4 V{base} Z" fill="var(--muted)" '
                   f'data-tip="{_esc(f"Peer {peer[0]}: {peer[1]:+.1f}%")}"><title>{_esc(f"Peer {peer[0]}: {peer[1]:+.1f}%")}</title></path>'
                   f'<text x="{cx:.1f}" y="{y - 6:.1f}" class="val" text-anchor="middle">{peer[1]:+.0f}%</text>'
                   f'<text x="{cx:.1f}" y="{base + 16}" class="tick" text-anchor="middle">{_esc(peer[0])} (peer)</text>')
        rows.append([f"peer: {peer[0]}", f"{peer[1]:+.1f}%", "same quarter"])
    out.append("</svg>")
    leg = _legend([("Core growth", "var(--s1)"), ("Policy tariff benefit", "var(--s2)")] + ([("Peer (same quarter)", "var(--muted)")] if peer else [])) if split_last else ""
    return f'<figure><figcaption>{_esc(title)}</figcaption>{leg}' + "".join(out) + _table(["Quarter", "YoY", "note"], rows) + "</figure>"


def capacity_lines(led, facts: dict, title: str) -> str:
    ops = led.series("beds_operational")
    if not ops:
        return ""
    end = periods.end_date(facts["by"]).toordinal()
    t0 = periods.end_date(ops[0].period).toordinal() - 30
    W, H, left, right, top, base = 1040, 300, 64, 150, 20, 260
    sx = lambda t: left + (W - left - right) * (t - t0) / (end + 20 - t0)
    vmax = _nice_max(facts["target_high"] * 1.05)
    vmin = math.floor(min(o.value for o in ops) * 0.9 / 1000) * 1000
    sy = lambda v: base - (base - top) * (v - vmin) / (vmax - vmin)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}" class="chart">']
    for k in range(0, 5):
        v = vmin + (vmax - vmin) * k / 4
        out.append(f'<line x1="{left}" y1="{sy(v):.1f}" x2="{W - right}" y2="{sy(v):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{sy(v) + 4:.1f}" class="tick" text-anchor="end">{v / 1000:.1f}K</text>')
    pts = [(sx(periods.end_date(o.period).toordinal()), sy(o.value), o) for o in ops]
    last = pts[-1]
    # required path band (low..high target): area wash + 2px lines
    xe = sx(end)
    out.append(f'<path d="M{last[0]:.1f},{last[1]:.1f} L{xe:.1f},{sy(facts["target_high"]):.1f} L{xe:.1f},{sy(facts["target_low"]):.1f} Z" '
               f'fill="var(--s2)" opacity="0.12"/>')
    for tv, nm in ((facts["target_low"], "low"), (facts["target_high"], "high")):
        out.append(f'<line x1="{last[0]:.1f}" y1="{last[1]:.1f}" x2="{xe:.1f}" y2="{sy(tv):.1f}" stroke="var(--s2)" stroke-width="2" stroke-linecap="round"/>'
                   f'<circle cx="{xe:.1f}" cy="{sy(tv):.1f}" r="5" fill="var(--s2)" stroke="var(--surface)" stroke-width="2" '
                   f'data-tip="Target ({nm}) by {facts["by"]}: {tv:,.0f} beds"><title>Target ({nm}) by {facts["by"]}: {tv:,.0f}</title></circle>'
                   f'<text x="{xe + 9:.1f}" y="{sy(tv) + 4:.1f}" class="lab">{tv:,.0f}</text>')
    # trailing pace extrapolated
    pace_end = last[2].value + facts["trailing"] * facts["months_left"] / 12
    out.append(f'<line x1="{last[0]:.1f}" y1="{last[1]:.1f}" x2="{xe:.1f}" y2="{sy(pace_end):.1f}" stroke="var(--s1)" stroke-width="2" '
               f'stroke-opacity="0.45" stroke-linecap="round"/>'
               f'<text x="{xe + 9:.1f}" y="{sy(pace_end) + 4:.1f}" class="lab">{pace_end:,.0f} at current pace</text>')
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
    out.append(f'<path d="{d}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for x, y, o in pts:
        tip = f"{o.period}: {o.value:,.0f} operational beds" + (" (derived)" if o.derived else "")
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="var(--s1)" stroke="var(--surface)" stroke-width="2" data-tip="{_esc(tip)}"><title>{_esc(tip)}</title></circle>'
                   f'<text x="{x:.1f}" y="{base + 16}" class="tick" text-anchor="middle">{_esc(o.period)}</text>')
    out.append(f'<text x="{xe:.1f}" y="{base + 16}" class="tick" text-anchor="middle">{_esc(facts["by"])}</text>')
    out.append(f'<line x1="{left}" y1="{base}" x2="{W - right}" y2="{base}" class="axis"/></svg>')
    rows = [[o.period, f"{o.value:,.0f}", "derived" if o.derived else "reported"] for o in ops]
    rows += [[facts["by"], f"{facts['target_low']:,.0f}–{facts['target_high']:,.0f}", "target"], [facts["by"], f"{pace_end:,.0f}", "at trailing pace"]]
    return (f'<figure><figcaption>{_esc(title)}</figcaption>'
            + _legend([("Operational beds (actual, then trailing pace)", "var(--s1)"), ("Path to stated target range", "var(--s2)")])
            + "".join(out) + _table(["Period", "Beds", "basis"], rows) + "</figure>")


def for_discovery(led, h, d) -> str:
    f = h.facts
    if h.angle in ("replacement_concentration", "geo_mix_shift"):
        return mix_bars(led, f["segment"], f"Revenue mix by segment: {f['segment']} share, annual then quarterly")
    if h.angle == "counterparty_regulatory":
        evs = [{"date": e["date"], "label": fmt.nice_date(e["date"]), "title": e["title"]} for e in
               sorted([{"date": led.events[i].date, "title": led.events[i].title} for i in h.events]
                      + [{"date": ev.date, "title": ev.title} for ev in led.events if ev.kind == "loe" and f.get("segment") in ev.tags],
                      key=lambda e: periods.parse_date(e["date"]))]
        seen, uniq = set(), []
        for e in evs:
            if e["title"] not in seen:
                seen.add(e["title"])
                uniq.append(e)
        return quarter_columns_with_events(led, f["segment"], uniq, f"{f['segment']} quarterly revenue with dated events")
    if h.angle == "payor_policy":
        ser = f.get("arpob_series", {})
        split = None
        if "arpob_core_growth" in f and f.get("benefit"):
            split = (f["arpob_core_growth"], f["benefit"]["pct_of_q_revenue"])
        peer = (f["peer_unit_price"][0]["peer"], f["peer_unit_price"][0]["value"]) if f.get("peer_unit_price") else None
        return growth_columns(ser, split, "ARPOB growth, year on year (last quarter split: core vs policy tariff benefit)", peer)
    if h.angle == "capacity_gap":
        return capacity_lines(led, f, "Operational beds vs the stated target")
    return ""
