"""Optional routing through Cloudflare — URLs and headers only, never credentials.

Everything here is derived from *environment variable names*; no account id,
gateway name or token is stored in the repository. Endpoints (Cloudflare docs,
read 2026-09-26):

* AI Gateway, provider-native Anthropic endpoint
  ``https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/anthropic`` + ``/v1/messages``
  — the Anthropic key still travels in ``x-api-key``; an *authenticated gateway*
  additionally wants ``cf-aig-authorization: Bearer <gateway token>``.
  https://developers.cloudflare.com/ai-gateway/usage/providers/anthropic/
  https://developers.cloudflare.com/ai-gateway/configuration/authentication/
* AI Gateway, OpenAI-compatible endpoint
  ``https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/compat`` + ``/chat/completions``
  https://developers.cloudflare.com/ai-gateway/usage/chat-completion/
* Workers AI, OpenAI-compatible endpoint (open-weight models hosted by Cloudflare)
  ``https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1`` + ``/chat/completions``
  with ``Authorization: Bearer <Cloudflare API token>``.
  https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/

What the gateway buys this project: request logs, caching, rate limits and cost
dashboards *per gateway* without code changes. What it does not do: make the
model smarter. Whether a Workers AI model can drive this agent's tool loop must
be measured with ``python3 -m godotai eval`` — it is not assumed.
"""
from __future__ import annotations

import os

ENV_ACCOUNT_ID = "CF_ACCOUNT_ID"          # non-secret identifier
ENV_GATEWAY_ID = "CF_AIG_GATEWAY"         # gateway name, non-secret
ENV_GATEWAY_TOKEN = "CF_AIG_TOKEN"        # secret: authenticated-gateway token (optional)
ENV_WORKERS_AI_TOKEN = "CF_WORKERS_AI_TOKEN"   # secret: a token with *Workers AI: Read* only — the preferred name
ENV_API_TOKEN = "CLOUDFLARE_API_TOKEN"         # secret: legacy fallback; never reuse the Tunnel/Access/DNS setup token here

GATEWAY_BASE = "https://gateway.ai.cloudflare.com/v1"
WORKERS_AI_BASE = "https://api.cloudflare.com/client/v4/accounts"


class CloudflareRouteError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CloudflareRouteError(f"{name} is not set (needed for this route; see docs/USAGE.md)")
    return value


def gateway_base_url(provider: str, account_id: str | None = None, gateway_id: str | None = None) -> str:
    """Base URL for the AI Gateway; *provider* is 'anthropic' or 'compat'."""
    account_id = account_id or _require(ENV_ACCOUNT_ID)
    gateway_id = gateway_id or _require(ENV_GATEWAY_ID)
    if provider not in ("anthropic", "compat"):
        raise CloudflareRouteError(f"unsupported gateway provider path {provider!r}")
    return f"{GATEWAY_BASE}/{account_id}/{gateway_id}/{provider}"


def gateway_headers() -> dict[str, str]:
    """Optional authenticated-gateway header; empty when the gateway is unauthenticated."""
    token = os.environ.get(ENV_GATEWAY_TOKEN, "").strip()
    return {"cf-aig-authorization": f"Bearer {token}"} if token else {}


def workers_ai_base_url(account_id: str | None = None) -> str:
    account_id = account_id or _require(ENV_ACCOUNT_ID)
    return f"{WORKERS_AI_BASE}/{account_id}/ai/v1"


def workers_ai_api_key() -> str:
    """The bearer token for Workers AI: ``CF_WORKERS_AI_TOKEN`` (a token that can *only* run Workers AI), else the
    legacy ``CLOUDFLARE_API_TOKEN``. Keeping a separate name makes it hard to hand the chat container the far more
    powerful Tunnel/Access/DNS setup token by accident."""
    value = os.environ.get(ENV_WORKERS_AI_TOKEN, "").strip()
    if value:
        return value
    value = os.environ.get(ENV_API_TOKEN, "").strip()
    if value:
        return value
    raise CloudflareRouteError(f"{ENV_WORKERS_AI_TOKEN} is not set (a Cloudflare API token with the Workers AI Read "
                               f"permission only; needed for route = workers_ai — see docs/USAGE.md)")
