"""Smoke-test every live connector against the real endpoints. Run on an open network before a demo.

    python scripts/smoke_live.py [SYMBOL]

Prints PASS/FAIL per connector with a short sample, so parser drift (a site
changing its HTML or JSON) is caught before the judges are watching.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signalrx.tools import sources as S  # noqa: E402
from signalrx.tools.http import Fetcher  # noqa: E402


def check(name, fn):
    try:
        out = fn()
        ok = bool(out)
        sample = out[:2] if isinstance(out, list) else (list(out.items())[:2] if isinstance(out, dict) else out)
        print(f"{'PASS' if ok else 'EMPTY'}  {name}: {str(sample)[:160]}")
        return ok
    except Exception as e:  # report and continue: we want the whole picture
        print(f"FAIL  {name}: {type(e).__name__}: {e}")
        return False


def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "CIPLA"
    f = Fetcher(cache_dir=Path(".cache/smoke"))
    results = [
        check("index files (universe)", lambda: S.track_universe(f)),
        check("screener search", lambda: S.screener_search(sym, f)),
        check("screener statements", lambda: S.screener_company(sym, f).get("quarters")),
        check("nse announcements", lambda: S.nse_announcements(sym, f)),
        check("openfda recalls", lambda: S.openfda_recalls(sym.title(), f)),
        check("openfda approvals", lambda: S.approvals_by_year(S.openfda_approvals(sym, f))),
        check("clinicaltrials.gov", lambda: S.ctgov_trials(sym.title(), f)),
        check("news rss", lambda: S.news_rss(sym, f)),
    ]
    try:
        import anthropic
        anthropic.Anthropic().models.retrieve("claude-opus-5-5")
        print("PASS  anthropic api: model reachable")
    except Exception as e:
        print(f"FAIL  anthropic api: {e}")
    print(f"\n{sum(results)}/{len(results)} connectors returned data")


if __name__ == "__main__":
    main()
