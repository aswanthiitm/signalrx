"""LLM-driven stages for live runs: investigation, open-ended hypotheses, the
adversarial pass, and prose rewriting. Every write into the ledger goes through
grounding checks here; the model can propose, only verified evidence is kept.
"""
from __future__ import annotations

import itertools
import json
import re

from .. import periods
from ..knowledge import load_pack
from ..ledger import EvidenceLedger
from ..llm.claude import Claude, ToolSpec
from ..models import Claim, Event, Evidence, Hypothesis, Link, Observation, Source
from ..tools import sources as S
from ..tools.documents import Doc, DocIndex, to_text, verify_quote
from ..tools.http import Fetcher

SOURCE_TYPES = ["company_filing", "exchange_filing", "transcript", "investor_presentation", "regulator", "counterparty",
                "rating_agency", "broker", "media", "aggregator", "blog"]

INVESTIGATOR_SYSTEM = """You are the investigator in SignalRx, an autonomous research agent for Indian listed pharma and
healthcare companies. You are given one research angle, the figures already in the evidence ledger, and the anomalies
the quantitative scan found. Your job is to find PUBLIC evidence that confirms, sizes, dates or contradicts a story
behind those anomalies, and to record it with the record_* tools.

Rules (the verifier enforces them; violations are rejected and cost budget):
- Read before you record. Use web_search to find documents, fetch_url to read them, search_document for long PDFs.
- Every record_* call needs a verbatim quote copied from a document you fetched with fetch_url, and that document's URL.
  Numbers you record must appear in the quote. Never paraphrase inside `quote`.
- Units: rupee amounts in INR_mn (1 crore = 10 INR_mn, 1 billion = 1,000 INR_mn); dollar amounts in USD_mn; ratios in %.
  Periods use the Indian fiscal year: FY26 = Apr-2025..Mar-2026; the June 2026 quarter is Q1FY27.
- Prefer primary documents (exchange filings, results releases, transcripts, regulator records). Then diversify:
  the strongest findings join the company's own disclosures to an independent family (a regulator record, a
  partner's or supplier's filing, a policy document, a peer's results).
- Look for third parties: contract manufacturers, API suppliers, licensors, acquirers, payors. When you find one,
  record_link and then check the regulator record for THAT entity.
- Tag negative regulatory, quality or supply events with "incident"; dated plans that have not happened with "future".
- Record evidence that cuts against the story too (record_claim with the speaker), and peers' figures for the same
  period (entity "peer:<name>").
- Stop with finish() when the angle is answered or the budget is spent. Do not write conclusions; the downstream
  stages form, test and score hypotheses from what you record."""


def _str(desc: str = "") -> dict:
    return {"type": "string", "description": desc}


def _nullable(t: str, desc: str = "") -> dict:
    return {"type": [t, "null"], "description": desc}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


