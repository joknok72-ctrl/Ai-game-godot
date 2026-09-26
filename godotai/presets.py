"""Named presets for *hosted* OpenAI-compatible endpoints that serve Qwen models with a free allowance.

The owner of this project has no budget. This module answers "which Qwen API can I use for free, and what does
that really mean?" in code, so that ``GODOTAI_PRESET=<id>`` is enough to switch the agent to one of them — and so
that the identity card, ``doctor`` and the chat page keep telling the truth about *where* the model runs.

What a preset is **not**:

* it is not a promise of a forever-free service — every allowance below was read from the provider's own pages on
  the ``verified`` date and can change without notice; the numbers are quoted, not guaranteed;
* it is not a credential — presets name the **environment variable** that must hold the key; no key, account id
  or workspace id is stored in this repository, ever;
* it is not private hosting — an open-weight model (Apache-2.0 weights you *could* download) served by OpenRouter,
  Groq, Alibaba Cloud or Cloudflare is ``serving = "managed"``: your prompts leave your machine and are processed
  under that provider's terms (summarised per preset, with the source);
* it is not a quality statement — whether any of these models can drive this agent's tool loop well is decided
  only by ``python3 -m godotai eval compare`` on the same engine-scored tasks (docs/MODEL.md §5).

Precedence when a preset is active (``load_config``): explicit ``GODOTAI_*`` environment variables win over the
preset, and the preset wins over the values written in ``godot.toml`` — a preset is a bundle of *defaults* for one
hosted endpoint, nothing more.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

VERIFIED = "2026-09-26"   # the day every page cited below was read


class PresetError(ValueError):
    pass


@dataclass(frozen=True)
class HostedPreset:
    id: str
    label_ar: str
    label_en: str
    base_url: str | None                 # None → the user must supply GODOTAI_BASE_URL (workspace-specific host)
    key_env: str                         # environment variable that holds the API key — the *name* only
    signup_url: str                      # where the account / key is created
    default_model: str                   # provider-side model id sent in the request
    base_model: str                      # what the weights are (Hugging Face id) — or the vendor model id
    base_license: str                    # licence of those weights ("proprietary" for a vendor model)
    kind: str                            # open_weight_deployment | vendor_api
    serving: str                         # managed | vendor
    owner: str
    free_ar: str                         # what is free — quoted from the provider, with limits
    free_en: str
    data_ar: str                         # what happens to prompts/outputs — quoted or explicitly "not established"
    data_en: str
    risks_ar: str
    risks_en: str
    sources: tuple[str, ...]
    route: str = "direct"                # direct | workers_ai
    required_env: tuple[str, ...] = ()   # non-secret identifiers that must also be set (e.g. CF_ACCOUNT_ID)
    alt_key_envs: tuple[str, ...] = ()   # legacy names accepted for the key
    max_output_tokens: int | None = None # documented hard cap on completion tokens (request is clamped to it)
    base_url_hint: str = ""              # shown when base_url is None
    alternatives: tuple[str, ...] = ()   # other model ids worth trying on the same endpoint (same key)
    verified: str = VERIFIED

    # ------------------------------------------------------------------ env
    def key(self, env: Mapping[str, str] | None = None) -> str:
        """The API key from the environment (first non-empty of key_env / alt_key_envs), or ""."""
        env = os.environ if env is None else env
        for name in (self.key_env, *self.alt_key_envs):
            value = (env.get(name) or "").strip()
            if value:
                return value
        return ""

    def missing_env(self, env: Mapping[str, str] | None = None) -> list[str]:
        env = os.environ if env is None else env
        missing = [] if self.key(env) else [self.key_env]
        missing += [name for name in self.required_env if not (env.get(name) or "").strip()]
        return missing

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id, "label_ar": self.label_ar, "label_en": self.label_en, "base_url": self.base_url,
            "base_url_hint": self.base_url_hint or None, "key_env": self.key_env, "required_env": list(self.required_env),
            "signup_url": self.signup_url, "default_model": self.default_model, "alternatives": list(self.alternatives),
            "base_model": self.base_model, "base_license": self.base_license, "kind": self.kind, "serving": self.serving,
            "route": self.route, "max_output_tokens": self.max_output_tokens, "free_ar": self.free_ar, "free_en": self.free_en,
            "data_ar": self.data_ar, "data_en": self.data_en, "risks_ar": self.risks_ar, "risks_en": self.risks_en,
            "sources": list(self.sources), "verified": self.verified,
            "quality_claim": None,      # never — see `eval compare`
        }


# Model ids on OpenRouter whose weights are proprietary (the vendor's own API behind OpenRouter). Using one of these
# through the OpenRouter preset is allowed — but only declared as kind = vendor_api (godotai/model_identity.py).
OPENROUTER_VENDOR_PREFIXES = ("openai/", "anthropic/", "google/", "x-ai/", "amazon/", "cohere/", "perplexity/")


PRESETS: dict[str, HostedPreset] = {
    "openrouter_free": HostedPreset(
        id="openrouter_free",
        label_ar="OpenRouter — النسخة المجانية من Qwen3.8-27B",
        label_en="OpenRouter — the free variant of Qwen3.8-27B",
        base_url="https://openrouter.ai/api/v1",
        key_env="OPENROUTER_API_KEY",
        signup_url="https://openrouter.ai/settings/keys",
        default_model="qwen/qwen3.8-27b:free",
        alternatives=("qwen/qwen3.8-27b",),      # the paid variant: same weights, no platform request cap
        base_model="Qwen/Qwen3.8-27B",
        base_license="Apache-2.0",
        kind="open_weight_deployment", serving="managed",
        owner="open weights (Qwen, Apache-2.0); server run by OpenRouter's upstream providers",
        free_ar=("الطرازات التي ينتهي اسمها بـ ‎:free‎ مجانية بالكامل ($0 لكل مليون رمز) لكن بحدود منصة: 20 طلبًا في الدقيقة، "
                 "و50 طلبًا في اليوم إن لم تشترِ رصيدًا من قبل، أو 1000 طلب في اليوم بعد شراء 10 دولارات رصيد مرة واحدة. "
                 "كل دورة في حلقة أدوات الوكيل = طلب واحد، فـ 50 طلبًا ≈ تشغيل واحد قصير في اليوم."),
        free_en=("Model ids ending in :free cost $0 per million tokens but are capped by the platform: 20 requests/minute, "
                 "and 50 requests/day until you have bought 10 credits once (then 1,000/day). Every iteration of the "
                 "agent's tool loop is one request, so 50/day ≈ one short run per day."),
        data_ar=("OpenRouter يمرّر طلبك إلى مزوّد خارجي؛ سياسة الاحتفاظ والتدريب تختلف من مزوّد لآخر (صفحة الخصوصية تعرض لكل "
                 "مزوّد: احتفاظ صفري / غير معلوم / 30 يومًا، ويدرّب أم لا). يمكنك من إعدادات الحساب استثناء المزوّدين الذين "
                 "قد يدرّبون على بياناتك أو يحتفظون بها."),
        data_en=("OpenRouter forwards your request to an upstream provider; retention/training differ per provider (the "
                 "privacy page lists each one: zero / unknown / 30-day retention, trains or not). Account settings can "
                 "exclude providers that may train on or retain your data."),
        risks_ar="قد يُزال الطراز المجاني أو يُبطَّأ في أي وقت؛ الحصة اليومية صغيرة جدًا لعمل وكيل يكرّر الطلبات؛ يلزم حساب ومفتاح.",
        risks_en="The free variant can be removed or throttled any day; the daily cap is tiny for an agent that loops; an account + key are required.",
        sources=("https://openrouter.ai/docs/api-reference/limits", "https://openrouter.ai/qwen/qwen3.8-27b:free",
                 "https://openrouter.ai/docs/features/privacy-and-logging", "https://huggingface.co/Qwen/Qwen3.8-27B"),
    ),
    "groq": HostedPreset(
        id="groq",
        label_ar="Groq — Qwen3.8-27B (معاينة) على GroqCloud",
        label_en="Groq — Qwen3.8-27B (Preview) on GroqCloud",
        base_url="https://api.groq.com/openai/v1",
        key_env="GROQ_API_KEY",
        signup_url="https://console.groq.com/keys",
        default_model="qwen/qwen3.8-27b",
        base_model="Qwen/Qwen3.8-27B",
        base_license="Apache-2.0",
        kind="open_weight_deployment", serving="managed",
        owner="open weights (Qwen, Apache-2.0); server run by Groq",
        max_output_tokens=16_384,       # documented maximum completion tokens for this model on Groq
        free_ar=("Groq يقدّم حسابات بلا بطاقة، لكن جدول الحدود المنشور (30 طلبًا/دقيقة، 1000/يوم، 8 آلاف رمز/دقيقة، 200 ألف "
                 "رمز/يوم لهذا الطراز) موصوف في الصفحة نفسها بأنه «الحدود الأساسية لخطة Developer» — حدود حسابك الفعلية "
                 "تظهر فقط في صفحة Limits داخل حسابك. لا نعِد بحصة مجانية محددة."),
        free_en=("Groq offers no-card accounts, but the published table (30 RPM, 1,000 RPD, 8K TPM, 200K TPD for this model) "
                 "is described on that very page as 'the base limits for the Developer plan' — your account's real limits "
                 "appear only on your Limits page. No specific free quota is promised here."),
        data_ar=("لم نجد في صفحات Groq العامة عبارة مختصرة عن احتفاظ المدخلات/المخرجات أو التدريب عليها؛ الشروط تُحيل إلى ملحق "
                 "معالجة البيانات (DPA). جدول OpenRouter (مصدر ثانوي) يصنّف Groq «احتفاظ صفري ولا يدرّب» — تحقّق بنفسك قبل الاعتماد."),
        data_en=("No concise primary statement about input/output retention or training was found on Groq's public pages; the "
                 "terms point to a Data Processing Addendum. OpenRouter's provider table (secondary source) lists Groq as "
                 "zero-retention / no training — verify yourself before relying on it."),
        risks_ar="الطراز مُعلَّم «Preview» (قد يُزال بإشعار قصير)؛ 8 آلاف رمز/دقيقة قد لا تكفي لمطالبات هذا الوكيل الطويلة؛ يلزم حساب ومفتاح.",
        risks_en="The model is labelled Preview (may be removed on short notice); 8K TPM may be too small for this agent's long prompts; account + key required.",
        sources=("https://console.groq.com/docs/model/qwen/qwen3.8-27b", "https://console.groq.com/docs/rate-limits",
                 "https://groq.com/terms-of-use", "https://huggingface.co/Qwen/Qwen3.8-27B"),
    ),
    "alibaba_model_studio": HostedPreset(
        id="alibaba_model_studio",
        label_ar="Alibaba Cloud Model Studio (سنغافورة) — حصة مجانية 90 يومًا",
        label_en="Alibaba Cloud Model Studio (Singapore) — 90-day free quota",
        base_url=None,
        base_url_hint="https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        key_env="DASHSCOPE_API_KEY",
        signup_url="https://modelstudio.console.alibabacloud.com/ap-southeast-1",
        default_model="qwen3-coder-30b-a3b-instruct",
        alternatives=("qwen3.5-27b", "qwen3.8-flash (closed weights → GODOTAI_MODEL_KIND=vendor_api GODOTAI_SERVING=vendor)"),
        base_model="Qwen/Qwen3-Coder-30B-A3B-Instruct",
        base_license="Apache-2.0",
        kind="open_weight_deployment", serving="managed",
        owner="open weights (Qwen, Apache-2.0); server run by Alibaba Cloud",
        free_ar=("عند أول تفعيل لـ Model Studio في منطقة سنغافورة تُمنح حصة مجانية لكل طراز (عادةً 1,000,000 رمز إدخال+إخراج "
                 "للطراز الواحد) صالحة 90 يومًا فقط؛ بعد نفادها أو انتهائها يبدأ الدفع حسب الاستخدام تلقائيًا ما لم تفعّل "
                 "«Free Quota Only» (معطّل افتراضيًا) لكل طراز. الطرازات ذات الحصة تظهر بشريط أزرق في القائمة — تأكّد أن "
                 "طرازك بينها."),
        free_en=("First activation of Model Studio in the Singapore region grants a per-model free quota (typically 1,000,000 "
                 "input+output tokens per model) valid for 90 days only; when it is exhausted or expires, pay-as-you-go "
                 "billing starts automatically unless 'Free Quota Only' (off by default) is enabled per model. Models with "
                 "a quota show a blue bar in the list — check yours is one of them."),
        data_ar=("لم تُسترجع صفحة الخصوصية الرسمية بشكل قابل للقراءة أثناء البحث (أربع محاولات، آخرها بعد التحديث)؛ نُبقي التعامل مع البيانات "
                 "«غير مُثبت من مصدر أولي». جدول OpenRouter (ثانوي) يذكر لـ Alibaba Cloud International «احتفاظ غير معلوم، "
                 "لا يدرّب». اقرأ سياسة Model Studio بنفسك قبل إرسال أي شيء حساس."),
        data_en=("The official privacy article did not load in readable form during research (four attempts, the page is script-rendered); data handling "
                 "is recorded as 'not established from a primary source'. OpenRouter's table (secondary) lists Alibaba Cloud "
                 "International as unknown retention / no training. Read the Model Studio policy yourself before sending "
                 "anything sensitive."),
        risks_ar=("يلزم إكمال معلومات الحساب؛ المفتاح مرتبط بالمنطقة التي أُنشئ فيها؛ عنوان الخدمة يحتوي معرّف مساحة العمل الخاص بك "
                  "(لذلك لا يمكن كتابته هنا — اضبط GODOTAI_BASE_URL)؛ خطر فوترة تلقائية بعد 90 يومًا."),
        risks_en=("Account information must be completed; the key is bound to the region it was created in; the endpoint contains "
                  "your workspace id (so it cannot be written here — set GODOTAI_BASE_URL); automatic billing risk after 90 days."),
        sources=("https://www.alibabacloud.com/help/en/model-studio/new-free-quota",
                 "https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope",
                 "https://www.alibabacloud.com/help/en/model-studio/text-generation-model",
                 "https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct"),
    ),
    "workers_ai": HostedPreset(
        id="workers_ai",
        label_ar="Cloudflare Workers AI — Qwen3-30B-A3B (10,000 نيورون/يوم مجانًا)",
        label_en="Cloudflare Workers AI — Qwen3-30B-A3B (10,000 Neurons/day free)",
        base_url=None,                   # derived from CF_ACCOUNT_ID in make_provider (godotai/providers/cloudflare.py)
        base_url_hint="https://api.cloudflare.com/client/v4/accounts/<CF_ACCOUNT_ID>/ai/v1 (derived automatically)",
        key_env="CF_WORKERS_AI_TOKEN",
        alt_key_envs=("CLOUDFLARE_API_TOKEN",),
        required_env=("CF_ACCOUNT_ID",),
        signup_url="https://dash.cloudflare.com/profile/api-tokens",
        default_model="@cf/qwen/qwen3-30b-a3b-fp8",
        alternatives=("@cf/qwen/qwen3.8-27b", "@cf/qwen/qwen2.5-coder-32b-instruct"),
        base_model="Qwen/Qwen3-30B-A3B",
        base_license="Apache-2.0",
        kind="open_weight_deployment", serving="managed", route="workers_ai",
        owner="open weights (Qwen, Apache-2.0); server run by Cloudflare",
        free_ar=("10,000 نيورون يوميًا مجانًا على خطة Workers المجانية (بلا بطاقة)؛ هذا الطراز يستهلك 4,625 نيورون لكل مليون رمز "
                 "إدخال و30,475 لكل مليون رمز إخراج، أي نحو 2 مليون رمز إدخال أو 330 ألف رمز إخراج يوميًا. الطراز "
                 "@cf/qwen/qwen3.8-27b أغلى بكثير (40,909 / 290,909) فتكفي الحصة لتشغيل واحد قصير. فوق الحصة: لا شيء يُخصم "
                 "على الخطة المجانية (تتوقف الطلبات)؛ على Workers Paid تُحاسَب 0.011 دولار لكل 1000 نيورون."),
        free_en=("10,000 Neurons/day free on the Workers Free plan (no card); this model costs 4,625 Neurons per million input "
                 "tokens and 30,475 per million output tokens — roughly 2M input or 330K output tokens a day. "
                 "@cf/qwen/qwen3.8-27b is far dearer (40,909 / 290,909), enough for about one short run. Beyond the "
                 "allowance nothing is charged on the Free plan (requests stop); on Workers Paid it is $0.011 per 1,000 Neurons."),
        data_ar=("Cloudflare تصرّح أنها لا تستخدم محتوى العميل (المدخلات/المخرجات) لتدريب النماذج أو تحسين خدماتها أو خدمات طرف "
                 "ثالث دون موافقة صريحة، ولا تتيحه لعملاء آخرين؛ النماذج نفسها «خدمات طرف ثالث» برخصها."),
        data_en=("Cloudflare states it does not use Customer Content (inputs/outputs) to train models or improve its or third-party "
                 "services without explicit consent, and does not make it available to other customers; the models are "
                 "'Third-Party Services' under their own licences."),
        risks_ar="يلزم حساب Cloudflare ومعرّف الحساب وتوكن بصلاحية Workers AI فقط؛ دعم استدعاء الأدوات لكل طراز يجب تأكيده من صفحته؛ الحصة تُقاس بالنيورون لا بالطلبات.",
        risks_en="Needs a Cloudflare account, the account id and a Workers-AI-only token; per-model tool-calling support must be confirmed on its page; the allowance is metered in Neurons, not requests.",
        sources=("https://developers.cloudflare.com/workers-ai/platform/pricing/", "https://developers.cloudflare.com/workers-ai/platform/limits/",
                 "https://developers.cloudflare.com/workers-ai/privacy/",
                 "https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/"),
    ),
}


def get(preset_id: str) -> HostedPreset:
    try:
        return PRESETS[preset_id]
    except KeyError:
        raise PresetError(f"unknown preset {preset_id!r}; known: {', '.join(sorted(PRESETS))}") from None


def apply_preset(data: dict[str, Any], preset_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Fold a preset's defaults into the raw ``godot.toml`` dict (called by ``load_config`` after env overrides).

    Explicit ``GODOTAI_*`` environment variables keep their value; everything else the preset defines replaces the
    toml value — including the ``[model]`` identity, which must describe the hosted weights, not the local default.
    """
    env = os.environ if env is None else env
    p = get(preset_id)
    agent = data.setdefault("agent", {})
    model = data.setdefault("model", {})

    def put(section: dict[str, Any], key: str, value: Any, env_name: str) -> None:
        if not (env.get(env_name) or "").strip():
            section[key] = value

    agent["preset"] = p.id
    put(agent, "provider", "openai_compat", "GODOTAI_PROVIDER")
    put(agent, "route", p.route, "GODOTAI_ROUTE")
    put(agent, "model", p.default_model, "GODOTAI_MODEL")
    if p.base_url:
        put(agent, "base_url", p.base_url, "GODOTAI_BASE_URL")
    elif p.route == "direct" and not (agent.get("base_url") or "").strip():
        raise PresetError(f"preset {p.id!r} has no fixed endpoint — set GODOTAI_BASE_URL={p.base_url_hint} "
                          f"(your own workspace id; it is never stored in the repository)")
    put(model, "kind", p.kind, "GODOTAI_MODEL_KIND")
    put(model, "serving", p.serving, "GODOTAI_SERVING")
    put(model, "base_model", p.base_model, "GODOTAI_BASE_MODEL")
    put(model, "base_license", p.base_license, "GODOTAI_BASE_LICENSE")
    if not (env.get("GODOTAI_MODEL_KIND") or "").strip():
        model["owner"] = p.owner
        model["adapter"] = ""          # a hosted preset serves the base weights, never your LoRA adapter
    return data


def uses_vendor_model(preset_id: str, model: str) -> bool:
    """True when *model* on this preset is a proprietary vendor model (must then be declared kind = vendor_api)."""
    if preset_id == "openrouter_free":
        return model.lower().startswith(OPENROUTER_VENDOR_PREFIXES)
    return False


__all__ = ["HostedPreset", "OPENROUTER_VENDOR_PREFIXES", "PRESETS", "PresetError", "VERIFIED", "apply_preset", "get",
           "uses_vendor_model"]
