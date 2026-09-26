"""Integration tests that need the real pinned Godot binary.

Skipped automatically when no matching binary is available (set $GODOT_BIN or run
`python3 -m godotai install-godot`). CI runs them in the godot-verify-template job.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from _helpers import repo_config

from godotai.godot import Godot, GodotNotFound, GodotVersionMismatch, find_godot_binary
from godotai.scaffold import create_project
from godotai.verify import verify_project

CFG = repo_config()
try:
    GODOT: Godot | None = Godot(CFG.engine) if find_godot_binary(CFG.engine) else None
except (GodotNotFound, GodotVersionMismatch):
    GODOT = None


@unittest.skipUnless(GODOT is not None, "pinned Godot binary not available")
class RealEngineTests(unittest.TestCase):
    def setUp(self):
        self.dest = Path(tempfile.mkdtemp(prefix="godotai-engine-")) / "game"
        create_project(CFG, "mobile-2d", self.dest, "CI Game", "com.example.cigame")

    def tearDown(self):
        shutil.rmtree(self.dest.parent, ignore_errors=True)

    def test_version_guard(self):
        assert GODOT is not None
        self.assertEqual(GODOT.version(), CFG.engine.version_string)
        from dataclasses import replace
        with self.assertRaises(GodotVersionMismatch):
            Godot(replace(CFG.engine, version="4.6.1"), binary=GODOT.binary)

    def test_template_passes_full_verification(self):
        report = verify_project(self.dest, CFG, GODOT, run_lint=False)
        self.assertTrue(report.passed, report.to_markdown())
        names = [s.name for s in report.steps]
        self.assertIn("import (--import)", names)
        self.assertIn("GDScript parse (--check-only)", names)
        self.assertIn("smoke test (tests/smoke_test.gd)", names)

    def test_broken_script_is_caught(self):
        (self.dest / "scripts" / "broken.gd").write_text(
            "extends Node\n\nvar speed: float = \"fast\"\n\nfunc _ready() -> void:\n\tundefined_call()\n", encoding="utf-8")
        report = verify_project(self.dest, CFG, GODOT, run_lint=False)
        self.assertFalse(report.passed)
        parse = next(s for s in report.steps if s.name.startswith("GDScript parse"))
        self.assertFalse(parse.passed)
        self.assertTrue(any("broken.gd" in e for e in parse.errors), parse.errors)

    def test_export_apk_if_templates_and_sdk_present(self):
        assert GODOT is not None
        if not GODOT.templates_installed() or not (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")):
            self.skipTest("export templates / Android SDK not configured")
        GODOT.import_project(self.dest)
        out = self.dest / "build" / "android" / "game.apk"
        res = GODOT.export(self.dest, "Android", out, debug=True)
        self.assertTrue(res.ok, res.output[-3000:])
        self.assertGreater(out.stat().st_size, 1_000_000)


if __name__ == "__main__":
    unittest.main()
