"""godot.toml loading, the engine pin and version-guard helpers."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import PRIVATE_ENV, REPO, repo_config, vendor_config, vendor_identity

from godotai.config import (
    OPENAI_VENDOR_URL,
    PRIVATE_BASE_URL,
    AgentConfig,
    ConfigError,
    EngineConfig,
    load_config,
    parse_godot_version_output,
    version_matches,
)


class EnginePinTests(unittest.TestCase):
    def test_repo_config_pins_4_7_2_stable(self):
        cfg = repo_config()
        self.assertEqual(cfg.engine.version, "4.7.2")
        self.assertEqual(cfg.engine.release, "stable")
        self.assertEqual(cfg.engine.tag, "4.7.2-stable")
        self.assertEqual(cfg.engine.version_string, "4.7.2.stable")
        self.assertEqual(cfg.engine.major_minor, "4.7")
        self.assertEqual(cfg.path, REPO / "godot.toml")

    def test_official_asset_names(self):
        eng = EngineConfig(version="4.7.2", release="stable")
        self.assertEqual(eng.editor_zip_name, "Godot_v4.7.2-stable_linux.x86_64.zip")
        self.assertEqual(eng.editor_binary_name, "Godot_v4.7.2-stable_linux.x86_64")
        self.assertEqual(eng.templates_archive_name, "Godot_v4.7.2-stable_export_templates.tpz")
        self.assertEqual(eng.templates_dirname, "4.7.2.stable")
        self.assertEqual(eng.editor_settings_filename, "editor_settings-4.7.tres")
        self.assertEqual(
            eng.url(eng.editor_zip_name),
            "https://github.com/godotengine/godot-builds/releases/download/4.7.2-stable/Godot_v4.7.2-stable_linux.x86_64.zip",
        )
        self.assertTrue(eng.checksums_url.endswith("/4.7.2-stable/SHA512-SUMS.txt"))

    def test_mono_flavor_names(self):
        eng = EngineConfig(version="4.7.2", release="stable", flavor="mono")
        self.assertEqual(eng.editor_zip_name, "Godot_v4.7.2-stable_mono_linux_x86_64.zip")
        self.assertEqual(eng.templates_archive_name, "Godot_v4.7.2-stable_mono_export_templates.tpz")
        self.assertEqual(eng.templates_dirname, "4.7.2.stable.mono")

    def test_future_version_is_configurable(self):
        eng = EngineConfig(version="4.8.1", release="rc2")
        self.assertEqual(eng.tag, "4.8.1-rc2")
        self.assertEqual(eng.templates_dirname, "4.8.1.rc2")
        self.assertEqual(eng.editor_settings_filename, "editor_settings-4.8.tres")

    def test_invalid_engine_values_rejected(self):
        with self.assertRaises(ConfigError):
            EngineConfig(version="four.seven")
        with self.assertRaises(ConfigError):
            EngineConfig(release="Stable Release")
        with self.assertRaises(ConfigError):
            EngineConfig(flavor="csharp")

    def test_pinned_checksum_file_exists_and_covers_assets(self):
        cfg = repo_config()
        sums = cfg.checksums_file()
        self.assertTrue(sums.is_file(), sums)
        text = sums.read_text(encoding="utf-8")
        self.assertIn(cfg.engine.editor_zip_name, text)
        self.assertIn(cfg.engine.templates_archive_name, text)
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 2:
                self.assertRegex(parts[0], r"^[0-9a-f]{128}$", line)


class AgentConfigTests(unittest.TestCase):
    def test_repo_agent_settings(self):
        """The shipped default is *your* model on *your* server — no vendor API in the runtime path."""
        with mock.patch.dict(os.environ, PRIVATE_ENV):
            cfg = repo_config()
            self.assertEqual(cfg.agent.provider, "openai_compat")
            self.assertEqual(cfg.agent.model, "godotai")
            self.assertEqual(cfg.agent.route, "direct")
            self.assertEqual(cfg.agent.endpoint, PRIVATE_BASE_URL)
            self.assertTrue(cfg.agent.private_endpoint)
        self.assertEqual(cfg.agent.effort, "max")
        self.assertGreaterEqual(cfg.agent.max_tokens, 32000, "high/xhigh/max effort needs a large max_tokens")
        self.assertTrue(cfg.agent.require_plan_approval)
        # and the identity block says plainly what the weights are
        self.assertEqual(cfg.model.kind, "open_weight_deployment")
        self.assertEqual(cfg.model.base_model, "Qwen/Qwen2.5-Coder-7B-Instruct")
        self.assertEqual(cfg.model.base_license, "Apache-2.0")
        self.assertTrue(cfg.model.yours)
        self.assertFalse(cfg.model.trained_by_you, "no adapter has been trained yet — must not claim otherwise")
        self.assertIsNone(cfg.model.describe()["quality_claim"])

    def test_endpoint_precedence(self):
        a = AgentConfig()
        with mock.patch.dict(os.environ, PRIVATE_ENV):
            self.assertEqual(a.endpoint, PRIVATE_BASE_URL, "nothing set → your private server")
            self.assertTrue(a.private_endpoint)
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_API_KEY": "sk-test"}):
            self.assertEqual(a.endpoint, OPENAI_VENDOR_URL, "a vendor key alone is an explicit choice of the vendor")
            self.assertFalse(a.private_endpoint)
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": "http://gpu-box:8000/v1"}):
            self.assertEqual(a.endpoint, "http://gpu-box:8000/v1", "OPENAI_BASE_URL beats the key-derived vendor URL")
            self.assertTrue(a.private_endpoint)
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_BASE_URL": "http://gpu-box:8000/v1"}):
            self.assertEqual(AgentConfig(base_url="http://explicit:1/v1").endpoint, "http://explicit:1/v1")
        self.assertIsNone(AgentConfig(provider="anthropic").endpoint, "Anthropic has its own default URL")
        self.assertIsNone(AgentConfig(route="workers_ai").endpoint, "Cloudflare routes derive the URL in make_provider")

    def test_vendor_model_must_be_declared_as_such(self):
        """A vendor API can be used — but never labelled as your own model (and the reverse)."""
        from dataclasses import replace
        cfg = repo_config()
        with self.assertRaises(ConfigError) as cm:
            replace(cfg, agent=replace(cfg.agent, provider="anthropic", model="claude-fable-5-1"))
        self.assertIn("vendor_api", str(cm.exception))
        ok = vendor_config()
        self.assertEqual(ok.agent.provider, "anthropic")
        self.assertEqual(ok.model.kind, "vendor_api")
        self.assertFalse(ok.model.yours)
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_API_KEY": "sk-test"}):
            with self.assertRaises(ConfigError):
                replace(cfg, agent=replace(cfg.agent, model="gpt-x"))       # api.openai.com via the key → vendor
        with self.assertRaises(ConfigError):
            replace(cfg, agent=replace(cfg.agent, route="cf_gateway", model="openai/gpt-x"))
        replace(cfg, agent=replace(cfg.agent, route="cf_gateway", model="workers-ai/@cf/qwen/qwen2.5-coder-32b-instruct"))
        replace(cfg, agent=replace(cfg.agent, route="workers_ai", model="@cf/qwen/qwen2.5-coder-32b-instruct"),
                model=replace(cfg.model, serving="managed"))
        with self.assertRaises(ConfigError):
            replace(cfg, agent=replace(cfg.agent, base_url="http://127.0.0.1:8000/v1"), model=vendor_identity("x"))

    def test_model_env_overrides(self):
        env = {**PRIVATE_ENV, "GODOTAI_MODEL_KIND": "fine_tune", "GODOTAI_ADAPTER": "training/out/godotai-lora",
               "GODOTAI_MODEL_NAME": "godotai-ft"}
        with mock.patch.dict(os.environ, env):
            cfg = load_config(REPO / "godot.toml")
        self.assertEqual(cfg.model.kind, "fine_tune")
        self.assertEqual(cfg.model.adapter, "training/out/godotai-lora")
        self.assertTrue(cfg.model.trained_by_you)
        self.assertIn("godotai-ft", cfg.model.disclosure("en"))
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "GODOTAI_MODEL_KIND": "fine_tune"}):
            with self.assertRaises(ConfigError):
                load_config(REPO / "godot.toml")      # fine_tune without an adapter is a claim without weights

    def test_invalid_agent_values(self):
        with self.assertRaises(ConfigError):
            AgentConfig(provider="gemini")
        with self.assertRaises(ConfigError):
            AgentConfig(effort="ultra")

    def test_android_requirements_from_official_docs(self):
        cfg = repo_config()
        self.assertEqual(cfg.android.jdk_major, 17)
        pk = set(cfg.android.sdk_packages)
        self.assertIn("build-tools;35.0.1", pk)
        self.assertIn("platforms;android-35", pk)
        self.assertIn("ndk;28.1.13356709", pk)
        self.assertIn("cmake;3.10.2.4988404", pk)
        self.assertIn("platform-tools", pk)


class LoadConfigTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp(prefix="godotai-cfg-"))
        p = d / "godot.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_unknown_key_is_rejected(self):
        p = self._write('[engine]\nversion = "4.7.2"\nrelease = "stable"\nbogus = 1\n')
        with self.assertRaises(ConfigError):
            load_config(p)

    def test_env_overrides(self):
        p = self._write('[engine]\nversion = "4.7.2"\n[agent]\neffort = "high"\n')
        env = {"GODOTAI_EFFORT": "low", "GODOTAI_ENGINE_VERSION": "4.9.0", "GODOTAI_PROVIDER": "openai_compat",
               "GODOTAI_MODEL": "qwen3-coder", "GODOTAI_BASE_URL": "http://localhost:11434/v1"}
        with mock.patch.dict(os.environ, env, clear=False):
            cfg = load_config(p)
        self.assertEqual(cfg.agent.effort, "low")
        self.assertEqual(cfg.engine.version, "4.9.0")
        self.assertEqual(cfg.agent.provider, "openai_compat")
        self.assertEqual(cfg.agent.model, "qwen3-coder")
        self.assertEqual(cfg.agent.base_url, "http://localhost:11434/v1")
        self.assertEqual(cfg.checksums_file(), p.parent / "engine" / "checksums" / "4.9.0-stable" / "SHA512-SUMS.txt")

    def test_sdk_packages_become_tuple(self):
        p = self._write('[android]\nsdk_packages = ["platform-tools", "build-tools;35.0.1"]\n')
        cfg = load_config(p)
        self.assertEqual(cfg.android.sdk_packages, ("platform-tools", "build-tools;35.0.1"))


class VersionGuardTests(unittest.TestCase):
    def test_parse_official_output(self):
        self.assertEqual(parse_godot_version_output("4.7.2.stable.official.ed1daf0bf"), "4.7.2.stable")
        self.assertEqual(parse_godot_version_output("noise\n4.7.2.stable.official.ed1daf0bf\n"), "4.7.2.stable")
        self.assertEqual(parse_godot_version_output("4.7.stable.official.abcdef"), "4.7.0.stable")
        self.assertEqual(parse_godot_version_output("4.8.rc1.official.abcdef"), "4.8.0.rc1")
        self.assertEqual(parse_godot_version_output("Godot Engine v4.7.2.stable.official.ed1daf0bf - https://godotengine.org"),
                         "4.7.2.stable")
        self.assertIsNone(parse_godot_version_output("command not found"))

    def test_version_matches_only_the_pin(self):
        eng = EngineConfig(version="4.7.2", release="stable")
        self.assertTrue(version_matches("4.7.2.stable.official.ed1daf0bf", eng))
        self.assertFalse(version_matches("4.7.1.stable.official.aaaaaaa", eng))
        self.assertFalse(version_matches("4.7.2.rc1.official.aaaaaaa", eng))
        self.assertFalse(version_matches("4.8.stable.official.aaaaaaa", eng))
        self.assertFalse(version_matches("3.6.stable.official", eng))
        self.assertFalse(version_matches("", eng))

    def test_minor_only_pin_accepts_x_y_0(self):
        eng = EngineConfig(version="4.8", release="stable")
        self.assertTrue(version_matches("4.8.stable.official.abc", eng))
        self.assertFalse(version_matches("4.8.1.stable.official.abc", eng))


class HarnessControlTests(unittest.TestCase):
    """New Fable 5.1 harness fields: defaults, validation and env overrides."""

    def test_defaults_keep_beta_features_off(self):
        a = repo_config().agent
        self.assertEqual(a.effort, "max")
        self.assertIsNone(a.act_effort)
        self.assertTrue(a.batch_nudge)
        self.assertTrue(a.long_output_note)
        self.assertFalse(a.progress_updates)
        self.assertFalse(a.turn_scoped_system)
        self.assertIsNone(a.task_budget_tokens)
        self.assertFalse(a.prefix_binding_drop)
        self.assertEqual(a.route, "direct")
        self.assertFalse(a.uses_beta)
        self.assertTrue(AgentConfig(progress_updates=True).uses_beta)
        self.assertTrue(AgentConfig(act_effort="high").uses_beta)
        self.assertFalse(AgentConfig(act_effort="max").uses_beta)

    def test_validation(self):
        with self.assertRaises(ConfigError):
            AgentConfig(act_effort="ultra")
        with self.assertRaises(ConfigError):
            AgentConfig(task_budget_tokens=1000)
        AgentConfig(task_budget_tokens=20_000)
        with self.assertRaises(ConfigError):
            AgentConfig(route="tor")
        with self.assertRaises(ConfigError):
            AgentConfig(route="workers_ai", provider="anthropic")
        AgentConfig(route="workers_ai", provider="openai_compat")
        AgentConfig(route="cf_gateway")

    def test_env_overrides_for_harness_controls(self):
        env = {
            "GODOTAI_ACT_EFFORT": "high", "GODOTAI_ROUTE": "cf_gateway", "GODOTAI_PROGRESS_UPDATES": "1",
            "GODOTAI_BATCH_NUDGE": "off", "GODOTAI_TURN_SCOPED_SYSTEM": "true", "GODOTAI_PREFIX_BINDING_DROP": "yes",
            "GODOTAI_LONG_OUTPUT_NOTE": "0", "GODOTAI_TASK_BUDGET": "150000",
        }
        with mock.patch.dict(os.environ, env):
            a = load_config(REPO / "godot.toml").agent
        self.assertEqual(a.act_effort, "high")
        self.assertEqual(a.route, "cf_gateway")
        self.assertTrue(a.progress_updates)
        self.assertFalse(a.batch_nudge)
        self.assertTrue(a.turn_scoped_system)
        self.assertTrue(a.prefix_binding_drop)
        self.assertFalse(a.long_output_note)
        self.assertEqual(a.task_budget_tokens, 150_000)
        self.assertTrue(a.uses_beta)
        with mock.patch.dict(os.environ, {"GODOTAI_TASK_BUDGET": "lots"}):
            with self.assertRaises(ConfigError):
                load_config(REPO / "godot.toml")
        with mock.patch.dict(os.environ, {"GODOTAI_TASK_BUDGET": "5"}):
            with self.assertRaises(ConfigError):
                load_config(REPO / "godot.toml")
        with mock.patch.dict(os.environ, {"GODOTAI_TASK_BUDGET": "0"}):   # 0 = off, as documented in godot.toml
            self.assertIsNone(load_config(REPO / "godot.toml").agent.task_budget_tokens)

    def test_godot_toml_documents_every_harness_key(self):
        # The commented example block in godot.toml must stay in sync with AgentConfig.
        text = (REPO / "godot.toml").read_text(encoding="utf-8")
        for key in ("route", "act_effort", "batch_nudge", "long_output_note", "progress_updates",
                    "turn_scoped_system", "task_budget_tokens", "prefix_binding_drop"):
            self.assertRegex(text, rf"(?m)^#?\s*{key}\s*=", key)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "godot.toml"
            p.write_text(text.replace("# task_budget_tokens = 0", "task_budget_tokens = 0"), encoding="utf-8")
            self.assertIsNone(load_config(p).agent.task_budget_tokens)


if __name__ == "__main__":
    unittest.main()
