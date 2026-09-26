"""Tool registry + execution policy.

Design constraints (see docs/RESEARCH.md):

* The **tool list never changes** during a conversation. Claude Fable 5.1 binds
  its thinking blocks to the ``system``/``tools`` prefix, so swapping tool sets
  between phases would invalidate them. Instead, *policy* is enforced here: a
  mutating tool called before the plan is approved returns an error result.
* Every mutating call in the ACT phase must reference a ``step_id`` from the
  approved plan.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..config import Config
from ..planner import Plan


class Phase(str, Enum):
    PLAN = "plan"
    AWAITING_APPROVAL = "awaiting_approval"
    ACT = "act"
    DONE = "done"


@dataclass
class ToolContext:
    cfg: Config
    workspace: Path
    phase: Phase = Phase.PLAN
    plan: Plan | None = None
    log: Callable[[str], None] = print
    # written by submit_plan so the agent loop can observe it
    pending_plan: dict[str, Any] | None = None
    pending_plan_problems: list[str] = field(default_factory=list)
    touched_steps: set[str] = field(default_factory=set)
    last_verification_passed: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    content: str
    is_error: bool = False

    @staticmethod
    def error(msg: str) -> "ToolResult":
        return ToolResult(False, msg, True)

    @staticmethod
    def success(msg: str) -> "ToolResult":
        return ToolResult(True, msg, False)


Handler = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Handler
    mutating: bool = False       # blocked until the plan is approved; needs step_id in ACT
    max_result_chars: int = 30_000

    def definition(self) -> dict[str, Any]:
        schema = dict(self.schema)
        if self.mutating:
            props = dict(schema.get("properties", {}))
            props.setdefault("step_id", {
                "type": "string",
                "description": "Id of the approved plan step this call implements (e.g. S3). Required.",
            })
            schema["properties"] = props
            req = list(schema.get("required", []))
            if "step_id" not in req:
                req.append("step_id")
            schema["required"] = req
        return {"name": self.name, "description": self.description, "input_schema": schema}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def add(self, name: str, description: str, schema: dict[str, Any], mutating: bool = False):
        def deco(fn: Handler) -> Handler:
            self.register(Tool(name, description, schema, fn, mutating))
            return fn
        return deco

    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self._tools.values()]

    # ------------------------------------------------------------------
    def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult.error(f"unknown tool {name!r}")
        if not isinstance(args, dict):
            return ToolResult.error("tool arguments must be a JSON object")

        policy = self._check_policy(tool, args, ctx)
        if policy is not None:
            return policy
        try:
            result = tool.handler(ctx, args)
        except Exception as exc:  # never crash the loop on a tool bug
            tb = traceback.format_exc(limit=3)
            result = ToolResult.error(f"{type(exc).__name__}: {exc}\n{tb}")
        if len(result.content) > tool.max_result_chars:
            result.content = (result.content[: tool.max_result_chars]
                              + f"\n… [truncated, {len(result.content) - tool.max_result_chars} more chars]")
        if tool.mutating and result.ok and "step_id" in args:
            ctx.touched_steps.add(str(args["step_id"]))
        return result

    @staticmethod
    def _check_policy(tool: Tool, args: dict[str, Any], ctx: ToolContext) -> ToolResult | None:
        if not tool.mutating:
            return None
        if ctx.phase in (Phase.PLAN, Phase.AWAITING_APPROVAL):
            return ToolResult.error(
                "POLICY: no file/system changes are allowed before a plan is approved. "
                "Think first, then call submit_plan; changes are enabled only after approval."
            )
        if ctx.phase == Phase.DONE:
            return ToolResult.error("POLICY: the run is finished; no further changes are allowed.")
        step_id = str(args.get("step_id", "")).strip()
        if not step_id:
            return ToolResult.error("POLICY: mutating tools require step_id referencing an approved plan step.")
        if ctx.plan is None or ctx.plan.get_step(step_id) is None:
            known = ", ".join(sorted(ctx.plan.step_ids())) if ctx.plan else "(none)"
            return ToolResult.error(
                f"POLICY: step_id {step_id!r} is not in the approved plan. Known steps: {known}. "
                "If the plan needs to change, call submit_plan with a revised plan first."
            )
        return None


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
