"""Model clients for the review agent.

Both clients return one normalised response shape so the agent loop is provider-agnostic:

    {"stop_reason": "tool_use" | "end_turn" | ...,
     "content": [{"type": "text", "text": ...} | {"type": "tool_use", "id", "name", "input"}],
     "usage": {"input_tokens": int, "output_tokens": int}}

* :class:`AnthropicClient` calls the Claude Messages API (key from ``ANTHROPIC_API_KEY``, model
  from ``GHOST_AUDIT_MODEL``); it is only constructed when the agent is switched on.
* :class:`OpenAICompatibleClient` calls any OpenAI-compatible chat-completions endpoint with
  function calling; the default is Groq (key from ``GROQ_API_KEY``, model ``openai/gpt-oss-120b``).
  It converts the conversation to that format and back, and waits out rate limits.
* :class:`ScriptedClient` replays fixed responses; the unit tests and ``--agent dry-run`` use it.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
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
    lock: object = field(default_factory=threading.Lock, repr=False)

    def add(self, model: str, usage: dict):
        """Record one call's token usage (thread-safe: parallel reviewers share one meter)."""
        with self.lock:
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


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
MAX_RATE_LIMIT_WAITS = 12
PACE_MIN_TOKENS = 4000       # below this many tokens left in the minute window, wait for the reset
# transient HTTP statuses: timeouts, too-large-for-this-minute, rate limits, provider capacity, 5xx
RETRYABLE_STATUS = {408, 409, 413, 425, 429, 498, 499, 500, 502, 503, 504}


class ToolCallFormatError(Exception):
    """The provider rejected the model's tool call as malformed (HTTP 400 tool_use_failed)."""


def _seconds(value) -> float | None:
    """Parse '7.66s', '1m26.4s', '2' (seconds) into a float; None when absent or unreadable."""
    if not value:
        return None
    total, num = 0.0, ""
    for ch in str(value):
        if ch.isdigit() or ch == ".":
            num += ch
        elif ch == "m" and num:
            total, num = total + float(num) * 60, ""
        elif ch == "s" and num:
            total, num = total + float(num), ""
        elif ch == "h" and num:
            total, num = total + float(num) * 3600, ""
    if num:
        total += float(num)
    return total or None


def to_openai_messages(system: str, messages: list) -> list:
    """Our Anthropic-style history -> OpenAI chat messages (tool calls and tool results mapped)."""
    out = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in content if b.get("type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif isinstance(content, str):
            out.append({"role": "user", "content": content})
        else:
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
            notes = [b["text"] for b in content if b.get("type") == "text"]
            if notes:
                out.append({"role": "user", "content": "\n".join(notes)})
    return out


def to_openai_tools(tools: list) -> list:
    """Anthropic tool specs -> OpenAI function specs."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


def from_openai_response(data: dict) -> dict:
    """OpenAI chat completion -> our normalised response.  Unparseable tool arguments become
    ``{"_unparsed": text}`` so validation rejects them and the loop asks again."""
    choice = data["choices"][0]
    msg = choice.get("message") or {}
    content = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {"_unparsed": fn.get("arguments", "")}
        content.append({"type": "tool_use", "id": call.get("id", ""), "name": fn.get("name", ""), "input": args})
    usage = data.get("usage") or {}
    stop = "tool_use" if choice.get("finish_reason") == "tool_calls" else choice.get("finish_reason", "")
    return {"stop_reason": stop, "content": content,
            "usage": {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}}


class OpenAICompatibleClient:
    """Function-calling chat completions over HTTPS (stdlib only); Groq by default."""

    def __init__(self, model: str | None = None, meter: UsageMeter | None = None, max_tokens: int = 1024,
                 url: str = GROQ_URL, key_env: str = "GROQ_API_KEY", api_key: str | None = None):
        self.api_key = api_key or os.environ.get(key_env)
        if not self.api_key:
            raise RuntimeError(f"{key_env} is not set; the review agent needs it (or use --agent off)")
        self.url = url
        self.model = model or os.environ.get("GHOST_AUDIT_MODEL", GROQ_DEFAULT_MODEL)
        self.meter = meter or UsageMeter()
        self.max_tokens = max_tokens

    def _post(self, body: dict) -> dict:
        """POST with waits on rate limits (429, honouring retry-after) and transient 5xx errors; also
        paces itself when the per-minute token budget reported in the response headers runs low."""
        data = json.dumps(body).encode()
        for attempt in range(MAX_RATE_LIMIT_WAITS + 1):
            req = urllib.request.Request(self.url, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "User-Agent": "ghost-audit/1.0"})  # the default urllib agent is blocked by some API gateways
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    payload = json.loads(resp.read())
                    self._pace(resp.headers)
                    return payload
            except urllib.error.HTTPError as err:
                detail = err.read()[:500].decode("utf-8", "replace")
                if err.code == 400 and "tool_use_failed" in detail:
                    raise ToolCallFormatError(detail) from None
                if err.code not in RETRYABLE_STATUS or attempt == MAX_RATE_LIMIT_WAITS:
                    raise RuntimeError(f"model API error {err.code}: {detail[:300]}") from None
                wait = _seconds(err.headers.get("retry-after")) or 2 ** min(attempt, 5)
                time.sleep(min(max(wait, 1.0), 60.0))
            except urllib.error.URLError:
                if attempt == MAX_RATE_LIMIT_WAITS:
                    raise
                time.sleep(2 ** min(attempt, 5))
        raise RuntimeError("unreachable")

    @staticmethod
    def _pace(headers):
        """Sleep until the token window resets when fewer than PACE_MIN_TOKENS remain."""
        remaining = headers.get("x-ratelimit-remaining-tokens")
        if remaining is not None and remaining.isdigit() and int(remaining) < PACE_MIN_TOKENS:
            time.sleep(min(_seconds(headers.get("x-ratelimit-reset-tokens")) or 5.0, 60.0))

    def create(self, system: str, messages: list, tools: list) -> dict:
        """One model turn in OpenAI format, normalised back.  A tool call the provider could not parse
        comes back as a turn without tool calls, so the agent loop asks again (bounded)."""
        body = {"model": self.model, "temperature": 0, "max_tokens": self.max_tokens,
                "messages": to_openai_messages(system, messages), "tools": to_openai_tools(tools),
                "tool_choice": "auto"}
        try:
            resp = from_openai_response(self._post(body))
        except ToolCallFormatError:
            return {"stop_reason": "tool_use_failed", "usage": {"input_tokens": 0, "output_tokens": 0},
                    "content": [{"type": "text", "text": "(the provider could not parse the tool call)"}]}
        self.meter.add(self.model, resp["usage"])
        return resp


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
