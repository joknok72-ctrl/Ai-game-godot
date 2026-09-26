"""Verification pipeline — the part that makes the agent trustworthy.

The model is never allowed to declare success by itself. After it edits a
project, this pipeline runs the *real* engine headless and turns the raw output
into a structured report the model has to react to:

1. version guard      — the binary must be the pinned Godot version
2. project sanity     — project.godot exists and declares a compatible feature tag
3. --import           — every resource imports without errors
4. --check-only       — every .gd file parses (static analysis by the engine)
5. smoke test         — tests/smoke_test.gd (SceneTree script) or --quit-after N frames
6. gdlint (optional)  — style/lint if gdtoolkit is installed
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .godot import Godot, RunResult

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTINUATION_RE = re.compile(r"^\s*at:\s", re.I)  # second line of every Godot error/warning block

# Godot prefixes for problems printed on stderr/stdout.
_ERROR_PATTERNS = (
    re.compile(r"^\s*(SCRIPT ERROR|USER SCRIPT ERROR|ERROR|USER ERROR|Parse Error|Parser Error)\b", re.I),
    re.compile(r"Failed to load script", re.I),
    re.compile(r"Could not (load|open|find|create|preload)", re.I),
    re.compile(r"Invalid (call|get index|set index|operands|type|argument)", re.I),
    re.compile(r"Nonexistent function", re.I),
    re.compile(r"Identifier .* not declared", re.I),
)
_WARNING_PATTERNS = (
    re.compile(r"^\s*(WARNING|USER WARNING|SCRIPT WARNING)\b", re.I),
)
# Lines that look like errors but are known-harmless in headless CI.
_IGNORE_PATTERNS = (
    re.compile(r"XDG_RUNTIME_DIR", re.I),
    re.compile(r"Started the engine as `root`", re.I),
    re.compile(r"GODOT_SILENCE_ROOT_WARNING", re.I),
    re.compile(r"Leaked instance", re.I),  # reported by --quit-after teardown, not a game bug
    re.compile(r"ObjectDB instances leaked at exit", re.I),
    re.compile(r"RIDs? (allocations )?of type .* were leaked", re.I),  # RID leaks at forced quit
    # Android export plugin chatter when an SDK is configured (seen with 4.7.2 + build-tools 35.0.1):
    re.compile(r"Could not find version of build tools that matches Target SDK, using", re.I),
    re.compile(r"cannot connect to daemon at tcp:5037", re.I),  # adb shutdown on exit, no daemon running
    re.compile(r"Orphan StringName", re.I),
    re.compile(r"StringName: \d+ unclaimed", re.I),
    re.compile(r"MemoryPool", re.I),
    re.compile(r"Vulkan|OpenGL|GLES|rendering device|display driver|Dummy", re.I),
)


def classify_output(text: str) -> tuple[list[str], list[str]]:
    """Split engine output into (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    last: str | None = None  # kind of the previous classified line
    for raw in _ANSI_RE.sub("", text).splitlines():
        line = raw.rstrip()
        if not line.strip():
            last = None
            continue
        if _CONTINUATION_RE.match(line):
            if last == "error":
                errors.append(line.strip())
            continue
        if any(p.search(line) for p in _IGNORE_PATTERNS):
            last = "ignored"
            continue
        if any(p.search(line) for p in _WARNING_PATTERNS):
            warnings.append(line.strip())
            last = "warning"
        elif any(p.search(line) for p in _ERROR_PATTERNS):
            errors.append(line.strip())
            last = "error"
        else:
            last = None
    return errors, warnings


@dataclass
class StepResult:
    name: str
    passed: bool
    summary: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output_tail: str = ""


@dataclass
class VerificationReport:
    project: Path
    steps: list[StepResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.steps) and bool(self.steps)

    def add(self, step: StepResult) -> StepResult:
        self.steps.append(step)
        return step

    def to_markdown(self, max_lines: int = 40) -> str:
        lines = [f"# Verification report — {self.project}", "",
                 f"**Result: {'PASS' if self.passed else 'FAIL'}**", ""]
        for s in self.steps:
            lines.append(f"## {'✅' if s.passed else '❌'} {s.name}")
            lines.append(s.summary)
            if s.errors:
                lines.append("")
                lines.append("Errors:")
                lines.extend(f"- {e}" for e in s.errors[:max_lines])
                if len(s.errors) > max_lines:
                    lines.append(f"- … {len(s.errors) - max_lines} more")
            if s.warnings:
                lines.append("")
                lines.append(f"Warnings ({len(s.warnings)}):")
                lines.extend(f"- {w}" for w in s.warnings[:10])
            if not s.passed and s.output_tail:
                lines.append("")
                lines.append("Output tail:")
                lines.append("```")
                lines.append(s.output_tail)
                lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "project": str(self.project),
            "passed": self.passed,
            "steps": [s.__dict__ for s in self.steps],
        }


def _tail(text: str, n: int = 30) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


