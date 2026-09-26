"""Run quotas (godotai/chat/quota.py): the cost/abuse brake in front of the model server — offline.

Unit tests drive :class:`RunQuota` with a fake clock; the server tests run the real HTTP server with the
scripted model and check that one message too many is answered with **429 + Retry-After** *before* any
agent run starts, that a reservation is given back when the session turns out to be busy, and that the
status payload tells the page what the limits are.
"""
from __future__ import annotations

import http.client
import unittest
from unittest import mock

from _helpers import repo_config, valid_plan
from test_chat import ServerFixture, ScriptedRuns, wait_until

from godotai.chat import QuotaExceeded, RunQuota, identity_of
from godotai.chat import quota as quota_mod
from godotai.chat.session import SessionError


class FakeClock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class RunQuotaUnitTests(unittest.TestCase):
    def test_unlimited_by_default_and_disabled_flag(self):
        q = RunQuota()
        self.assertFalse(q.enabled)
        for _ in range(50):
            q.reserve("a@example.com", running=99)
        self.assertEqual(q.used_today("a@example.com"), 50)

    def test_concurrency_limit_is_server_wide(self):
        q = RunQuota(max_concurrent=1)
        q.reserve("a@example.com", running=0)
        with self.assertRaises(QuotaExceeded) as cm:
            q.reserve("b@example.com", running=1)
        self.assertEqual(cm.exception.status, 429)
        self.assertEqual(cm.exception.kind, "concurrent")
        self.assertGreaterEqual(cm.exception.retry_after, 1)
        self.assertIn("مشغول", cm.exception.hint)
        self.assertIsInstance(cm.exception, SessionError, "the server maps it like every other session error")

    def test_daily_limit_is_per_identity_and_rolls_over(self):
        clock = FakeClock()
        q = RunQuota(per_day=2, clock=clock)
        q.reserve("a@example.com", 0)
        clock.t += 3600
        q.reserve("a@example.com", 0)
        q.reserve("b@example.com", 0)                       # another visitor has their own budget
        with self.assertRaises(QuotaExceeded) as cm:
            q.reserve("a@example.com", 0)
        self.assertEqual(cm.exception.kind, "daily")
        self.assertEqual(cm.exception.retry_after, 24 * 3600 - 3600, "wait until the oldest start leaves the window")
        self.assertIn("الحد اليومي", cm.exception.hint)
        clock.t += 24 * 3600 - 3600                        # the first start is now exactly 24 h old → freed
        q.reserve("a@example.com", 0)
        self.assertEqual(q.used_today("a@example.com"), 2)

    def test_release_gives_the_slot_back(self):
        q = RunQuota(per_day=1)
        stamp = q.reserve("a@example.com", 0)
        q.release("a@example.com", stamp)
        self.assertEqual(q.used_today("a@example.com"), 0)
        q.release("nobody", 1.0)                           # unknown identity / stamp: no error
        q.reserve("a@example.com", 0)

    def test_identity_table_is_bounded(self):
        q = RunQuota(per_day=5)
        with mock.patch.object(quota_mod, "MAX_IDENTITIES", 3):
            for i in range(10):
                q.reserve(f"user{i}@example.com", 0)
            self.assertLessEqual(len(q._starts), 3)

    def test_identity_of(self):
        self.assertEqual(identity_of({"email": " You@Example.com "}, False), "you@example.com")
        self.assertEqual(identity_of({"sub": "abc"}, False), "abc")
        self.assertEqual(identity_of({"email": ""}, True), "access:unknown")
        self.assertEqual(identity_of(None, True), "token")
        self.assertEqual(identity_of(None, False), "local")

    def test_from_env_and_validation(self):
        self.assertIsNone(quota_mod.from_env(env={}))
        self.assertIsNone(quota_mod.from_env(env={"GODOTAI_MAX_CONCURRENT_RUNS": "0", "GODOTAI_MAX_RUNS_PER_DAY": ""}))
        q = quota_mod.from_env(env={"GODOTAI_MAX_CONCURRENT_RUNS": "2", "GODOTAI_MAX_RUNS_PER_DAY": "40"})
        self.assertEqual((q.max_concurrent, q.per_day), (2, 40))
        q = quota_mod.from_env(env={"GODOTAI_MAX_RUNS_PER_DAY": "40"}, max_concurrent=1)
        self.assertEqual((q.max_concurrent, q.per_day), (1, 40), "explicit flags win over the environment")
        for bad in ({"GODOTAI_MAX_RUNS_PER_DAY": "many"}, {"GODOTAI_MAX_CONCURRENT_RUNS": "-1"}):
            with self.assertRaises(ValueError):
                quota_mod.from_env(env=bad)
        with self.assertRaises(ValueError):
            RunQuota(max_concurrent=-1)


