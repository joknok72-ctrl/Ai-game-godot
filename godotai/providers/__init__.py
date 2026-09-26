from __future__ import annotations

from ..config import AgentConfig
from .base import ModelTurn, Provider, ProviderError, ToolCall


def make_provider(agent_cfg: AgentConfig, **overrides) -> Provider:
    kw = dict(model=agent_cfg.model, max_tokens=agent_cfg.max_tokens, effort=agent_cfg.effort,
              strict_tools=agent_cfg.strict_tools, base_url=agent_cfg.base_url)
    kw.update(overrides)
    if agent_cfg.provider == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(**kw)
    if agent_cfg.provider == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(**kw)
    raise ProviderError(f"unknown provider {agent_cfg.provider!r}")


__all__ = ["ModelTurn", "Provider", "ProviderError", "ToolCall", "make_provider"]
