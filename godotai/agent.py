"""The agent loop: THINK → PLAN → GATE → ACT → VERIFY → REPORT.

Invariants:
* the ``system`` prompt and the ``tools`` list are computed once and never
  change during a run (append-only conversation);
* nothing is written before a validated plan is approved (enforced by the tool
  registry, not by prompt wording);
* the run only ends successfully after ``godot_verify`` reports PASS.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .godot import Godot, GodotNotFound, GodotVersionMismatch
from .planner import Plan
from .providers import ModelTurn, Provider, ToolCall
from .tools import Phase, ToolContext, ToolRegistry

PROMPTS = Path(__file__).parent / "prompts"
ApproveFn = Callable[[Plan], tuple[bool, str]]


@dataclass
class RunSummary:
    status: str                      # success | failed | aborted | refused
    message: str
    plan: dict[str, Any] | None = None
    verification_passed: bool | None = None
    iterations: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    log_path: Path | None = None


class Agent:
    def __init__(self, cfg: Config, workspace: Path, provider: Provider, registry: ToolRegistry,
                 approve: ApproveFn | None = None, log: Callable[[str], None] = print,
                 on_text: Callable[[str], None] | None = None, plan_only: bool = False):
        self.cfg = cfg
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self.registry = registry
        self.approve = approve or (lambda plan: (True, ""))
        self.log = log
        self.on_text = on_text or (lambda t: log(t))
        self.plan_only = plan_only
        self.ctx = ToolContext(cfg=cfg, workspace=self.workspace, log=log)
        self.system = self._render(PROMPTS / "system.md", {"GODOT_TAG": cfg.engine.tag})
        self.tools = registry.definitions()          # constant for the whole run
        self.messages: list[dict[str, Any]] = []     # append-only
        self.usage: dict[str, int] = {}
        self.iterations = 0

    # ------------------------------------------------------------------
    @staticmethod
    def _render(path: Path, values: dict[str, str]) -> str:
        text = path.read_text(encoding="utf-8")
        for k, v in values.items():
            text = text.replace("{{" + k + "}}", v)
        return text

    def _knowledge_index(self) -> str:
        idx = self.cfg.root / "knowledge" / "INDEX.md"
        if idx.is_file():
            lines = [l for l in idx.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
            return "\n".join(f"  {l}" for l in lines) or "  (empty)"
        return "  (knowledge folder not found)"

    def _godot_status(self) -> str:
        try:
            g = Godot(self.cfg.engine)
            self.ctx.extra["godot"] = g
            return f"binary OK: {g.version()} at {g.binary}; templates {'installed' if g.templates_installed() else 'MISSING'}"
        except (GodotNotFound, GodotVersionMismatch) as exc:
            return f"binary problem: {exc}"

    def _planning_prompt(self, task: str) -> str:
        files = [p for p in self.workspace.rglob("*") if p.is_file() and not any(x.startswith(".") for x in p.relative_to(self.workspace).parts)]
        has_project = (self.workspace / "project.godot").is_file()
        return self._render(PROMPTS / "planning.md", {
            "TASK": task.strip(),
            "WORKSPACE": str(self.workspace),
            "FILE_COUNT": str(len(files)),
            "PROJECT_STATE": "existing Godot project (project.godot found)" if has_project else "empty / no project.godot yet — you will create one",
            "GODOT_TAG": self.cfg.engine.tag,
            "GODOT_BINARY_STATUS": self._godot_status(),
            "KNOWLEDGE_INDEX": self._knowledge_index(),
        })

    # ------------------------------------------------------------------
    def _step(self) -> ModelTurn:
        self.iterations += 1
        turn = self.provider.complete(self.system, self.messages, self.tools)
        for k, v in (turn.usage or {}).items():
            if isinstance(v, int):
                self.usage[k] = self.usage.get(k, 0) + v
        self.messages.append(turn.raw_assistant_message)
        if turn.text:
            self.on_text(turn.text)
        return turn

    def _run_tools(self, calls: list[ToolCall]) -> None:
        results: list[tuple[ToolCall, str, bool]] = []
        for call in calls:
            self.log(f"  → {call.name}({_short_args(call.args)})")
            res = self.registry.execute(call.name, call.args, self.ctx)
            results.append((call, res.content, res.is_error))
        self.messages.extend(self.provider.tool_results_message(results))

    def _say(self, text: str) -> None:
        self.messages.append(self.provider.user_message(text))

    # ------------------------------------------------------------------
    def run(self, task: str) -> RunSummary:
        started = time.time()
        self._say(self._planning_prompt(task))
        summary = self._loop()
        summary.iterations = self.iterations
        summary.usage = dict(self.usage)
        summary.log_path = self._write_log(task, summary, time.time() - started)
        return summary

    def _loop(self) -> RunSummary:
        nudges = 0
        verify_rounds = 0
        while self.iterations < self.cfg.agent.max_iterations:
            turn = self._step()
            if turn.refusal:
                return RunSummary("refused", "the model declined the request (stop_reason=refusal)")
            if turn.stop_reason == "max_tokens":
                self._say("Your previous message was cut off by the output limit. Continue exactly where you stopped.")
                continue
            if turn.wants_tools:
                self._run_tools(turn.tool_calls)
                # --- plan submitted? ------------------------------------
                if self.ctx.phase == Phase.AWAITING_APPROVAL and self.ctx.pending_plan:
                    plan = Plan.from_dict(self.ctx.pending_plan)
                    if self.plan_only:
                        self.ctx.phase = Phase.DONE
                        return RunSummary("success", "plan created (plan-only mode)", plan.to_dict())
                    approved, feedback = self.approve(plan)
                    if approved:
                        self.ctx.plan = plan
                        self.ctx.phase = Phase.ACT
                        self.log("  ✅ plan approved — entering ACT phase")
                        self._say("Plan APPROVED. Phase 2 — ACT. Implement the steps in order; include step_id on "
                                  "every mutating call; run godot_check_script after each script and godot_verify at "
                                  "the end. Report briefly after each step.")
                    else:
                        self.ctx.phase = Phase.PLAN
                        self.ctx.pending_plan = None
                        self._say(f"Plan NOT approved. Reviewer feedback: {feedback or 'revise the plan'}. "
                                  "Revise and call submit_plan again.")
                continue

            # --- model ended its turn with text only ------------------------
            if self.ctx.phase in (Phase.PLAN, Phase.AWAITING_APPROVAL):
                nudges += 1
                if nudges > 3:
                    return RunSummary("aborted", "the model did not submit a plan after several reminders", None)
                if self.ctx.pending_plan_problems:
                    self._say("Your plan was rejected: " + "; ".join(self.ctx.pending_plan_problems) +
                              ". Fix it and call submit_plan.")
                else:
                    self._say("Reminder: nothing can be built until you call submit_plan with a full plan. "
                              "If the task is outside Godot game development, state that clearly.")
                continue

            if self.ctx.phase == Phase.ACT:
                if self.ctx.last_verification_passed:
                    self.ctx.phase = Phase.DONE
                    return RunSummary("success", turn.text or "done", self.ctx.plan.to_dict() if self.ctx.plan else None, True)
                verify_rounds += 1
                if verify_rounds > self.cfg.agent.max_verify_rounds:
                    return RunSummary("failed", "verification never passed within the allowed rounds",
                                      self.ctx.plan.to_dict() if self.ctx.plan else None, False)
                self.log("  ⚙ model stopped without a passing verification — running godot_verify")
                res = self.registry.execute("godot_verify", {}, self.ctx)
                if res.ok:
                    self.ctx.phase = Phase.DONE
                    return RunSummary("success", turn.text or "done", self.ctx.plan.to_dict() if self.ctx.plan else None, True)
                self._say("You stopped, but verification is not passing. Here is the report — fix the root causes "
                          "and run godot_verify again:\n\n" + res.content)
                continue
        return RunSummary("aborted", f"iteration limit ({self.cfg.agent.max_iterations}) reached",
                          self.ctx.plan.to_dict() if self.ctx.plan else None, self.ctx.last_verification_passed)

    # ------------------------------------------------------------------
    def _write_log(self, task: str, summary: RunSummary, seconds: float) -> Path:
        d = self.workspace / ".godotai" / "runs"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = d / f"{stamp}.json"
        path.write_text(json.dumps({
            "task": task, "status": summary.status, "message": summary.message,
            "engine": self.cfg.engine.tag, "model": self.provider.model, "provider": self.provider.name,
            "iterations": self.iterations, "seconds": round(seconds, 1), "usage": self.usage,
            "verification_passed": summary.verification_passed,
            "touched_steps": sorted(self.ctx.touched_steps),
            "messages": self.messages,
        }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path


def _short_args(args: dict[str, Any], limit: int = 90) -> str:
    parts = []
    for k, v in args.items():
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        s = s.replace("\n", "⏎")
        parts.append(f"{k}={s[:40]}{'…' if len(s) > 40 else ''}")
    joined = ", ".join(parts)
    return joined[:limit] + ("…" if len(joined) > limit else "")
