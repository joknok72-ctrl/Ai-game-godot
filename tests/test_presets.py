"""Hosted free-allowance presets (godotai/presets.py) and the PaaS helpers around them.

Everything here is offline: no request leaves the process, every key is a fake string in a patched environment, and
the point of most tests is that the repository *never* stores a key, account id or workspace id and never presents a
provider-hosted model as the user's own server.
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import PRIVATE_ENV, REPO, repo_config

from godotai import presets
from godotai.__main__ import DEFAULT_CHAT_PORT, main, platform_public_hosts, resolve_port
from godotai.chat.status import environment_status, model_ready, preset_ready
from godotai.config import ConfigError, load_config
from godotai.model_identity import ModelIdentityError, check_consistency, is_vendor_endpoint
from godotai.providers import ProviderError, make_provider
from godotai.providers.base import RETRY_AFTER_MAX, http_json_transport, retry_after_seconds
from godotai.providers.openai_compat import OpenAICompatProvider

# Every env var a preset can read — cleared so the developer's shell never leaks into these tests.
CLEAN = {**PRIVATE_ENV, "GODOTAI_PRESET": "", "OPENROUTER_API_KEY": "", "GROQ_API_KEY": "", "DASHSCOPE_API_KEY": "",
         "CF_WORKERS_AI_TOKEN": "", "CLOUDFLARE_API_TOKEN": "", "CF_ACCOUNT_ID": "", "GODOTAI_MODEL_KIND": "",
         "GODOTAI_SERVING": "", "GODOTAI_BASE_MODEL": "", "GODOTAI_BASE_LICENSE": "", "GODOTAI_MODEL_NAME": "",
         "GODOTAI_ENGINE_PLATFORM": "", "GODOTAI_PORT": "", "PORT": "", "RAILWAY_PUBLIC_DOMAIN": "",
         "RENDER_EXTERNAL_HOSTNAME": ""}
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
TOKEN_SHAPES = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{16,}|sk-or-v1-[0-9a-f]{16,}|Bearer\s+\S{16,})")


def load_with(env: dict[str, str]):
    with mock.patch.dict(os.environ, {**CLEAN, **env}):
        return load_config(REPO / "godot.toml")


class PresetCatalogueTests(unittest.TestCase):
    def test_the_four_documented_presets_exist_and_are_all_managed_open_weights(self):
        self.assertEqual(sorted(presets.PRESETS), ["alibaba_model_studio", "groq", "openrouter_free", "workers_ai"])
        for p in presets.PRESETS.values():
            self.assertEqual(p.id, [k for k, v in presets.PRESETS.items() if v is p][0])
            self.assertEqual(p.kind, "open_weight_deployment")
            self.assertEqual(p.serving, "managed", f"{p.id}: a provider runs the server — never 'self_hosted'")
            self.assertEqual(p.base_license, "Apache-2.0")
            self.assertTrue(p.base_model.startswith("Qwen/"), p.base_model)
            self.assertIn(p.route, ("direct", "workers_ai"))
            self.assertRegex(p.verified, r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(p.sources and all(s.startswith("https://") for s in p.sources), p.id)
            for field in ("free_ar", "free_en", "data_ar", "data_en", "risks_ar", "risks_en"):
                self.assertGreater(len(getattr(p, field)), 40, f"{p.id}.{field} must actually explain something")

    def test_no_preset_promises_forever_free_or_quality(self):
        for p in presets.PRESETS.values():
            for text in (p.free_en, p.risks_en, p.data_en):
                self.assertNotRegex(text.lower(), r"forever free|always free|unlimited free|guaranteed", f"{p.id}: {text}")
            self.assertIsNone(p.describe()["quality_claim"])

    def test_presets_module_carries_env_var_names_only_never_values(self):
        src = (REPO / "godotai" / "presets.py").read_text(encoding="utf-8")
        self.assertIsNone(HEX32.search(src), "an account/workspace id must never be written into the repository")
        self.assertIsNone(TOKEN_SHAPES.search(src))
        for p in presets.PRESETS.values():
            self.assertRegex(p.key_env, r"^[A-Z][A-Z0-9_]+$")
            for name in (*p.required_env, *p.alt_key_envs):
                self.assertRegex(name, r"^[A-Z][A-Z0-9_]+$")
            if p.base_url:
                self.assertTrue(p.base_url.startswith("https://"), p.base_url)
                self.assertNotIn("<", p.base_url, "placeholders belong in base_url_hint")
            else:
                self.assertIn("<", p.base_url_hint, f"{p.id}: without a fixed URL the hint must show what the user fills in")
        # the workspace-specific Alibaba host is *never* fixed in code: it identifies the user
        self.assertIsNone(presets.get("alibaba_model_studio").base_url)

    def test_key_lookup_prefers_the_dedicated_name_and_reports_what_is_missing(self):
        p = presets.get("workers_ai")
        self.assertEqual(p.key({"CF_WORKERS_AI_TOKEN": "narrow", "CLOUDFLARE_API_TOKEN": "broad"}), "narrow")
        self.assertEqual(p.key({"CLOUDFLARE_API_TOKEN": "broad"}), "broad", "legacy name still accepted")
        self.assertEqual(p.key({}), "")
        self.assertEqual(p.missing_env({}), ["CF_WORKERS_AI_TOKEN", "CF_ACCOUNT_ID"])
        self.assertEqual(p.missing_env({"CF_WORKERS_AI_TOKEN": "t"}), ["CF_ACCOUNT_ID"])
        self.assertEqual(p.missing_env({"CF_WORKERS_AI_TOKEN": "t", "CF_ACCOUNT_ID": "a"}), [])
        self.assertEqual(presets.get("groq").missing_env({"OPENAI_API_KEY": "x"}), ["GROQ_API_KEY"],
                         "a vendor key is not a Groq key")

    def test_unknown_preset_is_a_clear_error(self):
        with self.assertRaises(presets.PresetError) as ctx:
            presets.get("hugging_face")
        self.assertIn("known: alibaba_model_studio, groq, openrouter_free, workers_ai", str(ctx.exception))

    def test_vendor_model_ids_on_openrouter_are_recognised(self):
        self.assertTrue(presets.uses_vendor_model("openrouter_free", "anthropic/claude-fable-5-1"))
        self.assertTrue(presets.uses_vendor_model("openrouter_free", "OpenAI/gpt-5"))
        self.assertFalse(presets.uses_vendor_model("openrouter_free", "qwen/qwen3.8-27b:free"))
        self.assertFalse(presets.uses_vendor_model("groq", "openai/gpt-oss-120b"), "gpt-oss weights are open (Apache-2.0)")


class ConfigPrecedenceTests(unittest.TestCase):
    """env GODOTAI_* > preset > godot.toml — and the identity card follows the preset."""

    def test_openrouter_preset_rewrites_agent_and_identity(self):
        cfg = load_with({"GODOTAI_PRESET": "openrouter_free"})
        a, m = cfg.agent, cfg.model
        self.assertEqual(a.preset, "openrouter_free")
        self.assertEqual(a.provider, "openai_compat")
        self.assertEqual(a.route, "direct")
        self.assertEqual(a.model, "qwen/qwen3.8-27b:free")
        self.assertEqual(a.endpoint, "https://openrouter.ai/api/v1")
        self.assertFalse(a.private_endpoint, "a hosted preset is not your private server")
        self.assertEqual((m.kind, m.serving), ("open_weight_deployment", "managed"))
        self.assertEqual(m.base_model, "Qwen/Qwen3.8-27B")
        self.assertEqual(m.adapter, "", "a hosted preset serves the base weights, never your LoRA")
        self.assertIn("hosted by a provider", m.kind_label("en"))
        self.assertIn("The server is not yours", m.disclosure("en"))
        self.assertIn("الخادم ليس خادمك", m.disclosure("ar"))

    def test_explicit_env_beats_the_preset_which_beats_the_toml(self):
        cfg = load_with({"GODOTAI_PRESET": "openrouter_free", "GODOTAI_MODEL": "qwen/qwen3.8-27b"})
        self.assertEqual(cfg.agent.model, "qwen/qwen3.8-27b", "GODOTAI_MODEL wins over the preset default")
        self.assertEqual(cfg.agent.endpoint, "https://openrouter.ai/api/v1", "the rest still comes from the preset")
        base = load_with({})
        self.assertEqual(base.agent.model, "godotai", "without a preset the toml is untouched")
        self.assertEqual(base.model.serving, "self_hosted")

    def test_preset_in_toml_file_works_too(self):
        with tempfile.TemporaryDirectory() as d:
            src = (REPO / "godot.toml").read_text(encoding="utf-8")
            text = src.replace('strict_tools = false', 'strict_tools = false\npreset = "groq"', 1)
            path = Path(d) / "godot.toml"
            path.write_text(text, encoding="utf-8")
            with mock.patch.dict(os.environ, CLEAN):
                cfg = load_config(path)
        self.assertEqual(cfg.agent.preset, "groq")
        self.assertEqual(cfg.agent.endpoint, "https://api.groq.com/openai/v1")
        self.assertEqual(cfg.model.serving, "managed")

    def test_alibaba_needs_the_users_workspace_url(self):
        with self.assertRaises(ConfigError) as ctx:
            load_with({"GODOTAI_PRESET": "alibaba_model_studio"})
        self.assertIn("GODOTAI_BASE_URL=https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1", str(ctx.exception))
        cfg = load_with({"GODOTAI_PRESET": "alibaba_model_studio",
                         "GODOTAI_BASE_URL": "https://ws-example.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"})
        self.assertEqual(cfg.agent.endpoint, "https://ws-example.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(cfg.agent.model, "qwen3-coder-30b-a3b-instruct")

    def test_workers_ai_preset_switches_the_route(self):
        cfg = load_with({"GODOTAI_PRESET": "workers_ai"})
        self.assertEqual(cfg.agent.route, "workers_ai")
        self.assertEqual(cfg.agent.model, "@cf/qwen/qwen3-30b-a3b-fp8")
        self.assertIsNone(cfg.agent.endpoint, "the URL is derived from CF_ACCOUNT_ID at provider construction")
        self.assertEqual(cfg.model.serving, "managed")

    def test_unknown_preset_and_contradictory_overrides_are_rejected(self):
        with self.assertRaises(ConfigError):
            load_with({"GODOTAI_PRESET": "nope"})
        with self.assertRaises(ConfigError) as ctx:
            load_with({"GODOTAI_PRESET": "openrouter_free", "GODOTAI_PROVIDER": "anthropic"})
        self.assertIn("openai_compat", str(ctx.exception))
        with self.assertRaises(ConfigError) as ctx:
            load_with({"GODOTAI_PRESET": "openrouter_free", "GODOTAI_ROUTE": "workers_ai"})
        self.assertIn("route", str(ctx.exception))

    def test_a_preset_can_never_be_labelled_self_hosted(self):
        with self.assertRaises(ConfigError) as ctx:
            load_with({"GODOTAI_PRESET": "groq", "GODOTAI_SERVING": "self_hosted"})
        self.assertIn("would present it as your own server", str(ctx.exception))

    def test_vendor_model_through_openrouter_must_be_declared_vendor(self):
        with self.assertRaises(ConfigError) as ctx:
            load_with({"GODOTAI_PRESET": "openrouter_free", "GODOTAI_MODEL": "anthropic/claude-fable-5-1"})
        self.assertIn("third-party vendor model", str(ctx.exception))
        cfg = load_with({"GODOTAI_PRESET": "openrouter_free", "GODOTAI_MODEL": "anthropic/claude-fable-5-1",
                         "GODOTAI_MODEL_KIND": "vendor_api", "GODOTAI_SERVING": "vendor"})
        self.assertEqual(cfg.model.kind, "vendor_api")

    def test_engine_platform_override_and_validation(self):
        cfg = load_with({"GODOTAI_ENGINE_PLATFORM": "linux.arm64"})
        self.assertEqual(cfg.engine.platform, "linux.arm64")
        self.assertEqual(cfg.engine.editor_zip_name, "Godot_v4.7.2-stable_linux.arm64.zip")
        sums = (REPO / "engine" / "checksums" / cfg.engine.tag / "SHA512-SUMS.txt").read_text(encoding="utf-8")
        self.assertIn(cfg.engine.editor_zip_name, sums, "the ARM editor asset must be pinned like the x86_64 one")
        with self.assertRaises(ConfigError):
            load_with({"GODOTAI_ENGINE_PLATFORM": "windows.x64"})


class IdentityHelperTests(unittest.TestCase):
    def test_is_vendor_endpoint_judges_presets_by_model_id(self):
        self.assertFalse(is_vendor_endpoint("openai_compat", "https://openrouter.ai/api/v1", "direct", "qwen/qwen3.8-27b:free",
                                            "openrouter_free"))
        self.assertTrue(is_vendor_endpoint("openai_compat", "https://openrouter.ai/api/v1", "direct", "openai/gpt-5",
                                           "openrouter_free"))
        self.assertFalse(is_vendor_endpoint("openai_compat", "https://api.groq.com/openai/v1", "direct", "qwen/qwen3.8-27b", "groq"))

    def test_check_consistency_with_preset(self):
        cfg = load_with({"GODOTAI_PRESET": "groq"})
        check_consistency(cfg.model, "openai_compat", cfg.agent.model, cfg.agent.endpoint, "direct", "groq")   # no raise
        from dataclasses import replace
        with self.assertRaises(ModelIdentityError):
            check_consistency(replace(cfg.model, serving="self_hosted"), "openai_compat", cfg.agent.model,
                              cfg.agent.endpoint, "direct", "groq")


class ProviderFactoryTests(unittest.TestCase):
    def test_groq_key_and_output_cap(self):
        cfg = load_with({"GODOTAI_PRESET": "groq"})
        with mock.patch.dict(os.environ, {**CLEAN, "GROQ_API_KEY": "gsk_fake_for_test", "OPENAI_API_KEY": "sk-should-not-be-used"}):
            p = make_provider(cfg.agent)
        self.assertIsInstance(p, OpenAICompatProvider)
        self.assertEqual(p.url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(p.api_key, "gsk_fake_for_test", "the preset's own variable, never OPENAI_API_KEY")
        self.assertEqual(p.max_tokens, 16_384, "clamped to the documented Groq maximum")
        self.assertLess(p.max_tokens, cfg.agent.max_tokens)

    def test_missing_key_is_a_clear_error_not_a_not_needed_bearer(self):
        cfg = load_with({"GODOTAI_PRESET": "openrouter_free"})
        with mock.patch.dict(os.environ, CLEAN):
            with self.assertRaises(ProviderError) as ctx:
                make_provider(cfg.agent)
        msg = str(ctx.exception)
        self.assertIn("OPENROUTER_API_KEY", msg)
        self.assertIn("https://openrouter.ai/settings/keys", msg)
        self.assertIn("never in a file of this repo", msg)

    def test_openrouter_uses_its_key_and_keeps_the_configured_cap(self):
        cfg = load_with({"GODOTAI_PRESET": "openrouter_free"})
        with mock.patch.dict(os.environ, {**CLEAN, "OPENROUTER_API_KEY": "sk-or-v1-fake"}):
            p = make_provider(cfg.agent)
        self.assertEqual(p.url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(p.api_key, "sk-or-v1-fake")
        self.assertEqual(p.max_tokens, cfg.agent.max_tokens, "no documented cap → untouched")

    def test_workers_ai_preset_derives_url_from_account_id(self):
        cfg = load_with({"GODOTAI_PRESET": "workers_ai"})
        with mock.patch.dict(os.environ, {**CLEAN, "CF_ACCOUNT_ID": "acct-test", "CF_WORKERS_AI_TOKEN": "cf-fake"}):
            p = make_provider(cfg.agent)
        self.assertEqual(p.url, "https://api.cloudflare.com/client/v4/accounts/acct-test/ai/v1/chat/completions")
        self.assertEqual(p.api_key, "cf-fake")
        self.assertEqual(p.model, "@cf/qwen/qwen3-30b-a3b-fp8")


class StatusTests(unittest.TestCase):
    def test_preset_ready_reports_missing_names_and_the_allowance(self):
        with mock.patch.dict(os.environ, CLEAN):
            ready, ar, en = preset_ready("openrouter_free")
        self.assertFalse(ready)
        self.assertIn("export OPENROUTER_API_KEY=<…>", en)
        self.assertIn("https://openrouter.ai/settings/keys", en)
        self.assertIn("20 requests/minute", en)
        self.assertIn("OPENROUTER_API_KEY", ar)
        self.assertIn("لا تكتب المفتاح", ar)
        with mock.patch.dict(os.environ, {**CLEAN, "OPENROUTER_API_KEY": "sk-or-v1-fake"}):
            self.assertEqual(preset_ready("openrouter_free"), (True, "", ""))

    def test_workers_ai_readiness_accepts_narrow_or_legacy_token(self):
        with mock.patch.dict(os.environ, {**CLEAN, "CF_ACCOUNT_ID": "a", "CF_WORKERS_AI_TOKEN": "t"}):
            self.assertTrue(preset_ready("workers_ai")[0])
        with mock.patch.dict(os.environ, {**CLEAN, "CF_ACCOUNT_ID": "a", "CLOUDFLARE_API_TOKEN": "t"}):
            self.assertTrue(preset_ready("workers_ai")[0])
        with mock.patch.dict(os.environ, {**CLEAN, "CF_WORKERS_AI_TOKEN": "t"}):
            ready, _ar, en = preset_ready("workers_ai")
        self.assertFalse(ready)
        self.assertIn("CF_ACCOUNT_ID", en)
        # the non-preset workers_ai route is checked the same way
        cfg = load_with({"GODOTAI_ROUTE": "workers_ai", "GODOTAI_SERVING": "managed", "GODOTAI_MODEL": "@cf/qwen/qwen3-30b-a3b-fp8"})
        with mock.patch.dict(os.environ, {**CLEAN, "CF_ACCOUNT_ID": "a", "CF_WORKERS_AI_TOKEN": "t"}):
            self.assertTrue(model_ready(cfg)[0])
        with mock.patch.dict(os.environ, CLEAN):
            self.assertIn("CF_WORKERS_AI_TOKEN", model_ready(cfg)[2])

    def test_environment_status_exposes_preset_facts_but_never_key_values(self):
        cfg = load_with({"GODOTAI_PRESET": "groq"})
        with mock.patch.dict(os.environ, {**CLEAN, "GROQ_API_KEY": "gsk_fake_value_1234567890"}):
            st = environment_status(cfg)
        m = st["model"]
        self.assertEqual(m["key_env"], "GROQ_API_KEY")
        self.assertTrue(m["key_set"])
        self.assertTrue(m["ready"])
        self.assertFalse(m["private"])
        self.assertEqual(m["preset"]["id"], "groq")
        self.assertEqual(m["preset"]["missing_env"], [])
        self.assertIn("Developer plan", m["preset"]["free_en"])
        self.assertNotIn("gsk_fake_value_1234567890", json.dumps(st), "values never leave the process")
        self.assertFalse(m["server"]["probed"], "no request is made to a hosted preset from the status page")
        self.assertEqual(m["identity"]["serving"], "managed")
        with mock.patch.dict(os.environ, CLEAN):
            st = environment_status(cfg)
        self.assertFalse(st["model"]["ready"])
        self.assertEqual(st["model"]["preset"]["missing_env"], ["GROQ_API_KEY"])


class RetryAfterTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(retry_after_seconds({"Retry-After": "7"}), 7.0)
        self.assertEqual(retry_after_seconds({"Retry-After": " 1.5 "}), 1.5)
        self.assertEqual(retry_after_seconds({"Retry-After": "-3"}), 0.0)
        self.assertIsNone(retry_after_seconds({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), "HTTP-date form is ignored")
        self.assertIsNone(retry_after_seconds({"Retry-After": "nan"}))
        self.assertIsNone(retry_after_seconds({}))
        self.assertIsNone(retry_after_seconds(None))
        self.assertIsNone(retry_after_seconds(object()))
        self.assertEqual(RETRY_AFTER_MAX, 120.0)

    def _http_error(self, code: int, headers: dict[str, str]):
        import email.message
        hdrs = email.message.Message()
        for k, v in headers.items():
            hdrs[k] = v
        return urllib.error.HTTPError("https://example.invalid/v1/chat/completions", code, "Too Many Requests", hdrs,
                                      io.BytesIO(b'{"error":"rate limited"}'))

    def test_429_with_retry_after_waits_exactly_that_long_then_succeeds(self):
        calls = {"n": 0}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'

        def fake_urlopen(req, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._http_error(429, {"Retry-After": "3"})
            return _Resp()

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep") as sleep:
            out = http_json_transport("https://example.invalid/v1/chat/completions", {}, {"x": 1}, retries=3)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(calls["n"], 2)
        sleep.assert_called_once_with(3.0)

    def test_huge_retry_after_is_capped_and_absent_header_uses_backoff(self):
        seq = iter([self._http_error(429, {"Retry-After": "86400"}), self._http_error(503, {})])

        def fake_urlopen(req, timeout):
            raise next(seq)

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep") as sleep:
            with self.assertRaises(ProviderError) as ctx:
                http_json_transport("https://example.invalid/v1", {}, {}, retries=2)
        self.assertIn("HTTP 503", str(ctx.exception))
        sleep.assert_called_once_with(RETRY_AFTER_MAX)   # the 24 h request is not honoured blindly

    def test_non_retryable_status_raises_immediately(self):
        def fake_urlopen(req, timeout):
            raise self._http_error(401, {"Retry-After": "5"})

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("time.sleep") as sleep:
            with self.assertRaises(ProviderError):
                http_json_transport("https://example.invalid/v1", {}, {}, retries=3)
        sleep.assert_not_called()


class PaasHelperTests(unittest.TestCase):
    def test_resolve_port_precedence(self):
        self.assertEqual(resolve_port(9000, {"PORT": "1"}), 9000)
        self.assertEqual(resolve_port(None, {"GODOTAI_PORT": "9001", "PORT": "9002"}), 9001)
        self.assertEqual(resolve_port(None, {"PORT": "9002"}), 9002, "Railway / Render inject PORT")
        self.assertEqual(resolve_port(None, {}), DEFAULT_CHAT_PORT)
        self.assertEqual(resolve_port(None, {"PORT": "   "}), DEFAULT_CHAT_PORT, "blank is unset")
        self.assertEqual(DEFAULT_CHAT_PORT, 8765)
        with self.assertRaises(SystemExit):
            resolve_port(None, {"PORT": "eighty"})
        with self.assertRaises(SystemExit):
            resolve_port(None, {"PORT": "70000"})
        with self.assertRaises(SystemExit):
            resolve_port(None, {"PORT": "-1"})
        self.assertEqual(resolve_port(0, {}), 0, "--port 0 (ephemeral) stays explicit")
        self.assertEqual(resolve_port(None, {"PORT": "0"}), 0, "PORT=0 means the same ephemeral port")

    def test_platform_public_hosts(self):
        self.assertEqual(platform_public_hosts({}), [])
        self.assertEqual(platform_public_hosts({"RAILWAY_PUBLIC_DOMAIN": "My-App.up.railway.app"}), ["my-app.up.railway.app"])
        self.assertEqual(platform_public_hosts({"RENDER_EXTERNAL_HOSTNAME": "app.onrender.com", "RAILWAY_PUBLIC_DOMAIN": ""}),
                         ["app.onrender.com"])
        self.assertEqual(platform_public_hosts({"RAILWAY_PUBLIC_DOMAIN": "https://bad/with/path"}), [],
                         "a URL is not a hostname")

    def test_chat_check_honours_the_platform_port_and_host(self):
        """The CI smoke test with PORT injected like a PaaS does: the server must pick it up and accept the platform host."""
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {**CLEAN, "GODOTAI_SKIP_MODEL_PROBE": "1", "PORT": "0",
                                             "RAILWAY_PUBLIC_DOMAIN": "example-app.up.railway.app"}):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = main(["chat", "--games-dir", d, "--check", "--max-concurrent-runs", "1", "--max-runs-per-day", "40"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("run quota: max 1 concurrent run(s), 40 run(s) per visitor per 24 h", text)
        self.assertIn("example-app.up.railway.app", text)


class CliTests(unittest.TestCase):
    def test_presets_command_lists_everything_offline(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, CLEAN), redirect_stdout(out):
            rc = main(["presets"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        for pid in presets.PRESETS:
            self.assertIn(f"[{pid}]", text)
        self.assertIn("missing: OPENROUTER_API_KEY", text)
        self.assertIn("none is a forever-free promise", text)
        self.assertIn("2026-09-26", text)

    def test_presets_json_is_machine_readable_and_secret_free(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, CLEAN), redirect_stdout(out):
            rc = main(["presets", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual([d["id"] for d in data], sorted(presets.PRESETS))
        for d in data:
            self.assertIsNone(d["quality_claim"])
            self.assertNotIn("key", {k for k in d if k not in ("key_env",)}, "no key *value* field exists")
            self.assertIsNone(HEX32.search(json.dumps(d)))

    def test_doctor_with_a_preset_and_no_key_is_honest(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**CLEAN, "GODOTAI_PRESET": "openrouter_free", "GODOTAI_SKIP_MODEL_PROBE": "1"}), \
                redirect_stdout(out):
            main(["doctor"])
        text = out.getvalue()
        self.assertIn("preset openrouter_free", text)
        self.assertIn("export OPENROUTER_API_KEY", text)
        self.assertIn("identity: godotai — open-weight model hosted by a provider", text)
        self.assertIn("preset=openrouter_free", text)


if __name__ == "__main__":
    unittest.main()
