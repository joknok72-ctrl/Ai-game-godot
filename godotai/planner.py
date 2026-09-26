"""Plan-before-act: schema, validation and rendering of the mandatory plan.

The model cannot touch the filesystem until it has submitted a plan that passes
:func:`validate_plan`. During execution every mutating tool call must name the
plan step it implements, which keeps the agent honest about following its own
plan (see docs/RESEARCH.md, "From Plan to Action").
"""
from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config

STEP_KINDS = ("create", "modify", "delete", "command", "verify")
REQUIRED_VERIFICATION = ("godot_import", "check_scripts", "smoke_test")
_STEP_ID_RE = re.compile(r"^S\d{1,3}$")
_FORBIDDEN_PATH_PARTS = {"..", ""}


@dataclass
class PlanStep:
    id: str
    title: str
    kind: str
    paths: list[str] = field(default_factory=list)
    details: str = ""


@dataclass
class Plan:
    goal: str
    godot_version: str
    summary: str
    assumptions: list[str]
    design: dict[str, Any]
    steps: list[PlanStep]
    verification: list[str]
    risks: list[str]
    open_questions: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Plan":
        steps = [PlanStep(**{k: s.get(k, [] if k == "paths" else "") for k in ("id", "title", "kind", "paths", "details")})
                 for s in d.get("steps", [])]
        return cls(
            goal=str(d.get("goal", "")),
            godot_version=str(d.get("godot_version", "")),
            summary=str(d.get("summary", "")),
            assumptions=[str(a) for a in d.get("assumptions", [])],
            design=dict(d.get("design", {})),
            steps=steps,
            verification=[str(v) for v in d.get("verification", [])],
            risks=[str(r) for r in d.get("risks", [])],
            open_questions=[str(q) for q in d.get("open_questions", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "godot_version": self.godot_version,
            "summary": self.summary,
            "assumptions": self.assumptions,
            "design": self.design,
            "steps": [s.__dict__ for s in self.steps],
            "verification": self.verification,
            "risks": self.risks,
            "open_questions": self.open_questions,
            "created_at": self.created_at,
        }

    def step_ids(self) -> set[str]:
        return {s.id for s in self.steps}

    def get_step(self, step_id: str) -> PlanStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def to_markdown(self) -> str:
        out = [f"# Plan — {self.goal}", "", f"_Godot {self.godot_version} · created {self.created_at}_", "",
               "## Summary", self.summary, ""]
        if self.assumptions:
            out += ["## Assumptions", *[f"- {a}" for a in self.assumptions], ""]
        if self.design:
            out += ["## Design"]
            for k, v in self.design.items():
                if isinstance(v, list):
                    out.append(f"- **{k}**:")
                    out += [f"  - {item}" for item in v]
                else:
                    out.append(f"- **{k}**: {v}")
            out.append("")
        out += ["## Steps"]
        for s in self.steps:
            paths = f" — `{'`, `'.join(s.paths)}`" if s.paths else ""
            out.append(f"- **{s.id}** [{s.kind}] {s.title}{paths}")
            if s.details:
                out.append(f"  - {s.details}")
        out += ["", "## Verification", *[f"- {v}" for v in self.verification], ""]
        if self.risks:
            out += ["## Risks", *[f"- {r}" for r in self.risks], ""]
        if self.open_questions:
            out += ["## Open questions", *[f"- {q}" for q in self.open_questions], ""]
        return "\n".join(out)


# JSON schema for the submit_plan tool ------------------------------------
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["goal", "godot_version", "summary", "assumptions", "design", "steps",
                 "verification", "risks", "open_questions"],
    "properties": {
        "goal": {"type": "string", "description": "One sentence: what game/feature is being built."},
        "godot_version": {"type": "string",
                           "description": "Exact engine version string this plan targets, e.g. 4.7.2-stable."},
        "summary": {"type": "string", "description": "Short paragraph describing the approach."},
        "assumptions": {"type": "array", "items": {"type": "string"},
                        "description": "Assumptions made because the request was ambiguous."},
        "design": {
            "type": "object",
            "additionalProperties": False,
            "required": ["genre", "platforms", "scenes", "scripts", "input_scheme", "core_loop"],
            "properties": {
                "genre": {"type": "string"},
                "platforms": {"type": "array", "items": {"type": "string"}},
                "scenes": {"type": "array", "items": {"type": "string"},
                           "description": "Scene files with root node type, e.g. 'scenes/main.tscn (Node2D)'."},
                "scripts": {"type": "array", "items": {"type": "string"},
                            "description": "Scripts and their responsibility."},
                "input_scheme": {"type": "string", "description": "Touch / keyboard / gamepad mapping."},
                "core_loop": {"type": "string", "description": "The moment-to-moment gameplay loop."},
            },
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "title", "kind", "paths", "details"],
                "properties": {
                    "id": {"type": "string", "description": "S1, S2, … sequential."},
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": list(STEP_KINDS)},
                    "paths": {"type": "array", "items": {"type": "string"},
                              "description": "Project-relative paths touched by this step."},
                    "details": {"type": "string"},
                },
            },
        },
        "verification": {"type": "array", "items": {"type": "string"},
                         "description": "Must include godot_import, check_scripts, smoke_test plus any manual checks."},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


def _normalise_version(v: str) -> str:
    return v.strip().lower().replace("_", "-").replace(" ", "")


def is_safe_relative_path(p: str) -> bool:
    if not p or p.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", p):
        return False
    norm = posixpath.normpath(p.replace("\\", "/"))
    parts = norm.split("/")
    return not any(part in _FORBIDDEN_PATH_PARTS for part in parts) and not norm.startswith("../")


def validate_plan(data: dict[str, Any], cfg: Config) -> list[str]:
    """Return a list of human-readable problems; empty list means the plan is valid."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["plan must be a JSON object"]
    plan = Plan.from_dict(data)

    if len(plan.goal.strip()) < 8:
        problems.append("goal is too short")
    if len(plan.summary.strip()) < 20:
        problems.append("summary is too short — explain the approach")

    expected = {_normalise_version(cfg.engine.tag), _normalise_version(cfg.engine.version_string),
                _normalise_version(cfg.engine.version)}
    if _normalise_version(plan.godot_version) not in expected:
        problems.append(
            f"godot_version must be {cfg.engine.tag} (this AI only targets that version), got {plan.godot_version!r}"
        )

    for key in ("scenes", "scripts"):
        if not plan.design.get(key):
            problems.append(f"design.{key} must list at least one entry")
    if not plan.design.get("core_loop"):
        problems.append("design.core_loop is required")

    if not plan.steps:
        problems.append("steps must not be empty")
    seen: set[str] = set()
    for i, s in enumerate(plan.steps, 1):
        if not _STEP_ID_RE.match(s.id):
            problems.append(f"step {i}: id must look like S1, S2 … got {s.id!r}")
        elif s.id != f"S{i}":
            problems.append(f"step {i}: ids must be sequential — expected S{i}, got {s.id}")
        if s.id in seen:
            problems.append(f"duplicate step id {s.id}")
        seen.add(s.id)
        if s.kind not in STEP_KINDS:
            problems.append(f"step {s.id}: kind must be one of {STEP_KINDS}")
        if s.kind in ("create", "modify", "delete") and not s.paths:
            problems.append(f"step {s.id}: file steps must list the paths they touch")
        for p in s.paths:
            if not is_safe_relative_path(p):
                problems.append(f"step {s.id}: path {p!r} must be relative and inside the project")
            if p.endswith((".keystore", ".jks", ".pem", ".env")):
                problems.append(f"step {s.id}: refusing to plan writes to secret material {p!r}")
    if not any(s.kind == "verify" for s in plan.steps):
        problems.append("add a final step of kind 'verify' that runs godot_verify")

    verification_text = " ".join(plan.verification).lower()
    for req in REQUIRED_VERIFICATION:
        if req not in verification_text:
            problems.append(f"verification must include '{req}'")
    return problems


def save_plan(plan: Plan, workspace: Path) -> tuple[Path, Path]:
    """Persist plan.json + PLAN.md under ``<workspace>/.godotai/``."""
    d = Path(workspace) / ".godotai"
    d.mkdir(parents=True, exist_ok=True)
    json_path = d / "plan.json"
    md_path = d / "PLAN.md"
    json_path.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(plan.to_markdown(), encoding="utf-8")
    return json_path, md_path


def load_plan(workspace: Path) -> Plan | None:
    p = Path(workspace) / ".godotai" / "plan.json"
    if not p.is_file():
        return None
    return Plan.from_dict(json.loads(p.read_text(encoding="utf-8")))
