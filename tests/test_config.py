"""godot.toml loading, the engine pin and version-guard helpers."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import REPO, repo_config

from godotai.config import (
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
        cfg = repo_config()
        self.assertEqual(cfg.agent.provider, "anthropic")
        self.assertEqual(cfg.agent.model, "claude-fable-5-1")
        self.assertEqual(cfg.agent.effort, "max")
        self.assertGreaterEqual(cfg.agent.max_tokens, 32000, "high/xhigh/max effort needs a large max_tokens")
        self.assertTrue(cfg.agent.require_plan_approval)

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


if __name__ == "__main__":
    unittest.main()
