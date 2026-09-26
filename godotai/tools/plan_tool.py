"""The submit_plan tool — the only way out of the PLAN phase."""
from __future__ import annotations

from typing import Any

from ..planner import PLAN_SCHEMA, Plan, save_plan, validate_plan
from .base import Phase, ToolContext, ToolRegistry, ToolResult


def register(reg: ToolRegistry) -> None:
    @reg.add(
        "submit_plan",
        "Submit the implementation plan. REQUIRED before any file is created or modified. The plan is validated "
        "(engine version, safe paths, sequential step ids S1..Sn, mandatory verification) and then shown to the "
        "human for approval. Call it again with a revised plan whenever the plan must change.",
        PLAN_SCHEMA,
    )
    def submit_plan(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        problems = validate_plan(args, ctx.cfg)
        if problems:
            ctx.pending_plan = None
            ctx.pending_plan_problems = problems
            return ToolResult.error("plan REJECTED:\n- " + "\n- ".join(problems) + "\nFix these and call submit_plan again.")
        plan = Plan.from_dict(args)
        ctx.pending_plan = args
        ctx.pending_plan_problems = []
        json_path, md_path = save_plan(plan, ctx.workspace)
        if ctx.phase == Phase.ACT:
            # re-planning mid-execution: accept immediately but keep the audit trail
            ctx.plan = plan
            ctx.log("  📝 plan revised during execution")
            return ToolResult.success(f"plan revised and saved to {md_path.name}; continue with the new step ids.")
        ctx.phase = Phase.AWAITING_APPROVAL
        ctx.log(f"  📝 plan saved to {md_path}")
        return ToolResult.success(
            f"plan accepted and saved ({json_path.name}, {md_path.name}); waiting for human approval. "
            "Do not modify files until you are told the plan is approved."
        )
