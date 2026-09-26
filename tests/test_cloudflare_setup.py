"""Offline tests for scripts/cloudflare_setup.py — a fake transport stands in for the Cloudflare API.

No network, no real account/zone id, no real token: the ids below are obviously synthetic (repeating hex),
and the "token" strings are throw-away test values (marked secret-scan:allow where their shape could trip the scan).
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import REPO


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


cf = load_module(REPO / "scripts" / "cloudflare_setup.py", "cloudflare_setup")

ACCOUNT = "0123456789abcdef0123456789abcdef"      # synthetic — not anybody's account id
ZONE = "fedcba9876543210fedcba9876543210"         # synthetic
TUNNEL_ID = "11111111-2222-3333-4444-555555555555"
FAKE_API_TOKEN = "unit-test-api-token-value-not-real-000000"          # secret-scan:allow
FAKE_TUNNEL_TOKEN = "eyJhIjoidW5pdC10ZXN0Iiwi" + "dCI6Im5vdC1hLXJlYWwtdHVubmVsIn0"  # secret-scan:allow
FAKE_AUD = "aud" + "0" * 61


def make_plan(**kw) -> "cf.Plan":
    args = dict(account_id=ACCOUNT, zone_id=ZONE, hostname="games.example.com", team_domain="myteam",
                emails=["you@example.com"])
    args.update(kw)
    return cf.Plan(**args)


class FakeCloudflare:
    """Records every call; answers like the real API (``success``/``result``) unless told to fail."""

    def __init__(self, fail_on: str | None = None, token_as_string: bool = False):
        self.calls: list[tuple[str, str, dict | None, str]] = []
        self.fail_on = fail_on
        self.token_as_string = token_as_string

    def __call__(self, method: str, url: str, body: dict | None, token: str) -> dict:
        self.calls.append((method, url, body, token))
        if self.fail_on and self.fail_on in url:
            return {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}], "result": None}
        if url.endswith("/access/apps"):
            return {"success": True, "result": {"id": "app-id-1", "aud": FAKE_AUD}}
        if url.endswith("/cfd_tunnel"):
            return {"success": True, "result": {"id": TUNNEL_ID, "name": "godotai-chat", "token": FAKE_TUNNEL_TOKEN}}
        if url.endswith("/token"):
            return {"success": True, "result": FAKE_TUNNEL_TOKEN if self.token_as_string else {"token": FAKE_TUNNEL_TOKEN}}
        if url.endswith("/configurations"):
            return {"success": True, "result": body}
        if url.endswith("/dns_records"):
            return {"success": True, "result": {"id": "dns-1", **(body or {})}}
        raise AssertionError(f"unexpected call {method} {url}")


class PlanValidationTests(unittest.TestCase):
    def test_valid_inputs_are_normalised(self):
        p = make_plan(hostname="  Games.Example.COM ", team_domain="https://MyTeam.cloudflareaccess.com/",
                      emails=[" You@Example.com ", ""])
        self.assertEqual(p.hostname, "games.example.com")
        self.assertEqual(p.team, "myteam")
        self.assertEqual(p.emails, ["you@example.com"])

    def test_rejects_bad_ids_hostnames_teams_emails(self):
        cases = [
            dict(account_id="not-an-id"), dict(account_id=ACCOUNT[:-1]), dict(zone_id="ZONE"),
            dict(hostname="localhost"), dict(hostname="http://games.example.com"), dict(hostname="games.example.com/x"),
            dict(team_domain="my team"), dict(team_domain=""), dict(emails=[]), dict(emails=["nobody"]),
            dict(origin="chat:8765"), dict(origin="http://chat:8765/path"), dict(session="soon"),
        ]
        for kw in cases:
            with self.assertRaises(cf.SetupError, msg=str(kw)) as cm:
                make_plan(**kw)
            self.assertNotIn(FAKE_API_TOKEN, str(cm.exception))

    def test_steps_are_in_the_documented_order_with_safe_bodies(self):
        p = make_plan(emails=["a@example.com", "b@example.com"])
        steps = p.steps()
        self.assertEqual([s[1] for s in steps], ["POST", "POST", "PUT", "POST"])
        self.assertEqual([s[2] for s in steps], [
            f"/accounts/{ACCOUNT}/access/apps", f"/accounts/{ACCOUNT}/cfd_tunnel",
            f"/accounts/{ACCOUNT}/cfd_tunnel/<tunnel id>/configurations", f"/zones/{ZONE}/dns_records"])
        app = steps[0][3]
        self.assertEqual(app["type"], "self_hosted")
        self.assertEqual(app["domain"], "games.example.com")
        self.assertEqual(app["policies"][0]["decision"], "allow")
        self.assertEqual(app["policies"][0]["include"],
                         [{"email": {"email": "a@example.com"}}, {"email": {"email": "b@example.com"}}])
        self.assertFalse(app["app_launcher_visible"])
        self.assertEqual(steps[1][3], {"name": "godotai-chat", "config_src": "cloudflare"})
        ingress = steps[2][3]["config"]["ingress"]
        self.assertEqual(ingress[-1], {"service": "http_status:404"}, "catch-all rule must be last")
        self.assertEqual(ingress[0]["hostname"], "games.example.com")
        self.assertEqual(ingress[0]["service"], "http://chat:8765")
        self.assertEqual(ingress[0]["originRequest"]["access"], {"required": True, "teamName": "myteam", "audTag": ["<aud tag>"]})
        dns = steps[3][3]
        self.assertEqual((dns["type"], dns["proxied"], dns["name"], dns["content"]),
                         ("CNAME", True, "games.example.com", "<tunnel id>.cfargotunnel.com"))
        # nothing secret-shaped in any planned body
        for step in steps:
            self.assertNotIn("token", json.dumps(step[3]).lower())

    def test_ingress_without_aud_has_no_access_block(self):
        rule = make_plan().ingress(TUNNEL_ID, None)[3]["config"]["ingress"][0]
        self.assertEqual(rule["originRequest"], {})


class RunTests(unittest.TestCase):
    def test_live_order_bodies_and_facts(self):
        api = FakeCloudflare()
        lines: list[str] = []
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, out=lines.append)
        methods_and_paths = [(m, u.replace(cf.API, "")) for m, u, _, _ in api.calls]
        self.assertEqual(methods_and_paths, [
            ("POST", f"/accounts/{ACCOUNT}/access/apps"),
            ("POST", f"/accounts/{ACCOUNT}/cfd_tunnel"),
            ("PUT", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/configurations"),
            ("POST", f"/zones/{ZONE}/dns_records"),
        ])
        self.assertTrue(all(tok == FAKE_API_TOKEN for *_, tok in api.calls), "the API token goes to the transport only")
        ingress_rule = api.calls[2][2]["config"]["ingress"][0]
        self.assertEqual(ingress_rule["originRequest"]["access"]["audTag"], [FAKE_AUD], "the fresh AUD protects the route")
        self.assertEqual(api.calls[3][2]["content"], f"{TUNNEL_ID}.cfargotunnel.com")
        self.assertEqual(facts["GODOTAI_PUBLIC_HOST"], "games.example.com")
        self.assertEqual(facts["GODOTAI_ACCESS_TEAM_DOMAIN"], "myteam.cloudflareaccess.com")
        self.assertEqual(facts["GODOTAI_ACCESS_AUD"], FAKE_AUD)
        self.assertEqual(facts["CF_ACCOUNT_ID"], ACCOUNT)
        self.assertEqual(facts["TUNNEL_ID"], TUNNEL_ID)
        self.assertEqual(facts["ACCESS_APP_ID"], "app-id-1")
        self.assertEqual(facts["TUNNEL_TOKEN"], FAKE_TUNNEL_TOKEN)
        joined = "\n".join(lines)
        self.assertEqual(len(lines), 4)
        self.assertNotIn(FAKE_TUNNEL_TOKEN, joined, "the tunnel token is never printed")
        self.assertNotIn(FAKE_API_TOKEN, joined)
        self.assertIn("you@example.com", joined)

    def test_api_failure_raises_without_leaking_tokens(self):
        api = FakeCloudflare(fail_on="/cfd_tunnel")
        with self.assertRaises(cf.SetupError) as cm:
            cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None)
        msg = str(cm.exception)
        self.assertIn("Cloudflare Tunnel failed", msg)
        self.assertIn("10000: Authentication error", msg)
        self.assertNotIn(FAKE_API_TOKEN, msg)
        self.assertEqual(len(api.calls), 2, "stops at the failing step — no ingress/DNS for a tunnel that does not exist")

    def test_reuse_tunnel_and_aud_skip_creation_and_fetch_the_token(self):
        for as_string in (False, True):
            api = FakeCloudflare(token_as_string=as_string)
            lines: list[str] = []
            facts = cf.run(make_plan(), FAKE_API_TOKEN, api, reuse_tunnel=TUNNEL_ID, reuse_aud=FAKE_AUD, out=lines.append)
            self.assertEqual([(m, u.replace(cf.API, "")) for m, u, _, _ in api.calls], [
                ("GET", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/token"),
                ("PUT", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/configurations"),
                ("POST", f"/zones/{ZONE}/dns_records"),
            ])
            self.assertIsNone(api.calls[0][2], "GET carries no body")
            self.assertEqual(facts["TUNNEL_TOKEN"], FAKE_TUNNEL_TOKEN)
            self.assertEqual(facts["GODOTAI_ACCESS_AUD"], FAKE_AUD)
            self.assertNotIn("ACCESS_APP_ID", facts)
            self.assertNotIn(FAKE_TUNNEL_TOKEN, "\n".join(lines))
            self.assertIn(FAKE_AUD[:8], "\n".join(lines))
            self.assertNotIn(FAKE_AUD, "\n".join(lines), "only a prefix of the AUD is echoed")

    def test_reuse_tunnel_must_be_a_uuid(self):
        with self.assertRaises(cf.SetupError):
            cf.run(make_plan(), FAKE_API_TOKEN, FakeCloudflare(), reuse_tunnel="godotai-chat", out=lambda s: None)

    def test_missing_tunnel_id_is_an_error(self):
        def api(method, url, body, token):
            if url.endswith("/access/apps"):
                return {"success": True, "result": {"id": "a", "aud": FAKE_AUD}}
            return {"success": True, "result": {}}
        with self.assertRaises(cf.SetupError) as cm:
            cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None)
        self.assertIn("no id", str(cm.exception))

    def test_unexpected_result_shape_is_an_error(self):
        def api(method, url, body, token):
            return {"success": True, "result": "surprise"}
        with self.assertRaises(cf.SetupError) as cm:
            cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None)
        self.assertIn("unexpected response shape", str(cm.exception))


class WriteEnvTests(unittest.TestCase):
    def test_env_file_is_owner_only_and_holds_only_known_keys(self):
        d = Path(tempfile.mkdtemp())
        target = d / "nested" / ".env"
        facts = {"GODOTAI_PUBLIC_HOST": "games.example.com", "GODOTAI_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
                 "GODOTAI_ACCESS_AUD": FAKE_AUD, "CF_ACCOUNT_ID": ACCOUNT, "TUNNEL_ID": TUNNEL_ID, "ACCESS_APP_ID": "",
                 "TUNNEL_TOKEN": FAKE_TUNNEL_TOKEN, "UNRELATED": "ignored"}
        cf.write_env(target, facts)
        mode = stat.S_IMODE(target.stat().st_mode)
        self.assertEqual(mode, 0o600, oct(mode))
        text = target.read_text(encoding="utf-8")
        keys = [line.split("=", 1)[0] for line in text.splitlines() if line and not line.startswith("#")]
        self.assertEqual(keys, ["GODOTAI_PUBLIC_HOST", "GODOTAI_ACCESS_TEAM_DOMAIN", "GODOTAI_ACCESS_AUD", "CF_ACCOUNT_ID",
                                "TUNNEL_ID", "TUNNEL_TOKEN"], "empty and unknown facts are not written")
        self.assertIn(f"TUNNEL_TOKEN={FAKE_TUNNEL_TOKEN}", text)
        self.assertTrue(text.startswith("#"), "starts with the keep-out-of-git comment")
        # overwrite keeps the permissions and truncates
        cf.write_env(target, {"GODOTAI_PUBLIC_HOST": "other.example.com"})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertNotIn("TUNNEL_TOKEN", target.read_text(encoding="utf-8"))


class CliTests(unittest.TestCase):
    ARGS = ["--hostname", "games.example.com", "--team-domain", "myteam", "--emails", "you@example.com,a@example.com",
            "--account-id", ACCOUNT, "--zone-id", ZONE]

    def run_main(self, argv, transport=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cf.main(argv, transport=transport, env={} if env is None else env)
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_needs_no_token_and_makes_no_call(self):
        def explode(*a):
            raise AssertionError("dry-run must not touch the transport")
        code, out, err = self.run_main(["--dry-run", *self.ARGS], transport=explode, env={})
        self.assertEqual(code, 0, err)
        self.assertIn("DRY RUN", out)
        self.assertIn(f"POST {cf.API}/accounts/{ACCOUNT}/access/apps", out)
        self.assertIn(f"PUT {cf.API}/accounts/{ACCOUNT}/cfd_tunnel/<tunnel id>/configurations", out)
        self.assertIn(f"POST {cf.API}/zones/{ZONE}/dns_records", out)
        self.assertIn("http_status:404", out)
        self.assertIn("docker compose -f deploy/cloudflare/compose.yml", out)
        self.assertIn("you@example.com, a@example.com", out)
        self.assertEqual(out.count("\n1. "), 1)
        self.assertLess(out.index("Access application"), out.index("Cloudflare Tunnel"), "Access app is planned first")

    def test_dry_run_reads_the_documented_environment_names(self):
        env = {"GODOTAI_PUBLIC_HOST": "games.example.com", "GODOTAI_ACCESS_TEAM_DOMAIN": "myteam",
               "GODOTAI_ACCESS_EMAILS": "you@example.com", "CF_ACCOUNT_ID": ACCOUNT, "CF_ZONE_ID": ZONE}
        with mock.patch.dict(os.environ, env, clear=False):
            code, out, err = self.run_main(["--dry-run"], transport=None, env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("games.example.com", out)

    def test_invalid_input_is_a_clean_error(self):
        code, out, err = self.run_main(["--dry-run", *self.ARGS[:-2], "--zone-id", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("cloudflare-setup: CF_ZONE_ID must be", err)
        self.assertEqual(out, "")

    def test_live_run_requires_token_and_write_env(self):
        api = FakeCloudflare()
        code, out, err = self.run_main(self.ARGS, transport=api, env={})
        self.assertEqual(code, 1)
        self.assertIn("CLOUDFLARE_API_TOKEN is not set", err)
        self.assertIn("roll it first", err)
        self.assertEqual(api.calls, [], "no call without a token")
        code, out, err = self.run_main(self.ARGS, transport=api, env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("--write-env PATH is required", err)
        self.assertEqual(api.calls, [], "no call without a destination for the tunnel token")
        self.assertNotIn(FAKE_API_TOKEN, out + err)

    def test_live_run_with_fake_transport_writes_env_and_prints_no_secret(self):
        api = FakeCloudflare()
        d = Path(tempfile.mkdtemp())
        env_path = d / ".env"
        code, out, err = self.run_main([*self.ARGS, "--write-env", str(env_path)], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": f"  {FAKE_API_TOKEN}\n"})
        self.assertEqual(code, 0, err)
        self.assertEqual(len(api.calls), 4)
        self.assertEqual(api.calls[0][3], FAKE_API_TOKEN, "token is stripped before use")
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        self.assertIn(f"TUNNEL_TOKEN={FAKE_TUNNEL_TOKEN}", env_path.read_text(encoding="utf-8"))
        self.assertNotIn(FAKE_TUNNEL_TOKEN, out + err)
        self.assertNotIn(FAKE_API_TOKEN, out + err)
        self.assertIn("do not commit, do not paste anywhere", out)
        self.assertIn("https://games.example.com/", out)

    def test_live_failure_exits_1_and_writes_nothing(self):
        api = FakeCloudflare(fail_on="/access/apps")
        d = Path(tempfile.mkdtemp())
        env_path = d / ".env"
        code, out, err = self.run_main([*self.ARGS, "--write-env", str(env_path)], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("Access application + allow policy failed", err)
        self.assertFalse(env_path.exists())
        self.assertEqual(len(api.calls), 1)


class HttpTransportTests(unittest.TestCase):
    """The real transport is exercised against a mocked urlopen — no network."""

    def test_request_shape_and_json_decoding(self):
        seen = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"success": True, "result": {"id": "x"}}).encode()

        def fake_urlopen(req, timeout=0):
            seen["req"] = req
            seen["timeout"] = timeout
            return Resp()

        with mock.patch.object(cf.urllib.request, "urlopen", fake_urlopen):
            reply = cf.http_transport("PUT", cf.API + "/x", {"a": 1}, FAKE_API_TOKEN)
        req = seen["req"]
        self.assertEqual(reply, {"success": True, "result": {"id": "x"}})
        self.assertEqual(req.get_method(), "PUT")
        self.assertEqual(req.get_header("Authorization"), f"Bearer {FAKE_API_TOKEN}")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(req.data.decode()), {"a": 1})
        self.assertEqual(seen["timeout"], 30)

    def test_http_error_with_json_body_is_returned_for_a_readable_message(self):
        import urllib.error

        def fake_urlopen(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", hdrs=None, fp=io.BytesIO(
                json.dumps({"success": False, "errors": [{"code": 9109, "message": "Unauthorized to access requested resource"}]}).encode()))

        with mock.patch.object(cf.urllib.request, "urlopen", fake_urlopen):
            reply = cf.http_transport("GET", cf.API + "/x", None, FAKE_API_TOKEN)
        with self.assertRaises(cf.SetupError) as cm:
            cf._result(reply, "probe")
        self.assertIn("9109: Unauthorized", str(cm.exception))

    def test_network_error_never_includes_the_token(self):
        import urllib.error

        def fake_urlopen(req, timeout=0):
            raise urllib.error.URLError("name resolution failed")

        with mock.patch.object(cf.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(cf.SetupError) as cm:
                cf.http_transport("GET", cf.API + "/x", None, FAKE_API_TOKEN)
        self.assertIn("name resolution failed", str(cm.exception))
        self.assertNotIn(FAKE_API_TOKEN, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
