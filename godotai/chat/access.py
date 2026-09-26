"""Cloudflare Access (Zero Trust) JWT verification — standard library only.

When the chat is published through a Cloudflare Tunnel behind an **Access**
application, Cloudflare authenticates the visitor (email one-time PIN, Google,
GitHub, …) and forwards every request with a signed JWT in the
``Cf-Access-Jwt-Assertion`` header (browsers also carry it as the
``CF_Authorization`` cookie). Cloudflare's documentation says the origin should
**validate that token itself** rather than trust the network path, because a
misconfigured tunnel or a leaked origin address would otherwise expose the app.
This module does exactly that (docs read 2026-09-26):

* JWKS: ``https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`` → ``{"keys": [{"kid", "kty": "RSA", "n", "e"}]}``;
  keys rotate about every six weeks and the previous key stays valid ~7 days, so the
  set is cached and refreshed once when an unknown ``kid`` shows up.
* Signature: RS256 = RSASSA-PKCS1-v1_5 with SHA-256 — implemented with ``pow()`` and
  ``hashlib``; the encoded digest is compared in constant time.
* Claims: ``iss`` must equal ``https://<team>.cloudflareaccess.com``, ``aud`` must contain the
  application's AUD tag, ``exp`` (and ``nbf``/``iat`` when present) are checked with a small
  clock skew.

Nothing here logs tokens; failures report a *reason*, never the token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

HEADER = "Cf-Access-Jwt-Assertion"
COOKIE = "CF_Authorization"
CERTS_PATH = "/cdn-cgi/access/certs"
ENV_TEAM_DOMAIN = "GODOTAI_ACCESS_TEAM_DOMAIN"    # e.g. myteam  or  myteam.cloudflareaccess.com
ENV_AUD = "GODOTAI_ACCESS_AUD"                    # Access application "Application Audience (AUD) Tag"
CLOCK_SKEW = 60                                   # seconds
MIN_RSA_BITS = 2048
JWKS_TTL = 3600.0                                 # seconds between routine refreshes
FETCH_TIMEOUT = 5.0
# DER prefix of DigestInfo for SHA-256 (RFC 8017 §9.2 note 1)
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

Fetcher = Callable[[str], bytes]


class AccessError(Exception):
    """Token missing/invalid. ``str(exc)`` is safe to show (never contains the token)."""


# --------------------------------------------------------------------------- primitives
def b64url_decode(data: str) -> bytes:
    data = data.strip()
    pad = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + pad)
    except (ValueError, TypeError) as exc:
        raise AccessError("malformed base64url segment") from exc


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_uint(data: str) -> int:
    return int.from_bytes(b64url_decode(data), "big")


def rsa_pkcs1v15_sha256_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 verification with SHA-256 (RFC 8017 §8.2.2), no third-party crypto."""
    k = (n.bit_length() + 7) // 8
    if n.bit_length() < MIN_RSA_BITS or len(signature) != k:
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def normalise_team_domain(value: str) -> str:
    """``myteam`` / ``myteam.cloudflareaccess.com`` / ``https://myteam.cloudflareaccess.com/`` → ``myteam.cloudflareaccess.com``."""
    v = (value or "").strip().lower()
    if v.startswith("https://"):
        v = v[len("https://"):]
    elif v.startswith("http://"):
        v = v[len("http://"):]
    v = v.split("/", 1)[0].rstrip(".")
    if not v:
        raise AccessError(f"{ENV_TEAM_DOMAIN} is empty")
    if "." not in v:
        v = f"{v}.cloudflareaccess.com"
    if not v.endswith(".cloudflareaccess.com") or v.count(".") != 2:
        raise AccessError(f"Access team domain must look like <team>.cloudflareaccess.com, got {value!r}")
    return v


def _default_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "godotai-access"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return resp.read(1_000_000)


def token_from_headers(headers: Mapping[str, str]) -> str | None:
    """The Access JWT from the header Cloudflare adds, else from the ``CF_Authorization`` cookie."""
    tok = headers.get(HEADER) or headers.get(HEADER.lower())
    if tok:
        return tok.strip()
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name.strip() == COOKIE and value:
            return value.strip()
    return None


