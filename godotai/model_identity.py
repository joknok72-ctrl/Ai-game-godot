"""What *is* the model behind this AI — said plainly, and enforced.

The owner of this project wants an AI that is **theirs**: specialised in building
games for Godot Engine 4.7.2-stable, running under their control, not "someone
else's model". This module gives that wish a precise, checkable shape:

* ``[model]`` in ``godot.toml`` (or ``GODOTAI_MODEL_*`` env) records what the
  weights behind ``[agent]`` are — an unmodified open-weight model you deploy
  privately, an open-weight base **plus your own fine-tune/adapter**, a model
  trained from scratch (only if it really was), or a vendor's proprietary API.
* :func:`ModelIdentity.describe` produces the *honest label* the chat UI and
  ``doctor`` show (Arabic + English), including what the model is **not**
  (e.g. "not trained from scratch", "third-party model, not yours").
* :func:`check_consistency` refuses configurations that would present a vendor
  model (Claude, GPT, …) as your private model — the one lie this project must
  never tell.

Nothing here makes a claim about *quality*. Whether this model is as good as,
or better than, a reference model on Godot tasks is decided only by
``python3 -m godotai eval compare`` (engine-scored results on the same tasks);
see docs/MODEL.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

KINDS = ("open_weight_deployment", "fine_tune", "from_scratch", "vendor_api")
SERVINGS = ("self_hosted", "managed", "vendor")

_KIND_LABEL_AR = {
    "open_weight_deployment": "نشر خاص لنموذج مفتوح الوزن",
    "fine_tune": "نموذج مفتوح الوزن + تدريب خاص بك (LoRA)",
    "from_scratch": "نموذج مدرَّب من الصفر",
    "vendor_api": "نموذج شركة أخرى عبر API",
}
_KIND_LABEL_EN = {
    "open_weight_deployment": "private deployment of an open-weight model",
    "fine_tune": "open-weight base + your own fine-tune (LoRA adapter)",
    "from_scratch": "model trained from scratch",
    "vendor_api": "third-party vendor model via API",
}
_SERVING_LABEL_AR = {"self_hosted": "على خادمك أنت", "managed": "عند مزوّد استضافة (الأوزان مفتوحة)",
                     "vendor": "عند الشركة صاحبة النموذج"}
_SERVING_LABEL_EN = {"self_hosted": "on your own server", "managed": "at a hosting provider (open weights)",
                     "vendor": "at the vendor"}


class ModelIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class ModelIdentity:
    name: str = "godotai"                                  # what the UI calls *your* model
    kind: str = "open_weight_deployment"                   # see KINDS
    base_model: str = "Qwen/Qwen2.5-Coder-7B-Instruct"     # the weights you start from ("" only for from_scratch)
    base_license: str = "Apache-2.0"                       # licence of those weights (model card, read 2026-09-26)
    adapter: str = ""                                      # your LoRA adapter / fine-tuned weights (fine_tune)
    owner: str = "you"                                     # who controls weights + server
    serving: str = "self_hosted"                           # see SERVINGS
    training_report: str = ""                              # from_scratch: path to the pre-training report
    reference_model: str = "claude-fable-5-1"              # what `eval compare` measures against by default

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ModelIdentityError(f"model.kind must be one of {'|'.join(KINDS)}, got {self.kind!r}")
        if self.serving not in SERVINGS:
            raise ModelIdentityError(f"model.serving must be one of {'|'.join(SERVINGS)}, got {self.serving!r}")
        if not self.name.strip():
            raise ModelIdentityError("model.name must not be empty")
        if self.kind in ("open_weight_deployment", "fine_tune", "vendor_api") and not self.base_model.strip():
            raise ModelIdentityError(f"model.kind = {self.kind!r} needs model.base_model (the weights / vendor model id)")
        if self.kind == "fine_tune" and not self.adapter.strip():
            raise ModelIdentityError("model.kind = 'fine_tune' needs model.adapter (your LoRA adapter / weights); "
                                     "use kind = 'open_weight_deployment' until you have trained one")
        if self.kind == "from_scratch":
            if self.base_model.strip():
                raise ModelIdentityError("model.kind = 'from_scratch' means no base model — clear model.base_model, "
                                         "or use 'fine_tune' / 'open_weight_deployment'")
            if not self.training_report.strip():
                raise ModelIdentityError("model.kind = 'from_scratch' needs model.training_report (path to the report "
                                         "describing data, compute and evaluation of the pre-training run)")
        if self.kind == "vendor_api" and self.serving != "vendor":
            raise ModelIdentityError("model.kind = 'vendor_api' implies model.serving = 'vendor'")
        if self.kind != "vendor_api" and self.serving == "vendor":
            raise ModelIdentityError("model.serving = 'vendor' is only for kind = 'vendor_api'")

    # ------------------------------------------------------------------ facts
    @property
    def yours(self) -> bool:
        """Weights you may copy/modify/run anywhere *and* a server you control."""
        return self.kind in ("open_weight_deployment", "fine_tune", "from_scratch") and self.serving == "self_hosted"

    @property
    def open_weights(self) -> bool:
        return self.kind in ("open_weight_deployment", "fine_tune", "from_scratch")

    @property
    def trained_by_you(self) -> bool:
        return self.kind in ("fine_tune", "from_scratch")

    def kind_label(self, lang: str = "ar") -> str:
        return (_KIND_LABEL_AR if lang == "ar" else _KIND_LABEL_EN)[self.kind]

    def serving_label(self, lang: str = "ar") -> str:
        return (_SERVING_LABEL_AR if lang == "ar" else _SERVING_LABEL_EN)[self.serving]

    # ------------------------------------------------------------------ the honest sentence
    def disclosure(self, lang: str = "ar") -> str:
        base = self.base_model.strip()
        lic = self.base_license.strip() or ("unknown licence" if lang == "en" else "رخصة غير محددة")
        if lang == "ar":
            if self.kind == "open_weight_deployment":
                return (f"«{self.name}» = نموذج مفتوح الوزن {base} (رخصة {lic}) يعمل {self.serving_label('ar')} مع أدوات "
                        f"وفهرس محرك Godot 4.7.2 وتحقق بالمحرك. الأوزان ليست من تدريبك ولم تُدرَّب من الصفر؛ التخصص هنا يأتي "
                        f"من الأدوات والبيانات، وعند تدريب محوّل خاص بك يتغيّر النوع إلى fine_tune.")
            if self.kind == "fine_tune":
                return (f"«{self.name}» = نموذج مفتوح الوزن {base} (رخصة {lic}) + محوّل/تدريب خاص بك ({self.adapter}) على "
                        f"بيانات متحقَّق منها بمحرك Godot، يعمل {self.serving_label('ar')}. ليس مدرَّبًا من الصفر.")
            if self.kind == "from_scratch":
                return (f"«{self.name}» = نموذج مدرَّب من الصفر (تقرير التدريب: {self.training_report}) يعمل "
                        f"{self.serving_label('ar')}.")
            return (f"«{self.name}» يشير إلى {base} — نموذج شركة أخرى عبر API. هذا ليس نموذجك ولا يمكن تنزيله أو تدريبه؛ "
                    f"يُستخدم كمرجع للمقارنة فقط.")
        if self.kind == "open_weight_deployment":
            return (f"'{self.name}' = the open-weight model {base} ({lic}) running {self.serving_label('en')}, with the "
                    f"Godot 4.7.2 tools, API index and engine verification. The weights were not trained by you and not "
                    f"from scratch; the specialisation comes from the harness and data — once you train an adapter the "
                    f"kind becomes fine_tune.")
        if self.kind == "fine_tune":
            return (f"'{self.name}' = the open-weight base {base} ({lic}) plus your own fine-tune/adapter ({self.adapter}) "
                    f"trained on engine-verified Godot data, running {self.serving_label('en')}. Not trained from scratch.")
        if self.kind == "from_scratch":
            return f"'{self.name}' = a model trained from scratch (training report: {self.training_report}), running {self.serving_label('en')}."
        return (f"'{self.name}' points at {base} — a third-party vendor model behind an API. It is not yours, cannot be "
                f"downloaded or trained, and is used here only as a reference to compare against.")

    def describe(self) -> dict[str, Any]:
        """JSON-friendly identity block for ``/api/status`` and ``doctor``."""
        return {
            "name": self.name, "kind": self.kind, "kind_label_ar": self.kind_label("ar"), "kind_label_en": self.kind_label("en"),
            "base_model": self.base_model, "base_license": self.base_license, "adapter": self.adapter or None,
            "owner": self.owner, "serving": self.serving, "serving_label_ar": self.serving_label("ar"),
            "serving_label_en": self.serving_label("en"), "yours": self.yours, "open_weights": self.open_weights,
            "trained_by_you": self.trained_by_you, "reference_model": self.reference_model,
            "disclosure_ar": self.disclosure("ar"), "disclosure_en": self.disclosure("en"),
            # quality is *never* asserted here — see `python3 -m godotai eval compare`
            "quality_claim": None,
        }


# Cloudflare AI Gateway "compat" endpoint addresses models as ``<provider>/<model>``; these prefixes are
# proprietary vendor APIs (docs read 2026-09-26). ``workers-ai/`` (open weights hosted by Cloudflare) is not.
GATEWAY_VENDOR_PREFIXES = ("openai/", "anthropic/", "google-ai-studio/", "google-vertex-ai/", "azure-openai/",
                           "mistral/", "grok/", "cohere/", "perplexity-ai/", "deepseek/", "openrouter/", "groq/",
                           "cerebras/", "aws-bedrock/")


def is_vendor_endpoint(provider: str, base_url: str | None, route: str = "direct", model: str = "") -> bool:
    """True when the configured endpoint is a proprietary vendor API (Anthropic, api.openai.com, a vendor
    behind the AI Gateway). Best effort on purpose: it catches the obvious cases, it does not certify the rest."""
    if provider == "anthropic":
        return True
    if route == "workers_ai":                       # Cloudflare-hosted open weights: managed, not a vendor model
        return False
    if route == "cf_gateway":                       # URL is derived from CF_* env in make_provider; judge by model id
        return model.lower().startswith(GATEWAY_VENDOR_PREFIXES)
    return provider == "openai_compat" and (not base_url or "api.openai.com" in base_url)


def check_consistency(identity: ModelIdentity, provider: str, model: str, base_url: str | None,
                      route: str = "direct") -> None:
    """Refuse to label a vendor model as yours (and the reverse).

    * ``provider = "anthropic"`` is always a vendor API → ``kind`` must be ``vendor_api``.
    * ``openai_compat`` pointed at ``api.openai.com`` (or no URL at all, which defaults to it)
      is a vendor API too.
    * ``kind = "vendor_api"`` with a private/local endpoint is contradictory as well.
    """
    vendor_endpoint = is_vendor_endpoint(provider, base_url, route, model)
    if vendor_endpoint and identity.kind != "vendor_api":
        who = ("Claude (Anthropic)" if provider == "anthropic" else
               f"{model!r} through the AI Gateway" if route == "cf_gateway" else f"{model!r} at api.openai.com")
        raise ModelIdentityError(
            f"[agent] uses {who}, a third-party vendor model, but [model].kind = {identity.kind!r} would present it as "
            f"your own model. Set [model].kind = \"vendor_api\" (and serving = \"vendor\") for a vendor model, or point "
            f"[agent] at your private server (provider = openai_compat, base_url = http://…/v1).")
    if not vendor_endpoint and identity.kind == "vendor_api" and provider == "openai_compat" and base_url \
            and ("localhost" in base_url or "127.0.0.1" in base_url or "://model" in base_url or "://ollama" in base_url):
        raise ModelIdentityError(
            f"[model].kind = 'vendor_api' but [agent].base_url = {base_url!r} is a private endpoint; use kind = "
            f"'open_weight_deployment' or 'fine_tune'.")
