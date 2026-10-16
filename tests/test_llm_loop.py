"""The live investigator loop, exercised against a fake Anthropic client (no network)."""
from types import SimpleNamespace as NS

from signalrx.agent.llm_agent import Investigator
from signalrx.ledger import EvidenceLedger
from signalrx.llm.claude import Claude
from signalrx.tools.documents import Doc


class FakeMessages:
    def __init__(self, script):
        self.script, self.calls = list(script), []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.beta = NS(messages=FakeMessages(script))


def tool_use(i, name, inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


SRC = {"url": "https://example.com/results.pdf", "source_title": "Q1 results", "publisher": "Co", "source_type": "company_filing",
       "published": "2026-07-23", "entity_of_document": "company"}


def test_grounded_records_kept_and_inventions_rejected():
    good = dict(SRC, metric="revenue_segment", period="Q1FY27", value=162.0, value_as_quoted=162.0, unit="USD_mn", entity=None,
                segment="North America", basis=None, product=None, specialty=None, item=None, by=None, bound=None, policy=None,
                is_estimate=False, note=None, quote="North America revenue of $162 Mn for the quarter")
    invented = dict(good, value=200.0, value_as_quoted=200.0, quote="North America revenue of $200 Mn for the quarter")
    script = [
        NS(stop_reason="tool_use", content=[tool_use(1, "record_figure", good), tool_use(2, "record_figure", invented)]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="done")]),
    ]
    client = FakeClient(script)
    led = EvidenceLedger("Co")
    inv = Investigator(led, fetch=None, llm=Claude(client=client))
    inv.index.add(Doc(SRC["url"], "r", "Highlights. North America revenue of $162 Mn for the quarter, down 28%."))
    out = inv.investigate("Co", "test", "q", [], [], budget=5)
    assert len(led.observations) == 1 and led.observations[0].value == 162.0
    assert len(inv.rejections) == 1 and "quote" in inv.rejections[0]
    # both tool results returned to the model in ONE user message, failed one flagged
    msgs = client.beta.messages.calls[1]["messages"]
    results = [m for m in msgs if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(results) == 1 and len(results[0]["content"]) == 2
    assert all(r["type"] == "tool_result" for r in results[0]["content"])
    # request shape: adaptive thinking, fallbacks on, server web search declared
    first = client.beta.messages.calls[0]
    assert first["thinking"] == {"type": "adaptive"} and first["fallbacks"] == "default"
    assert any(t.get("type") == "web_search_20260209" for t in first["tools"])
    assert out["tool_calls"][0]["ok"]


def test_pause_turn_resumes():
    script = [NS(stop_reason="pause_turn", content=[NS(type="server_tool_use", name="web_search", input={"query": "x"})]),
              NS(stop_reason="end_turn", content=[NS(type="text", text="ok")])]
    c = Claude(client=FakeClient(script))
    tr = c.agent("sys", "user", [], max_turns=4)
    assert tr.turns == 2 and tr.final_text == "ok" and tr.searches[0]["query"] == "x"
