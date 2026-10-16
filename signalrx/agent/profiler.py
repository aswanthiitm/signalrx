"""Who is this company, structurally? Classifies the business archetype from the
company's own description and the segment names in its filings, so the same
code routes a formulations exporter, an API/CDMO and a hospital chain to the
right KPIs and angles without any per-company configuration."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..knowledge import load_pack
from ..ledger import EvidenceLedger


@dataclass
class CompanyProfile:
    name: str
    symbol: str
    archetype: str
    archetype_scores: dict[str, float]
    segments: list[str] = field(default_factory=list)
    counterparties: list[str] = field(default_factory=list)
    peers: list[str] = field(default_factory=list)


def classify(text: str) -> dict[str, float]:
    pack = load_pack()
    t = text.lower()
    scores: dict[str, float] = {}
    for name, spec in pack["archetypes"].items():
        hits = sum(len(re.findall(r"\b" + re.escape(k.lower()) + r"\b", t)) for k in spec["keywords"])
        scores[name] = hits / max(1, len(spec["keywords"])) ** 0.5
    total = sum(scores.values()) or 1.0
    return {k: round(v / total, 3) for k, v in sorted(scores.items(), key=lambda kv: -kv[1])}


def profile(led: EvidenceLedger, symbol: str, description: str) -> CompanyProfile:
    segs = led.dim_values("revenue_segment", "segment")
    metric_names = " ".join({o.metric for o in led.observations if o.entity == led.company})
    scores = classify(description + " " + " ".join(segs) + " " + metric_names.replace("_", " "))
    archetype = next(iter(scores))
    cps = sorted({ln.b for ln in led.links if ln.a == "company"})
    peers = sorted({o.entity.split(":", 1)[1] for o in led.observations if o.entity.startswith("peer:")})
    p = CompanyProfile(led.company, symbol, archetype, scores, segs, cps, peers)
    led.log("profile", "archetype", f"{archetype} (scores {scores}); segments {segs or '-'}; "
            f"counterparties {cps or '-'}; peers {peers or '-'}")
    return p
