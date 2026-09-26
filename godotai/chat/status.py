"""Readiness of the environment, as the chat UI reports it to a non-technical user.

Same facts as ``python3 -m godotai doctor`` (engine binary + templates, API index,
model provider/key, GitHub token, Java/SDK) returned as JSON, plus short
*Arabic and English hints* that say exactly which command or environment
variable is missing. Only ``set``/``not set`` is ever reported for credentials —
values never leave the process.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..config import Config
from ..godot import Godot, GodotNotFound, GodotVersionMismatch, export_templates_dir, find_godot_binary


def _set(name: str) -> bool:
    return bool(os.environ.get(name))


def model_ready(cfg: Config) -> tuple[bool, str, str]:
    """(ready, hint_ar, hint_en): can ``make_provider(cfg.agent).complete()`` be attempted at all?

    Mirrors the providers' own rules: the Claude provider needs ``ANTHROPIC_API_KEY`` unless a
    Cloudflare AI Gateway with a stored key is used (``route = cf_gateway`` + ``CF_AIG_TOKEN``);
    the OpenAI-compatible provider needs ``OPENAI_API_KEY`` for api.openai.com, or *any* custom
    base URL (a local Ollama/vLLM/llama.cpp server needs no key), or Workers AI credentials.
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
    if (_set("OPENAI_API_KEY") or a.base_url or _set("OPENAI_BASE_URL")
            or (a.route == "workers_ai" and _set("CLOUDFLARE_API_TOKEN"))):
        return True, "", ""
    return (False,
            "المزوّد openai_compat يحتاج إمّا OPENAI_API_KEY أو عنوان خادم محلي:\n"
            "export OPENAI_BASE_URL=http://localhost:11434/v1  GODOTAI_MODEL=<اسم الموديل>\n"
            "ثم شغّل: python3 -m godotai chat",
            "Provider openai_compat needs OPENAI_API_KEY or a local server URL:\n"
            "export OPENAI_BASE_URL=http://localhost:11434/v1 GODOTAI_MODEL=<model>\nthen: python3 -m godotai chat")


def environment_status(cfg: Config) -> dict[str, Any]:
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

    ready, hint_ar, hint_en = model_ready(cfg)
    a = cfg.agent
    key_env = "ANTHROPIC_API_KEY" if a.provider == "anthropic" else "OPENAI_API_KEY"
    model = {"provider": a.provider, "model": a.model, "effort": a.effort, "route": a.route,
             "key_env": key_env, "key_set": _set(key_env), "ready": ready, "hint_ar": hint_ar, "hint_en": hint_en,
             "base_url": a.base_url or os.environ.get("OPENAI_BASE_URL") or None}

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