class Investigator:
    """Owns the fetcher, the document index and the ledger writes for a live run."""

    def __init__(self, led: EvidenceLedger, fetch: Fetcher, llm: Claude):
        self.led, self.fetch, self.llm = led, fetch, llm
        self.index = DocIndex()
        self._sid = itertools.count(len(led.sources) + 1)
        self._eid = itertools.count(len(led.evidence) + 1)
        self.rejections: list[str] = []
        pack = load_pack()
        self.metrics = sorted(pack["metrics"])
        self.kinds = pack["event_kinds"]

    # ----------------------------------------------------------- ledger io
    def source_for(self, url: str, title: str, publisher: str, stype: str, published: str | None, entity: str | None) -> Source:
        for s in self.led.sources.values():
            if s.url == url:
                return s
        s = Source(f"L{next(self._sid):03d}", url, title or url, publisher or "", stype, published, entity,
                   retrieved_at=None, via="live")
        return self.led.add_source(s)

    def evidence(self, src: Source, kind: str, text: str, verified: bool | None, note: str | None = None) -> Evidence:
        return self.led.add_evidence(Evidence(f"X{next(self._eid):03d}", src.id, kind, text, verified=verified, note=note))

    def _grounded(self, url: str, quote: str) -> tuple[bool, str]:
        doc = self.index.docs.get(url)
        if not doc:
            return False, "fetch_url this document first; quotes must come from a fetched document"
        ok, score = verify_quote(quote, doc.text)
        return ok, f"quote match {score:.2f}"

    def _reject(self, why: str) -> dict:
        self.rejections.append(why)
        self.led.log("investigate", "rejected", why)
        return {"ok": False, "rejected": why}

    # --------------------------------------------------------------- tools
    def t_fetch_url(self, a: dict) -> dict:
        f = self.fetch.get(a["url"])
        if f.status != 200:
            return {"ok": False, "status": f.status}
        text = to_text(f.body, f.content_type, f.url)
        self.index.add(Doc(a["url"], a["url"], text))
        self.led.log("investigate", "fetch", f"{a['url']} ({len(text):,} chars)")
        return {"ok": True, "chars": len(text), "excerpt": text[:9000],
                "hint": "use search_document(url, query) to find passages further in" if len(text) > 9000 else ""}

    def t_search_document(self, a: dict) -> dict:
        hits = self.index.search(a["query"], k=4, url=a["url"])
        return {"passages": [h[1] for h in hits]}

    def t_record_figure(self, a: dict) -> dict:
        ok, why = self._grounded(a["url"], a["quote"])
        if not ok:
            return self._reject(f"figure {a['metric']} {a['period']}: {why}")
        if a["metric"] not in self.metrics:
            return self._reject(f"unknown metric {a['metric']}")
        try:
            periods.parse(a["period"])
        except ValueError as e:
            return self._reject(str(e))
        nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", a["quote"])]
        shown = a.get("value_as_quoted")
        if shown is None or not any(abs(n - shown) <= 0.005 * max(1, abs(shown)) for n in nums):
            return self._reject(f"value_as_quoted {shown} does not appear in the quote")
        src = self.source_for(a["url"], a["source_title"], a["publisher"], a["source_type"], a["published"], a.get("entity_of_document"))
        ev = self.evidence(src, "figure", a["quote"], True, why)
        dims = {k: v for k, v in {"segment": a.get("segment"), "basis": a.get("basis"), "product": a.get("product"),
                                  "specialty": a.get("specialty"), "item": a.get("item"), "by": a.get("by"),
                                  "bound": a.get("bound"), "policy": a.get("policy")}.items() if v}
        self.led.observe(Observation(a.get("entity") or self.led.company, a["metric"], a["period"].upper().replace(" ", ""),
                                     float(a["value"]), a["unit"], ev.id, dims, derived=bool(a.get("is_estimate")),
                                     note=a.get("note")))
        return {"ok": True, "evidence_id": ev.id}

    def t_record_event(self, a: dict) -> dict:
        ok, why = self._grounded(a["url"], a["quote"])
        if not ok:
            return self._reject(f"event '{a['title']}': {why}")
        if a["kind"] not in self.kinds:
            return self._reject(f"unknown event kind {a['kind']}")
        src = self.source_for(a["url"], a["source_title"], a["publisher"], a["source_type"], a["published"], a.get("entity_of_document"))
        ev = self.evidence(src, "event", a["quote"], True, why)
        self.led.events.append(Event(a["date"], a["kind"], a["title"], [ev.id], a.get("actor"), a.get("subject"),
                                     a.get("tags") or [], a.get("date_precision") or "day", a.get("short")))
        return {"ok": True, "evidence_id": ev.id}

    def t_record_link(self, a: dict) -> dict:
        ok, why = self._grounded(a["url"], a["quote"])
        if not ok:
            return self._reject(f"link {a['partner']}: {why}")
        src = self.source_for(a["url"], a["source_title"], a["publisher"], a["source_type"], a["published"], a.get("entity_of_document"))
        ev = self.evidence(src, "quote", a["quote"], True, why)
        self.led.links.append(Link(a["type"], "company", a["partner"], [ev.id], a.get("product"), a.get("segment"), a.get("note")))
        return {"ok": True, "evidence_id": ev.id, "next": f"check the regulator record for {a['partner']}"}

    def t_record_claim(self, a: dict) -> dict:
        ok, why = self._grounded(a["url"], a["quote"])
        if not ok:
            return self._reject(f"claim: {why}")
        src = self.source_for(a["url"], a["source_title"], a["publisher"], a["source_type"], a["published"], a.get("entity_of_document"))
        ev = self.evidence(src, "claim", a["quote"], True, why)
        self.led.claims.append(Claim(a["speaker"], a["summary"], ev.id, a.get("topic")))
        return {"ok": True, "evidence_id": ev.id}

    def t_fda_recalls(self, a: dict) -> dict:
        rows = S.openfda_recalls(a["firm"], self.fetch)
        src = self.source_for(f"https://api.fda.gov/drug/enforcement.json?search=recalling_firm:%22{a['firm']}%22", f"openFDA drug enforcement: {a['firm']}",
                              "US FDA (openFDA)", "regulator", None, "US FDA")
        for r in rows[:25]:
            ev = self.evidence(src, "event", f"{r['date']} {r['classification']} recall: {r['product']} - {r['reason']}", True, "regulator API record")
            self.led.events.append(Event(r["date"], "regulatory", f"{r['classification']} recall: {r['product'][:80]}", [ev.id],
                                         "US FDA", a.get("product") or None, ["incident", "recall", a["firm"]] + ([a["product"]] if a.get("product") else [])))
        return {"ok": True, "recalls": rows[:25]}

    def t_fda_approvals(self, a: dict) -> dict:
        by_year = S.approvals_by_year(S.openfda_approvals(a["sponsor"], self.fetch))
        src = self.source_for(f"https://api.fda.gov/drug/drugsfda.json?search=sponsor_name:%22{a['sponsor']}%22", f"Drugs@FDA approvals: {a['sponsor']}",
                              "US FDA (openFDA)", "regulator", None, "US FDA")
        for y, n in by_year.items():
            ev = self.evidence(src, "figure", f"{n} original approvals for sponsor {a['sponsor']} in calendar {y}", True, "regulator API record")
            fy = f"FY{(y + 1) % 100:02d}"   # calendar-year count mapped to the FY ending in the following March (approximation, labelled)
            self.led.observe(Observation(self.led.company, "anda_approvals", fy, n, "count", ev.id, derived=True, note=f"calendar {y}"))
        return {"ok": True, "approvals_by_calendar_year": by_year}

    def t_news(self, a: dict) -> dict:
        items = S.news_rss(a["query"], self.fetch)
        for it in items[-30:]:
            src = self.source_for(it["url"], it["title"], it["publisher"] or "news", "media", it["date"], None)
            self.evidence(src, "headline", it["title"], True, "headline as published")
        return {"ok": True, "headlines": items[-30:]}

    def t_announcements(self, a: dict) -> dict:
        rows = S.nse_announcements(a["symbol"], self.fetch)
        pat = re.compile(a.get("filter") or r"USFDA|FDA|inspection|observation|warning|import alert|recall|acquisi|approval|plant|clarification|supply", re.I)
        keep = [r for r in rows if pat.search(r["subject"] + " " + r["text"])][:40]
        for r in keep:
            src = self.source_for(r["url"] or f"https://www.nseindia.com/get-quotes/equity?symbol={a['symbol']}", r["subject"], "NSE",
                                  "exchange_filing", r["date"], "company")
            ev = self.evidence(src, "event", f"{r['subject']}: {r['text'][:400]}", True, "exchange record")
            self.led.events.append(Event(r["date"], "disclosure", r["subject"][:120], [ev.id], "company", None, []))
        return {"ok": True, "announcements": keep}

    def t_finish(self, a: dict) -> str:
        self.led.log("investigate", "finish", a["summary"])
        return "ok"

    # --------------------------------------------------------- tool specs
    def tools(self) -> list[ToolSpec]:
        src_props = {"url": _str("URL of a document you fetched"), "source_title": _str(), "publisher": _str(),
                     "source_type": {"type": "string", "enum": SOURCE_TYPES}, "published": _nullable("string", "YYYY-MM-DD or YYYY-MM"),
                     "entity_of_document": _nullable("string", "whose document: company, a partner's name, a regulator")}
        fig = _obj({"metric": {"type": "string", "enum": self.metrics}, "period": _str("FY26 / Q1FY27"), "value": {"type": "number"},
                    "value_as_quoted": {"type": "number", "description": "the number exactly as it appears in the quote, before unit conversion"},
                    "unit": {"type": "string", "enum": ["INR_mn", "USD_mn", "%", "INR_k", "beds", "count"]},
                    "entity": _nullable("string", "null for the company; 'peer:<name>' for a peer"),
                    "segment": _nullable("string"), "basis": _nullable("string"), "product": _nullable("string"),
                    "specialty": _nullable("string"), "item": _nullable("string"), "by": _nullable("string"), "bound": _nullable("string"),
                    "policy": _nullable("string"), "is_estimate": {"type": "boolean"}, "note": _nullable("string"),
                    "quote": _str("verbatim passage containing the number"), **src_props})
        ev = _obj({"date": _str("YYYY-MM-DD (or YYYY-MM)"), "date_precision": {"type": "string", "enum": ["day", "month", "quarter"]},
                   "kind": {"type": "string", "enum": self.kinds}, "title": _str("one line"), "short": _nullable("string", "2-5 word label"),
                   "actor": _nullable("string", "who acted: company, US FDA, Health Canada, CGHS, a partner..."),
                   "subject": _nullable("string", "product / plant / specialty concerned"),
                   "tags": {"type": "array", "items": {"type": "string"}}, "quote": _str(), **src_props})
        link = _obj({"type": {"type": "string", "enum": ["manufacturing_partner", "supplier", "customer", "acquirer", "payor", "licensor"]},
                     "partner": _str(), "product": _nullable("string"), "segment": _nullable("string"), "note": _nullable("string"),
                     "quote": _str(), **src_props})
        claim = _obj({"speaker": {"type": "string", "enum": ["management", "counterparty", "broker", "regulator", "third_party"]},
                      "topic": _nullable("string", "product, specialty or segment the claim is about"), "summary": _str(),
                      "quote": _str(), **src_props})
        return [
            ToolSpec("fetch_url", "Fetch a URL (HTML or PDF) and return its text. Required before quoting it.", _obj({"url": _str()}), self.t_fetch_url),
            ToolSpec("search_document", "BM25 search inside a fetched document.", _obj({"url": _str(), "query": _str()}), self.t_search_document),
            ToolSpec("record_figure", "Record one number with its verbatim source passage.", fig, self.t_record_figure),
            ToolSpec("record_event", "Record one dated event with its source passage.", ev, self.t_record_event),
            ToolSpec("record_link", "Record a relationship with a third party (partner, supplier, payor...).", link, self.t_record_link),
            ToolSpec("record_claim", "Record a claim by management or a third party, for testing against numbers.", claim, self.t_record_claim),
            ToolSpec("fda_recalls", "US FDA drug recalls (openFDA) for a firm name; recorded as regulator events.",
                     _obj({"firm": _str(), "product": _nullable("string")}), self.t_fda_recalls),
            ToolSpec("fda_approvals", "US FDA original approvals by sponsor, counted per year.", _obj({"sponsor": _str()}), self.t_fda_approvals),
            ToolSpec("news_headlines", "Google News headlines with dates (for first-coverage timing and novelty checks).",
                     _obj({"query": _str()}), self.t_news),
            ToolSpec("exchange_announcements", "NSE corporate announcements (Reg 30) for a symbol, filtered by a regex.",
                     _obj({"symbol": _str(), "filter": _nullable("string")}), self.t_announcements),
            ToolSpec("finish", "End this angle with a one-paragraph summary of what was recorded.", _obj({"summary": _str()}), self.t_finish),
        ]

    # --------------------------------------------------------------- loop
    def investigate(self, company: str, angle: str, question: str, queries: list[str], anomalies: list, budget: int) -> dict:
        ledger_view = [{"metric": o.metric, "period": o.period, "dims": o.dims, "value": o.value, "unit": o.unit}
                       for o in self.led.observations if o.entity == self.led.company][:120]
        user = json.dumps({
            "company": company, "angle": angle, "question": question, "suggested_queries": queries,
            "anomalies": [a.description for a in anomalies],
            "ledger_figures": ledger_view,
            "known_links": [{"type": ln.type, "partner": ln.b, "product": ln.product} for ln in self.led.links],
            "known_events": [f"{e.date} {e.kind}: {e.title}" for e in self.led.events][-40:],
            "budget_tool_calls": budget,
        }, default=str)
        tr = self.llm.agent(INVESTIGATOR_SYSTEM, user, self.tools(), max_turns=budget, web_search_uses=max(3, budget // 4))
        self.led.log("investigate", f"angle:{angle}", f"{len(tr.tool_calls)} tool calls, {len(tr.searches)} search events, "
                     f"{sum(1 for c in tr.tool_calls if c['tool'].startswith('record') and c['ok'])} records", [])
        return {"angle": angle, "tool_calls": tr.tool_calls, "searches": tr.searches, "summary": tr.final_text}


# ------------------------------------------------------------ open angles

OPEN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["hypotheses"],
    "properties": {"hypotheses": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "statement", "support", "contra", "why", "falsification", "core_terms", "materiality"],
        "properties": {"title": {"type": "string"}, "statement": {"type": "string"},
                       "support": {"type": "array", "items": {"type": "string"}}, "contra": {"type": "array", "items": {"type": "string"}},
                       "why": {"type": "array", "items": {"type": "string"}}, "falsification": {"type": "array", "items": {"type": "string"}},
                       "core_terms": {"type": "array", "items": {"type": "string"}}, "materiality": {"type": "number"}}}}},
}


