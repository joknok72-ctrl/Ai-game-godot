"""End-to-end agent loop with a scripted (offline) model.

Proves the invariants that make this agent trustworthy without calling any API:
* nothing is written before a validated plan is approved;
* every mutation must name an approved step;
* the run cannot end successfully without a passing verification;
* the system prompt / tool list never change and the transcript is append-only.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from _helpers import repo_config, valid_plan

from godotai.agent import Agent
from godotai.providers.base import ModelTurn, Provider, ToolCall
from godotai.tools import Tool, ToolContext, ToolRegistry, ToolResult
from godotai.tools import fs as fs_tools
from godotai.tools import knowledge, plan_tool


class ScriptedProvider(Provider):
    """Replays a list of turns; records every request for assertions."""
    name = "scripted"

    def __init__(self, turns: list[dict[str, Any]]):
        super().__init__(model="scripted-model")
        self.turns = list(turns)
        self.requests: list[tuple[str, int, list[dict]]] = []
        self.counter = 0

    def build_request(self, system, messages, tools):
        return {"system": system, "messages": messages, "tools": tools}

    def complete(self, system, messages, tools) -> ModelTurn:
        self.requests.append((system, len(messages), tools))
        if not self.turns:
            raise AssertionError("scripted provider ran out of turns")
        spec = self.turns.pop(0)
        calls = []
        for name, args in spec.get("calls", []):
            self.counter += 1
            calls.append(ToolCall(f"call_{self.counter}", name, dict(args)))
        text = spec.get("text", "")
        content = [{"type": "text", "text": text}] if text else []
        content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.args} for c in calls]
        return ModelTurn(text=text, tool_calls=calls, stop_reason=spec.get("stop", "tool_use" if calls else "end_turn"),
                         raw_assistant_message={"role": "assistant", "content": content},
                         usage={"output_tokens": 1}, refusal=spec.get("refusal", False))

    def user_message(self, text):
        return {"role": "user", "content": [{"type": "text", "text": text}]}

    def tool_results_message(self, results):
        return [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": err}
                                             for c, out, err in results]}]


class FakeVerify:
    """Stands in for godot_verify: fails `fail_times` times, then passes."""

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls = 0

    def __call__(self, ctx: ToolContext, args: dict) -> ToolResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            ctx.last_verification_passed = False
            return ToolResult.error("# Verification report\n**Result: FAIL**\n- SCRIPT ERROR: fake")
        ctx.last_verification_passed = True
        return ToolResult.success("# Verification report\n**Result: PASS**")


def make_registry(verify: FakeVerify) -> ToolRegistry:
    reg = ToolRegistry()
    knowledge.register(reg)
    fs_tools.register(reg)
    plan_tool.register(reg)
    reg.register(Tool("godot_verify", "fake", {"type": "object", "properties": {}, "required": []}, verify))
    return reg


def tool_result_texts(messages: list[dict]) -> list[str]:
    out = []
    for m in messages:
        if m["role"] == "user":
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    out.append(b["content"])
    return out


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()
        self.ws = Path(tempfile.mkdtemp(prefix="godotai-agent-"))
        self.plan = valid_plan(self.cfg)

    def run_agent(self, turns, approve=None, plan_only=False, verify=None):
        verify = verify or FakeVerify()
        provider = ScriptedProvider(turns)
        agent = Agent(self.cfg, self.ws, provider, make_registry(verify), approve=approve,
                      log=lambda s: None, on_text=lambda t: None, plan_only=plan_only)
        summary = agent.run("Make a tap-dodge game for Android")
        return agent, provider, summary, verify

    def test_full_cycle_with_gate_rejection_then_success(self):
        bad_plan = dict(self.plan, godot_version="4.3")
        turns = [
            # 1. tries to write before planning → POLICY error, file must not exist
            {"calls": [("write_file", {"path": "scripts/early.gd", "content": "extends Node\n", "step_id": "S1"})]},
            # 2. submits a plan for the wrong engine version → rejected
            {"calls": [("submit_plan", bad_plan)]},
            # 3. valid plan → human rejects with feedback
            {"calls": [("submit_plan", self.plan)]},
            # 4. resubmits → approved → ACT
            {"calls": [("submit_plan", self.plan)]},
            # 5. unknown step id → rejected; correct step id → written
            {"calls": [("write_file", {"path": "scripts/main.gd", "content": "extends Node\n", "step_id": "S9"}),
                       ("write_file", {"path": "scripts/main.gd", "content": "extends Node\n\nfunc _ready() -> void:\n    pass\n",
                                       "step_id": "S3"})]},
            # 6. verifies
            {"calls": [("godot_verify", {})]},
            # 7. final report
            {"text": "Done: main scene + player implemented and verified.", "stop": "end_turn"},
        ]
        decisions = iter([(False, "add a pause button"), (True, "")])
        seen_plans = []

        def approve(plan):
            seen_plans.append(plan.goal)
            return next(decisions)

        agent, provider, summary, verify = self.run_agent(turns, approve=approve)

        self.assertEqual(summary.status, "success", summary.message)
        self.assertTrue(summary.verification_passed)
        self.assertEqual(len(seen_plans), 2)
        self.assertFalse((self.ws / "scripts/early.gd").exists())
        self.assertIn("\tpass", (self.ws / "scripts/main.gd").read_text())
        self.assertEqual(agent.ctx.touched_steps, {"S3"})
        self.assertEqual(verify.calls, 1)

        results = tool_result_texts(agent.messages)
        self.assertIn("POLICY: no file/system changes", results[0])
        self.assertIn("godot_version must be 4.7.2-stable", results[1])
        self.assertIn("waiting for human approval", results[2])
        self.assertIn("not in the approved plan", results[4])
        transcript = json.dumps(agent.messages, ensure_ascii=False)
        self.assertIn("Plan NOT approved. Reviewer feedback: add a pause button", transcript)
        self.assertIn("Plan APPROVED. Phase 2 — ACT", transcript)

        # invariants: constant system prompt + tool list, strictly growing transcript
        systems = {s for s, _, _ in provider.requests}
        self.assertEqual(len(systems), 1)
        self.assertIn("Godot Engine 4.7.2-stable", systems.pop())
        tool_lists = {json.dumps(t, sort_keys=True) for _, _, t in provider.requests}
        self.assertEqual(len(tool_lists), 1)
        lengths = [n for _, n, _ in provider.requests]
        self.assertEqual(lengths, sorted(lengths))
        self.assertTrue(all(b > a for a, b in zip(lengths, lengths[1:])))

        # artefacts
        self.assertTrue((self.ws / ".godotai" / "PLAN.md").is_file())
        self.assertTrue(summary.log_path and summary.log_path.is_file())
        log = json.loads(summary.log_path.read_text(encoding="utf-8"))
        self.assertEqual(log["status"], "success")
        self.assertEqual(log["engine"], "4.7.2-stable")
        self.assertEqual(log["touched_steps"], ["S3"])

    def test_stopping_without_verification_triggers_verify_and_feedback(self):
        turns = [
            {"calls": [("submit_plan", self.plan)]},
            {"calls": [("write_file", {"path": "scripts/a.gd", "content": "extends Node\n", "step_id": "S3"})]},
            {"text": "I think it works.", "stop": "end_turn"},          # → agent runs godot_verify → FAIL → feedback
            {"calls": [("edit_file", {"path": "scripts/a.gd", "old_text": "Node", "new_text": "Node2D", "step_id": "S3"})]},
            {"text": "Fixed.", "stop": "end_turn"},                     # → agent runs godot_verify → PASS
        ]
        agent, provider, summary, verify = self.run_agent(turns, verify=FakeVerify(fail_times=1))
        self.assertEqual(summary.status, "success")
        self.assertEqual(verify.calls, 2)
        transcript = json.dumps(agent.messages)
        self.assertIn("You stopped, but verification is not passing", transcript)
        self.assertIn("extends Node2D", (self.ws / "scripts/a.gd").read_text())

    def test_verification_never_passing_fails_the_run(self):
        turns = [{"calls": [("submit_plan", self.plan)]}] + [{"text": "done?", "stop": "end_turn"}] * 10
        agent, provider, summary, verify = self.run_agent(turns, verify=FakeVerify(fail_times=99))
        self.assertEqual(summary.status, "failed")
        self.assertIs(summary.verification_passed, False)
        self.assertEqual(verify.calls, self.cfg.agent.max_verify_rounds)

    def test_model_that_never_plans_is_aborted(self):
        turns = [{"text": "Here is what I would do…", "stop": "end_turn"}] * 6
        agent, provider, summary, _ = self.run_agent(turns)
        self.assertEqual(summary.status, "aborted")
        self.assertIn("did not submit a plan", summary.message)
        self.assertEqual(len(provider.requests), 4)  # 3 reminders then abort

    def test_refusal_is_reported(self):
        agent, provider, summary, _ = self.run_agent([{"text": "", "stop": "refusal", "refusal": True}])
        self.assertEqual(summary.status, "refused")

    def test_plan_only_mode_stops_after_plan(self):
        approve_called = []
        agent, provider, summary, verify = self.run_agent(
            [{"calls": [("submit_plan", self.plan)]}], approve=lambda p: approve_called.append(1) or (True, ""), plan_only=True)
        self.assertEqual(summary.status, "success")
        self.assertIn("plan-only", summary.message)
        self.assertEqual(approve_called, [])
        self.assertEqual(summary.plan["goal"], self.plan["goal"])
        self.assertEqual(verify.calls, 0)

    def test_max_tokens_continuation(self):
        turns = [
            {"text": "partial…", "stop": "max_tokens"},
            {"calls": [("submit_plan", self.plan)]},
            {"calls": [("godot_verify", {})]},
            {"text": "done", "stop": "end_turn"},
        ]
        agent, provider, summary, _ = self.run_agent(turns)
        self.assertEqual(summary.status, "success")
        self.assertIn("cut off by the output limit", json.dumps(agent.messages))

    def test_planning_prompt_mentions_environment(self):
        provider = ScriptedProvider([{"text": "", "stop": "refusal", "refusal": True}])
        agent = Agent(self.cfg, self.ws, provider, make_registry(FakeVerify()), log=lambda s: None, on_text=lambda t: None)
        agent.run("build a runner")
        first_user = agent.messages[0]["content"][0]["text"]
        self.assertIn("build a runner", first_user)
        self.assertIn("Godot 4.7.2-stable", first_user)
        self.assertIn("no project.godot yet", first_user)
        self.assertIn("godot-4.7-essentials.md", first_user)  # knowledge index is injected
        self.assertIn("THINK & PLAN only", first_user)


if __name__ == "__main__":
    unittest.main()
