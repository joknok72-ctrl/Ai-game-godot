"""Engine tools: verify, export, run — always through the pinned Godot binary."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..godot import Godot, GodotNotFound, GodotVersionMismatch, export_templates_dir
from ..verify import classify_output, verify_project
from .base import ToolContext, ToolRegistry, ToolResult
from .fs import safe_path

ANDROID_KEYSTORE_ENV = (
    "GODOT_ANDROID_KEYSTORE_DEBUG_PATH", "GODOT_ANDROID_KEYSTORE_DEBUG_USER", "GODOT_ANDROID_KEYSTORE_DEBUG_PASSWORD",
    "GODOT_ANDROID_KEYSTORE_RELEASE_PATH", "GODOT_ANDROID_KEYSTORE_RELEASE_USER", "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD",
)
# Commands the model may run through run_command. Anything else is rejected.
SHELL_ALLOWLIST = ("godot", "gdlint", "gdformat", "gdparse", "python3", "ls", "cat", "grep", "find", "wc", "head", "tail",
                   "git", "keytool", "java", "sdkmanager", "adb", "unzip", "zip", "file", "stat", "diff")
SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "KEYSTORE")


def _godot(ctx: ToolContext) -> Godot:
    g = ctx.extra.get("godot")
    if g is None:
        g = Godot(ctx.cfg.engine)
        ctx.extra["godot"] = g
    return g


def scrubbed_env() -> dict[str, str]:
    """Child environment without API keys/tokens (keystore vars are re-added only for export)."""
    return {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in SECRET_ENV_MARKERS)}


def register(reg: ToolRegistry) -> None:
    @reg.add(
        "godot_info",
        "Report the pinned Godot version, whether the local binary matches it, and whether export templates / "
        "Android SDK are available. Call this first when unsure about the environment.",
        {"type": "object", "properties": {}, "required": []},
    )
    def godot_info(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        eng = ctx.cfg.engine
        info: dict[str, Any] = {
            "pinned_version": eng.tag,
            "templates_dir": str(export_templates_dir(eng)),
        }
        try:
            g = _godot(ctx)
            info["binary"] = str(g.binary)
            info["binary_version"] = g.version()
            info["templates_installed"] = g.templates_installed()
        except (GodotNotFound, GodotVersionMismatch) as exc:
            info["binary_error"] = str(exc)
        info["java"] = shutil.which("java") or "not found"
        info["android_sdk"] = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "not set"
        info["gdlint"] = shutil.which("gdlint") or "not installed"
        return ToolResult.success(json.dumps(info, indent=2))

    @reg.add(
        "godot_verify",
        "Run the full verification pipeline on the project with the real engine: project.godot sanity, --import, "
        "--check-only on every .gd, smoke test / headless run, gdlint. Returns a report. You MUST call this after "
        "implementing the plan and fix every error it reports.",
        {"type": "object", "properties": {
            "frames": {"type": "integer", "description": "Frames to run headless when no smoke test exists."},
        }, "required": []},
    )
    def godot_verify(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        try:
            g = _godot(ctx)
        except (GodotNotFound, GodotVersionMismatch) as exc:
            return ToolResult.error(str(exc))
        report = verify_project(ctx.workspace, ctx.cfg, g, frames=args.get("frames"))
        ctx.last_verification_passed = report.passed
        (ctx.workspace / ".godotai").mkdir(exist_ok=True)
        (ctx.workspace / ".godotai" / "last_verification.md").write_text(report.to_markdown(), encoding="utf-8")
        ctx.log(f"  ⚙ verification {'PASSED' if report.passed else 'FAILED'}")
        return ToolResult(report.passed, report.to_markdown(), is_error=not report.passed)

    @reg.add(
        "godot_check_script",
        "Parse a single GDScript with the engine (--check-only) and return errors. Cheap; use while iterating.",
        {"type": "object", "properties": {"path": {"type": "string", "description": "e.g. scripts/player.gd"}},
         "required": ["path"]},
    )
    def godot_check_script(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        p = safe_path(ctx.workspace, args["path"])
        if not p.is_file():
            return ToolResult.error(f"{args['path']!r} not found")
        g = _godot(ctx)
        res = g.check_script(ctx.workspace, "res://" + p.relative_to(ctx.workspace).as_posix())
        errors, warnings = classify_output(res.output)
        if res.returncode != 0 or errors:
            return ToolResult.error("parse FAILED\n" + "\n".join(errors or [res.output[-2000:]]))
        return ToolResult.success("parse OK" + (f"\nwarnings:\n" + "\n".join(warnings) if warnings else ""))

    @reg.add(
        "godot_run_headless",
        "Run the project's main scene headless for N frames and return engine output (script errors etc.).",
        {"type": "object", "properties": {"frames": {"type": "integer", "default": 120}}, "required": []},
    )
    def godot_run_headless(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        g = _godot(ctx)
        res = g.run_headless(ctx.workspace, frames=int(args.get("frames") or 120))
        errors, warnings = classify_output(res.output)
        status = "OK" if res.ok and not errors else "FAILED"
        return ToolResult(status == "OK", f"{status} (exit {res.returncode})\n{res.output[-6000:]}", status != "OK")

    @reg.add(
        "godot_export",
        "Export the project with an export preset from export_presets.cfg (e.g. 'Android' → .apk). Requires export "
        "templates; Android additionally needs JDK 17 + Android SDK configured in the editor settings. Keystore "
        "credentials are read from GODOT_ANDROID_KEYSTORE_* environment variables, never from files you write.",
        {"type": "object", "properties": {
            "preset": {"type": "string", "description": "Preset name, default 'Android'."},
            "output": {"type": "string", "description": "Relative output path, e.g. build/android/game.apk"},
            "release": {"type": "boolean", "description": "Release build (needs release keystore env vars)."},
        }, "required": ["output"]},
        mutating=True,
    )
    def godot_export(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        g = _godot(ctx)
        preset = args.get("preset") or ctx.cfg.android.preset_name
        out = safe_path(ctx.workspace, args["output"])
        if not g.templates_installed():
            return ToolResult.error(
                f"export templates for {ctx.cfg.engine.tag} are not installed at {export_templates_dir(ctx.cfg.engine)}. "
                "Run `python3 -m godotai install-godot` (with templates) first.")
        env = {k: os.environ[k] for k in ANDROID_KEYSTORE_ENV if k in os.environ}
        res = g.export(ctx.workspace, preset, out, debug=not bool(args.get("release")), env=env)
        errors, _ = classify_output(res.output)
        produced = out.is_file() and out.stat().st_size > 0
        if res.ok and produced and not errors:
            ctx.log(f"  📦 exported {out.relative_to(ctx.workspace)} ({out.stat().st_size // 1024} KiB)")
            return ToolResult.success(f"exported {args['output']} ({out.stat().st_size} bytes)\n{res.output[-1500:]}")
        return ToolResult.error(f"export FAILED (exit {res.returncode}, file produced: {produced})\n{res.output[-6000:]}")

    @reg.add(
        "run_command",
        "Run an allow-listed shell command inside the project directory (godot, gdlint, gdformat, git, ls, grep, "
        "find, cat, keytool …). No network tools. Output is truncated. Secrets are stripped from the environment.",
        {"type": "object", "properties": {
            "command": {"type": "array", "items": {"type": "string"},
                        "description": "argv list, e.g. [\"gdformat\", \"scripts/player.gd\"]"},
            "timeout": {"type": "integer", "default": 120},
        }, "required": ["command"]},
        mutating=True,
    )
    def run_command(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        argv = [str(a) for a in args.get("command", [])]
        if not argv:
            return ToolResult.error("command must be a non-empty argv list")
        exe = Path(argv[0]).name
        if exe not in SHELL_ALLOWLIST:
            return ToolResult.error(f"{exe!r} is not in the allow-list {SHELL_ALLOWLIST}")
        if exe == "godot":
            argv[0] = str(_godot(ctx).binary)
            if "--headless" not in argv:
                argv.insert(1, "--headless")
        try:
            proc = subprocess.run(argv, cwd=str(ctx.workspace), capture_output=True, text=True,
                                  timeout=int(args.get("timeout") or 120), env=scrubbed_env(), check=False)
        except subprocess.TimeoutExpired:
            return ToolResult.error("command timed out")
        except OSError as exc:
            return ToolResult.error(str(exc))
        out = (proc.stdout + ("\n" if proc.stdout and proc.stderr else "") + proc.stderr)[-12000:]
        return ToolResult(proc.returncode == 0, f"exit {proc.returncode}\n{out}", proc.returncode != 0)
