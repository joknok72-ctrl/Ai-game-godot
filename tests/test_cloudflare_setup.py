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

# the read-only lookups the idempotent default makes (query strings are part of the URL)
LOOKUP_APPS = f"/accounts/{ACCOUNT}/access/apps?domain=games.example.com"
LOOKUP_TUNNELS = f"/accounts/{ACCOUNT}/cfd_tunnel?name=godotai-chat&is_deleted=false"
LOOKUP_DNS = f"/zones/{ZONE}/dns_records?type=CNAME&name=games.example.com"
VERIFY = "/user/tokens/verify"


def make_plan(**kw) -> "cf.Plan":
    args = dict(account_id=ACCOUNT, zone_id=ZONE, hostname="games.example.com", team_domain="myteam",
                emails=["you@example.com"])
    args.update(kw)
    return cf.Plan(**args)


def trail(api: "FakeCloudflare") -> list[tuple[str, str]]:
    """(method, path-with-query) for every call, API prefix stripped."""
    return [(m, u.replace(cf.API, "")) for m, u, _, _ in api.calls]


class FakeCloudflare:
    """Records every call; answers like the real API (``success``/``result``) unless told to fail.

    ``fail_on`` is a substring of the path or a ``(method, substring)`` pair. ``existing`` pre-populates the
    lookups: ``{"apps": [...], "tunnels": [...], "dns": [...]}``. ``token_status`` is what
    ``GET /user/tokens/verify`` reports.
    """

    def __init__(self, fail_on: str | tuple[str, str] | None = None, token_as_string: bool = False,
                 existing: dict | None = None, token_status: str = "active"):
        self.calls: list[tuple[str, str, dict | None, str]] = []
        self.fail_on = fail_on
        self.token_as_string = token_as_string
        self.existing = existing or {}
        self.token_status = token_status

    def _fails(self, method: str, path: str) -> bool:
        if not self.fail_on:
            return False
        if isinstance(self.fail_on, tuple):
            return method == self.fail_on[0] and self.fail_on[1] in path
        return self.fail_on in path

    def __call__(self, method: str, url: str, body: dict | None, token: str) -> dict:
        self.calls.append((method, url, body, token))
        path = url.replace(cf.API, "").split("?", 1)[0]
        if self._fails(method, path):
            return {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}], "result": None}
        if path == VERIFY:
            return {"success": True, "result": {"id": "token-id-1", "status": self.token_status}}
        if method == "GET" and path.endswith("/access/apps"):
            return {"success": True, "result": list(self.existing.get("apps", []))}
        if method == "GET" and path.endswith("/cfd_tunnel"):
            return {"success": True, "result": list(self.existing.get("tunnels", []))}
        if method == "GET" and path.endswith("/dns_records"):
            return {"success": True, "result": list(self.existing.get("dns", []))}
        if method == "PATCH" and "/dns_records/" in path:
            return {"success": True, "result": {"id": path.rsplit("/", 1)[1], **(body or {})}}
        if method == "POST" and path.endswith("/access/apps"):
            return {"success": True, "result": {"id": "app-id-1", "aud": FAKE_AUD}}
        if method == "POST" and path.endswith("/cfd_tunnel"):
            return {"success": True, "result": {"id": TUNNEL_ID, "name": "godotai-chat", "token": FAKE_TUNNEL_TOKEN}}
        if method == "GET" and path.endswith("/token"):
            return {"success": True, "result": FAKE_TUNNEL_TOKEN if self.token_as_string else {"token": FAKE_TUNNEL_TOKEN}}
        if method == "PUT" and path.endswith("/configurations"):
            return {"success": True, "result": body}
        if method == "POST" and path.endswith("/dns_records"):
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

    def test_lookups_are_read_only_and_url_encoded(self):
        p = make_plan(hostname="games.example.com", tunnel_name="godotai chat & co")
        checks = p.checks()
        self.assertEqual([c[1] for c in checks], ["GET", "GET", "GET"])
        self.assertEqual(checks[0][2], f"/accounts/{ACCOUNT}/access/apps?domain=games.example.com")
        self.assertEqual(checks[1][2], f"/accounts/{ACCOUNT}/cfd_tunnel?name=godotai+chat+%26+co&is_deleted=false")
        self.assertEqual(checks[2][2], f"/zones/{ZONE}/dns_records?type=CNAME&name=games.example.com")

    def test_ingress_without_aud_has_no_access_block(self):
        rule = make_plan().ingress(TUNNEL_ID, None)[3]["config"]["ingress"][0]
        self.assertEqual(rule["originRequest"], {})


