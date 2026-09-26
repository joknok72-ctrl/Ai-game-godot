"""The chat UI is a thin, honest layer over the real agent — proven offline.

Runs the HTTP server on an ephemeral port with the scripted (no-network) model from
``test_agent_loop`` behind the *same* ``Agent``/tool registry the CLI uses, then drives
it like the browser does: create a project, send a message, receive the plan over
SSE, approve/reject it, watch tool events, read the final summary and the persisted
history. Also covers the safety rails (token, Host/Origin checks, path traversal,
busy/not-ready responses) and the CLI wiring.
"""
from __future__ import annotations

import http.client
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from dataclasses import replace

from _helpers import PRIVATE_ENV, fake_probe, repo_config, valid_plan, vendor_config, vendor_identity
from test_agent_loop import FakeVerify, ScriptedProvider, make_registry

from godotai import __main__ as cli
from godotai.chat import (ChatServer, ChatServerError, SessionBusy, SessionManager, SessionNotReady, NoPendingPlan,
                          environment_status, is_loopback, model_ready, slugify)
from godotai.chat.session import HISTORY_FILE, SessionError
from godotai.chat.server import _host_of

READY = (True, "", "")


def wait_until(pred, timeout: float = 10.0, step: float = 0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


class ScriptedRuns:
    """provider_factory: each call to the factory pops the next scripted run (list of turns)."""

    def __init__(self, *runs):
        self.runs = list(runs)
        self.providers: list[ScriptedProvider] = []

    def __call__(self, agent_cfg):
        if not self.runs:
            raise AssertionError("no scripted run left")
        p = ScriptedProvider(self.runs.pop(0))
        self.providers.append(p)
        return p


class ServerFixture:
    """A ChatServer on 127.0.0.1:<ephemeral> with injected provider/registry/readiness."""

    def __init__(self, cfg, provider_factory, verify: FakeVerify | None = None, ready=READY, token=None, host="127.0.0.1",
                 probe=None, **server_kw):
        self.tmp = Path(tempfile.mkdtemp(prefix="godotai-chat-"))
        self.verify = verify or FakeVerify()
        manager = SessionManager(cfg, self.tmp / "games", provider_factory=provider_factory,
                                 registry_factory=lambda: make_registry(self.verify), ready_check=lambda: ready)
        self.server = ChatServer(cfg, self.tmp / "games", host=host, port=0, token=token, manager=manager,
                                 probe=probe or fake_probe(), **server_kw)
        self.thread = self.server.start_in_thread()
        self.token = token

    @property
    def port(self) -> int:
        return self.server.port

    def request(self, method: str, path: str, body=None, headers=None, host_header=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": host_header or f"127.0.0.1:{self.port}"}
        if self.token and "Authorization" not in (headers or {}):
            hdrs["Authorization"] = f"Bearer {self.token}"
        hdrs.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            conn.putheader(k, v)
        if data is not None:
            conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        if raw:
            return resp.status, payload
        return resp.status, (json.loads(payload) if payload else None)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def sse(self, sid: str, since: int = 0, count: int = 1, timeout: float = 10.0, headers=None) -> list[dict]:
        """Open the event stream and return the first *count* events (parsed)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        hdrs = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        hdrs.update(headers or {})
        conn.request("GET", f"/api/sessions/{sid}/events?since={since}", headers=hdrs)
        resp = conn.getresponse()
        assert resp.status == 200, resp.status
        assert resp.getheader("Content-Type", "").startswith("text/event-stream")
        events: list[dict] = []
        current: dict = {}
        while len(events) < count:
            line = resp.readline().decode("utf-8").rstrip("\n")
            if line.startswith(":"):
                continue
            if line == "":
                if current.get("data"):
                    ev = json.loads(current["data"])
                    ev["_sse_id"] = int(current["id"])
                    ev["_sse_event"] = current["event"]
                    events.append(ev)
                current = {}
                continue
            k, _, v = line.partition(": ")
            current[k] = v
        conn.close()
        return events

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=5)


class ChatServerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()
        self.plan = valid_plan(self.cfg)
        self.fx: ServerFixture | None = None

    def tearDown(self):
        if self.fx:
            self.fx.close()

    def start(self, *runs, **kw) -> ServerFixture:
        self.fx = ServerFixture(self.cfg, ScriptedRuns(*runs), **kw)
        return self.fx

    # ------------------------------------------------------------------ static + status
    def test_page_assets_and_status(self):
        fx = self.start()
        status, html = fx.get("/", raw=True)
        self.assertEqual(status, 200)
        text = html.decode("utf-8")
        self.assertIn('dir="rtl"', text)
        self.assertIn("ابعت للذكاء الاصطناعي", text)
        self.assertIn('src="/static/app.js"', text)
        self.assertNotIn("<script>", text, "no inline script (strict CSP)")
        for asset in ("/static/app.js", "/static/app.css"):
            st, body = fx.get(asset, raw=True)
            self.assertEqual(st, 200, asset)
            self.assertGreater(len(body), 500)
        self.assertIn("EventSource", fx.get("/static/app.js", raw=True)[1].decode())
        st, js = fx.get("/api/status")
        self.assertEqual(st, 200)
        self.assertEqual(js["engine"]["pinned"], self.cfg.engine.tag)
        self.assertEqual(js["model"]["provider"], self.cfg.agent.provider)
        self.assertEqual(js["model"]["model"], self.cfg.agent.model)
        self.assertIn("ready", js)
        self.assertIn("hint_ar", js["model"])
        self.assertFalse(js["token_required"])
        self.assertTrue(js["url"].startswith("http://127.0.0.1:"))
        # no credential value can ever appear in the status payload
        self.assertNotIn("sk-", json.dumps(js))
        self.assertEqual(fx.get("/nope")[0], 404)
        self.assertEqual(fx.get("/api/nope")[0], 404)

    # ------------------------------------------------------------------ the real thing
    def test_full_conversation_reject_then_approve_then_follow_up(self):
        run1 = [
            {"calls": [("submit_plan", self.plan)]},                          # plan → human rejects with feedback
            {"calls": [("submit_plan", self.plan)]},                          # resubmits → approved
            {"calls": [("write_file", {"path": "scripts/main.gd", "content": "extends Node\n", "step_id": "S3"})]},
            {"calls": [("godot_verify", {})]},
            {"text": "تم: المشهد الرئيسي واللاعب جاهزان والتحقق ناجح.", "stop": "end_turn"},
        ]
        run2 = [
            {"calls": [("submit_plan", self.plan)]},
            {"calls": [("godot_verify", {})]},
            {"text": "Pause button added.", "stop": "end_turn"},
        ]
        fx = self.start(run1, run2)

        # create a project; Arabic names fall back to a generated slug
        st, snap = fx.post("/api/sessions", {"name": "My Flappy Game"})
        self.assertEqual(st, 201)
        self.assertEqual(snap["id"], "my-flappy-game")
        self.assertEqual(snap["state"], "idle")
        self.assertFalse(snap["has_project"])
        self.assertTrue(Path(snap["workspace"]).is_dir())
        st, listing = fx.get("/api/sessions")
        self.assertEqual([s["id"] for s in listing["sessions"]], ["my-flappy-game"])

        # send a message → 202, run starts, plan arrives on the stream
        st, acc = fx.post("/api/sessions/my-flappy-game/messages", {"text": "اعمل لعبة تفادي للموبايل"})
        self.assertEqual(st, 202, acc)
        self.assertEqual(acc["event"]["type"], "user")
        session = fx.server.manager.get("my-flappy-game")
        self.assertTrue(wait_until(lambda: session.state == "awaiting_approval"))
        st, snap = fx.get("/api/sessions/my-flappy-game")
        self.assertEqual(snap["state"], "awaiting_approval")
        self.assertIn("# Plan — Build a tiny tap-dodge", snap["pending_plan"]["markdown"])
        self.assertEqual(snap["pending_plan"]["plan"]["godot_version"], "4.7.2-stable")
        # nothing was written while waiting for the human
        self.assertFalse((Path(snap["workspace"]) / "scripts" / "main.gd").exists())

        # the stream replays everything from 0 with ids/event names
        events = fx.sse("my-flappy-game", since=0, count=3)
        self.assertEqual([e["type"] for e in events[:1]], ["user"])
        self.assertEqual(events[0]["_sse_event"], "user")
        self.assertEqual(events[0]["text"], "اعمل لعبة تفادي للموبايل")
        self.assertTrue(all(e["_sse_id"] == e["id"] for e in events))

        # a second message while busy → 409
        st, err = fx.post("/api/sessions/my-flappy-game/messages", {"text": "again"})
        self.assertEqual(st, 409)
        self.assertIn("busy", err["error"])
        self.assertTrue(err["hint"])

        # reject with feedback → model re-plans → approve
        st, _ = fx.post("/api/sessions/my-flappy-game/approve", {"approved": False, "feedback": "add a pause button"})
        self.assertEqual(st, 200)
        self.assertTrue(wait_until(lambda: session.state == "awaiting_approval"
                                   and sum(1 for e in session.events if e["type"] == "plan") == 2))
        st, _ = fx.post("/api/sessions/my-flappy-game/approve", {"approved": True})
        self.assertEqual(st, 200)
        self.assertTrue(wait_until(lambda: session.state == "idle"))
        st, snap = fx.get("/api/sessions/my-flappy-game")
        types = [h["type"] for h in snap["history"]]
        self.assertEqual(types.count("user"), 1)
        self.assertEqual(types.count("plan"), 2)
        self.assertEqual(types.count("approval"), 2)
        self.assertEqual(types[-1], "done")
        done = snap["history"][-1]
        self.assertEqual(done["status"], "success", done)
        self.assertTrue(done["verification_passed"])
        self.assertEqual(done["plan_path"], ".godotai/PLAN.md")
        self.assertTrue(done["log_path"].startswith(".godotai/runs/"))
        self.assertGreaterEqual(done["iterations"], 4)
        approvals = [h for h in snap["history"] if h["type"] == "approval"]
        self.assertEqual([(a["approved"], a["feedback"]) for a in approvals], [(False, "add a pause button"), (True, "")])
        self.assertTrue(any(h["type"] == "assistant" and "تم" in h["text"] for h in snap["history"]))
        ws = Path(snap["workspace"])
        self.assertEqual((ws / "scripts" / "main.gd").read_text(), "extends Node\n")
        self.assertTrue((ws / ".godotai" / "PLAN.md").is_file())
        self.assertEqual(fx.verify.calls, 1)
        # tool events were streamed with ok flags and step ids
        tools = [e for e in session.events if e["type"] == "tool"]
        self.assertIn(("write_file", True, "S3"), [(t["name"], t["ok"], t["step_id"]) for t in tools])
        self.assertIn("Result: PASS", next(t for t in tools if t["name"] == "godot_verify")["preview"])
        # the model saw the reviewer feedback; the first run carries no context block
        run_log = json.loads((ws / done["log_path"]).read_text(encoding="utf-8"))
        self.assertEqual(run_log["task"], "اعمل لعبة تفادي للموبايل")
        self.assertIn("Plan NOT approved. Reviewer feedback: add a pause button", json.dumps(run_log["messages"], ensure_ascii=False))

        # history is persisted next to the project and survives a fresh SessionManager
        hist_file = ws / HISTORY_FILE
        self.assertTrue(hist_file.is_file())
        reloaded = SessionManager(self.cfg, fx.tmp / "games", provider_factory=lambda c: None,
                                  registry_factory=lambda: None, ready_check=lambda: READY).get("my-flappy-game")
        self.assertEqual([h["type"] for h in reloaded.snapshot()["history"]], types)

        # follow-up in the same project: new run, same folder, model gets the earlier exchange as context
        st, acc = fx.post("/api/sessions/my-flappy-game/messages", {"text": "add a pause button", "auto_approve": True})
        self.assertEqual(st, 202)
        self.assertTrue(wait_until(lambda: session.state == "idle" and session.history[-1]["type"] == "done"
                                   and session.history[-1] is not done))
        second_done = session.history[-1]
        self.assertEqual(second_done["status"], "success")
        task2 = json.loads((ws / second_done["log_path"]).read_text(encoding="utf-8"))["task"]
        self.assertTrue(task2.startswith("add a pause button"))
        self.assertIn("Context — earlier requests in this same project directory", task2)
        self.assertIn('1. "اعمل لعبة تفادي للموبايل" → success:', task2)
        auto = [h for h in session.history if h["type"] == "approval"][-1]
        self.assertEqual((auto["approved"], auto["auto"]), (True, True))
        self.assertEqual(fx.verify.calls, 2)

    def test_not_ready_gives_503_with_arabic_hint_and_runs_nothing(self):
        fx = self.start(ready=(False, "مفتاح النموذج غير موجود…", "Model API key missing"))
        fx.post("/api/sessions", {"name": "x1"})
        st, err = fx.post("/api/sessions/x1/messages", {"text": "make a game"})
        self.assertEqual(st, 503)
        self.assertIn("not configured", err["error"])
        self.assertIn("مفتاح", err["hint"])
        self.assertEqual(fx.server.manager.get("x1").state, "idle")
        self.assertEqual(fx.get("/api/sessions/x1")[1]["history"], [])

    def test_cancel_while_awaiting_approval_aborts_without_writing(self):
        fx = self.start([{"calls": [("submit_plan", self.plan)]},
                         {"calls": [("write_file", {"path": "scripts/a.gd", "content": "x", "step_id": "S3"})]}])
        fx.post("/api/sessions", {"name": "c"})
        fx.post("/api/sessions/c/messages", {"text": "make a game"})
        session = fx.server.manager.get("c")
        self.assertTrue(wait_until(lambda: session.state == "awaiting_approval"))
        st, res = fx.post("/api/sessions/c/cancel")
        self.assertEqual((st, res), (200, {"cancelling": True}))
        self.assertTrue(wait_until(lambda: session.state == "idle"))
        done = session.history[-1]
        self.assertEqual((done["status"], done["message"]), ("aborted", "cancelled by the user"))
        self.assertFalse((session.workspace / "scripts" / "a.gd").exists())
        self.assertEqual(fx.post("/api/sessions/c/cancel")[1], {"cancelling": False})
        # approving now is an error: nothing is pending
        st, err = fx.post("/api/sessions/c/approve", {"approved": True})
        self.assertEqual(st, 409)

    def test_provider_failure_is_reported_and_session_recovers(self):
        from godotai.providers import ProviderError
        fx = self.start([{"error": ProviderError("HTTP 401: invalid x-api-key")}],
                        [{"calls": [("submit_plan", self.plan)]}, {"calls": [("godot_verify", {})]}, {"text": "ok", "stop": "end_turn"}])
        fx.post("/api/sessions", {"name": "p"})
        fx.post("/api/sessions/p/messages", {"text": "make a game"})
        session = fx.server.manager.get("p")
        self.assertTrue(wait_until(lambda: session.state == "idle" and session.history and session.history[-1]["type"] == "error"))
        err = session.history[-1]
        self.assertIn("invalid x-api-key", err["message"])
        self.assertIn("مفتاح", err["hint_ar"])
        # a new message works again
        st, _ = fx.post("/api/sessions/p/messages", {"text": "retry", "auto_approve": True})
        self.assertEqual(st, 202)
        self.assertTrue(wait_until(lambda: session.state == "idle" and session.history[-1]["type"] == "done"))
        self.assertEqual(session.history[-1]["status"], "success")

    def test_plan_only_message(self):
        fx = self.start([{"calls": [("submit_plan", self.plan)]}])
        fx.post("/api/sessions", {"name": "po"})
        st, _ = fx.post("/api/sessions/po/messages", {"text": "make a game", "plan_only": True})
        self.assertEqual(st, 202)
        session = fx.server.manager.get("po")
        self.assertTrue(wait_until(lambda: session.state == "idle" and session.history[-1]["type"] == "done"))
        done = session.history[-1]
        self.assertEqual(done["status"], "success")
        self.assertIn("plan-only", done["message"])
        self.assertEqual(fx.verify.calls, 0)
        self.assertTrue((session.workspace / ".godotai" / "PLAN.md").is_file())

    # ------------------------------------------------------------------ files + verify (no model)
    def test_files_endpoint_is_sandboxed(self):
        fx = self.start()
        st, snap = fx.post("/api/sessions", {"name": "files"})
        ws = Path(snap["workspace"])
        (ws / "scripts").mkdir()
        (ws / "scripts" / "main.gd").write_text("extends Node\n")
        (ws / "release.keystore").write_bytes(b"\x00\x01")
        (ws / ".godotai").mkdir()
        (ws / ".godotai" / "PLAN.md").write_text("# Plan\n")
        (ws / ".godotai" / "secret.txt").write_text("hidden\n")
        st, listing = fx.get("/api/sessions/files/files")
        self.assertEqual(st, 200)
        self.assertEqual(listing["files"], [".godotai/PLAN.md", "scripts/main.gd"])
        st, f = fx.get("/api/sessions/files/files?path=scripts/main.gd")
        self.assertEqual((st, f["content"]), (200, "extends Node\n"))
        self.assertEqual(fx.get("/api/sessions/files/files?path=res://scripts/main.gd")[0], 200)
        self.assertEqual(fx.get("/api/sessions/files/files?path=../../godot.toml")[0], 400)
        self.assertEqual(fx.get("/api/sessions/files/files?path=/etc/passwd")[0], 400)
        self.assertEqual(fx.get("/api/sessions/files/files?path=release.keystore")[0], 400)
        self.assertEqual(fx.get("/api/sessions/files/files?path=missing.gd")[0], 400)
        self.assertEqual(fx.get("/api/sessions/does-not-exist")[0], 404)
        self.assertEqual(fx.get("/api/sessions/Bad%20Name")[0], 404)

    def test_verify_without_project_and_without_engine(self):
        fx = self.start()
        fx.post("/api/sessions", {"name": "v"})
        st, err = fx.post("/api/sessions/v/verify")
        self.assertEqual(st, 400)
        self.assertIn("project.godot", err["error"])
        session = fx.server.manager.get("v")
        (session.workspace / "project.godot").write_text('config_version=5\n[application]\nconfig/name="x"\n')
        with mock.patch.dict(os.environ, {"GODOT_BIN": str(fx.tmp / "no-such-godot")}):
            with mock.patch("godotai.godot.find_godot_binary", return_value=None):
                st, rep = fx.post("/api/sessions/v/verify")
        self.assertEqual(st, 200)
        self.assertFalse(rep["passed"])
        self.assertIn("engine version", rep["markdown"])
        self.assertTrue((session.workspace / ".godotai" / "last_verification.md").is_file())
        self.assertEqual(session.history[-1]["type"], "system")

    # ------------------------------------------------------------------ safety rails
    def test_host_and_origin_guards(self):
        fx = self.start()
        self.assertEqual(fx.get("/api/status", host_header="evil.example:80")[0], 403)
        self.assertEqual(fx.get("/api/status", host_header=f"localhost:{fx.port}")[0], 200)
        self.assertEqual(fx.get("/api/status", host_header=f"[::1]:{fx.port}")[0], 200)
        self.assertEqual(fx.post("/api/sessions", {"name": "o"}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(fx.post("/api/sessions", {"name": "o"}, headers={"Origin": f"http://127.0.0.1:{fx.port}"})[0], 201)
        self.assertEqual(fx.post("/api/sessions", {"name": "o"}, headers={"Origin": f"http://localhost:{fx.port}"})[0], 201)
        # bad bodies never crash the server
        conn = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=5)
        conn.request("POST", "/api/sessions", body=b"not json", headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertEqual(fx.post("/api/sessions/o/approve", {})[0], 400)          # 'approved' missing
        self.assertEqual(fx.post("/api/sessions/o/messages", {"text": "   "})[0], 400)
        self.assertEqual(fx.request("POST", "/api/sessions/o/events", {})[0], 405)

    def test_token_mode(self):
        fx = self.start(token="s3cret-token-for-tests")
        self.assertEqual(fx.get("/", raw=True)[0], 200, "the page itself needs no token")
        self.assertEqual(fx.get("/api/status", headers={"Authorization": "Bearer wrong"})[0], 401)
        st, js = fx.get("/api/status")
        self.assertEqual(st, 200)
        self.assertTrue(js["token_required"])
        fx.post("/api/sessions", {"name": "t"})
        # EventSource cannot set headers → ?token= is accepted for the stream only
        conn = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=5)
        conn.request("GET", "/api/sessions/t/events?since=0")
        self.assertEqual(conn.getresponse().status, 401)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=5)
        conn.request("GET", f"/api/sessions/t/events?since=0&token={urllib.parse.quote('s3cret-token-for-tests')}")
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_non_loopback_requires_token(self):
        with self.assertRaises(ChatServerError):
            ChatServer(self.cfg, Path(tempfile.mkdtemp()), host="0.0.0.0", port=0)
        self.assertTrue(is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1") and is_loopback("[::1]"))
        self.assertFalse(is_loopback("0.0.0.0") or is_loopback("192.168.1.5") or is_loopback("example.com"))
        self.assertEqual(_host_of("[::1]:8765"), "::1")
        self.assertEqual(_host_of("localhost:8765"), "localhost")
        self.assertEqual(_host_of("127.0.0.1"), "127.0.0.1")

    def test_sse_resume_uses_last_event_id_header(self):
        fx = self.start()
        fx.post("/api/sessions", {"name": "r"})
        session = fx.server.manager.get("r")
        for i in range(3):
            session.emit("system", text=f"note {i}")
        events = fx.sse("r", since=0, count=1, headers={"Last-Event-ID": "2"})
        self.assertEqual(events[0]["text"], "note 2")
        self.assertEqual(events[0]["_sse_id"], 3)


class SessionUnitTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()
        self.tmp = Path(tempfile.mkdtemp(prefix="godotai-sess-"))

    def test_slugify(self):
        self.assertEqual(slugify("My Flappy Game"), "my-flappy-game")
        self.assertEqual(slugify("  Tap_Dodge 2 !!"), "tap_dodge-2")
        self.assertEqual(slugify("لعبتي"), "")                 # non-ascii → manager generates game-N
        self.assertEqual(slugify("../../etc"), "etc")
        self.assertEqual(slugify("-"), "")
        self.assertEqual(len(slugify("a" * 100)), 48)

    def test_manager_creates_and_lists_folders(self):
        mgr = SessionManager(self.cfg, self.tmp / "games", provider_factory=lambda c: None,
                             registry_factory=lambda: None, ready_check=lambda: READY)
        a = mgr.create("لعبتي")
        b = mgr.create("")
        self.assertEqual((a.id, b.id), ("game-1", "game-2"))
        self.assertTrue((self.tmp / "games" / "game-1").is_dir())
        self.assertIs(mgr.create("game-1"), a)
        (self.tmp / "games" / "pre-existing").mkdir()
        (self.tmp / "games" / "Not Valid").mkdir()
        self.assertEqual([s["id"] for s in mgr.list()], ["game-1", "game-2", "pre-existing"])
        self.assertIsNone(mgr.get("Not Valid"))
        self.assertIsNone(mgr.get("../x"))
        self.assertIsNone(mgr.get(""))

    def test_send_guards(self):
        mgr = SessionManager(self.cfg, self.tmp / "games", provider_factory=lambda c: None,
                             registry_factory=lambda: None, ready_check=lambda: (False, "hint-ar", "hint-en"))
        s = mgr.create("g")
        with self.assertRaises(SessionError):
            s.send("")
        with self.assertRaises(SessionNotReady) as cm:
            s.send("make a game")
        self.assertEqual(cm.exception.hint, "hint-ar")
        self.assertEqual(SessionNotReady.status, 503)
        self.assertEqual(SessionBusy.status, 409)
        self.assertEqual(NoPendingPlan.status, 409)
        with self.assertRaises(NoPendingPlan):
            s.approve(True)

    def test_history_survives_reload_and_ignores_junk(self):
        mgr = SessionManager(self.cfg, self.tmp / "games", provider_factory=lambda c: None,
                             registry_factory=lambda: None, ready_check=lambda: READY)
        s = mgr.create("h")
        s.emit("user", text="hello")
        s.emit("log", text="not persisted")
        s.emit("done", status="success", message="ok")
        with (s.workspace / HISTORY_FILE).open("a", encoding="utf-8") as fh:
            fh.write("{broken json\n")
            fh.write(json.dumps({"type": "tool", "name": "x"}) + "\n")
        fresh = SessionManager(self.cfg, self.tmp / "games", provider_factory=lambda c: None,
                               registry_factory=lambda: None, ready_check=lambda: READY).get("h")
        snap = fresh.snapshot()
        self.assertEqual([h["type"] for h in snap["history"]], ["user", "done"])
        self.assertEqual([h["id"] for h in snap["history"]], [1, 2])
        self.assertEqual(snap["last_event_id"], 2)
        self.assertEqual(snap["messages"], 1)
        self.assertEqual(fresh.events_since(1)[0]["type"], "done")

    def test_status_module_vendor_opt_in(self):
        vc = vendor_config()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "CF_AIG_TOKEN": ""}, clear=False):
            ready, ar, en = model_ready(vc)
        self.assertFalse(ready)
        self.assertIn("export ANTHROPIC_API_KEY", ar)
        self.assertIn("python3 -m godotai chat", en)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}):
            self.assertTrue(model_ready(vc)[0])
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x", "GITHUB_TOKEN": ""}):
            st = environment_status(vc)
        self.assertEqual(st["engine"]["pinned"], vc.engine.tag)
        self.assertIn("templates_dir", st["engine"])
        self.assertTrue(st["model"]["ready"])
        self.assertTrue(st["model"]["key_set"])
        self.assertFalse(st["model"]["private"])
        self.assertFalse(st["model"]["server"]["probed"], "vendor APIs are never probed from here")
        self.assertEqual(st["model"]["identity"]["kind"], "vendor_api")
        self.assertFalse(st["model"]["identity"]["yours"])
        self.assertFalse(st["github_token_set"])
        self.assertIn("export_possible", st["android"])
        # openai_compat at the vendor URL is key-gated too (the identity check reads the same env as `endpoint`)
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "OPENAI_API_KEY": "sk-test"}):
            oc = replace(self.cfg, agent=replace(self.cfg.agent, model="gpt-x"), model=vendor_identity("gpt-x"))
            self.assertTrue(model_ready(oc)[0])
            self.assertEqual(environment_status(oc)["model"]["key_env"], "OPENAI_API_KEY")

    def test_status_module_private_server_is_probed(self):
        """The default (your server) is *checked*, not assumed: unreachable → clear start-up hint, no send."""
        cfg = self.cfg
        with mock.patch.dict(os.environ, PRIVATE_ENV):
            ready, ar, en = model_ready(cfg, fake_probe(reachable=False))
            self.assertFalse(ready)
            self.assertIn("vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --served-model-name godotai", en)
            self.assertIn("ollama", en)
            self.assertIn("http://127.0.0.1:8000/v1", ar)
            self.assertIn("vllm serve", ar)
            ready, _, en = model_ready(cfg, fake_probe(reachable=True, authorized=False))
            self.assertFalse(ready)
            self.assertIn("401/403", en)
            self.assertTrue(model_ready(cfg, fake_probe())[0])
            st = environment_status(cfg, fake_probe(models=("godotai", "other")))
            m = st["model"]
            self.assertTrue(m["private"])
            self.assertIsNone(m["key_env"], "your own server: no vendor key involved")
            self.assertEqual(m["base_url"], "http://127.0.0.1:8000/v1")
            self.assertEqual(m["server"], {"probed": True, "reachable": True, "models": ["godotai", "other"],
                                           "model_listed": True, "error": None})
            self.assertEqual(m["identity"]["kind"], "open_weight_deployment")
            self.assertTrue(m["identity"]["yours"])
            self.assertIn("not trained by you", m["identity"]["disclosure_en"])
            self.assertIsNone(m["identity"]["quality_claim"])
            st = environment_status(cfg, fake_probe(models=("something-else",)))
            self.assertFalse(st["model"]["server"]["model_listed"], "configured model missing from /models is flagged")
            self.assertTrue(st["model"]["ready"], "…but the server is up, so sending is allowed (the server decides)")
            # OPENAI_BASE_URL retargets the probe (e.g. Ollama)
            seen: list[str] = []

            def spy(url):
                seen.append(url)
                return fake_probe()(url)
            with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "http://localhost:11434/v1"}):
                self.assertTrue(model_ready(cfg, spy)[0], "a local server needs no key")
            self.assertEqual(seen, ["http://localhost:11434/v1"])
            with mock.patch.dict(os.environ, {"GODOTAI_SKIP_MODEL_PROBE": "1"}):
                st = environment_status(cfg, fake_probe(reachable=False))
                self.assertTrue(st["ready"])
                self.assertFalse(st["model"]["server"]["probed"])

    def test_probe_cache_and_real_probe_on_closed_port(self):
        from godotai.chat import status as status_mod
        status_mod.clear_probe_cache()
        calls = []

        def counting(url):
            calls.append(url)
            return fake_probe()(url)
        with mock.patch.object(status_mod, "probe_openai_endpoint", counting):
            status_mod.cached_probe("http://x/v1")
            status_mod.cached_probe("http://x/v1")
            self.assertEqual(len(calls), 1, "second real probe within the TTL is served from the cache")
            status_mod.cached_probe("http://x/v1", counting)
            self.assertEqual(len(calls), 2, "an injected probe bypasses the cache")
        status_mod.clear_probe_cache()
        # a real probe against a port nobody listens on: reachable = False, no exception, no hang
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        r = status_mod.probe_openai_endpoint(f"http://127.0.0.1:{port}/v1", timeout=1.0)
        self.assertFalse(r["reachable"])
        self.assertEqual(r["models"], [])
        with mock.patch.dict(os.environ, PRIVATE_ENV):
            st = environment_status(self.cfg, fake_probe())
        if not st["engine"]["ok"]:
            self.assertIn("install-godot", st["engine"]["hint_ar"])


class PackagingTests(unittest.TestCase):
    """The chat package and its static files must ship with the wheel / Docker image / CI."""

    def test_pyproject_ci_and_gitignore_cover_the_chat_ui(self):
        from _helpers import REPO
        py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"godotai.chat"', py)
        self.assertIn('"chat/static/*"', py)
        static = REPO / "godotai" / "chat" / "static"
        self.assertEqual(sorted(p.name for p in static.iterdir()), ["app.css", "app.js", "index.html"])
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python3 -m godotai chat --port 0", ci)
        gi = (REPO / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/games/", gi)
        self.assertIn(".godotai/chat.jsonl", gi)
        docker = (REPO / "docker" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("EXPOSE 8765", docker)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m godotai chat", readme)
        self.assertIn("http://127.0.0.1:8765", readme)

    def test_page_has_no_inline_script_and_only_local_assets(self):
        from _helpers import REPO
        html = (REPO / "godotai" / "chat" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn("http://", html.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", html)
        self.assertNotRegex(html, r"\son[a-z]+=", "no inline event handlers (CSP)")


class CliChatTests(unittest.TestCase):
    def test_chat_check_mode_prints_url_and_readiness(self):
        tmp = Path(tempfile.mkdtemp(prefix="godotai-cli-chat-"))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "GODOTAI_SKIP_MODEL_PROBE": "1"}), redirect_stdout(out):
            rc = cli.main(["chat", "--port", "0", "--games-dir", str(tmp / "games"), "--check"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("Open this URL in your browser: http://127.0.0.1:", text)
        self.assertIn("افتح هذا العنوان", text)
        self.assertIn("➖ your model server (http://127.0.0.1:8000/v1): not probed", text,
                      "a skipped probe must not be shown as a pass")
        self.assertIn("model identity: godotai — private deployment of an open-weight model (base Qwen/Qwen2.5-Coder-7B-Instruct, Apache-2.0)", text)
        self.assertIn("Godot 4.7.2-stable", text)
        self.assertTrue((tmp / "games").is_dir())
        # the vendor opt-in still prints the key line
        out = io.StringIO()
        env = {**PRIVATE_ENV, "GODOTAI_PROVIDER": "anthropic", "GODOTAI_MODEL": "claude-fable-5-1", "GODOTAI_MODEL_KIND": "vendor_api",
               "GODOTAI_SERVING": "vendor", "GODOTAI_BASE_MODEL": "claude-fable-5-1", "ANTHROPIC_API_KEY": ""}
        with mock.patch.dict(os.environ, env), redirect_stdout(out):
            rc = cli.main(["chat", "--port", "0", "--games-dir", str(tmp / "games"), "--check"])
        self.assertEqual(rc, 0)
        self.assertIn("model key (ANTHROPIC_API_KEY): not set", out.getvalue())
        self.assertIn("third-party vendor model via API", out.getvalue())

    def test_chat_public_host_and_access_flags(self):
        tmp = Path(tempfile.mkdtemp(prefix="godotai-cli-chat-"))
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(io.StringIO()):
                cli.main(["chat", "--port", "0", "--games-dir", str(tmp), "--access-team-domain", "acme", "--check"])
        self.assertIn("GODOTAI_ACCESS_AUD", str(cm.exception), "team domain without AUD is refused")
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(io.StringIO()):
                cli.main(["chat", "--port", "0", "--games-dir", str(tmp), "--public-host", "http://bad host", "--check"])
        self.assertIn("hostname", str(cm.exception))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "GODOTAI_SKIP_MODEL_PROBE": "1"}), redirect_stdout(out):
            rc = cli.main(["chat", "--host", "0.0.0.0", "--port", "0", "--games-dir", str(tmp), "--check",
                           "--public-host", "games.example.com", "--access-team-domain", "acme",
                           "--access-aud", "a" * 64])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("public hostname(s) accepted in Host header: games.example.com", text)
        self.assertIn("Cloudflare Access required on every request — team acme.cloudflareaccess.com", text)
        self.assertNotIn("a" * 64, text, "the AUD tag is only ever shown truncated")

    def test_chat_refuses_public_bind_without_token(self):
        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(io.StringIO()):
                cli.main(["chat", "--host", "0.0.0.0", "--port", "0", "--games-dir", tempfile.mkdtemp()])
        self.assertIn("token", str(cm.exception))

    def test_help_lists_chat(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stdout(out):
                cli.main(["--help"])
        self.assertIn("chat", out.getvalue())

    def test_ctrl_c_in_serve_loop_exits_cleanly(self):
        """Ctrl+C lands inside serve_forever() on the main thread; the CLI must stop without deadlocking."""
        tmp = Path(tempfile.mkdtemp(prefix="godotai-cli-chat-"))
        out = io.StringIO()

        def interrupted(self_):
            # mimic what KeyboardInterrupt does to ChatServer.serve_forever: the loop ends, flags are reset
            self_._serving.set()
            self_._serving.clear()
            self_.stopping.set()
            raise KeyboardInterrupt

        with mock.patch.object(ChatServer, "serve_forever", interrupted):
            done: list[int] = []
            t = threading.Thread(target=lambda: done.append(
                cli.main(["chat", "--port", "0", "--games-dir", str(tmp / "games")])), daemon=True)
            with redirect_stdout(out):
                t.start()
                t.join(10.0)
        self.assertFalse(t.is_alive(), "cmd_chat hung after KeyboardInterrupt")
        self.assertEqual(done, [0])
        self.assertIn("stopping", out.getvalue())


class LifecycleTests(unittest.TestCase):
    """shutdown() must never block, whichever way the serve loop was (or was not) started."""

    def _server(self) -> ChatServer:
        cfg = repo_config()
        return ChatServer(cfg, Path(tempfile.mkdtemp(prefix="godotai-life-")) / "games", port=0)

    def _shutdown_promptly(self, server: ChatServer) -> None:
        t = threading.Thread(target=server.shutdown, daemon=True)
        t.start()
        t.join(5.0)
        self.assertFalse(t.is_alive(), "ChatServer.shutdown() blocked")

    def test_shutdown_without_serving(self):                   # the --check path
        self._shutdown_promptly(self._server())

    def test_shutdown_while_serving_in_thread(self):           # the tests' / embedders' path
        server = self._server()
        t = server.start_in_thread()
        self.assertTrue(server._serving.wait(5.0))
        self._shutdown_promptly(server)
        t.join(5.0)
        self.assertFalse(t.is_alive())
        self.assertTrue(server.stopping.is_set())

    def test_shutdown_after_loop_already_ended(self):          # the Ctrl+C-on-main-thread path
        server = self._server()
        t = server.start_in_thread()
        self.assertTrue(server._serving.wait(5.0))
        server.httpd.shutdown()                                # loop ends the way KeyboardInterrupt ends it
        t.join(5.0)
        self.assertFalse(t.is_alive())
        self.assertFalse(server._serving.is_set())
        self._shutdown_promptly(server)                        # must not call httpd.shutdown() again / block
        with self.assertRaises(OSError):
            http.client.HTTPConnection("127.0.0.1", server.port, timeout=1).request("GET", "/api/status")

    def test_shutdown_is_idempotent(self):
        server = self._server()
        server.start_in_thread()
        self.assertTrue(server._serving.wait(5.0))
        self._shutdown_promptly(server)
        self._shutdown_promptly(server)


if __name__ == "__main__":
    unittest.main()
