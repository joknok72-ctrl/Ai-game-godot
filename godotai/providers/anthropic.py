"""Claude Messages API provider, written against the Claude Fable 5.1 docs
(platform.claude.com — what's-new, thinking, effort, task-budgets,
mid-conversation-system-messages, preserved-thinking, prompting guide; read 2026-09-26):

* ``tool_choice`` must stay ``auto`` — forced tool use (``any``/``tool``) returns 400.
* ``thinking`` is adaptive and always on — never send ``budget_tokens`` or ``type: disabled``.
  The only thing we may set is ``display`` (``omitted`` default; ``updates`` is beta).
* ``temperature``/``top_p``/``top_k`` must stay default — never sent.
* The conversation must be append-only; ``system`` and ``tools`` must not change
  between requests (thinking blocks are bound to that prefix). We therefore keep
  them constant and return the *whole* assistant content (including thinking
  blocks and their signatures) so it is replayed verbatim.
* Depth of reasoning is controlled with ``output_config.effort``; a later change
  is made with an *effort-only system message* (beta), never by editing history.
* ``stop_reason: "refusal"`` must be handled.

Beta features are opt-in (see ``AgentConfig``); each adds exactly the header the
docs name, comma-joined in ``anthropic-beta``.
"""
from __future__ import annotations

import os
from typing import Any

from .base import ModelTurn, Provider, ProviderError, ToolCall

DEFAULT_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

BETA_PROGRESS_UPDATES = "thinking-display-updates-2026-08-18"
BETA_TASK_BUDGETS = "task-budgets-2026-03-13"
BETA_TURN_SCOPED_SYSTEM = "mid-conversation-system-clear-at-2026-08-21"
BETA_PER_MESSAGE_EFFORT = "mid-conversation-output-config-2026-07-01"
BETA_BINDING_CONTROLS = "thinking-binding-controls-2026-08-01"


class AnthropicProvider(Provider):
    name = "anthropic"
    supports_effort_messages = True

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None,
                 progress_updates: bool = False, task_budget_tokens: int | None = None,
                 turn_scoped_system: bool = False, prefix_binding_drop: bool = False,
                 per_message_effort: bool = False, **kw: Any):
        super().__init__(model, **kw)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.url = (base_url.rstrip("/") + "/v1/messages") if base_url else DEFAULT_URL
        self.progress_updates = progress_updates
        self.task_budget_tokens = task_budget_tokens
        self.turn_scoped_system = turn_scoped_system
        self.prefix_binding_drop = prefix_binding_drop
        self.per_message_effort = per_message_effort

    # ------------------------------------------------------------------
    def betas(self) -> list[str]:
        out: list[str] = []
        if self.progress_updates:
            out.append(BETA_PROGRESS_UPDATES)
        if self.task_budget_tokens:
            out.append(BETA_TASK_BUDGETS)
        if self.turn_scoped_system:
            out.append(BETA_TURN_SCOPED_SYSTEM)
        if self.per_message_effort:
            out.append(BETA_PER_MESSAGE_EFFORT)
        if self.prefix_binding_drop:
            out.append(BETA_BINDING_CONTROLS)
        return out

    @property
    def gateway_stored_key(self) -> bool:
        """Cloudflare AI Gateway BYOK: the Anthropic key is stored in the gateway, only
        ``cf-aig-authorization`` travels (docs: "With Stored Keys (BYOK)", read 2026-09-26)."""
        return not self.api_key and "cf-aig-authorization" in self.extra_headers

    def headers(self) -> dict[str, str]:
        h = {"anthropic-version": API_VERSION, **self.extra_headers}
        if self.api_key:
            h["x-api-key"] = self.api_key
        betas = self.betas()
        if betas:
            h["anthropic-beta"] = ",".join(betas)
        return h

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
        output_config: dict[str, Any] = {}
        if self.effort and self.effort != "default":
            output_config["effort"] = self.effort
        if self.task_budget_tokens:
            output_config["task_budget"] = {"type": "tokens", "total": int(self.task_budget_tokens)}
        if output_config:
            body["output_config"] = output_config
        thinking: dict[str, Any] = {}
        if self.progress_updates:
            thinking.update({"type": "adaptive", "display": "updates"})
        if self.prefix_binding_drop:
            thinking.setdefault("type", "adaptive")
            thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
        if thinking:
            body["thinking"] = thinking
        return body

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        if not self.api_key and not self.gateway_stored_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set (or use a Cloudflare AI Gateway with stored keys + CF_AIG_TOKEN)")
        body = self.build_request(system, messages, tools)
        data = self.transport(self.url, self.headers(), body)
        if data.get("type") == "error":
            raise ProviderError(str(data.get("error")))
        content = data.get("content", [])
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        progress: list[str] = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text", ""))
            elif kind == "tool_use":
                calls.append(ToolCall(block["id"], block["name"], dict(block.get("input") or {})))
            elif kind == "thinking" and self.progress_updates and (block.get("thinking") or "").strip():
                # under display="updates" any thinking block with text is a progress update
                progress.append(block["thinking"].strip())
        stop = data.get("stop_reason") or "end_turn"
        notices = list(data.get("input_transformations") or [])
        return ModelTurn(
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            stop_reason=stop,
            raw_assistant_message={"role": "assistant", "content": content},
            usage=data.get("usage", {}),
            refusal=(stop == "refusal"),
            progress=progress,
            notices=notices,
        )

    def user_message(self, text: str) -> dict[str, Any]:
        return {"role": "user", "content": [{"type": "text", "text": text}]}

    def tool_results_message(self, results: list[tuple[ToolCall, str, bool]],
                             nudge: str | None = None) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_error}
            for call, content, is_error in results]
        if nudge and not self.turn_scoped_system:
            # documented non-beta placement: a text block after the tool_result blocks, same user message
            blocks.append({"type": "text", "text": nudge})
        out: list[dict[str, Any]] = [{"role": "user", "content": blocks}]
        if nudge and self.turn_scoped_system:
            # documented beta placement: turn-scoped system message; earlier copies stay byte-for-byte
            out.append({"role": "system", "clear_at": "next_user_message", "content": nudge})
        return out

    def effort_message(self, effort: str) -> dict[str, Any] | None:
        if not self.per_message_effort:
            return None
        # effort-only system message: takes effect from the next user turn, keeps the prompt cache
        return {"role": "system", "content": [], "output_config": {"effort": effort}}
