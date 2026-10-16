"""The research loop.

    harvest -> verify inputs -> profile -> scan -> plan -> investigate
            -> hypothesise -> challenge -> score -> select -> write -> verify output -> render

The same loop runs in two modes:
  * replay: harvest comes from a recorded evidence bundle (no network, no LLM);
  * live:   harvest and investigation use the live toolbox and an LLM
            (see agent/live.py), then feed the identical downstream stages.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import periods
from ..analytics.scan import Anomaly, run_scan
from ..knowledge import load_pack
from ..ledger import EvidenceLedger
from ..models import Hypothesis, to_dict
from . import hypotheses as H
from . import planner, profiler, scorer, skeptic, verifier, writer


@dataclass
class RunResult:
    company: dict
    profile: profiler.CompanyProfile
    ledger: EvidenceLedger
    anomalies: list[Anomaly]
    plan: list[planner.Task]
    hypotheses: list[Hypothesis]
    discoveries: list[dict]
    verification: list[dict]
    mode: str
    started: str
    finished: str = ""
    search_trace: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "company": self.company, "mode": self.mode, "started": self.started, "finished": self.finished,
            "profile": to_dict(self.profile),
            "anomalies": [a.__dict__ for a in self.anomalies],
            "plan": [t.__dict__ for t in self.plan],
            "search_trace": self.search_trace,
            "hypotheses": [to_dict(h) for h in self.hypotheses],
            "discoveries": self.discoveries,
            "verification": self.verification,
            "ledger": self.ledger.to_json(),
        }


def _as_of_fy(as_of: str) -> int:
    d = periods.parse_date(as_of)
    return d.year + 1 if d.month >= 4 else d.year


def run_ledger(led: EvidenceLedger, company: dict, description: str, search=None, mode: str = "replay",
               llm=None, adversary=None) -> RunResult:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    H.reset_ids()
    pack = load_pack()

    # Inputs: recompute reported growth from levels, then resolve conflicts.
    verifier.recompute_growth(led)
    led.resolve_conflicts()

    prof = profiler.profile(led, company.get("symbol", ""), description)
    anoms = run_scan(led, pack["materiality"])
    tasks = planner.plan(led, prof, anoms, _as_of_fy(company.get("as_of", datetime.now().date().isoformat())))

    trace: list[dict] = []
    if search is not None:
        for t in tasks:
            for q in t.queries:
                hits = search.search(q, angle=t.angle)
                trace.append({"angle": t.angle, "query": q, "matched": hits[0].get("matched_query") if hits else None,
                              "results": [{"id": h["id"], "title": h["title"], "publisher": h["publisher"], "url": h["url"]} for h in hits]})
                led.log("investigate", f"search:{t.angle}", f"\"{q}\" -> {len(hits)} sources", [h["id"] for h in hits])

    hyps = H.generate(led, anoms, [t.angle for t in tasks])
    if llm is not None:
        from .llm_agent import propose_open_hypotheses
        hyps += propose_open_hypotheses(llm, led, prof, anoms)
    for h in hyps:
        skeptic.challenge(led, h)
        scorer.score(led, h)
    if adversary is not None:
        # Live mode: send an adversarial investigator after the two leading hypotheses, then re-test them.
        for h in sorted(hyps, key=lambda x: -x.scores["total"])[:2]:
            adversary(h)
            h.checks = []
            skeptic.challenge(led, h)
            scorer.score(led, h)
    live = sorted([h for h in hyps if h.status != "rejected"], key=lambda h: -h.scores["total"])

    discoveries, reports = [], []
    for rank, h in enumerate(live):
        if rank >= 2:
            h.status = "tested"
            continue
        d = writer.write(led, h)
        if llm is not None:
            from .llm_agent import rewrite_discovery
            d = rewrite_discovery(llm, led, h, d)
        rep = verifier.verify_discovery(led, d, h.facts)
        d["verification"] = rep.__dict__
        if not rep.passed:
            led.log("verify", f"{h.id}:failed", f"unmatched numbers {rep.numbers_unmatched}, broken {rep.citations_broken}")
        h.status = "selected" if rank == 0 else "supporting"
        d["role"] = "primary" if rank == 0 else "secondary"
        discoveries.append(d)
        reports.append(rep.__dict__)
    for h in hyps:
        if h.status == "rejected":
            led.log("select", f"{h.id}:rejected", h.rejection_reason or "")
    led.log("select", "done", f"{len(discoveries)} discoveries from {len(hyps)} hypotheses")
    return RunResult(company, prof, led, anoms, tasks, hyps, discoveries, reports, mode, started,
                     datetime.now(timezone.utc).isoformat(timespec="seconds"), trace)


def run_replay(bundle_path: str | Path) -> RunResult:
    from ..tools.replay import Bundle, ReplaySearch, load_into_ledger
    b = Bundle.load(bundle_path)
    led = load_into_ledger(b)
    return run_ledger(led, b.company, b.profile_text, ReplaySearch(b), mode="replay")


def save(result: RunResult, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "run.json"
    p.write_text(json.dumps(result.to_json(), indent=2, default=str), encoding="utf-8")
    return p
