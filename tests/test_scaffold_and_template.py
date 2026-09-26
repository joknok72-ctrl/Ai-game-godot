"""Template project integrity and scaffolding."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO, repo_config

from godotai.scaffold import ScaffoldError, create_project, list_templates, package_from_name
from godotai.verify import check_project_file, gd_scripts

TEMPLATE = REPO / "templates" / "mobile-2d"


class TemplateIntegrityTests(unittest.TestCase):
    def test_required_files(self):
        for rel in ("project.godot", "export_presets.cfg", "icon.svg", ".gitignore", "scenes/main.tscn",
                    "scenes/player.tscn", "scenes/obstacle.tscn", "scripts/main.gd", "scripts/player.gd",
                    "scripts/obstacle.gd", "tests/smoke_test.gd"):
            self.assertTrue((TEMPLATE / rel).is_file(), rel)

    def test_project_godot_is_godot4_mobile_ready(self):
        text = (TEMPLATE / "project.godot").read_text(encoding="utf-8")
        self.assertIn("config_version=5", text)
        self.assertIn('config/features=PackedStringArray("__GODOT_FEATURE__"', text)
        self.assertIn('config/name="__GAME_NAME__"', text)
        self.assertIn("run/main_scene=", text)
        self.assertIn("handheld/orientation", text)
        self.assertIn('stretch/mode="canvas_items"', text)
        self.assertIn('renderer/rendering_method="gl_compatibility"', text)

    def test_export_presets_have_android_and_no_secrets(self):
        text = (TEMPLATE / "export_presets.cfg").read_text(encoding="utf-8")
        self.assertIn('name="Android"', text)
        self.assertIn('platform="Android"', text)
        self.assertIn('package/unique_name="__PACKAGE__"', text)
        # Keystore credentials are intentionally absent: Godot reads them from the
        # GODOT_ANDROID_KEYSTORE_{DEBUG,RELEASE}_{PATH,USER,PASSWORD} env vars at export time.
        self.assertNotRegex(text, r'keystore/(debug|release)="[^"]+"', "no keystore paths may be committed")
        self.assertNotRegex(text, r'password="[^"]+"', "no passwords may be committed")
        self.assertIn("gradle_build/use_gradle_build=false", text)
        self.assertIn("arm64-v8a", text)

    def test_scripts_use_tabs_and_static_typing(self):
        for script in gd_scripts(TEMPLATE):
            text = script.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                self.assertFalse(line.startswith("    "), f"{script.name}:{i} uses spaces — GDScript style is tabs")
            self.assertIn("-> void", text, script.name)
        smoke = (TEMPLATE / "tests" / "smoke_test.gd").read_text(encoding="utf-8")
        self.assertIn("extends SceneTree", smoke)
        self.assertIn("SMOKE_TEST_OK", smoke)

    def test_scenes_reference_existing_scripts(self):
        for scene in TEMPLATE.glob("scenes/*.tscn"):
            text = scene.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("[gd_scene "), scene.name)
            for m in re.finditer(r'path="res://([^"]+)"', text):
                self.assertTrue((TEMPLATE / m.group(1)).is_file(), f"{scene.name} → {m.group(1)}")

    def test_gitignore_excludes_caches_and_secrets(self):
        text = (TEMPLATE / ".gitignore").read_text(encoding="utf-8")
        for entry in (".godot/", "*.keystore", "*.apk"):
            self.assertIn(entry, text)


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()

    def test_list_templates(self):
        self.assertIn("mobile-2d", list_templates(self.cfg))

    def test_package_from_name(self):
        self.assertEqual(package_from_name("Tap Dodge"), "com.example.tap_dodge")
        self.assertEqual(package_from_name("2048-clone", owner="studio"), "com.studio.g2048_clone")
        self.assertEqual(package_from_name("!!!"), "com.example.game")

    def test_create_project_substitutes_placeholders(self):
        dest = Path(tempfile.mkdtemp(prefix="godotai-scaffold-")) / "game"
        create_project(self.cfg, "mobile-2d", dest, "Tap Dodge", "com.example.tapdodge")
        pg = (dest / "project.godot").read_text(encoding="utf-8")
        self.assertIn('config/name="Tap Dodge"', pg)
        self.assertIn('PackedStringArray("4.7"', pg)
        presets = (dest / "export_presets.cfg").read_text(encoding="utf-8")
        self.assertIn('package/unique_name="com.example.tapdodge"', presets)
        for p in dest.rglob("*"):
            if p.is_file():
                self.assertNotIn("__GAME_NAME__", p.read_text(encoding="utf-8", errors="ignore"), p)
                self.assertNotIn("__PACKAGE__", p.read_text(encoding="utf-8", errors="ignore"), p)
                self.assertNotIn("__GODOT_", p.read_text(encoding="utf-8", errors="ignore"), p)
        self.assertTrue(check_project_file(dest, self.cfg).passed)
        self.assertTrue((dest / ".gitignore").is_file())

    def test_bad_inputs(self):
        dest = Path(tempfile.mkdtemp(prefix="godotai-scaffold-"))
        with self.assertRaises(ScaffoldError):
            create_project(self.cfg, "nope", dest / "x", "G")
        with self.assertRaises(ScaffoldError):
            create_project(self.cfg, "mobile-2d", dest / "y", "G", "NotAPackage")
        (dest / "z").mkdir()
        (dest / "z" / "file").write_text("x")
        with self.assertRaises(ScaffoldError):
            create_project(self.cfg, "mobile-2d", dest / "z", "G")

    def test_future_engine_version_flows_into_scaffold(self):
        from dataclasses import replace
        cfg = replace(self.cfg, engine=replace(self.cfg.engine, version="4.9.0"))
        dest = Path(tempfile.mkdtemp(prefix="godotai-scaffold-")) / "g"
        create_project(cfg, "mobile-2d", dest, "Future")
        self.assertIn('PackedStringArray("4.9"', (dest / "project.godot").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
