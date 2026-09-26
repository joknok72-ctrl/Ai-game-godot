"""Tool registry policy (plan gate, step_id) and the sandboxed filesystem tools."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _helpers import repo_config, valid_plan

from godotai.planner import Plan
from godotai.tools import Phase, Tool, ToolContext, ToolRegistry, ToolResult, build_registry
from godotai.tools import fs as fs_tools
from godotai.tools.fs import PathEscape, is_secret_name, safe_path
from godotai.tools.godot_tools import SHELL_ALLOWLIST, scrubbed_env


def make_ctx(phase: Phase = Phase.PLAN, with_plan: bool = False) -> ToolContext:
    cfg = repo_config()
    ws = Path(tempfile.mkdtemp(prefix="godotai-ws-"))
    ctx = ToolContext(cfg=cfg, workspace=ws, phase=phase, log=lambda s: None)
    if with_plan:
        ctx.plan = Plan.from_dict(valid_plan(cfg))
    return ctx


class RegistryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        fs_tools.register(self.reg)

    def test_full_registry_has_expected_tools(self):
        names = set(build_registry().names())
        for n in ("submit_plan", "list_files", "read_file", "write_file", "edit_file", "delete_file", "godot_info",
                  "godot_verify", "godot_check_script", "godot_run_headless", "godot_export", "run_command",
                  "knowledge_search", "knowledge_read", "github_create_repo", "github_push_project",
                  "github_build_apk", "github_build_status"):
            self.assertIn(n, names)
        self.assertNotIn("github_create_repo", build_registry(include_github=False).names())

    def test_mutating_definitions_require_step_id(self):
        defs = {d["name"]: d for d in build_registry().definitions()}
        for name in ("write_file", "edit_file", "delete_file", "godot_export", "run_command", "github_push_project"):
            self.assertIn("step_id", defs[name]["input_schema"]["properties"], name)
            self.assertIn("step_id", defs[name]["input_schema"]["required"], name)
        for name in ("read_file", "list_files", "submit_plan", "godot_verify", "knowledge_search"):
            self.assertNotIn("step_id", defs[name]["input_schema"]["properties"], name)

    def test_mutation_blocked_before_plan_approval(self):
        for phase in (Phase.PLAN, Phase.AWAITING_APPROVAL):
            ctx = make_ctx(phase)
            res = self.reg.execute("write_file", {"path": "a.gd", "content": "x", "step_id": "S1"}, ctx)
            self.assertTrue(res.is_error)
            self.assertIn("POLICY", res.content)
            self.assertFalse((ctx.workspace / "a.gd").exists())

    def test_reads_allowed_before_approval(self):
        ctx = make_ctx(Phase.PLAN)
        (ctx.workspace / "project.godot").write_text("config_version=5\n")
        res = self.reg.execute("read_file", {"path": "project.godot"}, ctx)
        self.assertTrue(res.ok)
        self.assertIn("config_version=5", res.content)

    def test_act_requires_known_step_id(self):
        ctx = make_ctx(Phase.ACT, with_plan=True)
        res = self.reg.execute("write_file", {"path": "a.gd", "content": "x"}, ctx)
        self.assertIn("require step_id", res.content)
        res = self.reg.execute("write_file", {"path": "a.gd", "content": "x", "step_id": "S42"}, ctx)
        self.assertIn("not in the approved plan", res.content)
        self.assertIn("S1, S2, S3, S4", res.content)
        res = self.reg.execute("write_file", {"path": "a.gd", "content": "x", "step_id": "S3"}, ctx)
        self.assertTrue(res.ok, res.content)
        self.assertEqual(ctx.touched_steps, {"S3"})

    def test_done_phase_blocks_mutation(self):
        ctx = make_ctx(Phase.DONE, with_plan=True)
        res = self.reg.execute("write_file", {"path": "a.gd", "content": "x", "step_id": "S1"}, ctx)
        self.assertIn("finished", res.content)

    def test_unknown_tool_and_bad_args(self):
        ctx = make_ctx()
        self.assertTrue(self.reg.execute("nope", {}, ctx).is_error)
        self.assertTrue(self.reg.execute("read_file", "not-a-dict", ctx).is_error)  # type: ignore[arg-type]

    def test_handler_exception_is_captured_not_raised(self):
        reg = ToolRegistry()

        def boom(ctx, args):
            raise RuntimeError("kaboom")

        reg.register(Tool("boom", "x", {"type": "object", "properties": {}}, boom))
        res = reg.execute("boom", {}, make_ctx())
        self.assertTrue(res.is_error)
        self.assertIn("RuntimeError: kaboom", res.content)

    def test_long_results_are_truncated(self):
        reg = ToolRegistry()
        reg.register(Tool("big", "x", {"type": "object", "properties": {}}, lambda c, a: ToolResult.success("y" * 50_000),
                          max_result_chars=1000))
        res = reg.execute("big", {}, make_ctx())
        self.assertLess(len(res.content), 1200)
        self.assertIn("truncated", res.content)

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError):
            fs_tools.register(self.reg)


class SafePathTests(unittest.TestCase):
    def test_safe_path(self):
        ws = Path(tempfile.mkdtemp(prefix="godotai-sp-"))
        self.assertEqual(safe_path(ws, "scenes/main.tscn"), (ws / "scenes/main.tscn").resolve())
        self.assertEqual(safe_path(ws, "res://scripts/a.gd"), (ws / "scripts/a.gd").resolve())
        self.assertEqual(safe_path(ws, "."), ws.resolve())
        for bad in ("../x", "/etc/passwd", "a/../../x", "", "\\x"):
            with self.assertRaises(PathEscape, msg=bad):
                safe_path(ws, bad)

    def test_secret_names(self):
        for n in ("release.keystore", "debug.jks", "key.pem", ".env", ".env.local", "cert.p12", "private.key",
                  "kaggle.json", "access_token", ".netrc", "gateway.token", "credentials.json", "service-account-prod.json"):
            self.assertTrue(is_secret_name(n), n)
        for n in ("player.gd", "main.tscn", "export_presets.cfg", "env.gd", "keystore_notes.md", "tokens.gd", "secrets.md"):
            self.assertFalse(is_secret_name(n), n)


class FsToolTests(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        fs_tools.register(self.reg)
        self.ctx = make_ctx(Phase.ACT, with_plan=True)
        self.ws = self.ctx.workspace

    def call(self, name, **args):
        args.setdefault("step_id", "S1")
        return self.reg.execute(name, args, self.ctx)

    def test_write_edit_delete_roundtrip(self):
        res = self.call("write_file", path="scripts/main.gd", content="extends Node\n\nfunc _ready() -> void:\n    pass\n")
        self.assertTrue(res.ok, res.content)
        text = (self.ws / "scripts/main.gd").read_text()
        self.assertIn("\tpass", text, "4-space indentation is converted to tabs for .gd files")
        res = self.call("edit_file", path="scripts/main.gd", old_text="\tpass", new_text="\tprint(\"hi\")")
        self.assertTrue(res.ok, res.content)
        self.assertIn('print("hi")', (self.ws / "scripts/main.gd").read_text())
        res = self.call("edit_file", path="scripts/main.gd", old_text="nope", new_text="x")
        self.assertIn("not found", res.content)
        (self.ws / "scripts/main.gd").write_text("a\na\n")
        res = self.call("edit_file", path="scripts/main.gd", old_text="a", new_text="b")
        self.assertIn("occurs 2 times", res.content)
        res = self.call("delete_file", path="scripts/main.gd")
        self.assertTrue(res.ok)
        self.assertFalse((self.ws / "scripts/main.gd").exists())

    def test_refuses_secrets_and_cache_dir_and_escapes(self):
        self.assertIn("secret", self.call("write_file", path="release.keystore", content="x").content)
        self.assertIn("secret", self.call("write_file", path=".env", content="TOKEN=1").content)
        self.assertIn(".godot", self.call("write_file", path=".godot/imported/x", content="x").content)
        res = self.call("write_file", path="../escape.gd", content="x")
        self.assertTrue(res.is_error)
        self.assertIn("PathEscape", res.content)
        self.assertFalse((self.ws.parent / "escape.gd").exists())
        (self.ws / "debug.keystore").write_bytes(b"\x00")
        self.assertIn("secret", self.reg.execute("read_file", {"path": "debug.keystore"}, self.ctx).content)

    def test_list_and_read(self):
        (self.ws / "scenes").mkdir()
        (self.ws / "scenes/main.tscn").write_text("[gd_scene]\n")
        (self.ws / ".godot").mkdir()
        (self.ws / ".godot/cache").write_text("x")
        (self.ws / "icon.png").write_bytes(b"\x89PNG")
        res = self.reg.execute("list_files", {}, self.ctx)
        self.assertIn("scenes/main.tscn", res.content)
        self.assertNotIn(".godot", res.content)
        res = self.reg.execute("list_files", {"glob": "*.tscn"}, self.ctx)
        self.assertIn("scenes/main.tscn", res.content)
        self.assertNotIn("icon.png", res.content)
        res = self.reg.execute("read_file", {"path": "icon.png"}, self.ctx)
        self.assertIn("binary file", res.content)
        res = self.reg.execute("read_file", {"path": "scenes/main.tscn"}, self.ctx)
        self.assertIn("    1| [gd_scene]", res.content)
        res = self.reg.execute("read_file", {"path": "missing.gd"}, self.ctx)
        self.assertTrue(res.is_error)


class CommandPolicyTests(unittest.TestCase):
    def test_allowlist_has_no_network_tools(self):
        for banned in ("curl", "wget", "ssh", "nc", "bash", "sh", "rm", "sudo", "pip", "pip3"):
            self.assertNotIn(banned, SHELL_ALLOWLIST)
        self.assertIn("godot", SHELL_ALLOWLIST)

    def test_scrubbed_env_drops_secrets(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x", "GITHUB_TOKEN": "ghp_x",
                                          "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD": "p", "HOME_SAFE": "1",
                                          "KAGGLE_API_TOKEN": "k", "KAGGLE_KEY": "k", "CLOUDFLARE_API_TOKEN": "c",
                                          "CF_AIG_TOKEN": "g", "GOOGLE_APPLICATION_CREDENTIALS": "/x.json"}):
            env = scrubbed_env()
        for name in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD", "KAGGLE_API_TOKEN",
                     "KAGGLE_KEY", "CLOUDFLARE_API_TOKEN", "CF_AIG_TOKEN", "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(name, env)
        self.assertIn("HOME_SAFE", env)

    def test_run_command_rejects_unlisted_binary(self):
        reg = build_registry(include_github=False)
        ctx = make_ctx(Phase.ACT, with_plan=True)
        res = reg.execute("run_command", {"command": ["curl", "http://x"], "step_id": "S1"}, ctx)
        self.assertIn("not in the allow-list", res.content)
        res = reg.execute("run_command", {"command": [], "step_id": "S1"}, ctx)
        self.assertTrue(res.is_error)
        (ctx.workspace / "hello.txt").write_text("hello\n")
        res = reg.execute("run_command", {"command": ["cat", "hello.txt"], "step_id": "S1"}, ctx)
        self.assertTrue(res.ok, res.content)
        self.assertIn("hello", res.content)


if __name__ == "__main__":
    unittest.main()
