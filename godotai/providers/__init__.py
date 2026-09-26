from __future__ import annotations

from ..config import AgentConfig
from .base import ModelTurn, Provider, ProviderError, ToolCall


def make_provider(agent_cfg: AgentConfig, **overrides) -> Provider:
    kw = dict(model=agent_cfg.model, max_tokens=agent_cfg.max_tokens, effort=agent_cfg.effort,
              strict_tools=agent_cfg.strict_tools, base_url=agent_cfg.base_url)
    extra_headers: dict[str, str] = {}

    # optional Cloudflare routing — URLs from env var *names*, no values in the repo
    if agent_cfg.route == "cf_gateway":
        from . import cloudflare
        if not kw["base_url"]:
            kw["base_url"] = cloudflare.gateway_base_url("anthropic" if agent_cfg.provider == "anthropic" else "compat")
        extra_headers.update(cloudflare.gateway_headers())
    elif agent_cfg.route == "workers_ai":
        from . import cloudflare
        if not kw["base_url"]:
            kw["base_url"] = cloudflare.workers_ai_base_url()
        kw.setdefault("api_key", cloudflare.workers_ai_api_key())

    kw["extra_headers"] = extra_headers
    kw.update(overrides)
    if agent_cfg.provider == "anthropic":
        from .anthropic import AnthropicProvider
        kw.setdefault("progress_updates", agent_cfg.progress_updates)
        kw.setdefault("task_budget_tokens", agent_cfg.task_budget_tokens)
        kw.setdefault("turn_scoped_system", agent_cfg.turn_scoped_system)
        kw.setdefault("prefix_binding_drop", agent_cfg.prefix_binding_drop)
        kw.setdefault("per_message_effort", bool(agent_cfg.act_effort and agent_cfg.act_effort != agent_cfg.effort))
        return AnthropicProvider(**kw)
    if agent_cfg.provider == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(**kw)
    raise ProviderError(f"unknown provider {agent_cfg.provider!r}")


__all__ = ["ModelTurn", "Provider", "ProviderError", "ToolCall", "make_provider"]
