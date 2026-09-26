"""Local chat UI for godotai — the place where a person *talks* to the Godot game-building AI.

``python3 -m godotai chat`` starts a small stdlib-only HTTP server (default
``http://127.0.0.1:8765``) that serves a browser page (Arabic-first, RTL) and a
JSON/SSE API on top of the *same* :class:`godotai.agent.Agent`, tool registry,
providers and plan-approval gate the CLI ``run`` command uses. Nothing here is a
mock: every message becomes a real ``Agent.run()`` in a real project directory
under ``--games-dir``; the plan gate is exposed as approve / reject-with-feedback
buttons; the engine verification report is shown as the model produces it.
"""
from __future__ import annotations

from .access import AccessError, AccessVerifier
from .server import ChatServer, ChatServerError, is_loopback, valid_public_host
from .session import (Session, SessionBusy, SessionError, SessionManager, SessionNotReady, NoPendingPlan,
                      slugify)
from .status import environment_status, model_ready

__all__ = [
    "AccessError", "AccessVerifier",
    "ChatServer", "ChatServerError", "is_loopback", "valid_public_host",
    "Session", "SessionBusy", "SessionError", "SessionManager", "SessionNotReady", "NoPendingPlan", "slugify",
    "environment_status", "model_ready",
]
