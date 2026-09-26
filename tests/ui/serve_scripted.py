"""Start the chat server on an ephemeral loopback port with the *scripted* (no-network) model.

Used by ``tests/ui/jsdom_smoke.mjs`` to drive the real page JavaScript in a DOM against the
real HTTP server — the same offline fixtures the Python suite uses (``tests/test_chat.py``,
``tests/_helpers.py``): a fake ``/models`` probe that says the private model server is up,
a scripted model that submits a plan, writes one file and calls ``godot_verify`` (faked, PASS).

Protocol: prints ``{"port": N}`` on the first stdout line, then blocks until stdin reaches
EOF (the driver closes it), then shuts the server down. No model, network or credentials.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO))

from _helpers import repo_config, valid_plan  # noqa: E402
from test_chat import ScriptedRuns, ServerFixture  # noqa: E402


def main() -> int:
    cfg = repo_config()
    plan = valid_plan(cfg)
    run = [
        {"calls": [("submit_plan", plan)]},
        {"calls": [("write_file", {"path": "scripts/main.gd", "content": "extends Node\n", "step_id": "S3"})]},
        {"calls": [("godot_verify", {})]},
        {"text": "تم: المشهد الرئيسي جاهز والتحقق ناجح.", "stop": "end_turn"},
    ]
    fx = ServerFixture(cfg, ScriptedRuns(run))
    print(json.dumps({"port": fx.port}), flush=True)
    try:
        sys.stdin.read()
    finally:
        fx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
