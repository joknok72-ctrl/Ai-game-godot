"""Engine-output classification and project sanity checks (no engine needed)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import repo_config

from godotai.godot import RunResult
from godotai.verify import StepResult, VerificationReport, _step_from_run, check_project_file, classify_output, gd_scripts

REAL_CHECK_ONLY_OUTPUT = """\x1b[1;31mSCRIPT ERROR: \x1b[0;91mParse Error: Cannot assign a value of type String to variable "speed" with specified type float.\x1b[0m
\x1b[0;91m   at: GDScript::reload (res://scripts/broken.gd:3)\x1b[0m
SCRIPT ERROR: Parse Error: Identifier "nonexistent_thing" not declared in the current scope.
   at: GDScript::reload (res://scripts/broken.gd:5)
WARNING: The parameter "delta" is never used in the function "_process()".
   at: GDScript::reload (res://scripts/player.gd:20)
"""

HEADLESS_NOISE = """Godot Engine v4.7.2.stable.official.ed1daf0bf - https://godotengine.org
WARNING: XDG_RUNTIME_DIR is not set; using /tmp
   at: ...
ERROR: Started the engine as `root`, this is not recommended.
   at: ...
Vulkan device not found; falling back to Dummy renderer
WARNING: 3 RIDs of type "CanvasItem" were leaked.
ERROR: 4 RID allocations of type 'N7Physics2D' were leaked at exit.
Leaked instance: Node2D:123
ObjectDB instances leaked at exit (run with --verbose for details).
SMOKE_TEST_OK frames=320 time=2.21 obstacles=2
"""


class ClassifyOutputTests(unittest.TestCase):
    def test_real_parse_errors_with_ansi(self):
        errors, warnings = classify_output(REAL_CHECK_ONLY_OUTPUT)
        self.assertEqual(len(errors), 4, errors)  # 2 errors + their "at:" locations
        self.assertIn('Cannot assign a value of type String to variable "speed"', errors[0])
        self.assertIn("res://scripts/broken.gd:3", errors[1])
        self.assertIn("not declared", errors[2])
        self.assertEqual(len(warnings), 1)
        self.assertIn("never used", warnings[0])
        self.assertNotIn("\x1b", "".join(errors + warnings))

    def test_harmless_headless_noise_is_ignored(self):
        errors, warnings = classify_output(HEADLESS_NOISE)
        self.assertEqual(errors, [], errors)
        self.assertEqual(warnings, [], warnings)  # teardown leak chatter is not a game problem

    def test_android_sdk_chatter_is_not_an_error(self):
        # Printed by --import / export once an Android SDK is configured (observed with 4.7.2, build-tools 35.0.1).
        text = ("Godot Engine v4.7.2.stable.official.ed1daf0bf - https://godotengine.org\n"
                "Could not find version of build tools that matches Target SDK, using 35.0.1\n"
                "cannot connect to daemon at tcp:5037: Connection refused\n")
        self.assertEqual(classify_output(text), ([], []))
        # …but a genuine missing-resource error with the same prefix is still caught
        errors, _ = classify_output('ERROR: Could not find file "res://scenes/missing.tscn".\n')
        self.assertEqual(len(errors), 1)

    def test_runtime_error_patterns(self):
        text = ("USER SCRIPT ERROR: Invalid call. Nonexistent function 'foo' in base 'Node2D'.\n"
                "          at: _ready (res://scripts/main.gd:12)\n"
                "ERROR: Failed to load script \"res://scripts/x.gd\" with error \"Parse error\".\n")
        errors, _ = classify_output(text)
        self.assertEqual(len(errors), 3)

    def test_empty(self):
        self.assertEqual(classify_output(""), ([], []))


class StepFromRunTests(unittest.TestCase):
    def test_pass_and_fail(self):
        ok = _step_from_run("import", RunResult(["godot"], 0, "fine", ""), "all good")
        self.assertTrue(ok.passed)
        self.assertEqual(ok.summary, "all good")
        bad = _step_from_run("import", RunResult(["godot"], 1, "", "ERROR: Could not load resource res://x.tscn"), "ok")
        self.assertFalse(bad.passed)
        self.assertIn("1 error line", bad.summary)
        self.assertIn("Could not load", bad.output_tail)
        timeout = _step_from_run("run", RunResult(["godot"], -1, "", "", timed_out=True), "ok")
        self.assertFalse(timeout.passed)
        self.assertEqual(timeout.summary, "timed out")
        # non-zero exit without error lines is still a failure unless explicitly allowed
        quiet = _step_from_run("run", RunResult(["godot"], 3, "", ""), "ok")
        self.assertFalse(quiet.passed)
        self.assertTrue(_step_from_run("run", RunResult(["godot"], 3, "", ""), "ok", allow_nonzero=True).passed)


class ProjectFileTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()
        self.ws = Path(tempfile.mkdtemp(prefix="godotai-verify-"))

    def test_missing(self):
        step = check_project_file(self.ws, self.cfg)
        self.assertFalse(step.passed)
        self.assertIn("missing", step.summary)

    def test_good(self):
        (self.ws / "project.godot").write_text(
            'config_version=5\n\n[application]\n\nconfig/name="X"\nrun/main_scene="res://scenes/main.tscn"\n'
            'config/features=PackedStringArray("4.7", "GL Compatibility")\n')
        step = check_project_file(self.ws, self.cfg)
        self.assertTrue(step.passed, step.summary)

    def test_wrong_feature_tag_and_godot3_format(self):
        (self.ws / "project.godot").write_text(
            'config_version=4\n[application]\nconfig/features=PackedStringArray("4.3")\n')
        step = check_project_file(self.ws, self.cfg)
        self.assertFalse(step.passed)
        self.assertIn("config_version=5", step.summary)
        self.assertIn("pinned engine is 4.7", step.summary)
        self.assertIn("main_scene", step.summary)

    def test_template_project_file_passes_after_placeholder_substitution(self):
        text = (self.cfg.root / "templates" / "mobile-2d" / "project.godot").read_text(encoding="utf-8")
        text = text.replace("__GODOT_FEATURE__", self.cfg.engine.major_minor).replace("__GAME_NAME__", "T")
        (self.ws / "project.godot").write_text(text)
        self.assertTrue(check_project_file(self.ws, self.cfg).passed)

    def test_gd_scripts_skips_addons_and_hidden(self):
        for rel in ("scripts/a.gd", "addons/x/y.gd", ".godot/z.gd", "tests/smoke_test.gd"):
            p = self.ws / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("extends Node\n")
        rels = [p.relative_to(self.ws).as_posix() for p in gd_scripts(self.ws)]
        self.assertEqual(rels, ["scripts/a.gd", "tests/smoke_test.gd"])


class ReportTests(unittest.TestCase):
    def test_report_semantics(self):
        r = VerificationReport(Path("/p"))
        self.assertFalse(r.passed, "an empty report is not a pass")
        r.add(StepResult("a", True, "ok"))
        self.assertTrue(r.passed)
        r.add(StepResult("b", False, "bad", errors=["E1"], output_tail="tail"))
        self.assertFalse(r.passed)
        md = r.to_markdown()
        self.assertIn("**Result: FAIL**", md)
        self.assertIn("## ✅ a", md)
        self.assertIn("## ❌ b", md)
        self.assertIn("- E1", md)
        self.assertIn("tail", md)
        d = r.to_dict()
        self.assertFalse(d["passed"])
        self.assertEqual(len(d["steps"]), 2)


if __name__ == "__main__":
    unittest.main()
