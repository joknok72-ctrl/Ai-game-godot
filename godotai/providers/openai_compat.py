"""OpenAI-compatible chat-completions provider.

Works with OpenAI, OpenRouter, vLLM, Ollama (``http://localhost:11434/v1``),
llama.cpp server, etc. — i.e. the way to run this agent on open-weight coding
models (Qwen3-Coder, GLM, DeepSeek, Kimi …) when you do not want a hosted model.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .base import ModelTurn, Provider, ProviderError, ToolCall

DEFAULT_BASE = "https://api.openai.com/v1"
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


class OpenAICompatProvider(Provider):
    name = "openai_compat"

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None,
                 send_reasoning_effort: bool | None = None, **kw: Any):
        super().__init__(model, **kw)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "") or "not-needed"
        base = base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE
        self.url = base.rstrip("/") + "/chat/completions"
        if send_reasoning_effort is None:
            env = os.environ.get("GODOTAI_SEND_REASONING_EFFORT", "0")
            send_reasoning_effort = env not in ("0", "false", "no", "")
        self.send_reasoning_effort = send_reasoning_effort

    def build_request(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
                **({"strict": True} if self.strict_tools else {}),
            }} for t in tools],
            "tool_choice": "auto",
            "max_tokens": self.max_tokens,
        }
        if self.send_reasoning_effort and self.effort in _EFFORT_MAP:
            body["reasoning_effort"] = _EFFORT_MAP[self.effort]
        return body

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        body = self.build_request(system, messages, tools)
        data = self.transport(self.url, {"Authorization": f"Bearer {self.api_key}"}, body)
        if "error" in data and not data.get("choices"):
            raise ProviderError(str(data["error"]))
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append(ToolCall(tc.get("id") or f"call_{len(calls)}", fn.get("name", ""), args))
        text = msg.get("content") or ""
        if isinstance(text, list):  # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        raw = {"role": "assistant", "content": text or None}
        if msg.get("tool_calls"):
            raw["tool_calls"] = msg["tool_calls"]
        if msg.get("reasoning_content"):  # keep reasoning for servers that require replay
            raw["reasoning_content"] = msg["reasoning_content"]
        finish = choice.get("finish_reason") or "stop"
        return ModelTurn(text=text.strip(), tool_calls=calls, stop_reason=finish, raw_assistant_message=raw,
                         usage=data.get("usage", {}), refusal=(finish == "content_filter"))

    def user_message(self, text: str) -> dict[str, Any]:
        return {"role": "user", "content": text}

    def tool_results_message(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        return [{"role": "tool", "tool_call_id": call.id, "content": (("ERROR: " if is_error else "") + content)}
                for call, content, is_error in results]
