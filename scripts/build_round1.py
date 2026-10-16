"""Build the Round 1 solution report PDF (max 2 pages).

    python scripts/build_round1.py --team "YourTeam" --members "A · B · C · D" --repo github.com/you/signalrx
Writes docs/<Team>_TrackD_Round1.pdf and fails if it is longer than 2 pages.
"""
import argparse
import base64
import sys
from pathlib import Path

from jinja2 import Template
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signalrx.render.dashboard import launch_browser  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="TeamName")
    ap.add_argument("--members", default="Member 1 · Member 2 · Member 3 · Member 4")
    ap.add_argument("--repo", default="github.com/<team>/signalrx")
    a = ap.parse_args()
    thumb = base64.b64encode((ROOT / "docs/dashboard_thumb.png").read_bytes()).decode()
    html = Template((ROOT / "docs/round1/report.html.j2").read_text(encoding="utf-8")).render(
        team=a.team, members=a.members, repo=a.repo, thumb=thumb)
    src = ROOT / "docs/round1/report.html"
    src.write_text(html, encoding="utf-8")
    out = ROOT / f"docs/{a.team.replace(' ', '')}_TrackD_Round1.pdf"
    with sync_playwright() as pw:
        b = launch_browser(pw)
        p = b.new_page()
        p.goto(src.as_uri())
        p.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
        b.close()
    n = len(PdfReader(str(out)).pages)
    print(f"{out} ({n} pages)")
    if n > 2:
        sys.exit("report exceeds 2 pages")


if __name__ == "__main__":
    main()