# --------------------------------------------------------------------------- verifier
class AccessVerifier:
    def __init__(self, team_domain: str, aud: str, fetch: Fetcher | None = None,
                 clock: Callable[[], float] = time.time, skew: int = CLOCK_SKEW, jwks_ttl: float = JWKS_TTL):
        self.team_domain = normalise_team_domain(team_domain)
        auds = {a.strip() for a in (aud or "").split(",") if a.strip()}
        if not auds:
            raise AccessError(f"{ENV_AUD} is empty — copy the Application Audience (AUD) tag from Zero Trust → Access → Applications")
        self.auds = frozenset(auds)
        self.fetch = fetch or _default_fetch
        self.clock = clock
        self.skew = skew
        self.jwks_ttl = jwks_ttl
        self._keys: dict[str, tuple[int, int]] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def jwks_url(self) -> str:
        return self.issuer + CERTS_PATH

    def describe(self) -> dict[str, Any]:
        """Non-secret summary for status output (the AUD tag is shown truncated)."""
        first = sorted(self.auds)[0]
        return {"team_domain": self.team_domain, "aud": first[:8] + "…" if len(first) > 8 else first,
                "issuer": self.issuer, "keys_cached": len(self._keys)}

    # -- keys ------------------------------------------------------------------------------------------------
    def _load_keys(self) -> None:
        try:
            raw = self.fetch(self.jwks_url)
            data = json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if self._keys:
                return                                        # keep serving with the cached set
            raise AccessError(f"cannot fetch Access signing keys from {self.jwks_url}: {type(exc).__name__}") from exc
        keys: dict[str, tuple[int, int]] = {}
        for k in data.get("keys", []) if isinstance(data, dict) else []:
            if not isinstance(k, dict) or k.get("kty") != "RSA" or not k.get("kid") or not k.get("n") or not k.get("e"):
                continue
            if k.get("alg", "RS256") != "RS256" or k.get("use", "sig") != "sig":
                continue
            try:
                keys[str(k["kid"])] = (b64url_uint(k["n"]), b64url_uint(k["e"]))
            except AccessError:
                continue
        if not keys:
            if self._keys:
                return
            raise AccessError(f"no RSA signing keys in {self.jwks_url}")
        self._keys = keys
        self._fetched_at = time.monotonic()

    def keys(self, force: bool = False) -> dict[str, tuple[int, int]]:
        with self._lock:
            stale = time.monotonic() - self._fetched_at > self.jwks_ttl
            if force or not self._keys or stale:
                self._load_keys()
            return dict(self._keys)

    # -- verification ----------------------------------------------------------------------------------------
    def verify(self, token: str) -> dict[str, Any]:
        """Return the claims of a valid token; raise :class:`AccessError` (with a safe reason) otherwise."""
        if not token or token.count(".") != 2 or len(token) > 8192:
            raise AccessError("token is not a JWT")
        h_b64, p_b64, s_b64 = token.split(".")
        try:
            header = json.loads(b64url_decode(h_b64).decode("utf-8"))
            payload = json.loads(b64url_decode(p_b64).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccessError("token header/payload is not JSON") from exc
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise AccessError("token header/payload is not an object")
        if header.get("alg") != "RS256":
            raise AccessError(f"unsupported alg {header.get('alg')!r} (Access signs with RS256)")
        kid = str(header.get("kid") or "")
        if not kid:
            raise AccessError("token has no kid")
        keys = self.keys()
        if kid not in keys:
            keys = self.keys(force=True)                     # rotation: refresh once
            if kid not in keys:
                raise AccessError("token signed by an unknown key")
        n, e = keys[kid]
        signature = b64url_decode(s_b64)
        if not rsa_pkcs1v15_sha256_verify(n, e, f"{h_b64}.{p_b64}".encode("ascii"), signature):
            raise AccessError("bad signature")
        now = self.clock()
        if payload.get("iss") != self.issuer:
            raise AccessError("issuer mismatch")
        aud = payload.get("aud")
        aud_list = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
        if not any(a in self.auds for a in aud_list if isinstance(a, str)):
            raise AccessError("audience mismatch (token is for another Access application)")
        exp = payload.get("exp")
        if not isinstance(exp, (int, float)):
            raise AccessError("token has no exp")
        if now > exp + self.skew:
            raise AccessError("token expired")
        nbf = payload.get("nbf")
        if isinstance(nbf, (int, float)) and now + self.skew < nbf:
            raise AccessError("token not yet valid")
        iat = payload.get("iat")
        if isinstance(iat, (int, float)) and now + self.skew < iat:
            raise AccessError("token issued in the future")
        return payload

    def authenticate(self, headers: Mapping[str, str]) -> dict[str, Any]:
        token = token_from_headers(headers)
        if not token:
            raise AccessError("no Cloudflare Access token (open the site through its Cloudflare Access URL)")
        return self.verify(token)


def from_env(env: Mapping[str, str] | None = None, fetch: Fetcher | None = None) -> AccessVerifier | None:
    """An :class:`AccessVerifier` when both ``GODOTAI_ACCESS_TEAM_DOMAIN`` and ``GODOTAI_ACCESS_AUD`` are set.

    Setting only one of them is a configuration error (raised), because a half-configured guard would
    silently disable itself.
    """
    env = os.environ if env is None else env
    team = (env.get(ENV_TEAM_DOMAIN) or "").strip()
    aud = (env.get(ENV_AUD) or "").strip()
    if not team and not aud:
        return None
    if not team or not aud:
        raise AccessError(f"set both {ENV_TEAM_DOMAIN} and {ENV_AUD} (or neither)")
    return AccessVerifier(team, aud, fetch=fetch)
