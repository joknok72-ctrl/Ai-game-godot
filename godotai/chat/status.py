"""Readiness of the environment, as the chat UI reports it to a non-technical user.

Same facts as ``python3 -m godotai doctor`` (engine binary + templates, API index,
model server / key, GitHub token, Java/SDK) returned as JSON, plus short
*Arabic and English hints* that say exactly which command or environment
variable is missing. Only ``set``/``not set`` is ever reported for credentials —
values never leave the process.

The model check is real, not a guess: for a private OpenAI-compatible server the
endpoint is **probed** (``GET <base>/models``, short timeout, cached a few seconds)
so the page can say "your model server is not running — start it with …" instead of
failing on the first message. The vendor providers and the hosted free-allowance presets
(``godotai/presets.py``) are checked by key presence only (no request is made to them here).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..godot import Godot, GodotNotFound, GodotVersionMismatch, export_templates_dir, find_godot_binary

PROBE_TIMEOUT = 1.5           # seconds; a local server answers in milliseconds, an absent one refuses instantly
PROBE_TTL = 5.0               # seconds a probe result is reused (status is polled, send() re-checks)
ENV_SKIP_PROBE = "GODOTAI_SKIP_MODEL_PROBE"

Probe = Callable[[str], dict[str, Any]]
_probe_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_probe_lock = threading.Lock()


def _set(name: str) -> bool:
    return bool(os.environ.get(name))


def probe_skipped() -> bool:
    return os.environ.get(ENV_SKIP_PROBE, "").strip().lower() in ("1", "true", "yes", "on")


def probe_openai_endpoint(base_url: str, timeout: float = PROBE_TIMEOUT) -> dict[str, Any]:
    """``GET <base_url>/models`` → {reachable, authorized, models: [...], error}.

    * connection refused / DNS / timeout → ``reachable = False``;
    * HTTP 401/403 → reachable but ``authorized = False`` (vendor key missing/wrong);
    * any other HTTP status → reachable (some servers do not implement /models); model ids parsed when present.
    Never sends more than the bearer key the provider itself would send.
    """
    url = base_url.rstrip("/") + "/models"
    key = os.environ.get("OPENAI_API_KEY", "") or "not-needed"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                               "User-Agent": "godotai-status"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        items = data.get("data") if isinstance(data, dict) else None
        models = sorted({str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")}) if isinstance(items, list) else []
        return {"reachable": True, "authorized": True, "models": models, "error": None}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"reachable": True, "authorized": False, "models": [], "error": f"HTTP {exc.code}"}
        return {"reachable": True, "authorized": True, "models": [], "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"reachable": False, "authorized": False, "models": [], "error": str(reason)}


def cached_probe(base_url: str, probe: Probe | None = None, ttl: float = PROBE_TTL) -> dict[str, Any]:
    """Probe *base_url*, reusing the last real result for *ttl* seconds (status is polled by the page).

    The cache only spares **your** server from being hit on every poll; an injected *probe* (tests, other
    transports) is called every time and never touches the cache.
    """
    if probe is not None:
        return probe(base_url)
    now = time.monotonic()
    with _probe_lock:
        hit = _probe_cache.get(base_url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    result = probe_openai_endpoint(base_url)
    with _probe_lock:
        _probe_cache[base_url] = (now, result)
    return result


def clear_probe_cache() -> None:
    with _probe_lock:
        _probe_cache.clear()


def _start_server_hint(cfg: Config, url: str) -> tuple[str, str]:
    m = cfg.model
    base = m.base_model or "<base model>"
    cmd = (f"vllm serve {base} --served-model-name {cfg.agent.model} --enable-auto-tool-choice --tool-call-parser hermes"
           if not m.adapter else
           f"vllm serve {base} --enable-lora --lora-modules {cfg.agent.model}={m.adapter} --enable-auto-tool-choice --tool-call-parser hermes")
    ar = (f"خادم نموذجك «{m.name}» غير متاح على {url} — الصفحة لا تستطيع الإرسال قبل تشغيله. على جهاز فيه GPU:\n"
          f"{cmd}\n"
          f"أو بدون GPU (أبطأ): ollama pull qwen2.5-coder:7b ثم export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 "
          f"GODOTAI_MODEL=qwen2.5-coder:7b\n"
          f"أو بالحاويات: docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d model\n"
          f"ثم اضغط «تحديث الحالة».")
    en = (f"Your model server '{m.name}' is not reachable at {url} — messages cannot be sent until it runs. With a GPU:\n"
          f"{cmd}\n"
          f"or without a GPU (slow): ollama pull qwen2.5-coder:7b, then export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 "
          f"GODOTAI_MODEL=qwen2.5-coder:7b\n"
          f"or with containers: docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d model\n"
          f"then press 'refresh status'.")
    return ar, en


def preset_ready(preset_id: str) -> tuple[bool, str, str]:
    """(ready, hint_ar, hint_en) for a hosted preset: every named environment variable must be set — nothing else is
    checked from here (no request leaves the process), and values are never reported."""
    from .. import presets
    p = presets.get(preset_id)
    missing = p.missing_env()
    if not missing:
        return True, "", ""
    exports = "\n".join(f"export {name}=<…>" for name in missing)
    ar = (f"الإعداد الجاهز «{p.label_ar}» يحتاج متغيرات البيئة التالية قبل الإرسال (أنشئ المفتاح من {p.signup_url}):\n"
          f"{exports}\nثم أعد تشغيل: python3 -m godotai chat — لا تكتب المفتاح في أي ملف داخل المشروع.\n"
          f"الحصة المجانية ({p.verified}): {p.free_ar}")
    en = (f"Preset '{p.label_en}' needs these environment variables before anything can be sent (create the key at "
          f"{p.signup_url}):\n{exports}\nthen restart: python3 -m godotai chat — never write the key into a file of this repo.\n"
          f"Free allowance ({p.verified}): {p.free_en}")
    return False, ar, en


def model_ready(cfg: Config, probe: Probe | None = None) -> tuple[bool, str, str]:
    """(ready, hint_ar, hint_en): can ``make_provider(cfg.agent).complete()`` be attempted at all?

    * ``anthropic`` (vendor, opt-in): needs ``ANTHROPIC_API_KEY`` unless a Cloudflare AI Gateway with a stored
      key is used (``route = cf_gateway`` + ``CF_AIG_TOKEN``);
    * ``openai_compat`` + ``route = workers_ai``: needs ``CLOUDFLARE_API_TOKEN`` (+ ``CF_ACCOUNT_ID``);
    * ``openai_compat`` at ``api.openai.com``: needs ``OPENAI_API_KEY``;
    * ``openai_compat`` at **your server** (the default): the endpoint is probed — this is the only case where a
      network request is made, and it goes to *your* URL only. ``GODOTAI_SKIP_MODEL_PROBE=1`` disables the probe.
    """
    a = cfg.agent
    if a.provider == "anthropic":
        if _set("ANTHROPIC_API_KEY") or (a.route == "cf_gateway" and _set("CF_AIG_TOKEN")):
            return True, "", ""
        return (False,
                "مفتاح النموذج غير موجود. في نفس الطرفية (terminal) قبل تشغيل الخادم اكتب:\n"
                "export ANTHROPIC_API_KEY=<المفتاح من console.anthropic.com>\n"
                "ثم شغّل: python3 -m godotai chat",
                "Model API key missing. In the same terminal, before starting the server:\n"
                "export ANTHROPIC_API_KEY=<key from console.anthropic.com>\nthen: python3 -m godotai chat")
    # openai_compat
    if a.preset:                                  # hosted free-allowance endpoint: key presence only, no request from here
        return preset_ready(a.preset)
    if a.route == "workers_ai":
        if (_set("CF_WORKERS_AI_TOKEN") or _set("CLOUDFLARE_API_TOKEN")) and _set("CF_ACCOUNT_ID"):
            return True, "", ""
        return (False,
                "المسار workers_ai يحتاج CF_ACCOUNT_ID و CF_WORKERS_AI_TOKEN (توكن بصلاحية Workers AI فقط؛ متغيرات بيئة فقط، "
                "لا تكتبها في ملفات المشروع).",
                "route = workers_ai needs CF_ACCOUNT_ID and CF_WORKERS_AI_TOKEN (a Workers-AI-only token; environment "
                "variables only).")
    if a.route == "cf_gateway":
        return True, "", ""                       # URL/headers come from CF_* env; make_provider reports what is missing
    url = a.endpoint or ""
    if "api.openai.com" in url:
        if _set("OPENAI_API_KEY"):
            return True, "", ""
        return (False, "export OPENAI_API_KEY=<key>  ثم أعد تشغيل الخادم", "export OPENAI_API_KEY=<key> and restart the server")
    if not url:
        return (False,
                "لا يوجد عنوان لخادم النموذج. اضبط GODOTAI_BASE_URL=http://<host>:8000/v1 ثم أعد التشغيل.",
                "No model server URL. Set GODOTAI_BASE_URL=http://<host>:8000/v1 and restart.")
    if probe_skipped():
        return True, "", ""
    result = cached_probe(url, probe)
    if not result["reachable"]:
        ar, en = _start_server_hint(cfg, url)
        return False, ar, en
    if not result["authorized"]:
        return (False,
                f"خادم النموذج على {url} رفض المفتاح (HTTP 401/403). اضبط OPENAI_API_KEY بالقيمة التي يتوقعها خادمك.",
                f"The model server at {url} rejected the key (HTTP 401/403). Set OPENAI_API_KEY to what your server expects.")
    return True, "", ""


def environment_status(cfg: Config, probe: Probe | None = None) -> dict[str, Any]:
    eng = cfg.engine
    engine: dict[str, Any] = {"pinned": eng.tag, "ok": False, "binary": None, "version": None,
                              "templates_installed": False, "templates_dir": str(export_templates_dir(eng)),
                              "error": None}
    binary = find_godot_binary(eng)
    if binary is None:
        engine["error"] = "binary not found"
    else:
        try:
            g = Godot(eng, binary)
            engine.update(ok=True, binary=str(binary), version=g.version(), templates_installed=g.templates_installed())
        except (GodotNotFound, GodotVersionMismatch) as exc:
            engine["error"] = str(exc)
    if not engine["ok"]:
        engine["hint_ar"] = ("محرك Godot " + eng.tag + " غير مثبّت أو إصداره مختلف. شغّل مرة واحدة:\n"
                             "python3 -m godotai install-godot\nثم أضِف ~/.local/bin إلى PATH وأعد تشغيل الخادم.")
        engine["hint_en"] = (f"Godot {eng.tag} is not installed (or another version was found). Run once:\n"
                             "python3 -m godotai install-godot\nthen add ~/.local/bin to PATH and restart the server.")

    from .. import apiref
    idx = apiref.load_index(eng)
    api_index = {"built": idx is not None, "classes": idx.stats()["classes"] if idx else 0}

    ready, hint_ar, hint_en = model_ready(cfg, probe)
    a = cfg.agent
    url = a.endpoint
    preset_info = None
    if a.provider == "anthropic":
        key_env = "ANTHROPIC_API_KEY"
    elif a.preset:
        from .. import presets
        p = presets.get(a.preset)
        key_env = p.key_env
        preset_info = {"id": p.id, "label_ar": p.label_ar, "label_en": p.label_en, "signup_url": p.signup_url,
                       "free_ar": p.free_ar, "free_en": p.free_en, "data_ar": p.data_ar, "data_en": p.data_en,
                       "verified": p.verified, "missing_env": p.missing_env()}
    elif a.route == "workers_ai":
        key_env = "CF_WORKERS_AI_TOKEN"
    elif url and "api.openai.com" in url:
        key_env = "OPENAI_API_KEY"
    else:
        key_env = None                            # your own server: no vendor key involved
    if a.preset:
        key_set: bool | None = bool(p.key())
    elif a.route == "workers_ai" and not a.preset:
        key_set = _set("CF_WORKERS_AI_TOKEN") or _set("CLOUDFLARE_API_TOKEN")
    else:
        key_set = _set(key_env) if key_env else None
    server: dict[str, Any] = {"probed": False, "reachable": None, "models": [], "model_listed": None, "error": None}
    if a.private_endpoint and url and not probe_skipped():
        r = cached_probe(url, probe)
        server.update(probed=True, reachable=r["reachable"], models=r["models"][:50], error=r["error"],
                      model_listed=(a.model in r["models"]) if r["models"] else None)
    model = {"provider": a.provider, "model": a.model, "effort": a.effort, "route": a.route, "preset": preset_info,
             "key_env": key_env, "key_set": key_set, "ready": ready,
             "hint_ar": hint_ar, "hint_en": hint_en, "base_url": url, "private": a.private_endpoint,
             "server": server, "identity": cfg.model.describe()}

    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    android = {"java": shutil.which("java") is not None, "sdk": bool(sdk and Path(sdk).is_dir()),
               "export_possible": bool(engine["ok"] and engine["templates_installed"] and shutil.which("java")
                                       and sdk and Path(sdk).is_dir())}
    return {
        "engine": engine,
        "api_index": api_index,
        "model": model,
        "github_token_set": _set("GITHUB_TOKEN"),
        "android": android,
        "ready": ready,                    # the only hard requirement to *send* a message
        "config_path": str(cfg.path) if cfg.path else None,
    }