class ServerQuotaTests(unittest.TestCase):
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

    def plan_only_run(self):
        return [{"calls": [("submit_plan", self.plan)]}]     # stops at the approval gate → state awaiting_approval

    def test_status_reports_the_limits_and_usage(self):
        fx = self.start(self.plan_only_run(), quota=RunQuota(max_concurrent=1, per_day=3))
        st, js = fx.get("/api/status")
        self.assertEqual(st, 200)
        self.assertEqual(js["quota"], {"max_concurrent": 1, "per_day": 3, "enabled": True, "running": 0, "used_today": 0})
        fx.post("/api/sessions", {"name": "a"})
        st, _ = fx.post("/api/sessions/a/messages", {"text": "make a game"})
        self.assertEqual(st, 202)
        self.assertTrue(wait_until(lambda: fx.get("/api/sessions/a")[1]["state"] == "awaiting_approval"))
        st, js = fx.get("/api/status")
        self.assertEqual(js["quota"]["used_today"], 1)
        self.assertEqual(js["quota"]["running"], 0, "a plan waiting for approval is not an in-flight model run")

    def test_no_quota_means_no_quota_field(self):
        fx = self.start()
        st, js = fx.get("/api/status")
        self.assertIsNone(js["quota"])

    def test_daily_quota_answers_429_with_retry_after_before_any_run_starts(self):
        fx = self.start(self.plan_only_run(), quota=RunQuota(per_day=1))
        fx.post("/api/sessions", {"name": "a"})
        fx.post("/api/sessions", {"name": "b"})
        st, _ = fx.post("/api/sessions/a/messages", {"text": "first"})
        self.assertEqual(st, 202)
        # raw request to read the headers
        conn = http.client.HTTPConnection("127.0.0.1", fx.port, timeout=10)
        conn.request("POST", "/api/sessions/b/messages", body=b'{"text": "second"}',
                     headers={"Content-Type": "application/json", "Host": f"127.0.0.1:{fx.port}"})
        resp = conn.getresponse()
        payload = resp.read()
        self.assertEqual(resp.status, 429, payload)
        self.assertTrue(resp.getheader("Retry-After", "").isdigit())
        self.assertGreater(int(resp.getheader("Retry-After")), 0)
        conn.close()
        st, snap = fx.get("/api/sessions/b")
        self.assertEqual(snap["state"], "idle", "the refused message never started a run")
        self.assertEqual(snap["history"], [], "nothing was recorded for the refused message")

    def test_concurrency_limit_answers_429_while_a_run_is_in_flight(self):
        fx = self.start(quota=RunQuota(max_concurrent=1))
        fx.post("/api/sessions", {"name": "a"})
        with mock.patch.object(fx.server.manager, "running_count", return_value=1):
            st, js = fx.post("/api/sessions/a/messages", {"text": "while another run is busy"})
        self.assertEqual(st, 429, js)
        self.assertIn("server busy", js["error"])
        self.assertIn("مشغول", js["hint"])
        self.assertEqual(fx.get("/api/sessions/a")[1]["state"], "idle")

    def test_busy_session_gives_the_reservation_back(self):
        fx = self.start(self.plan_only_run(), quota=RunQuota(per_day=2))
        fx.post("/api/sessions", {"name": "a"})
        st, _ = fx.post("/api/sessions/a/messages", {"text": "first"})
        self.assertEqual(st, 202)
        self.assertTrue(wait_until(lambda: fx.get("/api/sessions/a")[1]["state"] == "awaiting_approval"))
        st, js = fx.post("/api/sessions/a/messages", {"text": "again while waiting"})
        self.assertEqual(st, 409, js)                       # SessionBusy — not a quota decision
        self.assertEqual(fx.get("/api/status")[1]["quota"]["used_today"], 1, "the busy attempt did not consume quota")

    def test_cli_flags_and_env_build_the_quota(self):
        import io
        import os
        import tempfile
        from contextlib import redirect_stdout
        from godotai import __main__ as cli
        tmp = tempfile.mkdtemp()
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GODOTAI_SKIP_MODEL_PROBE": "1", "GODOTAI_MAX_RUNS_PER_DAY": "40"}), redirect_stdout(out):
            rc = cli.main(["chat", "--port", "0", "--games-dir", tmp, "--check", "--max-concurrent-runs", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("run quota: max 1 concurrent run(s), 40 run(s) per visitor per 24 h", out.getvalue())
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GODOTAI_SKIP_MODEL_PROBE": "1", "GODOTAI_MAX_RUNS_PER_DAY": ""}), redirect_stdout(out):
            rc = cli.main(["chat", "--port", "0", "--games-dir", tmp, "--check"])
        self.assertEqual(rc, 0)
        self.assertNotIn("run quota", out.getvalue(), "loopback without limits: no quota line, no warning")
        with mock.patch.dict(os.environ, {"GODOTAI_SKIP_MODEL_PROBE": "1", "GODOTAI_MAX_RUNS_PER_DAY": "lots"}):
            with self.assertRaises(SystemExit) as cm:
                cli.main(["chat", "--port", "0", "--games-dir", tmp, "--check"])
        self.assertIn("must be an integer", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
