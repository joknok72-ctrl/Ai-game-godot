"""Engine-verified evaluation tasks.

Why this exists: the only honest way to know whether a change (prompt, effort
level, provider, fine-tuned model) made the agent *better at Godot* is to run the
same tasks and let the real engine judge. Every task here is scored by

1. the full verification pipeline (``--import``, ``--check-only``, smoke test) — mandatory;
2. cheap structural checks declared in the task file (files that must exist,
   patterns that must appear, no Godot-3 idioms, typed GDScript ratio …).

Tasks live in ``evals/tasks/*.json``; results are written to ``evals/results/``
(git-ignored) as JSON so runs on different effort levels / models can be compared.
The same scorer is the acceptance filter for training data (:mod:`godotai.dataset`).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .godot import Godot, GodotNotFound, GodotVersionMismatch
from .verify import gd_scripts, verify_project

TASKS_DIRNAME = Path("evals") / "tasks"
RESULTS_DIRNAME = Path("evals") / "results"

_FUNC_RE = re.compile(r"^\s*(?:static\s+)?func\s+\w+\s*\([^)]*\)\s*(->\s*[\w\[\], .]+)?\s*:", re.M)
_TYPED_VAR_RE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*var\s+\w+\s*(:\s*[\w\[\], .]+|:=)", re.M)
_VAR_RE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*var\s+\w+", re.M)


class EvalError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvalTask:
    id: str
    title: str
    prompt: str
    tags: tuple[str, ...] = ()
    checks: tuple[dict[str, Any], ...] = ()
    template: str | None = None          # scaffold this template into the workspace before running

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvalTask":
        for k in ("id", "title", "prompt"):
            if not d.get(k):
                raise EvalError(f"task is missing {k!r}")
        return cls(d["id"], d["title"], d["prompt"], tuple(d.get("tags", [])),
                   tuple(d.get("checks", [])), d.get("template"))


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalResult:
    task_id: str
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    verification: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    seconds: float = 0.0
    engine: str = ""
    created: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [f"# Eval {self.task_id} — {'PASS' if self.passed else 'FAIL'}  (Godot {self.engine}, {self.seconds:.0f}s)", ""]
        for c in self.checks:
            lines.append(f"- {'✅' if c.passed else '❌'} {c.name}" + (f": {c.detail}" if c.detail else ""))
        if self.agent:
            lines.append("")
            lines.append(f"agent: status={self.agent.get('status')} iterations={self.agent.get('iterations')} "
                         f"usage={self.agent.get('usage')}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
def load_tasks(root: Path) -> list[EvalTask]:
    d = Path(root) / TASKS_DIRNAME
    tasks: list[EvalTask] = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        tasks.append(EvalTask.from_dict(json.loads(p.read_text(encoding="utf-8"))))
    return tasks


def get_task(root: Path, task_id: str) -> EvalTask | None:
    return next((t for t in load_tasks(root) if t.id == task_id), None)


# ---------------------------------------------------------------------------
def _scripts(project: Path) -> list[tuple[str, str]]:
    return [(p.relative_to(project).as_posix(), p.read_text(encoding="utf-8", errors="replace"))
            for p in gd_scripts(project)]


def run_check(check: dict[str, Any], project: Path, index=None) -> CheckResult:
    kind = check.get("type")
    if kind == "file_exists":
        p = project / check["path"]
        return CheckResult(f"file exists {check['path']}", p.is_file())
    if kind == "file_glob_min":
        n = len([p for p in project.rglob(check["glob"]) if not any(x.startswith(".") for x in p.relative_to(project).parts)])
        return CheckResult(f"at least {check['min']} × {check['glob']}", n >= int(check["min"]), f"found {n}")
    if kind == "file_glob_max":
        n = len([p for p in project.rglob(check["glob"]) if not any(x.startswith(".") for x in p.relative_to(project).parts)])
        return CheckResult(f"at most {check['max']} × {check['glob']}", n <= int(check["max"]), f"found {n}")
    if kind == "content_regex":
        p = project / check["path"]
        ok = p.is_file() and re.search(check["pattern"], p.read_text(encoding="utf-8", errors="replace"), re.M) is not None
        return CheckResult(f"{check['path']} matches /{check['pattern']}/", bool(ok))
    if kind == "any_script_regex":
        pat = re.compile(check["pattern"], re.M)
        hits = [rel for rel, text in _scripts(project) if pat.search(text)]
        return CheckResult(f"some script matches /{check['pattern']}/", bool(hits), ", ".join(hits[:4]))
    if kind == "no_script_regex":
        pat = re.compile(check["pattern"], re.M)
        hits = [rel for rel, text in _scripts(project) if pat.search(text)]
        return CheckResult(f"no script matches /{check['pattern']}/", not hits, ", ".join(hits[:4]))
    if kind == "typed_gdscript":
        funcs = typed = vars_ = typed_vars = 0
        for _, text in _scripts(project):
            fm = _FUNC_RE.findall(text)
            funcs += len(fm)
            typed += sum(1 for r in fm if r)
            vars_ += len(_VAR_RE.findall(text))
            typed_vars += len(_TYPED_VAR_RE.findall(text))
        ratio_f = typed / funcs if funcs else 1.0
        ratio_v = typed_vars / vars_ if vars_ else 1.0
        need = float(check.get("min_ratio", 0.8))
        return CheckResult(f"static typing ≥ {need:.0%}", ratio_f >= need and ratio_v >= need,
                           f"functions {typed}/{funcs}, vars {typed_vars}/{vars_}")
    if kind == "no_api_lint_errors":
        from . import apiref
        findings = apiref.lint_project(project, index)
        errs = [f"{rel}:{f.line} {f.message}" for rel, fs in findings.items() for f in fs if f.severity == "error"]
        return CheckResult("no Godot-3 idioms / invalid API (api_lint)", not errs, "; ".join(errs[:3]))
    if kind == "smoke_test":
        return CheckResult("tests/smoke_test.gd present", (project / "tests" / "smoke_test.gd").is_file())
    if kind == "min_lines":
        n = sum(text.count("\n") for _, text in _scripts(project))
        return CheckResult(f"≥ {check['min']} lines of GDScript", n >= int(check["min"]), f"{n} lines")
    return CheckResult(f"unknown check {kind!r}", False, "task file error")


def score_project(task: EvalTask, project: Path, cfg: Config, godot: Godot | None = None,
                  agent_summary: dict[str, Any] | None = None, seconds: float = 0.0) -> EvalResult:
    project = Path(project).resolve()
    started = time.time()
    result = EvalResult(task.id, False, engine=cfg.engine.tag,
                        created=datetime.now(timezone.utc).isoformat(timespec="seconds"), agent=agent_summary)
    try:
        godot = godot or Godot(cfg.engine)
    except (GodotNotFound, GodotVersionMismatch) as exc:
        result.checks.append(CheckResult("engine verification", False, str(exc)))
        result.seconds = seconds + time.time() - started
        return result
    report = verify_project(project, cfg, godot, run_lint=True)
    result.verification = report.to_dict()
    result.checks.append(CheckResult("engine verification (import, check-only, smoke test)", report.passed,
                                     "; ".join(s.name for s in report.steps if not s.passed) or "all steps passed"))
    index = None
    try:
        from . import apiref
        index = apiref.build_index(godot)
    except Exception:  # advisory only
        index = None
    for check in task.checks:
        result.checks.append(run_check(check, project, index))
    result.passed = all(c.passed for c in result.checks)
    result.seconds = seconds + time.time() - started
    return result


def run_task(task: EvalTask, agent, cfg: Config, results_dir: Path | None = None) -> EvalResult:
    """Run the agent on *task* inside ``agent.workspace`` and score the outcome."""
    if task.template and not (agent.workspace / "project.godot").is_file():
        from .scaffold import create_project
        create_project(cfg, task.template, agent.workspace, task.title, "com.example." + re.sub(r"\W", "", task.id))
    started = time.time()
    summary = agent.run(task.prompt)
    seconds = time.time() - started
    agent_summary = {"status": summary.status, "message": summary.message, "iterations": summary.iterations,
                     "usage": summary.usage, "log": str(summary.log_path), "model": agent.provider.model,
                     "provider": agent.provider.name, "effort": cfg.agent.effort}
    result = score_project(task, agent.workspace, cfg, agent_summary=agent_summary, seconds=seconds)
    save_result(result, results_dir or (cfg.root / RESULTS_DIRNAME))
    return result


def save_result(result: EvalResult, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = results_dir / f"{result.task_id}-{stamp}.json"
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Comparing models — the only place a "better than" may come from
# ---------------------------------------------------------------------------
@dataclass
class TaskScore:
    task_id: str
    attempts: int
    passes: int

    @property
    def rate(self) -> float:
        return self.passes / self.attempts if self.attempts else 0.0


@dataclass
class Comparison:
    candidate: str
    reference: str
    candidate_scores: dict[str, TaskScore] = field(default_factory=dict)
    reference_scores: dict[str, TaskScore] = field(default_factory=dict)
    models_seen: list[str] = field(default_factory=list)
    results_dir: str = ""

    @property
    def common_tasks(self) -> list[str]:
        return sorted(set(self.candidate_scores) & set(self.reference_scores))

    def _tally(self) -> tuple[int, int, int]:
        ahead = behind = tied = 0
        for t in self.common_tasks:
            c, r = self.candidate_scores[t].rate, self.reference_scores[t].rate
            if c > r:
                ahead += 1
            elif c < r:
                behind += 1
            else:
                tied += 1
        return ahead, behind, tied

    def verdict(self) -> str:
        """One honest sentence. Without common, engine-scored tasks there is *no* verdict at all."""
        common = self.common_tasks
        if not common:
            missing = []
            if not self.candidate_scores:
                missing.append(f"no results for candidate {self.candidate!r}")
            if not self.reference_scores:
                missing.append(f"no results for reference {self.reference!r}")
            if not missing:
                missing.append("the two models were run on different tasks")
            return ("NO EVIDENCE — " + "; ".join(missing) + ". No claim about which model is better can be made "
                    "until both have `godotai eval run` results on the same tasks.")
        ahead, behind, tied = self._tally()
        c_rate = sum(s.rate for t, s in self.candidate_scores.items() if t in common) / len(common)
        r_rate = sum(s.rate for t, s in self.reference_scores.items() if t in common) / len(common)
        head = (f"{self.candidate} vs {self.reference} on {len(common)} common task(s): mean pass rate "
                f"{c_rate:.0%} vs {r_rate:.0%}; ahead on {ahead}, behind on {behind}, tied on {tied}.")
        if len(common) < 3:
            head += " Too few tasks for a general claim — this is evidence about these tasks only."
        return head

    def to_markdown(self) -> str:
        lines = [f"# Model comparison — candidate `{self.candidate}` vs reference `{self.reference}`",
                 f"results: {self.results_dir}  ·  models seen: {', '.join(self.models_seen) or 'none'}", "",
                 "| task | candidate pass/attempts | reference pass/attempts |", "| --- | --- | --- |"]
        for t in sorted(set(self.candidate_scores) | set(self.reference_scores)):
            c = self.candidate_scores.get(t)
            r = self.reference_scores.get(t)
            lines.append(f"| {t} | {f'{c.passes}/{c.attempts}' if c else '—'} | {f'{r.passes}/{r.attempts}' if r else '—'} |")
        lines += ["", "**Verdict:** " + self.verdict(), "",
                  "_Scored by the pinned Godot engine + the task's structural checks; no model judged another model. "
                  "Only common tasks count; a model that trains well but fails `godot_verify` is worse, not better._"]
        return "\n".join(lines)


def load_results(results_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    d = Path(results_dir)
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("task_id"):
            out.append(data)
    return out


def compare_models(results_dir: Path, candidate: str, reference: str) -> Comparison:
    """Tally engine-scored results per model per task; the result's ``agent.model`` identifies the model."""
    cmp = Comparison(candidate=candidate, reference=reference, results_dir=str(results_dir))
    seen: set[str] = set()
    for r in load_results(results_dir):
        agent = r.get("agent") or {}
        model = str(agent.get("model") or "")
        if not model:
            continue                                   # score-only results (no model ran) prove nothing about a model
        seen.add(model)
        bucket = cmp.candidate_scores if model == candidate else cmp.reference_scores if model == reference else None
        if bucket is None:
            continue
        ts = bucket.setdefault(r["task_id"], TaskScore(r["task_id"], 0, 0))
        ts.attempts += 1
        ts.passes += 1 if r.get("passed") else 0
    cmp.models_seen = sorted(seen)
    return cmp
