"""Claude Messages API provider, written against the Claude Fable 5.1 notes
(platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1, read 2026-09-26):

* ``tool_choice`` must stay ``auto`` — forced tool use (``any``/``tool``) returns 400.
* ``thinking`` is adaptive and always on — do **not** send ``thinking.budget_tokens``.
* ``temperature``/``top_p``/``top_k`` must stay default — never sent.
* The conversation must be append-only; ``system`` and ``tools`` must not change
  between requests (thinking blocks are bound to that prefix). We therefore keep
  them constant and return the *whole* assistant content (including thinking
  blocks and their signatures) so it is replayed verbatim.
* Depth of reasoning is controlled with the ``effort`` parameter.
* ``stop_reason: "refusal"`` must be handled.
"""
from __future__ import annotations

import os
from typing import Any

from .base import ModelTurn, Provider, ProviderError, ToolCall

DEFAULT_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None, **kw: Any):
        super().__init__(model, **kw)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.url = (base_url.rstrip("/") + "/v1/messages") if base_url else DEFAULT_URL

    # ------------------------------------------------------------------
    def build_request(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        tool_defs = []
        for t in tools:
            d = {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
            if self.strict_tools:
                d["strict"] = True
            tool_defs.append(d)
        if tool_defs:
            # cache the (constant) tool prefix + system prompt
            tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": tool_defs,
            "tool_choice": {"type": "auto"},
        }
        if self.effort and self.effort != "default":
            body["output_config"] = {"effort": self.effort}
        return body

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        body = self.build_request(system, messages, tools)
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION}
        data = self.transport(self.url, headers, body)
        if data.get("type") == "error":
            raise ProviderError(str(data.get("error")))
        content = data.get("content", [])
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(block["id"], block["name"], dict(block.get("input") or {})))
        stop = data.get("stop_reason") or "end_turn"
        return ModelTurn(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            stop_reason=stop,
            raw_assistant_message={"role": "assistant", "content": content},
            usage=data.get("usage", {}),
            refusal=(stop == "refusal"),
        )

    def user_message(self, text: str) -> dict[str, Any]:
        return {"role": "user", "content": [{"type": "text", "text": text}]}

    def tool_results_message(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        blocks = [{"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error}
                  for call, content, is_error in results]
        return [{"role": "user", "content": blocks}]
