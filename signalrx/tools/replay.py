"""Replay mode: load a recorded evidence bundle and serve it through the same
interfaces the live tools use (search -> sources -> evidence -> ledger).

A bundle is what the harvest + extraction stages produced on a live run. Replay
exists so a run can be audited and re-scored offline, and so the downstream
reasoning can be tested without network access.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..ledger import EvidenceLedger
from ..models import Claim, Event, Evidence, Guidance, Link, Observation, Source


@dataclass
class Bundle:
    path: Path
    raw: dict

    @classmethod
    def load(cls, path: str | Path) -> "Bundle":
        p = Path(path)
        with p.open(encoding="utf-8") as fh:
            return cls(p, yaml.safe_load(fh))

    @property
    def company(self) -> dict:
        return self.raw["company"]

    @property
    def profile_text(self) -> str:
        return self.raw.get("profile_text", "")


class ReplaySearch:
    """Answers a planner query with the sources the recorded run found for the
    closest recorded query (token overlap), so the agent trace shows what was
    actually searched and what came back."""

    def __init__(self, bundle: Bundle):
        self.log = bundle.raw.get("search_log", [])
        self.sources = {s["id"]: s for s in bundle.raw["sources"]}

    @staticmethod
    def _tokens(s: str) -> set[str]:
        return {t.lower().strip("'\".,") for t in s.split() if len(t) > 2}

    def search(self, query: str, angle: str | None = None) -> list[dict]:
        qt = self._tokens(query)
        best, best_score = None, 0.0
        for entry in self.log:
            et = self._tokens(entry["q"])
            score = len(qt & et) / max(1, len(qt | et)) + (0.25 if angle and entry.get("angle") == angle else 0)
            if score > best_score:
                best, best_score = entry, score
        if not best or best_score < 0.12:
            return []
        return [self.sources[s] | {"matched_query": best["q"]} for s in best["found"] if s in self.sources]

    def recorded(self) -> list[dict]:
        return list(self.log)


def load_into_ledger(bundle: Bundle) -> EvidenceLedger:
    raw = bundle.raw
    led = EvidenceLedger(company=raw["company"]["name"])
    for s in raw["sources"]:
        led.add_source(Source(**{k: s.get(k) for k in Source.__dataclass_fields__ if k in s}))
    for e in raw["evidence"]:
        led.add_evidence(Evidence(**e))
    for o in raw.get("observations", []):
        o = dict(o)
        o.setdefault("entity", led.company)
        o.setdefault("dims", {})
        led.observe(Observation(**o))
    for ev in raw.get("events", []):
        led.events.append(Event(**ev))
    for ln in raw.get("links", []):
        led.links.append(Link(**ln))
    for c in raw.get("claims", []):
        led.claims.append(Claim(**c))
    for g in raw.get("guidance", []):
        led.guidance.append(Guidance(**g))
    # Every referenced evidence id must exist: fail loudly on a broken bundle.
    for ev in led.events:
        for e in ev.evidence:
            if e not in led.evidence:
                raise KeyError(f"event '{ev.title}' cites unknown evidence {e}")
    for ln in led.links:
        for e in ln.evidence:
            if e not in led.evidence:
                raise KeyError(f"link {ln.b} cites unknown evidence {e}")
    led.log("harvest", "bundle-loaded",
            f"{len(led.sources)} sources, {len(led.evidence)} passages, {len(led.observations)} figures, "
            f"{len(led.events)} dated events from {bundle.path.name}")
    return led
