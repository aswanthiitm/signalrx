"""Command line.

    python -m signalrx replay bundles/<bundle>.yaml --out runs/<name> [--pdf]
    python -m signalrx run "<company name or NSE symbol>" --out runs/<name> [--pdf]       (live: needs network + ANTHROPIC_API_KEY)
    python -m signalrx universe                                       (resolve the track universe from NSE index files)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _finish(result, out: Path, pdf: bool) -> None:
    from .agent.orchestrator import save
    from .render.dashboard import write_html, write_pdf
    save(result, out)
    html = write_html(result, out)
    print(f"dashboard: {html}")
    if pdf:
        print(f"report:    {write_pdf(html)}")
    for d in result.discoveries:
        v = d["verification"]
        print(f"\n[{d['role']}] {d['strength']['grade']} | verifier {'PASS' if v['passed'] else 'FAIL'} "
              f"({v['numbers_checked']} numbers, {v['citations_checked']} citations)\n  {d['discovery']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="signalrx", description="Autonomous financial research agent, Track D (Pharma & Healthcare)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="re-run the reasoning pipeline on a recorded evidence bundle (offline)")
    r.add_argument("bundle")
    r.add_argument("--out", default=None)
    r.add_argument("--pdf", action="store_true")
    l = sub.add_parser("run", help="live run: research a company from the public internet")
    l.add_argument("company", help="company name or NSE symbol")
    l.add_argument("--out", default=None)
    l.add_argument("--pdf", action="store_true")
    l.add_argument("--model", default="claude-opus-5-5")
    l.add_argument("--budget", type=int, default=40, help="max tool calls for the investigator")
    sub.add_parser("universe", help="list the track's companies from the official index files")
    a = ap.parse_args(argv)

    if a.cmd == "replay":
        from .agent.orchestrator import run_replay
        res = run_replay(a.bundle)
        _finish(res, Path(a.out or f"runs/{Path(a.bundle).stem}"), a.pdf)
    elif a.cmd == "run":
        from .agent.live import run_live
        res = run_live(a.company, model=a.model, budget=a.budget)
        _finish(res, Path(a.out or f"runs/{a.company.replace(' ', '_').lower()}_live"), a.pdf)
    elif a.cmd == "universe":
        from .tools.sources import track_universe
        for row in track_universe():
            print(f"{row['symbol']:<12} {row['name']:<45} {row['index']}")
    else:  # pragma: no cover
        ap.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
