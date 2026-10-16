"""No company-specific tuning: the package source and the knowledge pack must not
name any listed company, product partner or bundle-specific id."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "signalrx"
BANNED = ["reddy", "cipla", "sun pharma", "sunpharma", "apollo", "max health", "maxhealth", "lupin", "divi", "zydus", "torrent",
          "aurobindo", "mankind", "glenmark", "biocon", "fortis", "narayana", "onesource", "pharmathen", "haleon", "kalinga"]
BUNDLE_IDS = re.compile(r'["\'](E\d\d|F\d\d|H\d\d|S\d\d|C\d\d|M\d\d)["\']')


def test_no_company_names_in_code_or_pack():
    hits = []
    for p in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.yaml")) + list(ROOT.rglob("*.j2")):
        text = p.read_text(encoding="utf-8").lower()
        for name in BANNED:
            if re.search(r"\b" + re.escape(name) + r"('s)?\b", text):
                hits.append(f"{p.relative_to(ROOT)}: {name}")
    assert not hits, hits


def test_no_bundle_ids_hardcoded():
    hits = [f"{p.name}: {m.group(0)}" for p in ROOT.rglob("*.py") for m in BUNDLE_IDS.finditer(p.read_text())]
    assert not hits, hits
