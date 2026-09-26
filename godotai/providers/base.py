"""Provider abstraction — the agent loop is model-agnostic.

Two implementations ship: :mod:`openai_compat` (the default — *your own* model
server speaking the OpenAI chat-completions dialect: vLLM, Ollama, llama.cpp …, or
any hosted OpenAI-compatible endpoint) and :mod:`anthropic` (Claude Messages API,
an explicit opt-in used as the *reference* model for ``eval compare``; it must be
declared as ``[model].kind = "vendor_api"`` — it is never presented as your model).
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
    progress: list[str] = field(default_factory=list)   # progress-update blocks (display="updates"), user-facing
    notices: list[Any] = field(default_factory=list)    # e.g. input_transformations (dropped thinking blocks)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


RETRY_AFTER_MAX = 120.0     # seconds: the longest a provider's Retry-After is honoured (free tiers ask for a minute or two)


def retry_after_seconds(headers: Any) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form; HTTP-date is ignored) → seconds, or None."""
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except AttributeError:
        return None
    if not raw:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    return max(0.0, value) if value == value else None      # NaN guard


def http_json_transport(url: str, headers: dict[str, str], body: dict[str, Any],
                        retries: int = 5, timeout: int = 600) -> dict[str, Any]:
    """POST JSON with exponential backoff on 408/409/429/5xx.

    Free-allowance endpoints answer ``429`` with a ``Retry-After``; when present (and ≤ RETRY_AFTER_MAX) it replaces
    the computed delay, so the agent waits exactly as long as the provider asks instead of hammering the quota.
    """
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
                asked = retry_after_seconds(getattr(exc, "headers", None))
                time.sleep(min(asked, RETRY_AFTER_MAX) if asked is not None else delay)
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
    supports_effort_messages: bool = False   # per-message effort changes (Claude Fable 5.1 beta)

    def __init__(self, model: str, max_tokens: int = 16000, effort: str = "high",
                 transport: Transport | None = None, strict_tools: bool = False,
                 extra_headers: dict[str, str] | None = None):
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self.strict_tools = strict_tools
        self.transport: Transport = transport or http_json_transport
        self.extra_headers: dict[str, str] = dict(extra_headers or {})

    @abstractmethod
    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn: ...

    @abstractmethod
    def user_message(self, text: str) -> dict[str, Any]: ...

    @abstractmethod
    def tool_results_message(self, results: list[tuple[ToolCall, str, bool]],
                             nudge: str | None = None) -> list[dict[str, Any]]:
        """Return the message(s) carrying tool results back to the model.

        *nudge* is a short instruction for the next turn (e.g. the documented batching
        sentence). Providers place it where their API expects it; it must never edit
        earlier messages (append-only history).
        """

    def effort_message(self, effort: str) -> dict[str, Any] | None:
        """Message that switches effort from the next user turn on; None when unsupported."""
        return None

    @abstractmethod
    def build_request(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """Exposed for tests: the JSON body that would be sent."""
