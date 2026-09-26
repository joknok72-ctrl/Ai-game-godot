"""Thin wrapper around the Godot editor binary (headless usage only).

Every command is documented in the official command line tutorial:
https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import EngineConfig, parse_godot_version_output, version_matches

ENV_GODOT_BIN = "GODOT_BIN"


class GodotNotFound(RuntimeError):
    pass


class GodotVersionMismatch(RuntimeError):
    pass


@dataclass
class RunResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def output(self) -> str:
        return (self.stdout or "") + ("\n" if self.stdout and self.stderr else "") + (self.stderr or "")

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def find_godot_binary(engine: EngineConfig | None = None) -> Path | None:
    """Locate the editor binary: $GODOT_BIN, PATH, then common install dirs."""
    env = os.environ.get(ENV_GODOT_BIN)
    if env and Path(env).is_file():
        return Path(env)
    for name in ("godot", "godot4"):
        found = shutil.which(name)
        if found:
            return Path(found)
    candidates = [
        Path.home() / ".local" / "bin" / "godot",
        Path("/usr/local/bin/godot"),
        Path("/opt/godot/godot"),
    ]
    if engine is not None:
        candidates.append(Path.home() / ".local" / "bin" / engine.editor_binary_name)
    for c in candidates:
        if c.is_file():
            return c
    return None


def export_templates_dir(engine: EngineConfig) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "godot" / "export_templates" / engine.templates_dirname


def editor_settings_path(engine: EngineConfig) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "godot" / engine.editor_settings_filename


class Godot:
    """Runs the pinned Godot editor headless and refuses any other version."""

    def __init__(self, engine: EngineConfig, binary: Path | None = None, strict: bool = True):
        self.engine = engine
        self.binary = binary or find_godot_binary(engine)
        if self.binary is None:
            raise GodotNotFound(
                "Godot editor binary not found. Set $GODOT_BIN or run "
                "`python3 -m godotai install-godot`."
            )
        self._version_output: str | None = None
        if strict:
            self.assert_version()

    # ------------------------------------------------------------------
    def run(self, args: list[str], cwd: Path | None = None, timeout: int = 300,
            env: dict[str, str] | None = None) -> RunResult:
        full = [str(self.binary), *args]
        merged_env = os.environ.copy()
        merged_env.setdefault("GODOT_SILENCE_ROOT_WARNING", "1")  # containers usually run as root
        if env:
            merged_env.update(env)
        try:
            proc = subprocess.run(
                full, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                timeout=timeout, env=merged_env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return RunResult(full, -1, exc.stdout or "" if isinstance(exc.stdout, str) else "",
                             exc.stderr or "" if isinstance(exc.stderr, str) else "", timed_out=True)
        except OSError as exc:
            # ENOENT / EACCES / ENOEXEC ("Exec format error": an x86_64 editor on an ARM phone, or the glibc build
            # launched directly in Termux). Callers already handle GodotNotFound; a raw OSError was a traceback.
            from .hostenv import exec_failure_hint
            raise GodotNotFound(f"cannot execute Godot binary {self.binary}: {exc}\n{exec_failure_hint(self.engine)}") from exc
        return RunResult(full, proc.returncode, proc.stdout, proc.stderr)

    def version_output(self) -> str:
        if self._version_output is None:
            res = self.run(["--headless", "--version"], timeout=60)
            self._version_output = res.output.strip()
        return self._version_output

    def version(self) -> str | None:
        return parse_godot_version_output(self.version_output())

    def assert_version(self) -> None:
        out = self.version_output()
        if not version_matches(out, self.engine):
            raise GodotVersionMismatch(
                f"Godot binary {self.binary} reports {out!r}, but this AI is pinned to "
                f"{self.engine.version_string}. Install the pinned version "
                f"(`python3 -m godotai install-godot`) or change godot.toml deliberately."
            )

    # ------------------------------------------------------------------
    # Project-level operations (all headless)
    # ------------------------------------------------------------------
    def import_project(self, project: Path, timeout: int = 600) -> RunResult:
        """``--import``: open the editor headless, import all resources, quit."""
        return self.run(["--headless", "--path", str(project), "--import"], timeout=timeout)

    def check_script(self, project: Path, res_path: str, timeout: int = 120) -> RunResult:
        """``--check-only``: parse a GDScript for errors without running it."""
        return self.run(["--headless", "--path", str(project), "--script", res_path, "--check-only"],
                        timeout=timeout)

    def run_headless(self, project: Path, frames: int = 120, timeout: int = 180) -> RunResult:
        """Run the main scene headless for *frames* frames and quit."""
        return self.run(["--headless", "--path", str(project), "--quit-after", str(frames)],
                        timeout=timeout)

    def run_script(self, project: Path, res_path: str, extra: list[str] | None = None,
                   timeout: int = 300) -> RunResult:
        """Run a ``SceneTree``/``MainLoop`` script (e.g. a smoke test)."""
        return self.run(["--headless", "--path", str(project), "--script", res_path, *(extra or [])],
                        timeout=timeout)

    def export(self, project: Path, preset: str, output: Path, debug: bool = True,
               timeout: int = 1800, env: dict[str, str] | None = None) -> RunResult:
        """``--export-debug`` / ``--export-release`` with the named preset."""
        flag = "--export-debug" if debug else "--export-release"
        output.parent.mkdir(parents=True, exist_ok=True)
        return self.run(["--headless", "--path", str(project), flag, preset, str(output)],
                        timeout=timeout, env=env)

    def templates_installed(self) -> bool:
        d = export_templates_dir(self.engine)
        return d.is_dir() and any(d.iterdir())
