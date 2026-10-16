"""Claude wrapper: structured JSON calls and a manual tool-use loop.

* Model: claude-opus-5-5 by default, adaptive thinking, effort set explicitly.
* Server-side refusal fallbacks are enabled ("default" routing) so a declined
  request is retried on a fallback model inside the same call.
* Search uses Anthropic's server-side web_search tool, so a live run needs only
  ANTHROPIC_API_KEY; fetching stays client-side (tools/http.py) because the
  verifier needs the raw document text to check every quoted passage.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Any]

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema, "strict": True}


@dataclass
class AgentTranscript:
    turns: int = 0
    tool_calls: list[dict] = field(default_factory=list)
    searches: list[dict] = field(default_factory=list)
    final_text: str = ""
    stop_reason: str = ""


class Claude:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high", client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort

    def _create(self, **kw):
        return self.client.beta.messages.create(
            model=self.model, thinking={"type": "adaptive"}, betas=[FALLBACK_BETA], fallbacks="default", **kw)

    # -------------------------------------------------------------- json
    def json(self, system: str, user: str, schema: dict, max_tokens: int = 16000) -> dict:
        resp = self._create(max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}],
                            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}})
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model declined: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("structured output truncated (max_tokens)")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)

    # ------------------------------------------------------------- agent
    def agent(self, system: str, user: str, tools: list[ToolSpec], max_turns: int = 30,
              web_search_uses: int = 8, max_tokens: int = 16000) -> AgentTranscript:
        handlers = {t.name: t for t in tools}
        defs = [t.definition() for t in tools]
        if web_search_uses:
            defs.append({"type": "web_search_20260209", "name": "web_search", "max_uses": web_search_uses,
                         "user_location": {"type": "approximate", "country": "IN"}})
        messages: list[dict] = [{"role": "user", "content": user}]
        tr = AgentTranscript()
        while tr.turns < max_turns:
            tr.turns += 1
            resp = self._create(max_tokens=max_tokens, system=system, messages=messages, tools=defs,
                                output_config={"effort": self.effort})
            tr.stop_reason = resp.stop_reason
            for b in resp.content:
                if b.type == "web_search_tool_result" and isinstance(getattr(b, "content", None), list):
                    tr.searches.append({"results": [{"url": r.url, "title": r.title} for r in b.content]})
                if b.type == "server_tool_use" and getattr(b, "name", "") == "web_search":
                    tr.searches.append({"query": (b.input or {}).get("query")})
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason in ("end_turn", "refusal", "max_tokens"):
                tr.final_text = "".join(b.text for b in resp.content if b.type == "text")
                break
            if resp.stop_reason == "pause_turn":   # server tool hit its iteration limit: resume
                continue
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                spec = handlers.get(b.name)
                try:
                    out = spec.handler(b.input) if spec else {"error": f"unknown tool {b.name}"}
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": out if isinstance(out, str) else json.dumps(out, default=str)[:20000]})
                    tr.tool_calls.append({"tool": b.name, "input": b.input, "ok": True})
                except Exception as e:  # report tool failures back to the model, never drop them
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"error: {e}", "is_error": True})
                    tr.tool_calls.append({"tool": b.name, "input": b.input, "ok": False, "error": str(e)})
            if not results:
                tr.final_text = "".join(b.text for b in resp.content if b.type == "text")
                break
            messages.append({"role": "user", "content": results})
        return tr