def propose_open_hypotheses(llm: Claude, led: EvidenceLedger, prof, anomalies) -> list[Hypothesis]:
    """Let the model propose up to two hypotheses the angle library does not cover.
    They must cite ledger evidence ids; they then face the same skeptic, verifier and scorer."""
    view = {
        "company": led.company, "archetype": prof.archetype,
        "evidence": {e.id: {"text": e.text[:400], "source": led.sources[e.source].publisher, "type": led.sources[e.source].type,
                            "date": led.sources[e.source].published} for e in led.evidence.values()},
        "anomalies": [a.description for a in anomalies],
        "events": [f"{e.date} {e.kind} {e.actor}: {e.title} {e.evidence}" for e in led.events],
    }
    sys = ("You are the hypothesis generator. Propose at most two NON-OBVIOUS, testable hypotheses about this company that a "
           "headline reader would miss, using only the evidence ids given. Every number in `statement` must appear in a cited "
           "passage. Prefer hypotheses that join two independent source families or that rest on timing. If nothing qualifies, "
           "return an empty list. materiality is 0-1 (share of earnings plausibly affected).")
    try:
        out = llm.json(sys, json.dumps(view, default=str), OPEN_SCHEMA)
    except Exception as e:  # an LLM failure must not kill a run; templates still produce hypotheses
        led.log("hypothesis", "open:error", str(e))
        return []
    hyps = []
    for i, x in enumerate(out.get("hypotheses", [])[:2], 1):
        sup = [e for e in x["support"] if e in led.evidence]
        if len(sup) < 2:
            led.log("hypothesis", "open:dropped", f"'{x['title']}' cites <2 valid evidence ids")
            continue
        h = Hypothesis(f"O{i}", "open", x["title"], x["statement"],
                       {"core_terms": x["core_terms"], "why": x["why"], "falsification": x["falsification"]},
                       support=sup, contra=[e for e in x["contra"] if e in led.evidence])
        h.scores["materiality"] = max(0.0, min(1.0, float(x["materiality"])))
        hyps.append(h)
        led.log("hypothesis", f"proposed:{h.id}", f"[open] {h.statement}", sup)
    return hyps


REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["discovery", "why_it_matters"],
                  "properties": {"discovery": {"type": "string"}, "why_it_matters": {"type": "array", "items": {"type": "string"}}}}


def rewrite_discovery(llm: Claude, led: EvidenceLedger, h: Hypothesis, d: dict) -> dict:
    """Improve readability without changing facts. If the rewrite introduces a number the
    verifier cannot trace, the template text is kept."""
    from .verifier import check_numbers
    sys = ("Rewrite for an equity-research reader: one clear sentence for `discovery` (it may be long but must stay one "
           "sentence), and 3-5 crisp `why_it_matters` bullets. Use ONLY numbers and facts present in the input. Do not add "
           "recommendations, price targets or adjectives that are not supported.")
    try:
        out = llm.json(sys, json.dumps({"discovery": d["discovery"], "why_it_matters": d["why_it_matters"], "facts": h.facts}, default=str),
                       REWRITE_SCHEMA)
    except Exception as e:
        led.log("write", "rewrite:error", str(e))
        return d
    _, bad = check_numbers(led, out["discovery"] + " " + " ".join(out["why_it_matters"]), h.facts)
    if bad:
        led.log("write", "rewrite:rejected", f"untraceable numbers {bad}; keeping template text")
        return d
    d = dict(d)
    d["template_discovery"] = d["discovery"]
    d["discovery"], d["why_it_matters"] = out["discovery"], out["why_it_matters"][:5]
    led.log("write", "rewrite:accepted", "prose rewritten; all numbers traced")
    return d
