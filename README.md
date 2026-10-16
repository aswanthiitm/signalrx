# SignalRx: an autonomous research agent for Indian pharma & healthcare

**ATMOS 2026 · Finding the Next Signal · Track D (Pharma & Healthcare)**

Give SignalRx a NIFTY 50 pharma or healthcare company (or any listed peer) and it investigates public sources on its own. It
looks for a **non-obvious, evidence-backed finding**, tries to break that finding, and publishes it as an auditable report or
dashboard with all eight required elements (discovery, why it matters, evidence, timeline, numbers, counterargument,
falsification condition, evidence strength).

It doesn't summarise. It **joins the company's own disclosures to independent records** (regulator actions at plants it
doesn't own, payor-policy orders, partner filings, peers' results). It computes what no single source states, works out
*when* the story changed, and then sends a skeptic after its own hypothesis.

## Pilot results (replayable from recorded evidence, all three pass the verifier)

| Company | Discovery (one line) | Brief Q | Strength |
|---|---|---|---|
| **Dr. Reddy's** | North America has fallen to **27.4%** of revenue (Q1FY27), **below even FY21's 37.8%**, while ex-NA revenue grew **23.5%** in FY26. So **semaglutide, not lenalidomide, is now the swing factor**, and it has slipped twice in 8 months: a Health Canada notice (Oct-25), then an out-of-spec API issue (Jul-26). The result was a ₹240 cr write-down (**54% of Q1 profit**) and an FY27 pen plan cut from 12 to 6–7M. | D1 | Strong |
| **Cipla** | The sharpest US decline came from **a plant Cipla doesn't own**. Pharmathen (Greece), the exclusive lanreotide maker, closed an FDA inspection with 9 observations on **21-Nov-2025**. North America then fell **28% QoQ** ($233M→$167M) in a quarter that ended **14 days before Cipla disclosed it**. Lanreotide explains only **~51%** of the step-down, and the $1bn exit-rate target now needs **+54%**. | D2 | Strong |
| **Max Healthcare** | The H2-FY26 **ARPOB stall was a payor-policy shock**: after CGHS drug-billing rules, Max stopped high-value chemo for institutional patients, and oncology fell from **26% to 22%** of inpatient revenue. Reported growth (16%) understates the core business (20%), about **1.2pp** of the Q1 ARPOB rebound is the CGHS tariff revision, and management's "normalisation" date is just the **base effect lapping**. *Secondary finding: the bed target needs 3.3–3.7× the current build pace.* | D2 | Moderate |

Open `runs/<company>/dashboard.html` (interactive) or `runs/<company>/dashboard.pdf` (research note).

## How it works

```
company ─► resolve ─► harvest ─► verify inputs ─► profile ─► scan ─► plan ─► investigate ─► expand counterparties
                                                                                   │
        render ◄─ verify output ◄─ write ◄─ score ◄─ adversarial pass ◄─ skeptic ◄─ hypothesise
```

