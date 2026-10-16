"""The evidence ledger: single source of truth for a run.

Every number, date and passage in the final output must resolve to an entry
here. The ledger also detects conflicting figures (same metric, same period,
different values) and resolves them by source hierarchy, keeping an audit log
of what was overruled and why.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import periods
from .models import (Calculation, Claim, Event, Evidence, Guidance, Link,
                     Observation, Source, TraceStep, to_dict)


@dataclass
class Conflict:
    key: str
    kept: Observation
    dropped: list[Observation]
    reason: str


@dataclass
class EvidenceLedger:
    company: str
    sources: dict[str, Source] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    observations: list[Observation] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    guidance: list[Guidance] = field(default_factory=list)
    calcs: dict[str, Calculation] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    _calc_ids: itertools.count = field(default_factory=lambda: itertools.count(1), repr=False)

    # ------------------------------------------------------------------ add
    def add_source(self, s: Source) -> Source:
        self.sources.setdefault(s.id, s)
        return self.sources[s.id]

    def add_evidence(self, e: Evidence) -> Evidence:
        if e.source not in self.sources:
            raise KeyError(f"evidence {e.id} cites unknown source {e.source}")
        self.evidence.setdefault(e.id, e)
        return self.evidence[e.id]

    def observe(self, o: Observation) -> None:
        if o.evidence not in self.evidence:
            raise KeyError(f"observation {o.metric}/{o.period} cites unknown evidence {o.evidence}")
        self.observations.append(o)

    def log(self, stage: str, action: str, detail: str, refs: list[str] | None = None) -> None:
        self.trace.append(TraceStep(stage, action, detail, refs or []))

    # --------------------------------------------------------------- lookup
    def source_of(self, evidence_id: str) -> Source:
        return self.sources[self.evidence[evidence_id].source]

    def families(self, evidence_ids) -> set[str]:
        return {self.source_of(e).family for e in evidence_ids if e in self.evidence}

    def get(self, metric: str, period: str, entity: str | None = None, **dims) -> Observation | None:
        entity = entity or self.company
        for o in self.observations:
            if o.entity == entity and o.metric == metric and o.period == period and \
                    all(o.dims.get(k) == v for k, v in dims.items()) and len(o.dims) == len(dims):
                return o
        return None

    def series(self, metric: str, entity: str | None = None, freq: str | None = None, **dims) -> list[Observation]:
        entity = entity or self.company
        out = [o for o in self.observations
               if o.entity == entity and o.metric == metric
               and all(o.dims.get(k) == v for k, v in dims.items()) and len(o.dims) == len(dims)
               and (freq is None or periods.frequency(o.period) == freq)]
        return sorted(out, key=lambda o: periods.sort_key(o.period))

    def dim_values(self, metric: str, dim: str, entity: str | None = None) -> list[str]:
        entity = entity or self.company
        seen: list[str] = []
        for o in self.observations:
            if o.entity == entity and o.metric == metric and dim in o.dims and o.dims[dim] not in seen:
                seen.append(o.dims[dim])
        return seen

    def events_where(self, kind: str | None = None, tag: str | None = None, actor_not: str | None = None) -> list[tuple[int, Event]]:
        out = []
        for i, ev in enumerate(self.events):
            if kind and ev.kind != kind:
                continue
            if tag and tag not in ev.tags and tag != (ev.subject or ""):
                continue
            if actor_not and (ev.actor or "") == actor_not:
                continue
            out.append((i, ev))
        return sorted(out, key=lambda t: periods.parse_date(t[1].date))

    # ---------------------------------------------------------------- calcs
    def calc(self, name: str, formula: str, value: float, unit: str,
             inputs: list[Observation | Calculation], note: str | None = None) -> Calculation:
        ev: list[str] = []
        keys: list[str] = []
        for i in inputs:
            if isinstance(i, Observation):
                ev.append(i.evidence)
                keys.append(f"{i.metric}[{','.join(i.dims.values())}]@{i.period}")
            else:
                ev.extend(i.evidence)
                keys.append(i.id)
        cid = f"C{next(self._calc_ids)}"
        c = Calculation(cid, name, formula, keys, value, unit, sorted(set(ev)), note)
        self.calcs[cid] = c
        return c

    # ------------------------------------------------------------ conflicts
    def resolve_conflicts(self, rel_tol: float = 0.01) -> list[Conflict]:
        """Group observations by key; if values disagree beyond tolerance, keep the
        one from the highest-ranked source (then the non-derived one, then the
        most recent publication) and record the conflict."""
        groups: dict[tuple, list[Observation]] = {}
        for o in self.observations:
            groups.setdefault(o.key(), []).append(o)
        kept: list[Observation] = []
        for key, obs in groups.items():
            if len(obs) == 1:
                kept.append(obs[0])
                continue
            base = obs[0].value
            agree = all(abs(o.value - base) <= max(rel_tol, o.tolerance, obs[0].tolerance) * max(abs(base), 1e-9) for o in obs)

            def rank(o: Observation):
                s = self.source_of(o.evidence)
                return (s.rank, o.derived, -(periods.parse_date(s.published).toordinal() if s.published else 0))

            obs_sorted = sorted(obs, key=rank)
            winner = obs_sorted[0]
            kept.append(winner)
            if not agree:
                ws = self.source_of(winner.evidence)
                reason = (f"kept {winner.value:g} from {ws.publisher} ({ws.type}, rank {ws.rank}); "
                          + "; ".join(f"dropped {o.value:g} from {self.source_of(o.evidence).publisher} "
                                      f"({self.source_of(o.evidence).type})" for o in obs_sorted[1:]))
                c = Conflict(f"{key[1]}{list(dict(key[3]).values())}@{key[2]}", winner, obs_sorted[1:], reason)
                self.conflicts.append(c)
                self.log("verify", "conflict-resolved", reason, [winner.evidence] + [o.evidence for o in obs_sorted[1:]])
        self.observations = kept
        return self.conflicts

    # --------------------------------------------------------------- export
    def to_json(self) -> dict:
        return {
            "company": self.company,
            "sources": {k: to_dict(v) | {"family": v.family, "rank": v.rank} for k, v in self.sources.items()},
            "evidence": {k: to_dict(v) for k, v in self.evidence.items()},
            "observations": to_dict(self.observations),
            "events": to_dict(self.events),
            "links": to_dict(self.links),
            "claims": to_dict(self.claims),
            "guidance": to_dict(self.guidance),
            "calcs": {k: to_dict(v) for k, v in self.calcs.items()},
            "conflicts": [{"key": c.key, "reason": c.reason, "kept": to_dict(c.kept),
                           "dropped": to_dict(c.dropped)} for c in self.conflicts],
            "trace": to_dict(self.trace),
        }
