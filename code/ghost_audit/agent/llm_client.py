"""Model clients for the review agent.

Both clients return one normalised response shape so the agent loop is provider-agnostic:

    {"stop_reason": "tool_use" | "end_turn" | ...,
     "content": [{"type": "text", "text": ...} | {"type": "tool_use", "id", "name", "input"}],
     "usage": {"input_tokens": int, "output_tokens": int}}

* :class:`AnthropicClient` calls the Claude Messages API (key from ``ANTHROPIC_API_KEY``, model
  from ``GHOST_AUDIT_MODEL``); it is only constructed when the agent is switched on.
* :class:`ScriptedClient` replays fixed responses; the unit tests and ``--agent dry-run`` use it.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_API_RETRIES = 3          # transient API errors (rate limit, overload) are retried with backoff


@dataclass
class UsageMeter:
    """Counts model calls and tokens per model, for usage_report.md."""
    calls: dict = field(default_factory=lambda: defaultdict(int))
    input_tokens: dict = field(default_factory=lambda: defaultdict(int))
    output_tokens: dict = field(default_factory=lambda: defaultdict(int))

    def add(self, model: str, usage: dict):
        """Record one call's token usage."""
        self.calls[model] += 1
        self.input_tokens[model] += int(usage.get("input_tokens", 0))
        self.output_tokens[model] += int(usage.get("output_tokens", 0))

    def as_dict(self) -> dict:
        """Per-model totals as plain dicts."""
        return {m: {"calls": self.calls[m], "input_tokens": self.input_tokens[m],
                    "output_tokens": self.output_tokens[m]} for m in sorted(self.calls)}


class AnthropicClient:
    """Thin wrapper over ``anthropic.Anthropic().messages.create`` with retries and metering."""

    def __init__(self, model: str | None = None, meter: UsageMeter | None = None, max_tokens: int = 1024):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; the review agent needs it (or use --agent off)")
        import anthropic  # optional dependency, imported only when the agent is on
        self._sdk = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model or os.environ.get("GHOST_AUDIT_MODEL", DEFAULT_MODEL)
        self.meter = meter or UsageMeter()
        self.max_tokens = max_tokens

    def create(self, system: str, messages: list, tools: list) -> dict:
        """One model turn; transient errors are retried up to MAX_API_RETRIES times."""
        for attempt in range(MAX_API_RETRIES + 1):
            try:
                resp = self._client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system,
                                                    messages=messages, tools=tools, temperature=0)
                break
            except (self._sdk.RateLimitError, self._sdk.APIConnectionError, self._sdk.InternalServerError):
                if attempt == MAX_API_RETRIES:
                    raise
                time.sleep(2 ** attempt)
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        self.meter.add(self.model, usage)
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return {"stop_reason": resp.stop_reason, "content": content, "usage": usage}


class ScriptedClient:
    """Replays a fixed list of responses (or a function of the conversation) without any network."""

    def __init__(self, script, model: str = "scripted", meter: UsageMeter | None = None):
        self.script = script
        self.model = model
        self.meter = meter or UsageMeter()
        self.requests: list = []

    def create(self, system: str, messages: list, tools: list) -> dict:
        """Return the next scripted response; usage is estimated as characters / 4."""
        self.requests.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        turn = len(self.requests) - 1
        resp = self.script(turn, messages) if callable(self.script) else self.script[min(turn, len(self.script) - 1)]
        prompt_chars = len(system) + sum(len(str(m.get("content"))) for m in messages) + len(str(tools))
        usage = {"input_tokens": prompt_chars // 4, "output_tokens": len(str(resp.get("content"))) // 4}
        self.meter.add(self.model, usage)
        return dict(resp, usage=usage)
