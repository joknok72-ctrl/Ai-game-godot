"""api_lookup / api_search / api_lint tools: registry policy and behaviour with a synthetic index."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import repo_config
from test_apiref import make_index

from godotai.tools import Phase, ToolContext, build_registry
from godotai.tools import apiref_tools


def make_ctx(idx=None, phase=Phase.PLAN) -> ToolContext:
    ws = Path(tempfile.mkdtemp(prefix="godotai-apitools-"))
    ctx = ToolContext(cfg=repo_config(), workspace=ws, phase=phase, log=lambda s: None)
    if idx is not None:
        ctx.extra["apiref"] = idx
    return ctx


class ApiToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = build_registry(include_github=False)
        cls.idx = make_index()

    def setUp(self):
        self.ctx = make_ctx(self.idx)

    def tearDown(self):
        shutil.rmtree(self.ctx.workspace, ignore_errors=True)

    def test_tools_are_registered_read_only_and_allowed_in_plan_phase(self):
        defs = {d["name"]: d for d in self.reg.definitions()}
        for name in ("api_lookup", "api_search", "api_lint"):
            self.assertIn(name, defs)
            self.assertNotIn("step_id", defs[name]["input_schema"]["properties"], f"{name} is read-only")
        self.assertEqual(defs["api_lookup"]["input_schema"]["required"], ["class_name"])
        self.assertIn("BEFORE using any API you are not certain about", defs["api_lookup"]["description"])
        # read-only → usable before the plan is approved (PLAN phase ctx)
        res = self.reg.execute("api_lookup", {"class_name": "Node", "member": "queue_free"}, self.ctx)
        self.assertTrue(res.ok, res.content)

    def test_lookup_class_member_enum_and_errors(self):
        res = self.reg.execute("api_lookup", {"class_name": "CharacterBody2D"}, self.ctx)
        self.assertTrue(res.ok)
        self.assertIn("move_and_slide() -> bool", res.content)
        res = self.reg.execute("api_lookup", {"class_name": "characterbody2d", "member": "position"}, self.ctx)
        self.assertTrue(res.ok)
        self.assertIn("(inherited from Node2D)", res.content)
        res = self.reg.execute("api_lookup", {"class_name": "Node", "enum": "ProcessMode"}, self.ctx)
        self.assertEqual(res.content, "enum Node.ProcessMode: PROCESS_MODE_INHERIT = 0, PROCESS_MODE_PAUSABLE = 1")
        res = self.reg.execute("api_lookup", {"class_name": "Node", "enum": "Nope"}, self.ctx)
        self.assertTrue(res.is_error)
        res = self.reg.execute("api_lookup", {"class_name": "KinematicBody2D", "member": "move_and_slide"}, self.ctx)
        self.assertTrue(res.is_error)
        self.assertIn("in Godot 4 use CharacterBody2D", res.content)
        res = self.reg.execute("api_lookup", {"class_name": "Node", "member": "nope_nope"}, self.ctx)
        self.assertTrue(res.is_error)
        self.assertIn("has no member 'nope_nope'", res.content)
        # unknown class + a member that exists as a global → both facts reported
        res = self.reg.execute("api_lookup", {"class_name": "Math", "member": "lerp"}, self.ctx)
        self.assertTrue(res.is_error)
        self.assertIn("@GlobalScope.lerp(", res.content)

    def test_search(self):
        res = self.reg.execute("api_search", {"query": "timer timeout"}, self.ctx)
        self.assertTrue(res.ok)
        self.assertTrue(res.content.startswith("Timer.timeout("), res.content)
        res = self.reg.execute("api_search", {"query": "zzzz"}, self.ctx)
        self.assertTrue(res.ok)
        self.assertIn("no matches", res.content)
        res = self.reg.execute("api_search", {"query": "node", "limit": 2}, self.ctx)
        self.assertEqual(len(res.content.splitlines()), 2)

    def test_lint_file_and_project(self):
        ws = self.ctx.workspace
        (ws / "scripts").mkdir()
        (ws / "scripts" / "enemy.gd").write_text("class_name Enemy\nextends Area2D\n", encoding="utf-8")
        (ws / "scripts" / "main.gd").write_text("extends Node2D\nvar e: Enemy\nfunc _ready():\n\tvar s = load(\"x\").instance()\n", encoding="utf-8")
        (ws / "scripts" / "ok.gd").write_text("extends Node\nvar arr: Array = []\nfunc f() -> void:\n\tif arr.empty():\n\t\tpass\n", encoding="utf-8")
        res = self.reg.execute("api_lint", {"path": "scripts/main.gd"}, self.ctx)
        self.assertTrue(res.is_error, "errors → is_error so the model must react")
        self.assertIn("scripts/main.gd:4: [error]", res.content)
        self.assertNotIn("Enemy", res.content, "project-wide class_name is known when linting a single file")
        res = self.reg.execute("api_lint", {"path": "scripts/ok.gd"}, self.ctx)
        self.assertTrue(res.ok, "warnings alone do not fail the tool")
        self.assertIn("[warning]", res.content)
        res = self.reg.execute("api_lint", {"path": "scripts/enemy.gd"}, self.ctx)
        self.assertEqual(res.content, "api_lint: no findings")
        res = self.reg.execute("api_lint", {}, self.ctx)
        self.assertTrue(res.is_error)
        self.assertIn("scripts/main.gd:4", res.content)
        self.assertIn("scripts/ok.gd:4", res.content)
        res = self.reg.execute("api_lint", {"path": "scripts/missing.gd"}, self.ctx)
        self.assertTrue(res.is_error)
        res = self.reg.execute("api_lint", {"path": "../../etc/passwd"}, self.ctx)
        self.assertTrue(res.is_error, "sandbox escape is refused")

    def test_index_unavailable_degrades_gracefully(self):
        ctx = make_ctx()
        try:
            with mock.patch.dict(os.environ, {"GODOT_BIN": str(ctx.workspace / "no-godot"), "PATH": str(ctx.workspace),
                                              "GODOTAI_APIREF_DIR": str(ctx.workspace / "no-cache")}):
                res = self.reg.execute("api_lookup", {"class_name": "Node"}, ctx)
                self.assertTrue(res.is_error)
                self.assertIn("API index unavailable", res.content)
                self.assertIn("knowledge_search", res.content)
                self.assertIn("apiref_error", ctx.extra)
                (ctx.workspace / "a.gd").write_text("extends Node\nvar x: Nope\nvar y = rand_range(1, 2)\n", encoding="utf-8")
                res = self.reg.execute("api_lint", {"path": "a.gd"}, ctx)
                self.assertTrue(res.is_error)
                self.assertIn("randf_range", res.content)
                self.assertNotIn("Nope", res.content, "unknown-class check needs the index")
                (ctx.workspace / "a.gd").write_text("extends Node\n", encoding="utf-8")
                res = self.reg.execute("api_lint", {"path": "a.gd"}, ctx)
                self.assertIn("index unavailable", res.content)
        finally:
            shutil.rmtree(ctx.workspace, ignore_errors=True)

    def test_get_index_uses_cache_then_ctx(self):
        ctx = make_ctx()
        try:
            with mock.patch.dict(os.environ, {"GODOTAI_APIREF_DIR": str(ctx.workspace / "cache")}):
                from godotai import apiref
                self.idx.save(apiref.index_path(ctx.cfg.engine))
                idx = apiref_tools.get_index(ctx)
                self.assertIsNotNone(idx)
                self.assertIs(apiref_tools.get_index(ctx), idx, "second call is served from ctx.extra")
        finally:
            shutil.rmtree(ctx.workspace, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
