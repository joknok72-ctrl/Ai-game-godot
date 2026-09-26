"""Eval task files + structural checks, and the run-log → training-data extractor."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO, repo_config

from godotai import dataset, evals
from godotai.evals import CheckResult, EvalError, EvalResult, EvalTask, load_tasks, run_check

CFG = repo_config()
CHECK_TYPES = {"file_exists", "file_glob_min", "file_glob_max", "content_regex", "any_script_regex", "no_script_regex",
               "typed_gdscript", "no_api_lint_errors", "smoke_test", "min_lines"}


class TaskFileTests(unittest.TestCase):
    def test_shipped_tasks_are_valid_and_unique(self):
        tasks = load_tasks(REPO)
        self.assertGreaterEqual(len(tasks), 6)
        ids = [t.id for t in tasks]
        self.assertEqual(len(ids), len(set(ids)), "duplicate task ids")
        for t in tasks:
            self.assertRegex(t.id, r"^[a-z0-9][a-z0-9-]+$", t.id)
            self.assertTrue(t.prompt.strip())
            self.assertTrue(t.checks or t.template, f"{t.id}: a task needs checks or a template")
            for c in t.checks:
                self.assertIn(c.get("type"), CHECK_TYPES, f"{t.id}: unknown check {c}")
            if t.template:
                self.assertTrue((REPO / "templates" / t.template / "project.godot").is_file(), t.template)
        self.assertIn("template-baseline", ids)
        self.assertIn("godot3-migration-trap", ids)
        self.assertIn("out-of-scope-refusal", ids)

    def test_task_requires_fields(self):
        with self.assertRaises(EvalError):
            EvalTask.from_dict({"id": "x", "title": "y"})
        t = EvalTask.from_dict({"id": "x", "title": "y", "prompt": "p", "checks": [{"type": "smoke_test"}], "tags": ["a"]})
        self.assertEqual(t.tags, ("a",))
        self.assertEqual(evals.get_task(REPO, "nope"), None)
        self.assertEqual(evals.get_task(REPO, "template-baseline").template, "mobile-2d")


class RunCheckTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="godotai-eval-"))
        (self.ws / "scripts").mkdir()
        (self.ws / "scenes").mkdir()
        (self.ws / "tests").mkdir()
        (self.ws / "project.godot").write_text('config_version=5\n[application]\nconfig/name="X"\n', encoding="utf-8")
        (self.ws / "scenes" / "main.tscn").write_text("[gd_scene format=3]\n", encoding="utf-8")
        (self.ws / "scripts" / "main.gd").write_text(
            "extends Node2D\n\nvar score: int = 0\nvar speed := 2.0\nvar untyped = 1\n\n"
            "func _ready() -> void:\n\tpass\n\nfunc helper():\n\tpass\n\n"
            "func _unhandled_input(event: InputEvent) -> void:\n\tif event is InputEventScreenTouch:\n\t\tscore += 1\n",
            encoding="utf-8")
        (self.ws / "tests" / "smoke_test.gd").write_text("extends SceneTree\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def check(self, **c) -> CheckResult:
        return run_check(c, self.ws)

    def test_file_and_glob_checks(self):
        self.assertTrue(self.check(type="file_exists", path="project.godot").passed)
        self.assertFalse(self.check(type="file_exists", path="export_presets.cfg").passed)
        self.assertTrue(self.check(type="file_glob_min", glob="*.tscn", min=1).passed)
        self.assertFalse(self.check(type="file_glob_min", glob="*.tscn", min=2).passed)
        self.assertTrue(self.check(type="file_glob_max", glob="*.gd", max=2).passed)
        (self.ws / ".godot").mkdir()
        (self.ws / ".godot" / "x.tscn").write_text("", encoding="utf-8")
        self.assertEqual(self.check(type="file_glob_min", glob="*.tscn", min=1).detail, "found 1", "hidden dirs are ignored")
        self.assertTrue(self.check(type="smoke_test").passed)

    def test_regex_checks(self):
        self.assertTrue(self.check(type="content_regex", path="project.godot", pattern=r'config/name="X"').passed)
        self.assertFalse(self.check(type="content_regex", path="missing.cfg", pattern="x").passed)
        r = self.check(type="any_script_regex", pattern=r"InputEventScreenTouch")
        self.assertTrue(r.passed)
        self.assertIn("scripts/main.gd", r.detail)
        self.assertFalse(self.check(type="any_script_regex", pattern=r"\byield\(").passed)
        self.assertTrue(self.check(type="no_script_regex", pattern=r"\byield\(").passed)
        r = self.check(type="no_script_regex", pattern=r"^extends Node2D")
        self.assertFalse(r.passed)
        self.assertIn("scripts/main.gd", r.detail)

    def test_typed_gdscript_ratio(self):
        r = self.check(type="typed_gdscript", min_ratio=0.6)
        self.assertEqual(r.detail, "functions 2/3, vars 2/3")
        self.assertTrue(r.passed)
        self.assertFalse(self.check(type="typed_gdscript", min_ratio=0.9).passed)

    def test_min_lines_and_unknown(self):
        self.assertTrue(self.check(type="min_lines", min=5).passed)
        self.assertFalse(self.check(type="min_lines", min=500).passed)
        r = self.check(type="teleport")
        self.assertFalse(r.passed)
        self.assertIn("unknown check", r.name)

    def test_api_lint_check_without_index_uses_patterns(self):
        self.assertTrue(self.check(type="no_api_lint_errors").passed)
        (self.ws / "scripts" / "old.gd").write_text("extends Node\nfunc _ready():\n\tyield(get_tree(), \"idle_frame\")\n", encoding="utf-8")
        r = self.check(type="no_api_lint_errors")
        self.assertFalse(r.passed)
        self.assertIn("old.gd:3", r.detail)

    def test_result_serialisation(self):
        res = EvalResult("t", False, [CheckResult("a", True), CheckResult("b", False, "why")], engine="4.7.2-stable",
                         seconds=3.2, agent={"status": "failed", "iterations": 4, "usage": {"output_tokens": 9}})
        d = res.to_dict()
        self.assertEqual(d["checks"][1], {"name": "b", "passed": False, "detail": "why"})
        md = res.to_markdown()
        self.assertIn("# Eval t — FAIL", md)
        self.assertIn("❌ b: why", md)
        self.assertIn("status=failed iterations=4", md)
        out = evals.save_result(res, self.ws / "results")
        self.assertTrue(out.name.startswith("t-") and out.suffix == ".json")
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["task_id"], "t")

    def test_score_project_without_engine_reports_the_problem(self):
        from dataclasses import replace
        from unittest import mock
        task = EvalTask("t", "T", "p", checks=({"type": "smoke_test"},))
        with mock.patch.dict("os.environ", {"GODOT_BIN": str(self.ws / "no-such-godot"), "PATH": str(self.ws)}):
            res = evals.score_project(task, self.ws, replace(CFG))
        self.assertFalse(res.passed)
        self.assertEqual(res.checks[0].name, "engine verification")
        self.assertIn("Godot", res.checks[0].detail)


# ---------------------------------------------------------------------------
def anthropic_run(passed=True, iterations=4, token="") -> dict:
    return {
        "task": "make a game", "status": "success" if passed else "failed", "engine": "4.7.2-stable",
        "model": "claude-fable-5-1", "provider": "anthropic", "iterations": iterations, "verification_passed": passed,
        "system": "SYSTEM PROMPT", "tools": [{"name": "write_file", "description": "d", "input_schema": {"type": "object"}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Task: make a game"}]},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "secret reasoning", "signature": "sig"},
                {"type": "text", "text": "Planning."},
                {"type": "tool_use", "id": "toolu_1", "name": "write_file", "input": {"path": "a.gd", "content": "extends Node" + token}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "wrote a.gd", "is_error": False},
                {"type": "text", "text": "First privately list what you need next; …"}]},
            {"role": "system", "clear_at": "next_user_message", "content": "nudge"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_2", "name": "godot_verify", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_2",
                                          "content": [{"type": "text", "text": "FAIL"}], "is_error": True}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Fixed and verified."}]},
        ],
    }


def openai_run() -> dict:
    return {
        "task": "t", "status": "success", "engine": "4.7.2-stable", "model": "qwen", "provider": "openai_compat",
        "iterations": 3, "verification_passed": True, "system": "S", "tools": [],
        "messages": [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "Task"},
            {"role": "assistant", "content": None, "reasoning_content": "hidden",
             "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a\"}"}},
                            {"id": "c2", "type": "function", "function": {"name": "x", "arguments": "{bad"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "tool", "tool_call_id": "c2", "content": "ERROR: nope"},
            {"role": "user", "content": [{"type": "text", "text": "part 1"}, {"type": "text", "text": "part 2"}]},
            {"role": "assistant", "content": "done"},
        ],
    }


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="godotai-dataset-"))
        self.runs = self.tmp / ".godotai" / "runs"
        self.runs.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_anthropic_normalisation_drops_thinking_and_nudges(self):
        ex = dataset.normalise_run(anthropic_run())
        assert ex is not None
        roles = [m["role"] for m in ex["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "assistant", "tool", "assistant"])
        self.assertNotIn("secret reasoning", json.dumps(ex))
        self.assertNotIn("nudge", json.dumps(ex))
        self.assertNotIn("First privately list", json.dumps(ex), "batching nudge text blocks are not trajectory content")
        a1 = ex["messages"][1]
        self.assertEqual(a1["content"], "Planning.")
        self.assertEqual(a1["tool_calls"], [{"id": "toolu_1", "name": "write_file", "args": {"path": "a.gd", "content": "extends Node"}}])
        self.assertEqual(ex["messages"][2], {"role": "tool", "tool_call_id": "toolu_1", "content": "wrote a.gd", "is_error": False})
        self.assertEqual(ex["messages"][4], {"role": "tool", "tool_call_id": "toolu_2", "content": "FAIL", "is_error": True})
        self.assertEqual(ex["source_model"], "claude-fable-5-1")
        self.assertEqual(ex["system"], "SYSTEM PROMPT")
        self.assertEqual(ex["tools"][0]["name"], "write_file")
        self.assertTrue(ex["verification_passed"])
        self.assertEqual(len(ex["id"]), 16)

    def test_openai_normalisation(self):
        ex = dataset.normalise_run(openai_run())
        assert ex is not None
        roles = [m["role"] for m in ex["messages"]]
        self.assertEqual(roles, ["user", "assistant", "tool", "tool", "user", "assistant"])
        self.assertNotIn("hidden", json.dumps(ex))
        a = ex["messages"][1]
        self.assertEqual(a["tool_calls"][0], {"id": "c1", "name": "read_file", "args": {"path": "a"}})
        self.assertEqual(a["tool_calls"][1]["args"], {"_raw": "{bad"})
        self.assertEqual(ex["messages"][3], {"role": "tool", "tool_call_id": "c2", "content": "nope", "is_error": True})
        self.assertEqual(ex["messages"][4]["content"], "part 1\npart 2")

    def test_normalise_rejects_empty_or_assistant_less_runs(self):
        self.assertIsNone(dataset.normalise_run({"messages": []}))
        self.assertIsNone(dataset.normalise_run({"provider": "anthropic", "messages": [{"role": "user", "content": [{"type": "text", "text": "x"}]}]}))

    def test_extract_filters_dedupes_and_redacts(self):
        # Built at runtime (str.join is not constant-folded), so neither the source
        # nor the compiled .pyc contains a string that looks like a real key.
        fake_key = "-".join(["sk", "ant", "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4"])
        (self.runs / "a.json").write_text(json.dumps(anthropic_run(token=" # " + fake_key)), encoding="utf-8")
        (self.runs / "b.json").write_text(json.dumps(anthropic_run(token=" # " + fake_key)), encoding="utf-8")  # duplicate
        (self.runs / "c.json").write_text(json.dumps(anthropic_run(passed=False)), encoding="utf-8")
        (self.runs / "d.json").write_text(json.dumps(anthropic_run(iterations=1)), encoding="utf-8")
        (self.runs / "e.json").write_text(json.dumps(openai_run()), encoding="utf-8")
        (self.runs / "junk.json").write_text("{not json", encoding="utf-8")
        (self.runs / "other.json").write_text(json.dumps({"unrelated": True}), encoding="utf-8")
        out = self.tmp / "data" / "sft.jsonl"
        stats = dataset.extract(self.runs, out)
        self.assertEqual((stats["files"], stats["kept"], stats["skipped_failed"], stats["skipped_short"]), (5, 2, 1, 1))
        self.assertEqual(stats["redactions"], 1)
        self.assertGreater(stats["approx_tokens"], 100)
        lines = out.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        text = "\n".join(lines)
        self.assertNotIn(fake_key, text)
        self.assertIn("[REDACTED]", text)
        ids = {json.loads(l)["id"] for l in lines}
        self.assertEqual(len(ids), 2)
        # failed runs can be included explicitly (repair data)
        stats2 = dataset.extract(self.runs, out, include_failed=True)
        self.assertEqual(stats2["kept"], 3)


if __name__ == "__main__":
    unittest.main()
