"""Plan validation — the gate that forces thinking before file operations."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from _helpers import repo_config, valid_plan

from godotai.planner import PLAN_SCHEMA, Plan, is_safe_relative_path, load_plan, save_plan, validate_plan


class ValidatePlanTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()
        self.plan = valid_plan(self.cfg)

    def test_valid_plan_has_no_problems(self):
        self.assertEqual(validate_plan(self.plan, self.cfg), [])

    def test_version_aliases_accepted(self):
        for v in ("4.7.2-stable", "4.7.2.stable", "4.7.2", "4.7.2_stable", " 4.7.2-STABLE "):
            p = dict(self.plan, godot_version=v)
            self.assertEqual(validate_plan(p, self.cfg), [], v)

    def test_other_engine_version_rejected(self):
        for v in ("4.3", "4.7.1-stable", "3.5", "4.8-dev1", ""):
            problems = validate_plan(dict(self.plan, godot_version=v), self.cfg)
            self.assertTrue(any("godot_version must be 4.7.2-stable" in p for p in problems), (v, problems))

    def test_non_object_rejected(self):
        self.assertEqual(validate_plan(["not", "a", "dict"], self.cfg), ["plan must be a JSON object"])  # type: ignore[arg-type]

    def test_requires_verify_step_and_mandatory_checks(self):
        p = copy.deepcopy(self.plan)
        p["steps"] = [s for s in p["steps"] if s["kind"] != "verify"]
        p["verification"] = ["looks fine"]
        problems = validate_plan(p, self.cfg)
        self.assertTrue(any("kind 'verify'" in x for x in problems))
        for req in ("godot_import", "check_scripts", "smoke_test"):
            self.assertTrue(any(req in x for x in problems), req)

    def test_step_ids_must_be_sequential_and_unique(self):
        p = copy.deepcopy(self.plan)
        p["steps"][1]["id"] = "S3"  # S1, S3, S3, S4
        problems = validate_plan(p, self.cfg)
        self.assertTrue(any("sequential" in x for x in problems), problems)
        self.assertTrue(any("duplicate step id S3" in x for x in problems), problems)
        p = copy.deepcopy(self.plan)
        p["steps"][0]["id"] = "step-one"
        self.assertTrue(any("must look like S1" in x for x in validate_plan(p, self.cfg)))

    def test_file_steps_need_paths(self):
        p = copy.deepcopy(self.plan)
        p["steps"][0]["paths"] = []
        self.assertTrue(any("must list the paths" in x for x in validate_plan(p, self.cfg)))

    def test_unsafe_and_secret_paths_rejected(self):
        p = copy.deepcopy(self.plan)
        p["steps"][0]["paths"] = ["../outside.gd", "/etc/passwd", "C:\\win.gd", "release.keystore", "scripts/ok.gd"]
        problems = validate_plan(p, self.cfg)
        self.assertEqual(sum("must be relative and inside the project" in x for x in problems), 3, problems)
        self.assertTrue(any("secret material 'release.keystore'" in x for x in problems), problems)

    def test_design_must_be_concrete(self):
        p = copy.deepcopy(self.plan)
        p["design"]["scenes"] = []
        p["design"]["core_loop"] = ""
        problems = validate_plan(p, self.cfg)
        self.assertTrue(any("design.scenes" in x for x in problems))
        self.assertTrue(any("core_loop" in x for x in problems))

    def test_bad_kind_and_empty_steps(self):
        p = copy.deepcopy(self.plan)
        p["steps"][0]["kind"] = "yolo"
        self.assertTrue(any("kind must be one of" in x for x in validate_plan(p, self.cfg)))
        p["steps"] = []
        self.assertIn("steps must not be empty", validate_plan(p, self.cfg))

    def test_short_goal_or_summary(self):
        p = dict(self.plan, goal="hi", summary="short")
        problems = validate_plan(p, self.cfg)
        self.assertIn("goal is too short", problems)
        self.assertTrue(any("summary is too short" in x for x in problems))


class SafePathTests(unittest.TestCase):
    def test_paths(self):
        self.assertTrue(is_safe_relative_path("scenes/main.tscn"))
        self.assertTrue(is_safe_relative_path("./scripts/a.gd"))
        self.assertTrue(is_safe_relative_path("a/../b.gd"))  # normalises to b.gd, still inside
        self.assertFalse(is_safe_relative_path(""))
        self.assertFalse(is_safe_relative_path("../x"))
        self.assertFalse(is_safe_relative_path("a/../../x"))
        self.assertFalse(is_safe_relative_path("/abs"))
        self.assertFalse(is_safe_relative_path("\\\\server\\share"))
        self.assertFalse(is_safe_relative_path("D:/x"))


class PlanPersistenceTests(unittest.TestCase):
    def test_roundtrip_and_markdown(self):
        cfg = repo_config()
        plan = Plan.from_dict(valid_plan(cfg))
        ws = Path(tempfile.mkdtemp(prefix="godotai-plan-"))
        json_path, md_path = save_plan(plan, ws)
        self.assertTrue(json_path.is_file() and md_path.is_file())
        loaded = load_plan(ws)
        assert loaded is not None
        self.assertEqual(loaded.to_dict(), plan.to_dict())
        self.assertEqual(loaded.step_ids(), {"S1", "S2", "S3", "S4"})
        self.assertEqual(loaded.get_step("S2").kind, "create")
        self.assertIsNone(loaded.get_step("S99"))
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("# Plan — Build a tiny tap-dodge mobile game", md)
        self.assertIn("**S4** [verify] verify", md)
        self.assertIn("`scenes/main.tscn`, `scenes/player.tscn`", md)
        self.assertIn("## Verification", md)
        self.assertIsNone(load_plan(ws / "nowhere"))

    def test_schema_is_strict_json_schema(self):
        # Claude strict tool use requires additionalProperties:false and every property required.
        def walk(schema: dict):
            if schema.get("type") == "object":
                self.assertFalse(schema.get("additionalProperties", True), schema.get("properties", {}).keys())
                self.assertEqual(set(schema.get("required", [])), set(schema.get("properties", {}).keys()))
                for sub in schema["properties"].values():
                    walk(sub)
            if schema.get("type") == "array":
                walk(schema["items"])
        walk(PLAN_SCHEMA)
        json.dumps(PLAN_SCHEMA)  # serialisable


if __name__ == "__main__":
    unittest.main()