| Stage | What it does | Where |
|---|---|---|
| Harvest | Statements & quarterly results (Screener), Reg-30 announcements (NSE), recalls & approvals (openFDA), trials (ClinicalTrials.gov), headlines with dates (news RSS) | `tools/sources.py` |
| Verify inputs | Recomputes every reported growth rate from the underlying levels and drops reports that disagree (one outlet printed Dr. Reddy's Q1 as **+6%**; the filed levels give **−5.6%**). Resolves conflicts by source hierarchy and logs both. | `agent/verifier.py`, `ledger.py` |
| Profile | Classifies the business archetype (formulations / API-CDMO / hospital / diagnostics) **from the company's own text and segment names**, never from a name list | `agent/profiler.py` |
| Scan | Mix shifts, segment step-downs (adjacent quarters only), growth gaps (reported vs ex-item), unit-price stalls, announced-vs-commissioned capacity | `analytics/scan.py` |
| Plan | Scores the Track D angle library by archetype fit × anomaly triggers, plus one open slot for an LLM-proposed angle | `agent/planner.py`, `knowledge/track_d.yaml` |
| Investigate | Claude tool loop per angle (server-side web search + our own fetcher). **Every write is grounded**: the quote must be found in the fetched document, and its numbers must match exactly. | `agent/llm_agent.py` |
| Expand | Every third party found (contract manufacturer, supplier, payor) gets its own regulator probe | `agent/live.py` |
| Hypothesise | Angle templates read only the ledger; the LLM may add two open hypotheses that face the same tests | `agent/hypotheses.py` |
| Skeptic | Sector-wide (peers), inorganic, one-off (re-computes with the one-off added back), temporary, other driver (with timing), breadth, low-margin, already-in-headlines, data conflicts | `agent/skeptic.py` |
| Score | Evidence strength (Strong / Moderate / Weak with reasons) · novelty · materiality · robustness | `agent/scorer.py` |
| Verify output | Every number in the prose must trace to a source passage, a ledger figure or a recorded calculation. Every citation must resolve to a URL. The discovery needs ≥ 2 independent source families. | `agent/verifier.py` |
| Render | Self-contained HTML dashboard (click any claim to reach its passage and link) plus an A4 PDF | `render/` |

**No company-specific tuning.** `tests/test_generality.py` fails the build if any listed company, partner name or bundle id
appears in the package code or the knowledge pack. Sector knowledge (regulatory ladders, CGHS/NPPA/Section 232 policy
watchlist, the loss-of-exclusivity calendar, the metric vocabulary) lives in `knowledge/track_d.yaml`.

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && playwright install chromium

# offline: replay a recorded evidence bundle through the full reasoning pipeline
python -m signalrx replay bundles/cipla.yaml --out runs/cipla --pdf

# live: research from the public internet (needs network + ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=...
python -m signalrx run "Sun Pharma" --pdf          # also freezes the run into bundles/live_<symbol>_<date>.yaml
python -m signalrx universe                        # track companies, from the official NSE index files

pytest -q                                          # 25 tests: calculators, verifier, skeptic, parsers, LLM loop (fake client), generality
python scripts/smoke_live.py                       # checks every live connector against the real endpoints
```

The model is `claude-opus-5-5` (adaptive thinking, explicit effort). Server-side refusal fallbacks are enabled.
Search uses Anthropic's server-side web search, so the only key needed is `ANTHROPIC_API_KEY`.

## Honest status

* **Replay pipeline**: complete and tested end to end on three companies across two archetypes.
* **Evidence bundles** (`bundles/*.yaml`) were collected on 2026-10-16 with a web-search tool. Passages are as the search
  results reported them, and figures marked `derived` are back-computed from a reported value and growth rate (they carry
  tolerance bands). Re-run live to replace search snippets with full-document quotes.
* **Live connectors and the live loop** were built against the documented public endpoints and unit-tested on fixtures, with
  the LLM loop tested against a fake client. They could not be exercised against the real sites from the build sandbox
  (outbound network was allow-listed), so run `scripts/smoke_live.py` on an open network first and fix any parser drift
  before the finals.
* No investment recommendation is made or implied.

## Repository map

```
signalrx/
  models.py ledger.py periods.py fmt.py      data model, evidence ledger, Indian FY arithmetic
  knowledge/track_d.yaml                     sector knowledge pack (no company names)
  analytics/metrics.py analytics/scan.py     calculators; quantitative anomaly scan
  agent/  profiler planner hypotheses skeptic scorer verifier writer orchestrator llm_agent live
  tools/  http documents sources replay      fetch+cache, PDF/HTML text + BM25 + quote grounding, connectors, replay
  llm/claude.py                              Claude wrapper: structured JSON + tool loop (pause_turn, server web search)
  render/ charts.py dashboard.py templates/  inline-SVG charts, HTML dashboard, PDF via Chromium
bundles/   recorded evidence (Dr. Reddy's, Cipla, Max Healthcare)
runs/      rendered outputs (dashboard.html, dashboard.pdf, run.json with the full ledger and trace)
docs/      Round 1 report, finals playbook, architecture notes
tests/     pytest suite + fixtures
```