def _step_from_run(name: str, res: RunResult, ok_summary: str, allow_nonzero: bool = False) -> StepResult:
    errors, warnings = classify_output(res.output)
    passed = (res.ok or allow_nonzero) and not errors and not res.timed_out
    if res.timed_out:
        summary = "timed out"
    elif passed:
        summary = ok_summary
    else:
        summary = f"exit code {res.returncode}, {len(errors)} error line(s)"
    return StepResult(name, passed, summary, errors, warnings, _tail(res.output))


def gd_scripts(project: Path) -> list[Path]:
    """All GDScript files in the project, skipping addons and hidden dirs."""
    out: list[Path] = []
    for p in sorted(project.rglob("*.gd")):
        rel = p.relative_to(project)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if rel.parts and rel.parts[0] == "addons":
            continue
        out.append(p)
    return out


def res_path(project: Path, file: Path) -> str:
    return "res://" + file.relative_to(project).as_posix()


def check_project_file(project: Path, cfg: Config) -> StepResult:
    pg = project / "project.godot"
    if not pg.is_file():
        return StepResult("project.godot", False, f"missing {pg}")
    text = pg.read_text(encoding="utf-8", errors="replace")
    problems = []
    if "config_version=5" not in text:
        problems.append("project.godot must declare config_version=5 (Godot 4 format)")
    m = re.search(r'config/features=PackedStringArray\(([^)]*)\)', text)
    if not m:
        problems.append("config/features is missing — add PackedStringArray(\"%s\")" % cfg.engine.major_minor)
    elif f'"{cfg.engine.major_minor}"' not in m.group(1):
        problems.append(
            f"config/features declares {m.group(1)} but the pinned engine is {cfg.engine.major_minor}"
        )
    if "run/main_scene=" not in text:
        problems.append("application/run/main_scene is not set")
    return StepResult("project.godot", not problems, "ok" if not problems else "; ".join(problems), problems)


def verify_project(project: Path, cfg: Config, godot: Godot | None = None,
                   frames: int | None = None, run_lint: bool = True) -> VerificationReport:
    project = Path(project).resolve()
    report = VerificationReport(project)
    frames = frames or cfg.agent.verify_frames

    # 1. version guard -----------------------------------------------------
    try:
        godot = godot or Godot(cfg.engine)
        report.add(StepResult("engine version", True, f"{godot.version()} at {godot.binary}"))
    except Exception as exc:  # GodotNotFound / GodotVersionMismatch
        report.add(StepResult("engine version", False, str(exc)))
        return report

    # 2. project.godot -----------------------------------------------------
    pf = report.add(check_project_file(project, cfg))
    if not pf.passed:
        return report

    # 3. import ------------------------------------------------------------
    res = godot.import_project(project)
    report.add(_step_from_run("import (--import)", res, "all resources imported"))

    # 4. per-script parse check -------------------------------------------
    scripts = gd_scripts(project)
    failed: list[str] = []
    all_warnings: list[str] = []
    for script in scripts:
        rp = res_path(project, script)
        r = godot.check_script(project, rp)
        errs, warns = classify_output(r.output)
        all_warnings.extend(f"{rp}: {w}" for w in warns)
        if r.returncode != 0 or errs:
            failed.append(f"{rp}: " + (errs[0] if errs else f"exit {r.returncode}") )
            failed.extend(f"    {e}" for e in errs[1:6])
    report.add(StepResult(
        "GDScript parse (--check-only)", not failed,
        f"{len(scripts) - len([f for f in failed if not f.startswith('    ')])}/{len(scripts)} scripts parse cleanly",
        failed, all_warnings,
    ))

    # 5. smoke test / headless run ----------------------------------------
    smoke = project / "tests" / "smoke_test.gd"
    if smoke.is_file():
        r = godot.run_script(project, "res://tests/smoke_test.gd", timeout=300)
        step = _step_from_run("smoke test (tests/smoke_test.gd)", r, "smoke test passed")
        if "SMOKE_TEST_OK" not in r.output:
            step.passed = False
            step.summary = "smoke test did not print SMOKE_TEST_OK"
        report.add(step)
    else:
        r = godot.run_headless(project, frames=frames)
        report.add(_step_from_run(f"headless run ({frames} frames)", r, "ran without script errors"))

    # 6. optional lint -----------------------------------------------------
    if run_lint and scripts and shutil.which("gdlint"):
        try:
            proc = subprocess.run(["gdlint", *[str(s) for s in scripts]], capture_output=True, text=True,
                                  timeout=300, check=False)
            problems = [l for l in proc.stdout.splitlines() + proc.stderr.splitlines() if l.strip()]
            # lint is advisory: report but never block
            report.add(StepResult("gdlint (advisory)", True,
                                  "clean" if proc.returncode == 0 else f"{len(problems)} finding(s)",
                                  warnings=problems[:40]))
        except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
            report.add(StepResult("gdlint (advisory)", True, f"skipped: {exc}"))
    return report
