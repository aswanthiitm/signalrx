"""Live run: research a company from the public internet, then reason over it
with exactly the same downstream stages as replay.

  1. resolve      company name / symbol -> NSE symbol (Screener search, index files)
  2. harvest      deterministic pulls: statements & quarterly results (Screener),
                  Reg-30 announcements (NSE), recalls & approvals (openFDA), headlines (news RSS)
  3. scan + plan  anomalies -> angles (agent/planner.py)
  4. investigate  one LLM tool loop per angle (agent/llm_agent.Investigator), grounded writes only
  5. expand       every new third party found -> its own regulator probe
  6. hypothesise / challenge / adversarial pass / score / write / verify  (orchestrator.run_ledger)
  7. freeze       the ledger is written back out as a replay bundle for audit
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from ..analytics.scan import run_scan
from ..knowledge import load_pack
from ..ledger import EvidenceLedger
from ..llm.claude import Claude
from ..models import Evidence, Observation, Source
from ..tools import sources as S
from ..tools.http import Fetcher
from . import planner, profiler
from .llm_agent import Investigator
from .orchestrator import RunResult, run_ledger

# Screener row labels -> ledger metrics (values are Rs crore on Screener; ledger uses INR_mn)
SCREENER_ROWS = {"Sales": "revenue_total", "Revenue": "revenue_total", "Net Profit": "net_profit",
                 "Operating Profit": "ebitda", "OPM %": "ebitda_margin"}


def resolve(company: str, fetch: Fetcher) -> dict:
    q = company.strip()
    if q.isupper() and " " not in q:
        return {"symbol": q, "name": q}
    hits = S.screener_search(q, fetch)
    if not hits:
        raise LookupError(f"could not resolve '{company}'")
    return {"symbol": hits[0]["symbol"], "name": hits[0]["name"]}


def harvest(led: EvidenceLedger, symbol: str, fetch: Fetcher) -> dict:
    data = S.screener_company(symbol, fetch)
    src = led.add_source(Source("L000", data["url"], f"{data['name']} financial statements (Screener)", "Screener", "aggregator",
                                data["retrieved_at"][:10], "company", data["retrieved_at"], "harvest"))
    n = 0
    for sec in ("quarters", "profit-loss"):
        for label, vals in data.get(sec, {}).items():
            metric = SCREENER_ROWS.get(label)
            if not metric:
                continue
            for period, v in vals.items():
                eid = f"SCR-{sec}-{label}-{period}".replace(" ", "_").replace("%", "pct")
                led.add_evidence(Evidence(eid, src.id, "figure", f"{label} {period}: {v:,.2f}" + ("" if "%" in label else " (Rs crore)"),
                                          verified=True, note="statement table"))
                unit = "%" if "%" in label else "INR_mn"
                led.observe(Observation(led.company, metric, period, v if unit == "%" else v * 10, unit, eid))
                n += 1
    try:
        peers = S.screener_peers(data["warehouse_id"], fetch) if data.get("warehouse_id") else []
    except ConnectionError:
        peers = []
    led.log("harvest", "screener", f"{n} statement figures; {len(data.get('documents', []))} document links; peers {peers[:8]}")
    return {"about": data.get("about", ""), "documents": data.get("documents", []), "peers": peers, "name": data.get("name") or symbol}


def freeze_bundle(led: EvidenceLedger, company: dict, profile_text: str, path: str | Path) -> Path:
    """Write the live ledger out in bundle format so the run can be replayed and audited offline."""
    def clean(d):
        return {k: v for k, v in d.items() if v not in (None, [], {})}
    raw = {
        "company": company, "profile_text": profile_text,
        "sources": [clean({k: getattr(s, k) for k in ("id", "type", "publisher", "entity", "published", "title", "url")})
                    for s in led.sources.values()],
        "evidence": [clean({"id": e.id, "source": e.source, "kind": e.kind, "text": e.text, "verified": e.verified, "note": e.note})
                     for e in led.evidence.values()],
        "observations": [clean({"entity": o.entity if o.entity != led.company else None, "metric": o.metric, "dims": o.dims,
                                "period": o.period, "value": o.value, "unit": o.unit, "evidence": o.evidence, "derived": o.derived or None,
                                "note": o.note}) for o in led.observations],
        "events": [clean(e.__dict__) for e in led.events],
        "links": [clean(ln.__dict__) for ln in led.links],
        "claims": [clean(c.__dict__) for c in led.claims],
        "guidance": [clean(g.__dict__) for g in led.guidance],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=140), encoding="utf-8")
    return p


def run_live(company: str, model: str = "claude-opus-5-5", budget: int = 40, out_bundle: str | Path | None = None) -> RunResult:
    fetch = Fetcher()
    llm = Claude(model=model)
    who = resolve(company, fetch)
    led = EvidenceLedger(company=who["name"])
    meta = harvest(led, who["symbol"], fetch)
    inv = Investigator(led, fetch, llm)
    # cheap deterministic probes before any LLM call
    for probe in (lambda: inv.t_announcements({"symbol": who["symbol"], "filter": None}),
                  lambda: inv.t_fda_recalls({"firm": who["name"].split()[0], "product": None}),
                  lambda: inv.t_news({"query": who["name"]})):
        try:
            probe()
        except (ConnectionError, ValueError) as e:
            led.log("harvest", "probe-failed", str(e))
    description = meta["about"] + " " + " ".join(d["title"] for d in meta["documents"][:20])
    prof = profiler.profile(led, who["symbol"], description)
    anoms = run_scan(led, load_pack()["materiality"])
    fy = date.today().year + (1 if date.today().month >= 4 else 0)
    tasks = planner.plan(led, prof, anoms, fy)
    per_task = max(8, budget // max(1, len(tasks)))
    for t in tasks:
        docs = [d for d in meta["documents"] if d["kind"] in ("presentation", "transcript", "annual_report")][:6]
        q = t.queries + [f"read: {d['title']} {d['url']}" for d in docs]
        inv.investigate(who["name"], t.angle, t.question, q, run_scan(led, load_pack()["materiality"]), per_task)
    # counterparty expansion: every third party found gets its own regulator probe
    for ln in list(led.links):
        if ln.type in ("manufacturing_partner", "supplier", "licensor"):
            inv.investigate(who["name"], "counterparty_regulatory",
                            f"What does the regulator record say about {ln.b} (maker of {ln.product}), and when?",
                            [f"{ln.b} FDA Form 483", f"{ln.b} warning letter OAI", f"{ln.b} {ln.product} recall"], anoms, per_task // 2 or 6)
    company_meta = {"name": who["name"], "symbol": who["symbol"], "as_of": date.today().isoformat()}
    from .skeptic import adversarial_queries

    def adversary(h):
        inv.investigate(who["name"], "adversarial",
                        "Find the strongest public evidence AGAINST this hypothesis (alternative explanations, peers with the "
                        "same move, management rebuttals, later reversals). Record it with record_claim / record_figure: "
                        + h.statement, adversarial_queries(who["name"], h), anoms, max(6, per_task // 2))
    res = run_ledger(led, company_meta, description, search=None, mode="live", llm=llm, adversary=adversary)
    freeze_bundle(led, company_meta, description, out_bundle or f"bundles/live_{who['symbol'].lower()}_{date.today().isoformat()}.yaml")
    return res