class RunTests(unittest.TestCase):
    def test_live_order_bodies_and_facts(self):
        api = FakeCloudflare()
        lines: list[str] = []
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, out=lines.append)
        self.assertEqual(trail(api), [
            ("GET", LOOKUP_APPS),
            ("POST", f"/accounts/{ACCOUNT}/access/apps"),
            ("GET", LOOKUP_TUNNELS),
            ("POST", f"/accounts/{ACCOUNT}/cfd_tunnel"),
            ("PUT", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/configurations"),
            ("GET", LOOKUP_DNS),
            ("POST", f"/zones/{ZONE}/dns_records"),
        ], "each create is preceded by a read-only lookup on a fresh account; the create order is Access → tunnel → ingress → DNS")
        self.assertTrue(all(tok == FAKE_API_TOKEN for *_, tok in api.calls), "the API token goes to the transport only")
        self.assertTrue(all(body is None for m, _, body, _ in api.calls if m == "GET"), "GETs carry no body")
        ingress_rule = api.calls[4][2]["config"]["ingress"][0]
        self.assertEqual(ingress_rule["originRequest"]["access"]["audTag"], [FAKE_AUD], "the fresh AUD protects the route")
        self.assertEqual(api.calls[6][2]["content"], f"{TUNNEL_ID}.cfargotunnel.com")
        self.assertEqual(facts["GODOTAI_PUBLIC_HOST"], "games.example.com")
        self.assertEqual(facts["GODOTAI_ACCESS_TEAM_DOMAIN"], "myteam.cloudflareaccess.com")
        self.assertEqual(facts["GODOTAI_ACCESS_AUD"], FAKE_AUD)
        self.assertEqual(facts["CF_ACCOUNT_ID"], ACCOUNT)
        self.assertEqual(facts["TUNNEL_ID"], TUNNEL_ID)
        self.assertEqual(facts["ACCESS_APP_ID"], "app-id-1")
        self.assertEqual(facts["DNS_RECORD_ID"], "dns-1")
        self.assertEqual(facts["TUNNEL_TOKEN"], FAKE_TUNNEL_TOKEN)
        self.assertNotIn("REUSED", facts)
        joined = "\n".join(lines)
        self.assertEqual(len(lines), 4)
        self.assertNotIn(FAKE_TUNNEL_TOKEN, joined, "the tunnel token is never printed")
        self.assertNotIn(FAKE_API_TOKEN, joined)
        self.assertIn("you@example.com", joined)

    def test_without_reuse_existing_exactly_the_four_documented_calls_are_made(self):
        api = FakeCloudflare()
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None, reuse_existing=False)
        self.assertEqual(trail(api), [
            ("POST", f"/accounts/{ACCOUNT}/access/apps"),
            ("POST", f"/accounts/{ACCOUNT}/cfd_tunnel"),
            ("PUT", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/configurations"),
            ("POST", f"/zones/{ZONE}/dns_records"),
        ])
        self.assertEqual(facts["TUNNEL_TOKEN"], FAKE_TUNNEL_TOKEN)

    def test_second_run_reuses_the_access_app_tunnel_and_dns_record(self):
        existing = {
            "apps": [{"id": "other-app", "domain": "other.example.com", "aud": "x" * 64},
                     {"id": "app-id-9", "domain": "GAMES.example.com", "aud": FAKE_AUD}],
            "tunnels": [{"id": "99999999-8888-7777-6666-555555555555", "name": "godotai-chat", "deleted_at": "2026-01-01T00:00:00Z"},
                        {"id": TUNNEL_ID, "name": "godotai-chat", "deleted_at": None}],
            "dns": [{"id": "dns-7", "name": "games.example.com", "type": "CNAME", "content": "old.example.net", "proxied": False}],
        }
        api = FakeCloudflare(existing=existing)
        lines: list[str] = []
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, out=lines.append)
        self.assertEqual(trail(api), [
            ("GET", LOOKUP_APPS),
            ("GET", LOOKUP_TUNNELS),
            ("GET", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/token"),
            ("PUT", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/configurations"),
            ("GET", LOOKUP_DNS),
            ("PATCH", f"/zones/{ZONE}/dns_records/dns-7"),
        ], "nothing is created twice; the stale CNAME is updated in place; the deleted tunnel is ignored")
        patch = api.calls[5][2]
        self.assertEqual((patch["type"], patch["name"], patch["content"], patch["proxied"]),
                         ("CNAME", "games.example.com", f"{TUNNEL_ID}.cfargotunnel.com", True))
        self.assertEqual(facts["ACCESS_APP_ID"], "app-id-9")
        self.assertEqual(facts["GODOTAI_ACCESS_AUD"], FAKE_AUD)
        self.assertEqual(facts["TUNNEL_ID"], TUNNEL_ID)
        self.assertEqual(facts["DNS_RECORD_ID"], "dns-7")
        self.assertEqual(facts["REUSED"], "access_app,tunnel,dns")
        self.assertEqual(facts["TUNNEL_TOKEN"], FAKE_TUNNEL_TOKEN)
        joined = "\n".join(lines)
        self.assertIn("check the allow-list", joined, "a reused app keeps its policies — the operator is told to check them")
        self.assertIn("updated existing record", joined)
        self.assertNotIn(FAKE_TUNNEL_TOKEN, joined)
        self.assertNotIn(FAKE_AUD, joined, "only a prefix of the AUD is echoed")

    def test_correct_existing_dns_record_is_left_alone(self):
        existing = {"dns": [{"id": "dns-7", "name": "games.example.com", "type": "CNAME",
                             "content": f"{TUNNEL_ID}.CFARGOTUNNEL.com", "proxied": True}]}
        api = FakeCloudflare(existing=existing)
        lines: list[str] = []
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, out=lines.append)
        self.assertEqual([m for m, _ in trail(api) if m in ("PATCH",)], [])
        self.assertEqual(trail(api)[-1], ("GET", LOOKUP_DNS), "no POST/PATCH after the lookup")
        self.assertEqual(facts["REUSED"], "dns")
        self.assertIn("already correct", "\n".join(lines))

    def test_api_failure_raises_without_leaking_tokens(self):
        api = FakeCloudflare(fail_on=("POST", "/cfd_tunnel"))
        with self.assertRaises(cf.SetupError) as cm:
            cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None)
        msg = str(cm.exception)
        self.assertIn("Cloudflare Tunnel failed", msg)
        self.assertIn("10000: Authentication error", msg)
        self.assertNotIn(FAKE_API_TOKEN, msg)
        self.assertEqual(trail(api)[-1], ("POST", f"/accounts/{ACCOUNT}/cfd_tunnel"),
                         "stops at the failing step — no ingress/DNS for a tunnel that does not exist")
        self.assertFalse(any(m in ("PUT", "PATCH") or "dns_records" in u for m, u in trail(api)))

    def test_lookup_failure_is_reported_as_such(self):
        api = FakeCloudflare(fail_on=("GET", "/access/apps"))
        with self.assertRaises(cf.SetupError) as cm:
            cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None)
        self.assertIn("existing Access application on this hostname? failed", str(cm.exception))
        self.assertEqual(len(api.calls), 1, "a failing permission check stops everything before any create")

    def test_reuse_tunnel_and_aud_skip_creation_and_fetch_the_token(self):
        for as_string in (False, True):
            api = FakeCloudflare(token_as_string=as_string)
            lines: list[str] = []
            facts = cf.run(make_plan(), FAKE_API_TOKEN, api, reuse_tunnel=TUNNEL_ID, reuse_aud=FAKE_AUD, out=lines.append)
            self.assertEqual(trail(api), [
                ("GET", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/token"),
                ("PUT", f"/accounts/{ACCOUNT}/cfd_tunnel/{TUNNEL_ID}/configurations"),
                ("GET", LOOKUP_DNS),
                ("POST", f"/zones/{ZONE}/dns_records"),
            ], "explicit ids skip the Access/tunnel lookups; only the DNS record is still looked up")
            self.assertIsNone(api.calls[0][2], "GET carries no body")
            self.assertEqual(facts["TUNNEL_TOKEN"], FAKE_TUNNEL_TOKEN)
            self.assertEqual(facts["GODOTAI_ACCESS_AUD"], FAKE_AUD)
            self.assertNotIn("ACCESS_APP_ID", facts)
            self.assertNotIn(FAKE_TUNNEL_TOKEN, "\n".join(lines))
            self.assertIn(FAKE_AUD[:8], "\n".join(lines))
            self.assertNotIn(FAKE_AUD, "\n".join(lines), "only a prefix of the AUD is echoed")

    def test_reuse_tunnel_must_be_a_uuid_and_is_checked_before_any_call(self):
        for bad in ("godotai-chat", TUNNEL_ID[:-1], TUNNEL_ID.replace("-", ""), "11111111-2222-3333-4444-55555555555g"):
            api = FakeCloudflare()
            with self.assertRaises(cf.SetupError, msg=bad) as cm:
                cf.run(make_plan(), FAKE_API_TOKEN, api, reuse_tunnel=bad, out=lambda s: None)
            self.assertIn("tunnel UUID", str(cm.exception))
            self.assertEqual(api.calls, [], "invalid input is rejected before the first API call")
        api = FakeCloudflare()
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, reuse_tunnel=TUNNEL_ID.upper(), out=lambda s: None)
        self.assertEqual(facts["TUNNEL_ID"], TUNNEL_ID, "upper-case UUIDs are accepted and normalised")

    def test_missing_tunnel_id_is_an_error(self):
        def api(method, url, body, token):
            if method == "GET":
                return {"success": True, "result": []}
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
        with self.assertRaises(cf.SetupError) as cm:
            cf.run(make_plan(), FAKE_API_TOKEN, api, out=lambda s: None, reuse_existing=False)
        self.assertIn("unexpected response shape", str(cm.exception))

    def test_missing_tunnel_token_is_a_warning_not_a_secret_leak(self):
        def api(method, url, body, token):
            if method == "GET" and url.endswith("/token"):
                return {"success": True, "result": {}}
            return FakeCloudflare()(method, url, body, token)
        lines: list[str] = []
        facts = cf.run(make_plan(), FAKE_API_TOKEN, api, reuse_tunnel=TUNNEL_ID, reuse_aud=FAKE_AUD, out=lines.append)
        self.assertEqual(facts["TUNNEL_TOKEN"], "")
        self.assertIn("no tunnel token in the response", "\n".join(lines))


