"""Cloudflare Access JWT verification (godotai/chat/access.py) — offline, stdlib only.

Tokens are signed here with a throw-away 2048-bit RSA key generated for this test file
(it protects nothing; it never leaves the test suite) so the *verification* path — JWKS
parsing, RS256, issuer/audience/expiry, key rotation, cookie fallback — is exercised
end-to-end, including through the HTTP server.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _helpers import fake_probe, repo_config
from test_chat import READY, ScriptedRuns, ServerFixture

from godotai.chat import access
from godotai.chat.access import AccessError, AccessVerifier, from_env, normalise_team_domain, token_from_headers
from godotai.chat.server import ChatServer, ChatServerError, valid_public_host

# --- test-only RSA key (generated with `openssl genrsa 2048` for this file; not a credential) ----------------
N = int("b5a58b6a65ea8b388e18b2ca9d2861307bee1259af3c3db8448e8aa861d8d65669aa96f96919843af06723bb1d1a1d0f290dabd021dc46a1"
        "5057791ef5891dae5e1a9c3c40ab55e492aadd107d3658b0dd81b0afa1fc8be85de2eada610fff8c597b63bd808052a164168c4293aa0ec69"
        "f9c691b0dbdd8f2037758b31c03375fb48a27edffc000e6c1ebba0f99d7b94ccec7c30e510df9d7ee2be60314e1a03c5685917017fb2b081"
        "2457f139c4bbb74ef422efec3a88ad62654bb60af95dae7da23b3c5fd76193f83ee14d4bacebd4d69d21ff6540d42c569c436755be917de7f"
        "2a1a40080eae4970c637cc2a3710a29383cbe8ec78df38bd6b5f68e2ab8fc1", 16)
D = int("5cf082d0c1acabe717ed532d9013a506a5a23e095d3bb9689acb43eebb81b2c92bef78a7cc3a9e097a0f8b9ca55b431b0aaa500a8208aeaa"
        "c2ecbeb034791f92a3db81ace279c7ccd7ae4cc5af0b2b7df317f44b28da8acb2d9e79039b3e1046c351faca6f013946126be6fff985bd61ab"
        "30e02653759b96d8672c1f186a1cde21fc303c70b15e49c90f4071d14965fc2ed5fdee3bc09863c96cec1b3c6606781ae7660334b115dca7"
        "617c74467f27b4572c1004bb1dad6698788038a48ba8dbb498057f8b4ceec2fda8ed7cb4ca5a0f18f7f8aceb1929ec4b38a4d62173a2f3414"
        "b94195b6715dda4240c8acc07a1f5475ed395e57c1c315edc19a3b9fb095", 16)
E = 65537
KID = "test-key-1"
TEAM = "acme"
ISS = "https://acme.cloudflareaccess.com"
AUD = "a" * 64
NOW = 1_800_000_000


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def b64u_uint(n: int) -> str:
    return b64u(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def jwks(kid: str = KID, n: int = N, **extra) -> bytes:
    return json.dumps({"keys": [{"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig", "n": b64u_uint(n),
                                 "e": b64u_uint(E), **extra}]}).encode()


def sign(payload: dict, kid: str = KID, alg: str = "RS256", d: int = D, n: int = N) -> str:
    header = {"alg": alg, "kid": kid, "typ": "JWT"}
    signing_input = f"{b64u(json.dumps(header).encode())}.{b64u(json.dumps(payload).encode())}"
    k = (n.bit_length() + 7) // 8
    t = access._SHA256_DIGEST_INFO + hashlib.sha256(signing_input.encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return f"{signing_input}.{b64u(sig)}"


def claims(**over) -> dict:
    base = {"iss": ISS, "aud": [AUD], "exp": NOW + 600, "iat": NOW - 5, "nbf": NOW - 5, "email": "owner@example.com",
            "sub": "user-1", "type": "app"}
    base.update(over)
    return base


class Fetch:
    """Fake JWKS endpoint: records URLs, can rotate keys or fail."""

    def __init__(self, body: bytes | None = None):
        self.body = body if body is not None else jwks()
        self.urls: list[str] = []
        self.fail = False

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if self.fail:
            raise OSError("network down")
        return self.body


def verifier(fetch: Fetch | None = None, **kw) -> AccessVerifier:
    return AccessVerifier(TEAM, AUD, fetch=fetch or Fetch(), clock=lambda: NOW, **kw)


class PrimitiveTests(unittest.TestCase):
    def test_team_domain_normalisation(self):
        for raw in ("acme", "acme.cloudflareaccess.com", "https://acme.cloudflareaccess.com/", " ACME "):
            self.assertEqual(normalise_team_domain(raw), "acme.cloudflareaccess.com", raw)
        for bad in ("", "acme.example.com", "a.b.cloudflareaccess.com", "https://"):
            with self.assertRaises(AccessError):
                normalise_team_domain(bad)

    def test_rsa_verify_rejects_short_keys_and_garbage(self):
        msg = b"hello"
        good = sign(claims())            # any RS256 signature over some input
        sig = access.b64url_decode(good.rsplit(".", 1)[1])
        self.assertFalse(access.rsa_pkcs1v15_sha256_verify(N, E, msg, sig), "signature over other data")
        self.assertFalse(access.rsa_pkcs1v15_sha256_verify(N, E, msg, sig[:-1]), "wrong length")
        self.assertFalse(access.rsa_pkcs1v15_sha256_verify(N, E, msg, b"\xff" * len(sig)), "s >= n")
        self.assertFalse(access.rsa_pkcs1v15_sha256_verify(2 ** 1023 + 1, E, msg, b"\x00" * 128), "key too small")
        signing_input = good.rsplit(".", 1)[0].encode()
        self.assertTrue(access.rsa_pkcs1v15_sha256_verify(N, E, signing_input, sig))

    def test_token_from_headers_prefers_header_then_cookie(self):
        self.assertEqual(token_from_headers({"Cf-Access-Jwt-Assertion": " t1 "}), "t1")
        self.assertEqual(token_from_headers({"Cookie": "a=b; CF_Authorization=t2; c=d"}), "t2")
        self.assertEqual(token_from_headers({"Cookie": "CF_Authorization=t2", "Cf-Access-Jwt-Assertion": "t1"}), "t1")
        self.assertIsNone(token_from_headers({"Cookie": "CF_Authorization=; x=y"}))
        self.assertIsNone(token_from_headers({}))

    def test_valid_public_host(self):
        for ok in ("games.example.com", "Godot.Example.COM", "a.b.c.d.example.io"):
            self.assertTrue(valid_public_host(ok), ok)
        for bad in ("localhost", "example", "https://games.example.com", "games.example.com:8765", "games.example.com/x",
                    "-bad.example.com", "127.0.0.1", ""):
            self.assertFalse(valid_public_host(bad), bad)


class VerifierTests(unittest.TestCase):
    def test_valid_token_returns_claims(self):
        f = Fetch()
        v = verifier(f)
        got = v.verify(sign(claims()))
        self.assertEqual(got["email"], "owner@example.com")
        self.assertEqual(f.urls, ["https://acme.cloudflareaccess.com/cdn-cgi/access/certs"])
        v.verify(sign(claims(aud=AUD)))                     # string audience is accepted too
        self.assertEqual(len(f.urls), 1, "JWKS cached across verifications")
        d = v.describe()
        self.assertEqual(d["team_domain"], "acme.cloudflareaccess.com")
        self.assertEqual(d["aud"], "aaaaaaaa…", "AUD only ever shown truncated")
        self.assertEqual(d["keys_cached"], 1)

    def test_multiple_auds_and_env_constructor(self):
        v = AccessVerifier(TEAM, f"{AUD}, other-app", fetch=Fetch(), clock=lambda: NOW)
        v.verify(sign(claims(aud=["other-app"])))
        self.assertIsNone(from_env({}))
        with self.assertRaises(AccessError):
            from_env({access.ENV_TEAM_DOMAIN: TEAM})
        with self.assertRaises(AccessError):
            from_env({access.ENV_AUD: AUD})
        with self.assertRaises(AccessError):
            AccessVerifier(TEAM, "", fetch=Fetch())
        got = from_env({access.ENV_TEAM_DOMAIN: TEAM, access.ENV_AUD: AUD}, fetch=Fetch())
        self.assertIsInstance(got, AccessVerifier)

    def test_rejections_never_leak_the_token(self):
        v = verifier()
        tok = sign(claims())
        cases = {
            "expired": sign(claims(exp=NOW - 120)),
            "not yet valid": sign(claims(nbf=NOW + 600)),
            "issued in the future": sign(claims(iat=NOW + 600)),
            "issuer mismatch": sign(claims(iss="https://evil.cloudflareaccess.com")),
            "audience mismatch": sign(claims(aud=["someone-else"])),
            "no exp": sign({k: v_ for k, v_ in claims().items() if k != "exp"}),
            "bad signature": tok[:-6] + ("AAAAAA" if not tok.endswith("AAAAAA") else "BBBBBB"),
            "unsupported alg": sign(claims(), alg="HS256"),
            "unknown key": sign(claims(), kid="rotated-away"),
            "not a JWT": "just.one",
            "not JSON": "eyJ.eyJ.sig",
        }
        for reason, bad in cases.items():
            with self.assertRaises(AccessError, msg=reason) as cm:
                v.verify(bad)
            text = str(cm.exception)
            self.assertNotIn(bad.split(".")[-1][:20], text, f"{reason}: message must not contain the token")
            self.assertIn(reason.split()[0], text.lower(), reason)
        # tampered payload with the original signature
        h, p, s = tok.split(".")
        forged = f"{h}.{b64u(json.dumps(claims(email='attacker@example.com')).encode())}.{s}"
        with self.assertRaises(AccessError):
            v.verify(forged)
        # skew tolerance: a token that expired 30 s ago is still accepted (60 s skew), 90 s is not
        v.verify(sign(claims(exp=NOW - 30)))

    def test_key_rotation_refreshes_once(self):
        f = Fetch()
        v = verifier(f)
        v.verify(sign(claims()))
        f.body = jwks(kid="test-key-2")                          # Cloudflare rotated; new kid in the JWKS
        v.verify(sign(claims(), kid="test-key-2"))
        self.assertEqual(len(f.urls), 2, "unknown kid → exactly one refresh")
        with self.assertRaises(AccessError):
            v.verify(sign(claims(), kid="test-key-1"))            # old kid no longer published
        self.assertEqual(len(f.urls), 3)

    def test_jwks_problems(self):
        f = Fetch(b"not json")
        with self.assertRaises(AccessError) as cm:
            verifier(f).verify(sign(claims()))
        self.assertIn("signing keys", str(cm.exception))
        f = Fetch(json.dumps({"keys": [{"kid": "x", "kty": "EC"}]}).encode())
        with self.assertRaises(AccessError):
            verifier(f).verify(sign(claims()))
        # a network failure after a successful fetch keeps the cached keys
        f = Fetch()
        v = verifier(f, jwks_ttl=0)
        v.verify(sign(claims()))
        f.fail = True
        v.verify(sign(claims()))
        self.assertEqual(len(f.urls), 2)
        # a wrong key for the right kid → bad signature (other key pair with same kid)
        f = Fetch(jwks(n=N + 2))
        with self.assertRaises(AccessError) as cm:
            verifier(f).verify(sign(claims()))
        self.assertIn("bad signature", str(cm.exception))


class ServerAccessTests(unittest.TestCase):
    """Behind the tunnel: every request must carry a valid Access JWT; the token mode is unchanged."""

    def setUp(self):
        self.cfg = repo_config()
        self.fx = None

    def tearDown(self):
        if self.fx:
            self.fx.close()

    def test_refuses_public_bind_without_token_or_access_and_bad_public_host(self):
        tmp = Path(tempfile.mkdtemp())
        with self.assertRaises(ChatServerError):
            ChatServer(self.cfg, tmp, host="0.0.0.0", port=0)
        with self.assertRaises(ChatServerError):
            ChatServer(self.cfg, tmp, host="127.0.0.1", port=0, public_hosts=["http://games.example.com"])
        srv = ChatServer(self.cfg, tmp, host="0.0.0.0", port=0, access=verifier(), public_hosts=["Games.Example.com"],
                         probe=fake_probe())
        try:
            self.assertEqual(srv.public_hosts, ("games.example.com",))
            self.assertEqual(srv.hosting, "access")
            self.assertTrue(srv.allowed_host("games.example.com"))
        finally:
            srv.httpd.server_close()

    def test_every_route_requires_a_valid_access_token(self):
        self.fx = ServerFixture(self.cfg, ScriptedRuns(), access=verifier(), public_hosts=["games.example.com"])
        fx = self.fx
        for path in ("/", "/static/app.js", "/api/status", "/api/sessions"):
            st, body = fx.get(path, raw=True)
            self.assertEqual(st, 401, path)
            self.assertIn(b"Cloudflare Access", body)
        st, body = fx.post("/api/sessions", {"name": "x"}, raw=True)
        self.assertEqual(st, 401)
        st, js = fx.get("/healthz")
        self.assertEqual((st, js), (200, {"ok": True}), "the container liveness probe needs no token and says nothing else")
        self.assertEqual(fx.request("POST", "/healthz", body={}, raw=True)[0], 401, "only GET is exempt")
        # a token for another application / expired → still 401
        for bad in (sign(claims(aud=["other"])), sign(claims(exp=NOW - 600))):
            self.assertEqual(fx.get("/api/status", headers={"Cf-Access-Jwt-Assertion": bad})[0], 401)
        good = {"Cf-Access-Jwt-Assertion": sign(claims())}
        st, page = fx.get("/", raw=True, headers=good)
        self.assertEqual(st, 200)
        self.assertIn(b"app.js", page)
        st, js = fx.get("/api/status", headers=good)
        self.assertEqual(st, 200)
        self.assertEqual(js["hosting"], "access")
        self.assertTrue(js["access_required"])
        self.assertEqual(js["public_hosts"], ["games.example.com"])
        self.assertEqual(js["viewer"], {"email": "owner@example.com", "sub": "user-1"})
        self.assertEqual(js["access"]["aud"], "aaaaaaaa…")
        self.assertNotIn(AUD, json.dumps(js), "the AUD tag never appears in full")
        # the browser sends the cookie form through the tunnel; Host is the public hostname
        cookie = {"Cookie": f"CF_Authorization={sign(claims())}"}
        st, js = fx.get("/api/status", headers=cookie, host_header="games.example.com")
        self.assertEqual(st, 200)
        # state-changing request with the public Origin is same-origin
        st, snap = fx.post("/api/sessions", {"name": "My Runner"},
                           headers={**cookie, "Origin": "https://games.example.com"}, host_header="games.example.com")
        self.assertEqual(st, 201, snap)
        self.assertEqual(snap["id"], "my-runner")
        st, _ = fx.post("/api/sessions", {"name": "Evil"},
                        headers={**cookie, "Origin": "https://evil.example.com"}, host_header="games.example.com")
        self.assertEqual(st, 403, "foreign Origin is rejected even with a valid Access token")
        st, _ = fx.get("/api/status", headers=cookie, host_header="other.example.com")
        self.assertEqual(st, 403, "loopback bind: only loopback and the declared public hostnames pass the Host check")

    def test_access_and_token_can_be_combined(self):
        self.fx = ServerFixture(self.cfg, ScriptedRuns(), access=verifier(), token="shared-secret")
        fx = self.fx
        good = {"Cf-Access-Jwt-Assertion": sign(claims())}
        self.assertEqual(fx.get("/api/status", headers={**good, "Authorization": "Bearer wrong"})[0], 401)
        st, js = fx.get("/api/status", headers=good)
        self.assertEqual(st, 200)
        self.assertEqual(js["hosting"], "access")
        self.assertTrue(js["token_required"])


if __name__ == "__main__":
    unittest.main()
