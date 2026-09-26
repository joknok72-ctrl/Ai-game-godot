"""Shared fixtures for the unit tests (stdlib only)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from godotai.config import AgentConfig, Config, load_config  # noqa: E402
from godotai.model_identity import ModelIdentity  # noqa: E402

# Hosted-model env that must not leak into tests of the *private* default (your own server, no vendor key).
PRIVATE_ENV = {"OPENAI_API_KEY": "", "OPENAI_BASE_URL": "", "GODOTAI_BASE_URL": "", "GODOTAI_PROVIDER": "",
               "GODOTAI_MODEL": "", "GODOTAI_ROUTE": "", "GODOTAI_SKIP_MODEL_PROBE": ""}


def repo_config() -> Config:
    return load_config(REPO / "godot.toml")


def anthropic_agent(**overrides) -> AgentConfig:
    """The repo's agent settings pointed at Claude — the explicit vendor opt-in used by the Anthropic tests."""
    from dataclasses import replace
    overrides.setdefault("base_url", None)
    return replace(repo_config().agent, provider="anthropic", model="claude-fable-5-1", **overrides)


def vendor_identity(model: str = "claude-fable-5-1") -> ModelIdentity:
    return ModelIdentity(name=model, kind="vendor_api", base_model=model, base_license="proprietary",
                         owner="the vendor", serving="vendor", adapter="")


def vendor_config(**agent_overrides) -> Config:
    """Repo config with Claude as the (honestly labelled) vendor model — what a user who opts in would write."""
    from dataclasses import replace
    cfg = repo_config()
    return replace(cfg, agent=anthropic_agent(**agent_overrides), model=vendor_identity())


def fake_probe(reachable: bool = True, models: tuple[str, ...] = ("godotai",), authorized: bool = True):
    """An injectable stand-in for status.probe_openai_endpoint — never touches the network."""
    def probe(base_url: str) -> dict:
        return {"reachable": reachable, "authorized": authorized, "models": list(models),
                "error": None if reachable else "connection refused"}
    return probe


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