class TokenVerificationTests(unittest.TestCase):
    def test_active_token_passes_and_inactive_is_rejected_without_echo(self):
        api = FakeCloudflare()
        self.assertEqual(cf.verify_token(api, FAKE_API_TOKEN), "active")
        self.assertEqual(trail(api), [("GET", VERIFY)])
        self.assertIsNone(api.calls[0][2])
        for status in ("disabled", "expired", ""):
            api = FakeCloudflare(token_status=status)
            with self.assertRaises(cf.SetupError) as cm:
                cf.verify_token(api, FAKE_API_TOKEN)
            self.assertIn("not active", str(cm.exception))
            self.assertNotIn(FAKE_API_TOKEN, str(cm.exception))

    def test_public_facts_never_include_the_tunnel_token(self):
        facts = {"GODOTAI_PUBLIC_HOST": "games.example.com", "GODOTAI_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
                 "GODOTAI_ACCESS_AUD": FAKE_AUD, "CF_ACCOUNT_ID": ACCOUNT, "TUNNEL_ID": TUNNEL_ID, "ACCESS_APP_ID": "",
                 "TUNNEL_TOKEN": FAKE_TUNNEL_TOKEN, "REUSED": "dns", "UNRELATED": "x"}
        pub = cf.public_facts(facts)
        self.assertEqual(set(pub), {"GODOTAI_PUBLIC_HOST", "GODOTAI_ACCESS_TEAM_DOMAIN", "GODOTAI_ACCESS_AUD", "CF_ACCOUNT_ID",
                                    "TUNNEL_ID", "REUSED"}, "empty and unknown keys are dropped; the token is never public")
        self.assertNotIn(FAKE_TUNNEL_TOKEN, json.dumps(pub))
        self.assertNotIn("TUNNEL_TOKEN", cf.NON_SECRET_KEYS)


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
        # the idempotent default also announces its read-only checks and the token verification
        self.assertIn(f"GET {cf.API}{LOOKUP_APPS}", out)
        self.assertIn(f"GET {cf.API}{LOOKUP_TUNNELS}", out)
        self.assertIn(f"GET {cf.API}{LOOKUP_DNS}", out)
        self.assertIn(f"GET {cf.API}{VERIFY}", out)
        code, out2, err = self.run_main(["--dry-run", "--no-reuse-existing", *self.ARGS], transport=explode, env={})
        self.assertEqual(code, 0, err)
        self.assertNotIn("Read-only checks", out2)
        self.assertNotIn("?domain=", out2)

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

    def test_live_run_requires_token_and_a_destination_for_the_tunnel_token(self):
        api = FakeCloudflare()
        code, out, err = self.run_main(self.ARGS, transport=api, env={})
        self.assertEqual(code, 1)
        self.assertIn("CLOUDFLARE_API_TOKEN is not set", err)
        self.assertIn("roll it first", err)
        self.assertEqual(api.calls, [], "no call without a token")
        code, out, err = self.run_main(self.ARGS, transport=api, env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("--write-env PATH is required", err)
        self.assertIn("--discard-tunnel-token", err)
        self.assertEqual(api.calls, [], "no call without a destination for the tunnel token")
        self.assertNotIn(FAKE_API_TOKEN, out + err)
        code, out, err = self.run_main([*self.ARGS, "--write-env", "/tmp/x.env", "--discard-tunnel-token"], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("exclude each other", err)
        self.assertEqual(api.calls, [])

    def test_live_run_with_fake_transport_writes_env_and_prints_no_secret(self):
        api = FakeCloudflare()
        d = Path(tempfile.mkdtemp())
        env_path = d / ".env"
        code, out, err = self.run_main([*self.ARGS, "--write-env", str(env_path)], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": f"  {FAKE_API_TOKEN}\n"})
        self.assertEqual(code, 0, err)
        self.assertEqual(trail(api)[0], ("GET", VERIFY), "the token is verified read-only before anything is touched")
        self.assertEqual(len(api.calls), 8, "verify + 3 lookups + 4 creates")
        self.assertEqual(api.calls[0][3], FAKE_API_TOKEN, "token is stripped before use")
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        self.assertIn(f"TUNNEL_TOKEN={FAKE_TUNNEL_TOKEN}", env_path.read_text(encoding="utf-8"))
        self.assertIn("token check: active", out)
        self.assertNotIn(FAKE_TUNNEL_TOKEN, out + err)
        self.assertNotIn(FAKE_API_TOKEN, out + err)
        self.assertIn("do not commit, do not paste anywhere", out)
        self.assertIn("https://games.example.com/", out)

    def test_inactive_token_stops_before_any_write(self):
        api = FakeCloudflare(token_status="disabled")
        d = Path(tempfile.mkdtemp())
        env_path = d / ".env"
        code, out, err = self.run_main([*self.ARGS, "--write-env", str(env_path)], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("not active (status: disabled)", err)
        self.assertIn("roll or re-create", err)
        self.assertEqual(trail(api), [("GET", VERIFY)], "nothing else is attempted with a dead token")
        self.assertFalse(env_path.exists())
        self.assertNotIn(FAKE_API_TOKEN, out + err)

    def test_skip_token_check_omits_the_verify_call(self):
        api = FakeCloudflare(token_status="disabled")          # would fail the check — but the check is skipped
        d = Path(tempfile.mkdtemp())
        code, out, err = self.run_main([*self.ARGS, "--skip-token-check", "--write-env", str(d / ".env")], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 0, err)
        self.assertNotIn(("GET", VERIFY), trail(api))
        self.assertNotIn("token check", out)

    def test_ci_mode_discards_the_tunnel_token_and_writes_only_public_facts(self):
        api = FakeCloudflare()
        d = Path(tempfile.mkdtemp())
        facts_path = d / "summary" / "facts.json"
        code, out, err = self.run_main([*self.ARGS, "--discard-tunnel-token", "--facts-json", str(facts_path)], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 0, err)
        self.assertEqual(len(api.calls), 8)
        self.assertFalse(any(p.suffix == ".env" for p in d.rglob("*")), "no env file anywhere")
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        self.assertEqual(facts, {"GODOTAI_PUBLIC_HOST": "games.example.com", "GODOTAI_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
                                 "GODOTAI_ACCESS_AUD": FAKE_AUD, "CF_ACCOUNT_ID": ACCOUNT, "TUNNEL_ID": TUNNEL_ID,
                                 "ACCESS_APP_ID": "app-id-1", "DNS_RECORD_ID": "dns-1"})
        self.assertNotIn(FAKE_TUNNEL_TOKEN, facts_path.read_text(encoding="utf-8") + out + err)
        self.assertNotIn(FAKE_API_TOKEN, facts_path.read_text(encoding="utf-8") + out + err)
        self.assertIn("tunnel token discarded", out)
        self.assertIn("Networking → Tunnels", out)
        self.assertIn(f"--reuse-tunnel {TUNNEL_ID}", out, "tells the operator how to finish locally without re-creating anything")

    def test_live_failure_exits_1_and_writes_nothing(self):
        api = FakeCloudflare(fail_on=("POST", "/access/apps"))
        d = Path(tempfile.mkdtemp())
        env_path = d / ".env"
        code, out, err = self.run_main([*self.ARGS, "--write-env", str(env_path)], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertIn("Access application + allow policy failed", err)
        self.assertFalse(env_path.exists())
        self.assertEqual(trail(api), [("GET", VERIFY), ("GET", LOOKUP_APPS), ("POST", f"/accounts/{ACCOUNT}/access/apps")])

    def test_no_reuse_existing_flag_fails_instead_of_reusing(self):
        api = FakeCloudflare(existing={"apps": [{"id": "app-id-9", "domain": "games.example.com", "aud": FAKE_AUD}]},
                             fail_on=("POST", "/access/apps"))        # the real API would answer "already exists"
        d = Path(tempfile.mkdtemp())
        code, out, err = self.run_main([*self.ARGS, "--no-reuse-existing", "--write-env", str(d / ".env")], transport=api,
                                       env={"CLOUDFLARE_API_TOKEN": FAKE_API_TOKEN})
        self.assertEqual(code, 1)
        self.assertEqual(trail(api), [("GET", VERIFY), ("POST", f"/accounts/{ACCOUNT}/access/apps")], "no lookup was made")
        self.assertFalse((d / ".env").exists())


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

    def test_get_sends_no_body(self):
        seen = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"success": True, "result": []}).encode()

        def fake_urlopen(req, timeout=0):
            seen["req"] = req
            return Resp()

        with mock.patch.object(cf.urllib.request, "urlopen", fake_urlopen):
            reply = cf.http_transport("GET", cf.API + "/x?name=y", None, FAKE_API_TOKEN)
        self.assertEqual(reply, {"success": True, "result": []})
        self.assertEqual(seen["req"].get_method(), "GET")
        self.assertIsNone(seen["req"].data)
        self.assertEqual(seen["req"].full_url, cf.API + "/x?name=y")

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
