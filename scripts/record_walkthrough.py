"""Record a silent ~100 s dashboard walkthrough (docs/demo/walkthrough.webm) as a timing reference for the demo video."""
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signalrx.render.dashboard import launch_browser  # noqa: E402

OUT = ROOT / "docs/demo"


def smooth(page, to, steps=30, wait=25):
    y0 = page.evaluate("window.scrollY")
    for i in range(1, steps + 1):
        page.evaluate(f"window.scrollTo(0, {y0 + (to - y0) * i / steps})")
        page.wait_for_timeout(wait)


def y_of(page, sel):
    return page.evaluate(f"document.querySelector({sel!r}).getBoundingClientRect().top + window.scrollY - 70")


with sync_playwright() as pw:
    b = launch_browser(pw)
    ctx = b.new_context(viewport={"width": 1280, "height": 720}, record_video_dir=str(OUT / "raw"),
                        record_video_size={"width": 1280, "height": 720})
    p = ctx.new_page()
    p.goto((ROOT / "runs/cipla/dashboard.html").as_uri())
    p.wait_for_timeout(5000)                                   # discovery sentence
    smooth(p, y_of(p, ".tiles")); p.wait_for_timeout(4000)      # key numbers
    smooth(p, y_of(p, "figure")); p.wait_for_timeout(2000)
    for dot in p.locator("svg circle.evdot").all()[:4]:         # hover dated events
        dot.hover(); p.wait_for_timeout(1300)
    smooth(p, y_of(p, ".counter")); p.wait_for_timeout(6000)    # why / counterargument / falsification
    smooth(p, y_of(p, ".timeline")); p.wait_for_timeout(5000)
    p.locator(".timeline a.cite").first.click(); p.wait_for_timeout(3500)   # click-through to evidence
    smooth(p, y_of(p, ".filters")); p.locator('.filters button[data-f="opposes"]').click(); p.wait_for_timeout(3500)
    p.locator('.filters button[data-f="all"]').click()
    smooth(p, y_of(p, "table")); p.wait_for_timeout(1000)
    p.evaluate("window.scrollTo(0, document.querySelector('.verdict').getBoundingClientRect().top + window.scrollY - 200)")
    p.wait_for_timeout(6000)                                   # skeptic table
    p.evaluate("window.scrollTo(0, document.body.scrollHeight - 1800)"); p.wait_for_timeout(4000)  # calculations/hypotheses
    for name in ("drreddy", "maxhealth"):
        p.goto((ROOT / f"runs/{name}/dashboard.html").as_uri()); p.wait_for_timeout(5500)
        smooth(p, y_of(p, "figure")); p.wait_for_timeout(4000)
    video = p.video
    ctx.close()
    video.save_as(str(OUT / "walkthrough.webm"))
    b.close()
shutil.rmtree(OUT / "raw", ignore_errors=True)
print(OUT / "walkthrough.webm")
