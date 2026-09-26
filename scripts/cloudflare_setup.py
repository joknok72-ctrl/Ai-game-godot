#!/usr/bin/env python3
"""Publish the godotai chat behind Cloudflare Access through a Cloudflare Tunnel — API calls only, no secrets kept.

    python3 scripts/cloudflare_setup.py --dry-run --hostname games.example.com --team-domain myteam \
        --emails you@example.com --zone-id <zone id> --account-id <account id>       # prints the plan, no network
    export CLOUDFLARE_API_TOKEN=<token with Access: Apps and Policies Edit + Cloudflare Tunnel Edit + DNS Edit>
    python3 scripts/cloudflare_setup.py --hostname games.example.com --team-domain myteam --emails you@example.com \
        --zone-id <zone id> --account-id <account id> --write-env deploy/cloudflare/.env

What it creates, in the order Cloudflare recommends (docs read 2026-09-26 — an Access application *before* the
tunnel route, otherwise the hostname would be public for a moment):

1. an **Access application** (self-hosted, public hostname) with one *Allow* policy for the given e-mail
   addresses — everyone else is denied at Cloudflare's edge before reaching your server;
2. a **Cloudflare Tunnel** (remotely managed) — your server makes an outbound connection, no inbound port is opened;
3. the tunnel's **ingress**: ``<hostname> → http://chat:8765`` (the compose service) with *Protect with Access*
   (``originRequest.access``), plus the mandatory ``http_status:404`` catch-all;
4. a proxied **CNAME** ``<hostname> → <tunnel id>.cfargotunnel.com``.

The chat server *also* validates the Access JWT itself (``godotai/chat/access.py``), as Cloudflare's docs ask.

Secrets: the API token is read from ``CLOUDFLARE_API_TOKEN`` and sent only as the ``Authorization`` header;
the tunnel token returned by step 2 is **never printed** — it is written to the file named by ``--write-env``
(mode 0600, git-ignored ``.env``) together with the non-secret values the compose file needs, and nothing else.
Any credential that was ever pasted into a chat or a ticket must be rotated (see SECURITY.md).

This script was tested offline only (dry-run + fake transport in tests/test_cloudflare_setup.py); no live call
was made from this repository.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

API = "https://api.cloudflare.com/client/v4"
ENV_TOKEN = "CLOUDFLARE_API_TOKEN"
ENV_ACCOUNT = "CF_ACCOUNT_ID"
ENV_ZONE = "CF_ZONE_ID"
ENV_HOST = "GODOTAI_PUBLIC_HOST"
ENV_TEAM = "GODOTAI_ACCESS_TEAM_DOMAIN"
ENV_EMAILS = "GODOTAI_ACCESS_EMAILS"
DEFAULT_ORIGIN = "http://chat:8765"          # the chat service name + port in deploy/cloudflare/compose.yml
DEFAULT_TUNNEL_NAME = "godotai-chat"
DEFAULT_SESSION = "24h"
_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

Transport = Callable[[str, str, dict[str, Any] | None, str], dict[str, Any]]


class SetupError(RuntimeError):
    """Configuration or API failure; the message never contains a credential."""


# --------------------------------------------------------------------------- transport
def http_transport(method: str, url: str, body: dict[str, Any] | None, token: str) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "godotai-cloudflare-setup"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SetupError(f"{method} {url}: HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise SetupError(f"{method} {url}: {getattr(exc, 'reason', exc)}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SetupError(f"{method} {url}: response is not JSON") from None


def _result_any(reply: dict[str, Any], what: str) -> Any:
    if not isinstance(reply, dict) or not reply.get("success"):
        errs = reply.get("errors") if isinstance(reply, dict) else None
        detail = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in errs if isinstance(e, dict)) if errs else "no detail"
        raise SetupError(f"{what} failed — Cloudflare said: {detail}")
    return reply.get("result")


def _result(reply: dict[str, Any], what: str) -> dict[str, Any]:
    res = _result_any(reply, what)
    if not isinstance(res, dict):
        raise SetupError(f"{what}: unexpected response shape")
    return res


# --------------------------------------------------------------------------- plan
class Plan:
    """The four API calls, built from validated inputs. ``steps()`` is what ``--dry-run`` prints."""

    def __init__(self, account_id: str, zone_id: str, hostname: str, team_domain: str, emails: list[str],
                 origin: str = DEFAULT_ORIGIN, tunnel_name: str = DEFAULT_TUNNEL_NAME, session: str = DEFAULT_SESSION):
        if not _ID_RE.match(account_id):
            raise SetupError(f"{ENV_ACCOUNT} must be the 32-hex-character account id from the dashboard sidebar")
        if not _ID_RE.match(zone_id):
            raise SetupError(f"{ENV_ZONE} must be the 32-hex-character zone id (dashboard → your domain → Overview → API)")
        hostname = hostname.strip().lower()
        if not _HOST_RE.match(hostname):
            raise SetupError(f"{ENV_HOST} must be a hostname inside your zone, like games.example.com (got {hostname!r})")
        team = team_domain.strip().lower()
        for prefix in ("https://", "http://"):
            if team.startswith(prefix):
                team = team[len(prefix):]
        team = team.split("/", 1)[0].removesuffix(".cloudflareaccess.com")
        if not re.match(r"^[a-z0-9-]{1,63}$", team):
            raise SetupError(f"{ENV_TEAM} must be your Zero Trust team name (the <team> in <team>.cloudflareaccess.com)")
        emails = [e.strip().lower() for e in emails if e.strip()]
        if not emails or any(not _EMAIL_RE.match(e) for e in emails):
            raise SetupError(f"{ENV_EMAILS}: give at least one valid e-mail address; only these people may open the site")
        if not re.match(r"^https?://[a-z0-9.-]+(?::\d{1,5})?$", origin):
            raise SetupError(f"origin must look like http://chat:8765 (got {origin!r})")
        if not re.match(r"^\d+[mh]$", session):
            raise SetupError("session duration must look like 24h or 30m")
        self.account_id, self.zone_id, self.hostname, self.team, self.emails = account_id, zone_id, hostname, team, emails
        self.origin, self.tunnel_name, self.session = origin, tunnel_name, session

    # each step: (label, method, path, body) — bodies contain no secrets, so they may be printed
    def access_app(self) -> tuple[str, str, str, dict[str, Any]]:
        body = {
            "name": f"godotai chat ({self.hostname})", "type": "self_hosted", "domain": self.hostname,
            "destinations": [{"type": "public", "uri": self.hostname}],
            "session_duration": self.session, "app_launcher_visible": False,
            "policies": [{"name": "godotai owners", "decision": "allow",
                          "include": [{"email": {"email": e}} for e in self.emails]}],
        }
        return ("Access application + allow policy", "POST", f"/accounts/{self.account_id}/access/apps", body)

    def tunnel(self) -> tuple[str, str, str, dict[str, Any]]:
        return ("Cloudflare Tunnel", "POST", f"/accounts/{self.account_id}/cfd_tunnel",
                {"name": self.tunnel_name, "config_src": "cloudflare"})

    def ingress(self, tunnel_id: str, aud: str | None) -> tuple[str, str, str, dict[str, Any]]:
        rule: dict[str, Any] = {"hostname": self.hostname, "service": self.origin, "originRequest": {}}
        if aud:
            rule["originRequest"] = {"access": {"required": True, "teamName": self.team, "audTag": [aud]}}
        return ("tunnel ingress (public hostname → chat service, protected with Access)", "PUT",
                f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations",
                {"config": {"ingress": [rule, {"service": "http_status:404"}]}})

    def dns(self, tunnel_id: str) -> tuple[str, str, str, dict[str, Any]]:
        return ("DNS CNAME → tunnel", "POST", f"/zones/{self.zone_id}/dns_records",
                {"type": "CNAME", "proxied": True, "name": self.hostname, "content": f"{tunnel_id}.cfargotunnel.com",
                 "comment": "godotai chat via Cloudflare Tunnel"})

    def steps(self) -> list[tuple[str, str, str, dict[str, Any]]]:
        return [self.access_app(), self.tunnel(), self.ingress("<tunnel id>", "<aud tag>"), self.dns("<tunnel id>")]


# --------------------------------------------------------------------------- execution
def run(plan: Plan, token: str, transport: Transport, reuse_tunnel: str | None = None,
        reuse_aud: str | None = None, out: Callable[[str], None] = print) -> dict[str, str]:
    """Execute the plan; return the non-secret facts + the tunnel token (caller decides where it goes)."""
    facts: dict[str, str] = {ENV_HOST: plan.hostname, ENV_TEAM: f"{plan.team}.cloudflareaccess.com",
                             ENV_ACCOUNT: plan.account_id}
    if reuse_aud:
        facts["GODOTAI_ACCESS_AUD"] = reuse_aud
        out(f"1/4 Access application: reusing AUD tag {reuse_aud[:8]}…")
    else:
        label, method, path, body = plan.access_app()
        res = _result(transport(method, API + path, body, token), label)
        aud = str(res.get("aud") or "")
        facts["ACCESS_APP_ID"] = str(res.get("id") or "")
        if aud:
            facts["GODOTAI_ACCESS_AUD"] = aud
        out(f"1/4 {label}: created (app id {facts['ACCESS_APP_ID'] or '?'}, allowed: {', '.join(plan.emails)})")
        if not aud:
            out("    ⚠ the response carried no AUD tag — copy it from Zero Trust → Access → Applications → Overview "
                "into GODOTAI_ACCESS_AUD before starting the chat")
    if reuse_tunnel:
        if not re.match(r"^[0-9a-f-]{36}$", reuse_tunnel):
            raise SetupError("--reuse-tunnel expects the tunnel UUID")
        tunnel_id = reuse_tunnel
        res = _result_any(transport("GET", f"{API}/accounts/{plan.account_id}/cfd_tunnel/{tunnel_id}/token", None, token),
                          "tunnel token")
        connector_secret = res if isinstance(res, str) else str((res or {}).get("token") or "") if isinstance(res, dict) else ""
        out(f"2/4 Cloudflare Tunnel: reusing {tunnel_id}")
    else:
        label, method, path, body = plan.tunnel()
        res = _result(transport(method, API + path, body, token), label)
        tunnel_id = str(res.get("id") or "")
        connector_secret = str(res.get("token") or "")
        if not tunnel_id:
            raise SetupError("tunnel created but no id in the response")
        out(f"2/4 {label}: created (id {tunnel_id})")
    facts["TUNNEL_ID"] = tunnel_id
    label, method, path, body = plan.ingress(tunnel_id, facts.get("GODOTAI_ACCESS_AUD"))
    _result(transport(method, API + path, body, token), label)
    out(f"3/4 {label}: {plan.hostname} → {plan.origin}")
    label, method, path, body = plan.dns(tunnel_id)
    _result(transport(method, API + path, body, token), label)
    out(f"4/4 {label}: {plan.hostname} CNAME {tunnel_id}.cfargotunnel.com (proxied)")
    if not connector_secret:
        out("    ⚠ no tunnel token in the response — fetch it with GET /accounts/<id>/cfd_tunnel/<tunnel id>/token")
    facts["TUNNEL_TOKEN"] = connector_secret
    return facts


def write_env(path: Path, facts: dict[str, str]) -> None:
    """Write the compose ``.env`` with owner-only permissions; the tunnel token lives only here."""
    lines = ["# generated by scripts/cloudflare_setup.py — contains the tunnel token: keep out of git (.env is ignored)"]
    for key in (ENV_HOST, ENV_TEAM, "GODOTAI_ACCESS_AUD", ENV_ACCOUNT, "TUNNEL_ID", "ACCESS_APP_ID", "TUNNEL_TOKEN"):
        if facts.get(key):
            lines.append(f"{key}={facts[key]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hostname", default=os.environ.get(ENV_HOST, ""), help=f"public hostname (or ${ENV_HOST})")
    p.add_argument("--team-domain", default=os.environ.get(ENV_TEAM, ""), help=f"Zero Trust team name (or ${ENV_TEAM})")
    p.add_argument("--emails", default=os.environ.get(ENV_EMAILS, ""), help=f"comma-separated allowed e-mails (or ${ENV_EMAILS})")
    p.add_argument("--account-id", default=os.environ.get(ENV_ACCOUNT, ""), help=f"Cloudflare account id (or ${ENV_ACCOUNT})")
    p.add_argument("--zone-id", default=os.environ.get(ENV_ZONE, ""), help=f"zone id of the domain (or ${ENV_ZONE})")
    p.add_argument("--origin", default=DEFAULT_ORIGIN, help=f"where cloudflared finds the chat (default {DEFAULT_ORIGIN})")
    p.add_argument("--tunnel-name", default=DEFAULT_TUNNEL_NAME)
    p.add_argument("--session-duration", default=DEFAULT_SESSION, help="Access session length, e.g. 24h")
    p.add_argument("--reuse-tunnel", metavar="TUNNEL_ID", help="do not create a tunnel; configure this existing one")
    p.add_argument("--reuse-aud", metavar="AUD", help="do not create an Access application; use this AUD tag")
    p.add_argument("--write-env", metavar="PATH", help="where to write the .env for compose (mode 0600); required for a live run")
    p.add_argument("--dry-run", action="store_true", help="print the planned API calls and exit — no network, no token needed")
    return p


def main(argv: list[str] | None = None, transport: Transport | None = None, env: dict[str, str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if env is None else env
    try:
        plan = Plan(args.account_id, args.zone_id, args.hostname, args.team_domain, args.emails.split(","),
                    origin=args.origin, tunnel_name=args.tunnel_name, session=args.session_duration)
        if args.dry_run:
            print(f"DRY RUN — nothing is sent. Cloudflare API calls that a live run would make, in this order "
                  f"(bodies contain no secrets; the API token would travel only in the Authorization header):\n")
            for i, (label, method, path, body) in enumerate(plan.steps(), 1):
                print(f"{i}. {label}\n   {method} {API}{path}\n   {json.dumps(body, ensure_ascii=False)}\n")
            print(f"Then: docker compose -f deploy/cloudflare/compose.yml --profile <gpu-base|gpu-lora|cpu> up -d\n"
                  f"and open https://{plan.hostname}/ — Cloudflare Access asks for a one-time PIN sent to: {', '.join(plan.emails)}")
            return 0
        token = (env.get(ENV_TOKEN) or "").strip()
        if not token:
            raise SetupError(f"{ENV_TOKEN} is not set. Create a token (My Profile → API Tokens) with Account → Access: Apps "
                             f"and Policies: Edit, Account → Cloudflare Tunnel: Edit, Zone → DNS: Edit for this zone, and export it "
                             f"in this shell only. If a token was ever pasted into a chat, roll it first (SECURITY.md).")
        if not args.write_env:
            raise SetupError("--write-env PATH is required for a live run: the tunnel token is written there (mode 0600) and "
                             "never printed. Use deploy/cloudflare/.env for the compose file.")
        facts = run(plan, token, transport or http_transport, reuse_tunnel=args.reuse_tunnel, reuse_aud=args.reuse_aud)
        write_env(Path(args.write_env), facts)
        print(f"\nwrote {args.write_env} (owner-only; contains TUNNEL_TOKEN — do not commit, do not paste anywhere)")
        print(f"next: docker compose -f deploy/cloudflare/compose.yml --profile <gpu-base|gpu-lora|cpu> up -d\n"
              f"      then open https://{plan.hostname}/ — only {', '.join(plan.emails)} can pass Cloudflare Access")
        return 0
    except SetupError as exc:
        print(f"cloudflare-setup: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
