"""Shared fixtures for the unit tests (stdlib only)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from godotai.config import Config, load_config  # noqa: E402


def repo_config() -> Config:
    return load_config(REPO / "godot.toml")


def valid_plan(cfg: Config) -> dict:
    return {
        "goal": "Build a tiny tap-dodge mobile game",
        "godot_version": cfg.engine.tag,
        "summary": "Create main/player/obstacle scenes with typed GDScript, spawn timer and HUD, then verify headless.",
        "assumptions": ["placeholder ColorRect art"],
        "design": {
            "genre": "arcade", "platforms": ["Android"],
            "scenes": ["scenes/main.tscn (Node2D)", "scenes/player.tscn (Area2D)"],
            "scripts": ["scripts/main.gd — game loop", "scripts/player.gd — input"],
            "input_scheme": "touch drag / arrow keys", "core_loop": "dodge falling blocks, score = seconds survived",
        },
        "steps": [
            {"id": "S1", "title": "project.godot", "kind": "create", "paths": ["project.godot"], "details": "mobile settings"},
            {"id": "S2", "title": "scenes", "kind": "create", "paths": ["scenes/main.tscn", "scenes/player.tscn"], "details": ""},
            {"id": "S3", "title": "scripts", "kind": "create", "paths": ["scripts/main.gd", "scripts/player.gd"], "details": ""},
            {"id": "S4", "title": "verify", "kind": "verify", "paths": [], "details": "godot_verify must pass"},
        ],
        "verification": ["godot_import", "check_scripts", "smoke_test prints SMOKE_TEST_OK"],
        "risks": ["touch feel untested on device"],
        "open_questions": [],
    }
