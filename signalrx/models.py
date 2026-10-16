"""Core data model.

Everything the agent knows lives in an EvidenceLedger (see ledger.py) as one of
these records. The LLM never writes a number or a date into the final output
directly: it can only point at Observations, Events and Calculations that are
backed by Evidence, which is backed by a Source with a URL.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Source types, ordered by how much weight a figure from them carries when two
# sources disagree. Lower rank wins a conflict.
SOURCE_RANK = {
    "regulator": 1,          # FDA, Health Canada, CGHS office memoranda, SEBI/NSE orders
    "company_filing": 1,     # exchange filings, SEC 6-K/20-F, annual reports, results releases
    "exchange_filing": 1,
    "counterparty": 2,       # filings / releases of a partner, supplier, acquirer, customer
    "transcript": 2,         # earnings-call transcripts (management's own words)
    "investor_presentation": 2,
    "rating_agency": 2,
    "broker": 3,             # sell-side notes
    "media": 3,              # mainstream business press
    "aggregator": 4,         # market-data and results-summary sites
    "blog": 5,               # newsletters / substacks
}

# Which source types count as "the company talking about itself".
COMPANY_VOICE = {"company_filing", "exchange_filing", "transcript", "investor_presentation"}


@dataclass
class Source:
    id: str
    url: str
    title: str
    publisher: str
    type: str
    published: str | None = None      # ISO date (YYYY-MM-DD) or partial (YYYY-MM)
    entity: str | None = None         # whose document this is (company, counterparty, regulator)
    retrieved_at: str | None = None
    via: str | None = None            # how the agent found it (search query id, crawl, API)

    @property
    def rank(self) -> int:
        return SOURCE_RANK.get(self.type, 5)

    @property
    def family(self) -> str:
        """Independence key. Two sources in the same family are not independent.

        All of the company's own documents are one family ("company"); each
        regulator, counterparty and publisher is its own family.
        """
        if self.type in COMPANY_VOICE:
            return "company"
        if self.type == "regulator":
            return f"regulator:{(self.entity or self.publisher).lower()}"
        if self.type == "counterparty":
            return f"counterparty:{(self.entity or self.publisher).lower()}"
        return f"{self.type}:{self.publisher.lower()}"


@dataclass
class Evidence:
    """A specific passage or figure inside a Source."""
    id: str
    source: str                       # Source.id
    kind: str                         # figure | quote | event | claim | headline | context
    text: str                         # the passage, as retrieved
    stance: str = "neutral"           # supports | contradicts | neutral (set per hypothesis later)
    verified: bool | None = None      # set by the verifier
    note: str | None = None


@dataclass
class Observation:
    """One numeric data point, e.g. North America revenue in FY26."""
    entity: str
    metric: str
    period: str                       # FY26, Q1FY27, 2026-06 ...
    value: float
    unit: str
    evidence: str                     # Evidence.id
    dims: dict[str, str] = field(default_factory=dict)   # e.g. {"segment": "North America"}
    derived: bool = False             # back-computed (e.g. from a reported growth rate)
    tolerance: float = 0.0            # relative error band for derived values
    note: str | None = None

    def key(self) -> tuple:
        return (self.entity, self.metric, self.period, tuple(sorted(self.dims.items())))


@dataclass
class Event:
    date: str                         # ISO or partial ISO
    kind: str                         # regulatory | disclosure | results | policy | acquisition | launch | loe | guidance | media | operations
    title: str
    evidence: list[str]
    actor: str | None = None          # who acted (FDA, the company, a partner, a ministry)
    subject: str | None = None        # product / plant / segment it concerns
    tags: list[str] = field(default_factory=list)
    date_precision: str = "day"       # day | month | quarter
    short: str | None = None          # short label for prose


@dataclass
class Link:
    """A relationship between the company and another entity (hidden dependencies live here)."""
    type: str                         # manufacturing_partner | supplier | customer | acquirer | payor | licensor
    a: str
    b: str
    evidence: list[str]
    product: str | None = None
    segment: str | None = None        # which reported segment the product sits in
    note: str | None = None


@dataclass
class Claim:
    """Something management (or a third party) asserts, to be tested against numbers."""
    speaker: str
    text: str
    evidence: str
    topic: str | None = None


@dataclass
class Guidance:
    metric: str
    period: str
    before: float | None
    after: float | None
    unit: str
    evidence: list[str]
    subject: str | None = None
    label: str | None = None          # how the source states it, e.g. "6-7"


@dataclass
class Calculation:
    id: str
    name: str
    formula: str
    inputs: list[str]                 # Observation keys rendered as strings, or calc ids
    value: float
    unit: str
    evidence: list[str]               # union of evidence behind the inputs
    note: str | None = None


@dataclass
class Check:
    """A skeptic test run against a hypothesis."""
    name: str
    question: str
    verdict: str                      # survives | weakens | refutes | inconclusive
    detail: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class Hypothesis:
    id: str
    angle: str
    title: str
    statement: str
    facts: dict[str, Any] = field(default_factory=dict)
    support: list[str] = field(default_factory=list)       # Evidence ids
    contra: list[str] = field(default_factory=list)        # Evidence ids
    calcs: list[str] = field(default_factory=list)         # Calculation ids
    events: list[int] = field(default_factory=list)        # indexes into ledger.events
    checks: list[Check] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    status: str = "proposed"          # proposed | tested | selected | supporting | rejected
    rejection_reason: str | None = None


@dataclass
class TraceStep:
    stage: str
    action: str
    detail: str
    refs: list[str] = field(default_factory=list)


def to_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    if isinstance(obj, list):
        return [to_dict(o) for o in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj
