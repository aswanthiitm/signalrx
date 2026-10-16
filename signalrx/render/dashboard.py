"""Render a run as a self-contained HTML dashboard (and, via Chromium, a PDF report)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..agent.orchestrator import RunResult
from ..models import to_dict
from . import charts

TEMPLATES = Path(__file__).with_name("templates")


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))


def render_html(result: RunResult) -> str:
    led = result.ledger
    if not result.discoveries:
        raise ValueError("run produced no discovery to render")
    hyps = {h.id: h for h in result.hypotheses}
    d = result.discoveries[0]
    h = hyps[d["hypothesis"]]
    secondaries = []
    for s in result.discoveries[1:]:
        hs = hyps[s["hypothesis"]]
        secondaries.append({"d": s, "chart": charts.for_discovery(led, hs, s)})
    families = sorted({it["family"] for it in d["evidence_trail"]})
    trail_types = sorted({it["type"] for it in d["evidence_trail"]})
    tried = {x.angle for x in result.hypotheses}
    untested = [{"angle": t.angle, "note": next((s.detail for s in led.trace if s.action in (f"none:{t.angle}", f"skip:{t.angle}")), "")}
                for t in result.plan if t.angle not in tried]
    audit = [s.detail for s in led.trace if s.stage == "verify" and s.action in ("growth-recompute:drop", "conflict-resolved")]
    return _env().get_template("dashboard.html.j2").render(
        company=result.company, run={"started": result.started, "mode": result.mode}, profile=result.profile,
        d=d, chart=charts.for_discovery(led, h, d), secondaries=secondaries,
        disc_cites=d["discovery_evidence"], n_families=len(families), trail_types=trail_types,
        hypotheses=[to_dict(x) | {"scores": x.scores, "facts": x.facts} for x in sorted(result.hypotheses, key=lambda x: -x.scores.get("total", 0))],
        untested=untested, anomalies=result.anomalies, plan=result.plan, audit=audit,
        trace=led.trace, searches=result.search_trace, n_sources=len(led.sources))


def write_html(result: RunResult, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "dashboard.html"
    p.write_text(render_html(result), encoding="utf-8")
    return p


def launch_browser(pw):
    """Use a system/preinstalled Chromium if present (SIGNALRX_CHROMIUM or /opt/pw-browsers/chromium), else Playwright's."""
    import os
    exe = os.environ.get("SIGNALRX_CHROMIUM") or ("/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
    return pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()


def write_pdf(html_path: str | Path, pdf_path: str | Path | None = None) -> Path:
    """Print the dashboard to an A4 research-note PDF with headless Chromium."""
    from playwright.sync_api import sync_playwright
    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path or html_path.with_suffix(".pdf"))
    with sync_playwright() as pw:
        browser = launch_browser(pw)
        page = browser.new_page()
        page.emulate_media(color_scheme="light", media="print")
        page.goto(html_path.as_uri())
        page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"})
        browser.close()
    return pdf_path
