# SignalRx: finals playbook (25 Oct, online)

Format: **10 min presentation + live demo, then 15 min Q&A**. The judges pick a NIFTY 50 Track D company on the spot (Sun
Pharma, Cipla, Dr. Reddy's, Apollo Hospitals, Max Healthcare). In a tie-break they may pick any listed sector company.

The judging criteria, and the single proof point for each that we put on screen:

| Criterion | Our proof point |
|---|---|
| Discovery quality | One sentence that joins two facts no headline joined (Cipla: a plant it doesn't own, plus a quarter that closed 14 days before the disclosure) |
| Evidence quality | Click a number, land on the passage, open the URL. Show the families count. |
| Novelty | The "already in headlines" check, and why the finding still adds timing and sizing |
| Temporal reasoning | The timeline: inspection → Form 483 public → disclosure → coverage → results |
| Cross-source reasoning | Families list: company + regulator facts + press + peers + policy documents |
| Falsifiability | The counterargument box and dated falsification conditions |
| Explainability | The trace: plan → searches → scan → skeptic → verifier |
| Robustness | The verifier dropping a misreported +6% (levels say −5.6%); the skeptic resizing a claim to "~51% explained" |
| Generality & autonomy | A live run on the judges' pick; the generality test; the archetype chosen from data |

---

## 1. Run-of-show (10:00)

**The moment the company is named: start the live run** (`python -m signalrx run "<company>" --pdf`) in a visible terminal,
then present while it works. Never wait in silence.

| Time | Who | What is on screen | What is said (gist) |
|---|---|---|---|
| 0:00–0:40 | Lead | Cipla dashboard, discovery card | "Cipla's sharpest US drop came from a factory Cipla doesn't own. The FDA closed its inspection on 21 November; the hit landed in a quarter that ended 14 days before Cipla told anyone. A summariser can't find that, because no single document says it." |
| 0:40–1:40 | Lead | Pipeline slide (one diagram) | Three principles: **joins, not reads** (≥2 independent source families); **numbers first, LLM second** (code computes, the LLM can't invent a number); **try to kill it** (skeptic + adversarial search; ship only what survives, with its counterargument). |
| 1:40–2:00 | Engineer | Terminal: live run started, trace streaming | "The agent is now researching <company> live. We'll come back to it in four minutes." |
| 2:00–5:30 | Analyst | Prepared dashboard (pick the one *not* chosen by the judges: Dr. Reddy's or Max) | Walk the eight elements in order: discovery → tiles → chart → why → timeline → **click a citation** → evidence trail filter "opposes" → skeptic table (point at a *weakens* verdict and how it changed the claim) → falsification → strength reasons. |
| 5:30–8:00 | Analyst + Engineer | **Live result** for the judges' company | Read the discovery sentence aloud. Show three citations. Show the hypothesis board (what was tested and rejected). If the grade is Weak, say so and show why: that *is* the system working. |
| 8:00–9:15 | Engineer | Audit log + tests | The verifier dropped a misreported growth rate; `pytest`: 25 green, including the generality guard; archetype picked from the company's own text; universe read from NSE index files. |
| 9:15–10:00 | Lead | Summary slide | Three findings across two archetypes, every claim traceable, no company tuning. "What it found today on <company>: <one line>." |

**Fallbacks (decide in advance, rehearse each once):**
- Live run slower than 5:30 → show the partial trace (plan, anomalies, records so far), then switch to the frozen
  bundle for that company (`python -m signalrx replay bundles/live_<symbol>_<date>.yaml`) and say so plainly.
- Network or API failure → replay the pre-frozen bundle for that company (freeze all 5 + the tie-break pool on 23–24 Oct).
- Judges pick an off-list company → the same command; the universe check and archetype classification are part of the demo.

---

## 2. Two-minute MVP demo video (Round 1, optional but valued)

Record at 1080p. Use a screen-only video with a voice-over, recorded in one take with OBS. A scripted walkthrough is in
`docs/demo/walkthrough.webm` (silent, for timing); re-record with the live terminal once the API key is in place.

| t | Visual | Voice-over |
|---|---|---|
| 0:00 | Title card: SignalRx, Track D | "SignalRx is an autonomous research agent for Indian pharma and healthcare. It doesn't summarise. It finds what no single document says." |
| 0:08 | Terminal: `python -m signalrx run "Cipla"` with the trace scrolling | "Given only a company name, it pulls statements, exchange filings, FDA records and headlines, then scans for where the numbers moved." |
| 0:25 | Trace lines: plan → angles | "It picks research angles from a pharma-specific library based on what moved: here, a step-down in US revenue." |
| 0:38 | Trace: record_link Pharmathen → FDA probe | "It finds that a key product is made by a third party, then checks that partner's regulator record." |
| 0:52 | Dashboard: discovery card | "The finding: the decline came from a plant Cipla doesn't own, and the quarter that absorbed it closed 14 days before Cipla disclosed it." |
| 1:08 | Click a citation chip → evidence card → open the link | "Every number is one click from its source passage." |
| 1:20 | Skeptic table: other_driver *weakens* | "Then it tries to break its own idea. A second driver, the lenalidomide cliff, explains part of the drop, so the claim is resized to about half." |
| 1:35 | Falsification card + strength reasons | "It ends with what would prove it wrong, and how strong the evidence is, with the reasons shown." |
| 1:47 | Quick cuts: Dr. Reddy's and Max dashboards | "Same system, no company tuning: a formulations exporter and a hospital chain." |
| 1:56 | End card: repo URL | "Code and evidence are on GitHub." |

---

## 3. Q&A bank (15 minutes; answer in under 30 seconds, then offer the screen)

**On novelty**
- *How is this not RAG plus a summary?* A summariser answers from one document. Our discovery sentence is required to cite
  ≥2 independent source families, and its key numbers are calculations no source states (e.g. "~51% of the step-down",
  "54% of Q1 profit", "quarter closed 14 days before disclosure"). We also measure novelty against the headlines we
  collected, and penalise findings the headlines already carry.
- *Isn't the Cipla story already in the press?* The dependency is; we flag that ourselves (the `already_mainstream`
  check: *weakens*). What isn't in the press is the timing (the hit landed in a quarter that closed before the disclosure)
  and the sizing (lanreotide explains only ~half the drop, so the rest is a second, separate problem).
- *Would a conventional dashboard miss this?* Yes. The input is a partner's FDA record and a policy order, neither of which
  is in a company dashboard.

**On evidence and hallucination**
- *How do you stop invented numbers?* Three gates. (1) A record is accepted only if its verbatim quote is found in a document
  we fetched and every number in the quote matches exactly (a test proves a quote with one changed number is rejected).
  (2) Calculations are executed in Python with recorded inputs. (3) The output verifier traces every number in the prose to
  a passage, a ledger figure or a calculation; an untraceable number fails the run.
- *What if a source is simply wrong?* We recompute rather than trust. In the Dr. Reddy's pilot, one outlet reported Q1
  revenue +6% and NA +35%; the filed levels give −5.6% and −35.4%. Both reports were dropped and logged in the audit section.
- *Your replay passages are search snippets. Is that a weakness?* Yes, and we say it. Live runs fetch the full documents and
  verify quotes against them; replay is for audit and offline demos.
- *Why use aggregators like Screener?* Only for the statement harvest. Any figure we rely on is checked against primary
  filings where they exist, and in a conflict the primary filing wins by rule.

**On generality and autonomy**
- *Did you tune it for these three companies?* No. A test fails the build if any company or partner name appears in the
  package or the knowledge pack. The archetype is classified from the company's own description and segment names; the
  universe is read from NSE's index files.
- *What if it finds nothing interesting?* It still outputs the best-supported hypothesis with an honest grade (Weak if need
  be) and the full hypothesis board, including what was rejected and why. Refusing to overclaim is part of the design.
- *Will it work on Divi's or Syngene (CDMO)?* The API/CDMO archetype routes to customer concentration, capacity, the
  BIOSECURE watchlist and the counterparty probe. Same code path; we dry-ran the tie-break pool on 22 Oct (bring the numbers).
- *Hospitals vs pharma?* Different KPIs (beds, ARPOB, occupancy, payor mix), the same scan → plan → skeptic machinery. Max
  shows it.

**On reasoning**
- *Where is the temporal reasoning?* Every event has a date and a precision. The agent computes lead–lag between the
  regulator record, the disclosure, the first headline and results. It also caught that "normalisation by Q3–Q4 FY27" is
  13 months after the change, i.e. the base effect lapping rather than a recovery.
- *How does the skeptic decide?* Each alternative is a test that re-runs the numbers: add the one-off back, strip the
  acquisition, compare peers in the same quarter, check whether a second driver's date falls inside the quarter.
- *What does "Strong" mean?* Shown with its reasons: number of families, presence of a primary filing, third-party records,
  how many skeptic tests survived, and conflicts resolved. The formula is in `agent/scorer.py`.

**On engineering**
- *Why Claude, and is it locked in?* Opus 5.5 for long tool-use loops and structured outputs. The model sits behind one
  wrapper (`llm/claude.py`), and the reasoning stages that matter (scan, skeptic, verifier, scoring) are model-free code.
- *Cost and latency of a live run?* Bounded by `--budget` (tool calls per run, default 40). Bring measured numbers from the
  dry runs (time to discovery, tokens).
- *Scraping and terms of use?* Public pages only, a polite rate-limited fetcher with a cache, no logins, no paid data.
  Earnings-call transcripts are public because listed companies must publish them on their websites.
- *Could this run continuously?* Yes. Falsification conditions name what to monitor; a scheduled re-run checks them and
  flags when a finding flips.

---

## 4. Risk register for the live demo

| Risk | Likelihood | Mitigation |
|---|---|---|
| NSE blocks the session (cookies / rate limit) | Medium | Fetcher retries; Reg-30 filings are also mirrored in SEC 6-Ks (ADR issuers), on BSE and on company IR pages; searching finds them. |
| Screener HTML changes | Low–medium | `scripts/smoke_live.py` on the morning of the finals; parsers are isolated and unit-tested. |
| API rate limit / outage | Low | Retries in the SDK; refusal fallbacks enabled; replay bundles frozen for all track companies. |
| Live run exceeds the time slot | Medium | `--budget 30`; start the run the moment the company is named; show the partial trace; fall back to the frozen bundle. |
| A weak or odd finding on the judges' pick | Medium | Present it honestly with the grade and the skeptic table; show the hypothesis board; this demonstrates calibration. |
| A wrong number on screen | Low | The verifier blocks untraceable numbers; we never edit output by hand during the demo. |

---

## 5. Pre-finals checklist and task split (four people)

| Owner | 20–21 Oct | 22 Oct | 23–24 Oct |
|---|---|---|---|
| **Engineer A** (connectors) | Run `smoke_live.py` on an open network; fix parser drift; freeze live bundles for all 5 track companies | Tie-break pool runs (Divi's, Lupin, Zydus, Torrent, Aurobindo, Fortis, Narayana, Syngene) | Freeze final bundles; morning-of smoke test |
| **Engineer B** (agent) | Live end-to-end runs; tune budgets; collect time/token stats | Add `own_site_regulatory` and `specialty_pivot` templates (Sun is the likely test) | Code freeze 24 Oct 18:00 |
| **Analyst** (finance) | Manually audit 20 citations per run (precision) and judge each discovery's novelty vs the headlines | Write the "what we'd expect" note per company to sanity-check live outputs | Rehearse the 2:00–5:30 walkthrough |
| **Lead** (story) | Slides: hook, three principles, pipeline, summary | Q&A drills from section 3 | Two full rehearsals with a timer, one with a forced fallback |

Metrics to bring to the finals: verifier pass rate, median families per discovery, citation precision from the manual
audit, time to discovery, and the share of runs where the skeptic changed the claim.
