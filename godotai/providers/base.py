"""Provider abstraction — the agent loop is model-agnostic.

Two implementations ship: :mod:`anthropic` (Claude Messages API, the default,
tuned for Claude Fable 5.1) and :mod:`openai_compat` (any OpenAI-compatible
chat-completions endpoint: OpenAI, OpenRouter, vLLM, Ollama, llama.cpp …), so
the same specialised agent can run on an open-weight model when preferred.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

Transport = Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]]


class ProviderError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    raw_assistant_message: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    refusal: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def http_json_transport(url: str, headers: dict[str, str], body: dict[str, Any],
                        retries: int = 5, timeout: int = 600) -> dict[str, Any]:
    """POST JSON with exponential backoff on 408/409/429/5xx."""
    data = json.dumps(body).encode()
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            if exc.code in (408, 409, 429, 500, 502, 503, 504, 529) and attempt < retries - 1:
                last = ProviderError(f"HTTP {exc.code}: {detail}")
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            raise ProviderError(f"HTTP {exc.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            last = ProviderError(str(exc))
            if attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
    raise last or ProviderError("request failed")


class Provider(ABC):
    name: str = "base"

    def __init__(self, model: str, max_tokens: int = 16000, effort: str = "high",
                 transport: Transport | None = None, strict_tools: bool = False):
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.strict_tools = strict_tools
        self.transport: Transport = transport or http_json_transport

    @abstractmethod
    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn: ...

    @abstractmethod
    def user_message(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def tool_results_message(self, results: list[tuple[ToolCall, str, bool]]) -> list[dict[str, Any]]:
        """Return the message(s) carrying tool results back to the model."""

    @abstractmethod
    def build_request(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Exposed for tests: the JSON body that would be sent."""
