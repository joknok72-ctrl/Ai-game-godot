"""stdlib HTTP server for the chat UI: static page + JSON API + Server-Sent Events.

Routes (all JSON unless noted):

    GET  /                                   the page (Arabic-first, RTL)           [no token needed]
    GET  /static/app.js | /static/app.css    assets                                   [no token needed]
    GET  /api/status                         engine / model / key readiness (+ hints)
    GET  /api/sessions                       list projects under --games-dir
    POST /api/sessions          {name}       create (or open) a project folder
    GET  /api/sessions/<id>                  snapshot: state, history, pending plan
    POST /api/sessions/<id>/messages {text, auto_approve?, plan_only?}   → starts Agent.run() (202)
    POST /api/sessions/<id>/approve  {approved, feedback?}               → answers the plan gate
    POST /api/sessions/<id>/cancel
    POST /api/sessions/<id>/verify           engine verification without a model
    GET  /api/sessions/<id>/files[?path=]    list project files / read one text file
    GET  /api/sessions/<id>/events?since=N   text/event-stream (id/event/data), resumes from N or Last-Event-ID

Safety: binds 127.0.0.1 by default; any other host *requires* a token (``--token`` /
``GODOTAI_CHAT_TOKEN``) sent as ``Authorization: Bearer`` (or ``?token=`` for EventSource)
**or** Cloudflare Access verification (``--access-team-domain`` + ``--access-aud``, see
``access.py``: every request — page, assets and API — must carry a valid Access JWT);
the ``Host`` header must be local (or one of ``--public-host``) when bound to loopback
(DNS-rebinding guard) and a foreign ``Origin`` on a state-changing request is rejected
(CSRF guard); the page is served with a strict Content-Security-Policy and no inline
script. Session ids are folder-safe slugs, file reads go through the same
``safe_path``/secret-name filters as the model's tools.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Config
from .access import AccessError, AccessVerifier
from .session import SessionError, SessionManager
from .status import Probe, environment_status, model_ready

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
                "/static/app.css": ("app.css", "text/css; charset=utf-8")}
MAX_BODY = 1_000_000
HEALTH_PATH = "/healthz"          # liveness probe for containers/cloudflared; answers {"ok": true} without any auth
SSE_KEEPALIVE_SECONDS = 15.0
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_SESSION_ROUTE = re.compile(r"^/api/sessions/([a-z0-9][a-z0-9_-]{0,47})(?:/([a-z]+))?$")


class ChatServerError(RuntimeError):
    pass


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _host_of(header: str) -> str:
    """Host part of a Host/Origin authority, without port (handles ``[::1]:8765``)."""
    h = header.strip()
    if h.startswith("["):
        return h[1: h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")


def valid_public_host(host: str) -> bool:
    """A public hostname such as ``games.example.com`` (lower-case, dotted, no scheme/port/path)."""
    return bool(_HOSTNAME_RE.match((host or "").strip().lower()))


class ChatServer:
    def __init__(self, cfg: Config, games_dir: Path, host: str = "127.0.0.1", port: int = 8765,
                 token: str | None = None, manager: SessionManager | None = None,
                 auto_approve_default: bool = False, include_github: bool = True, quiet: bool = True,
                 access: AccessVerifier | None = None, public_hosts: tuple[str, ...] | list[str] = (),
                 probe: Probe | None = None):
        if not is_loopback(host) and not token and access is None:
            raise ChatServerError(
                f"refusing to listen on {host!r} without a token or Cloudflare Access: the chat can write files and "
                "run your model. Pass --token <secret> (or set GODOTAI_CHAT_TOKEN), configure --access-team-domain + "
                "--access-aud (GODOTAI_ACCESS_TEAM_DOMAIN / GODOTAI_ACCESS_AUD), or keep the default 127.0.0.1.")
        hosts: list[str] = []
        for h in public_hosts:
            h = (h or "").strip().lower()
            if not h:
                continue
            if not valid_public_host(h):
                raise ChatServerError(f"--public-host {h!r} is not a hostname like games.example.com")
            hosts.append(h)
        self.public_hosts: tuple[str, ...] = tuple(dict.fromkeys(hosts))
        self.access = access
        self.probe = probe                          # model-server probe (tests inject one; None = real GET /models)
        self.cfg = cfg
        self.host = host
        self.token = token or None
        self.auto_approve_default = auto_approve_default
        self.quiet = quiet
        self.manager = manager or SessionManager(
            cfg, games_dir, include_github=include_github,
            ready_check=(lambda: model_ready(cfg, probe)) if probe is not None else None)
        self.stopping = threading.Event()
        self._serving = threading.Event()          # set while serve_forever() runs (httpd.shutdown() needs it)
        self._serve_thread: threading.Thread | None = None
        handler = type("GodotAIChatHandler", (_Handler,), {"app": self})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]

    # ------------------------------------------------------------------
    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else ("[::1]" if self.host == "::" else self.host)
        return f"http://{host}:{self.port}/"

    def serve_forever(self) -> None:
        self._serving.set()
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            self._serving.clear()
            self.stopping.set()

    def shutdown(self) -> None:
        """Stop accepting requests, cancel running agent threads, close the socket.

        ``httpd.shutdown()`` deadlocks unless ``serve_forever()`` is running in another
        thread, so it is only called when that is (or is about to be) the case.
        """
        self.stopping.set()
        self.manager.shutdown()
        if self._serve_thread is not None and not self._serving.is_set():
            self._serving.wait(2.0)                # thread started but not yet inside serve_forever()
        if self._serving.is_set():
            self.httpd.shutdown()
        self.httpd.server_close()

    def start_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, name="godotai-chat-http", daemon=True)
        self._serve_thread = t
        t.start()
        return t

    def allowed_host(self, host_header: str) -> bool:
        h = _host_of(host_header or "").lower()
        if h in self.public_hosts:
            return True                                    # reached through the tunnel / reverse proxy
        if not is_loopback(self.host):
            return True                                    # token/Access-protected; hostnames vary behind Docker/LAN
        return is_loopback(h) or h == self.host

    @property
    def hosting(self) -> str:
        """``access`` (Cloudflare Access in front), ``token`` (shared secret) or ``local`` (loopback only)."""
        if self.access is not None:
            return "access"
        return "token" if self.token else "local"

    def status(self) -> dict[str, Any]:
        st = environment_status(self.cfg, self.probe)
        st.update(version=__version__, auto_approve_default=self.auto_approve_default,
                  games_dir=str(self.manager.games_dir), token_required=bool(self.token),
                  access_required=self.access is not None, public_hosts=list(self.public_hosts),
                  hosting=self.hosting, access=self.access.describe() if self.access else None, url=self.url)
        return st


class _Handler(BaseHTTPRequestHandler):
    app: ChatServer
    server_version = "godotai-chat"
    sys_version = ""
    viewer: dict[str, Any] | None = None     # Access claims of the current request (email/sub), when Access is on

    # ---------------------------------------------------------------- plumbing
    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default; errors still surface as responses
        if not self.app.quiet:
            super().log_message(fmt, *args)

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # never write the access token (EventSource sends it as ?token=) into a log line
        if not self.app.quiet:
            line = re.sub(r"([?&]token=)[^&\s]*", r"\1[REDACTED]", self.requestline)
            self.log_message('"%s" %s %s', line, str(code), str(size))

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, hint: str = "") -> None:
        self._send_json(status, {"error": message, "hint": hint})

    def _send_static(self, name: str, ctype: str) -> None:
        path = STATIC_DIR / name
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, f"missing asset {name}")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise SessionError("request body too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SessionError("body must be JSON") from None
        if not isinstance(data, dict):
            raise SessionError("body must be a JSON object")
        return data

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        if not self.app.token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == self.app.token:
            return True
        return query.get("token", [""])[0] == self.app.token

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in ("http", "https"):
            return False
        host_hdr = self.headers.get("Host", "")
        return (parsed.netloc.lower() == host_hdr.lower()) or (
            is_loopback(self.app.host) and is_loopback(_host_of(parsed.netloc)) and is_loopback(_host_of(host_hdr))
            and _port(parsed.netloc) == _port(host_hdr))

    # ---------------------------------------------------------------- routing
    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            if not self.app.allowed_host(self.headers.get("Host", "")):
                return self._error(HTTPStatus.FORBIDDEN, "unexpected Host header (open the URL printed by the server)")
            if method == "GET" and urllib.parse.urlsplit(self.path).path == HEALTH_PATH:
                return self._send_json(HTTPStatus.OK, {"ok": True})   # container health probe: no auth, no facts
            if self.app.access is not None:                # page, assets and API alike: no valid Access JWT → 401
                try:
                    self.viewer = self.app.access.authenticate(self.headers)
                except AccessError as exc:
                    return self._error(HTTPStatus.UNAUTHORIZED, f"Cloudflare Access: {exc}",
                                       "افتح الموقع من رابطه العام (عبر Cloudflare Access) وسجّل الدخول ببريدك المصرّح به.")
            url = urllib.parse.urlsplit(self.path)
            path, query = url.path, urllib.parse.parse_qs(url.query)
            if method == "GET" and path in ("/", "/index.html"):
                return self._send_static("index.html", "text/html; charset=utf-8")
            if method == "GET" and path in STATIC_FILES:
                return self._send_static(*STATIC_FILES[path])
            if not path.startswith("/api/"):
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            if not self._authorized(query):
                return self._error(HTTPStatus.UNAUTHORIZED, "token required",
                                   "أضِف الرمز (token) الذي طبعه الخادم عند التشغيل.")
            if method == "POST" and not self._same_origin():
                return self._error(HTTPStatus.FORBIDDEN, "cross-origin request rejected")
            return self._api(method, path, query)
        except SessionError as exc:
            return self._error(exc.status, str(exc), exc.hint)
        except (BrokenPipeError, ConnectionResetError):
            return None
        except Exception as exc:  # keep the server alive; report the failure to the page
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        mgr = self.app.manager
        if path == "/api/status" and method == "GET":
            st = self.app.status()
            if self.viewer:
                st["viewer"] = {"email": self.viewer.get("email"), "sub": self.viewer.get("sub")}
            return self._send_json(HTTPStatus.OK, st)
        if path == "/api/sessions":
            if method == "GET":
                return self._send_json(HTTPStatus.OK, {"sessions": mgr.list(), "games_dir": str(mgr.games_dir)})
            body = self._read_json()
            s = mgr.create(str(body.get("name", "")))
            return self._send_json(HTTPStatus.CREATED, s.snapshot())
        m = _SESSION_ROUTE.match(path)
        if not m:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        sid, action = m.group(1), m.group(2)
        session = mgr.get(sid)
        if session is None:
            return self._error(HTTPStatus.NOT_FOUND, f"unknown project {sid!r}",
                               "أنشئ المشروع أولًا من القائمة الجانبية.")
        if action is None and method == "GET":
            return self._send_json(HTTPStatus.OK, session.snapshot())
        if action == "events" and method == "GET":
            since = self.headers.get("Last-Event-ID") or query.get("since", ["0"])[0]
            try:
                return self._sse(session, int(since))
            except ValueError:
                raise SessionError("since must be an integer") from None
        if action == "files" and method == "GET":
            rel = query.get("path", [""])[0]
            if rel:
                return self._send_json(HTTPStatus.OK, session.read_file(rel))
            return self._send_json(HTTPStatus.OK, {"files": session.list_files()})
        if action in (None, "events", "files") or method != "POST":
            return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
        body = self._read_json()
        if action == "messages":
            auto = body.get("auto_approve")
            ev = session.send(str(body.get("text", "")),
                              auto_approve=self.app.auto_approve_default if auto is None else bool(auto),
                              plan_only=bool(body.get("plan_only", False)))
            return self._send_json(HTTPStatus.ACCEPTED, {"accepted": True, "event": ev, "state": session.state})
        if action == "approve":
            if "approved" not in body:
                raise SessionError("'approved' (true/false) is required")
            session.approve(bool(body["approved"]), str(body.get("feedback", "")))
            return self._send_json(HTTPStatus.OK, {"ok": True})
        if action == "cancel":
            return self._send_json(HTTPStatus.OK, {"cancelling": session.cancel()})
        if action == "verify":
            return self._send_json(HTTPStatus.OK, session.verify())
        return self._error(HTTPStatus.NOT_FOUND, "not found")

    # ---------------------------------------------------------------- SSE
    def _sse(self, session, since: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        last = since
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not self.app.stopping.is_set():
                events = session.wait_for_events(last, SSE_KEEPALIVE_SECONDS)
                if not events:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                for ev in events:
                    data = json.dumps(ev, ensure_ascii=False, default=str)
                    self.wfile.write(f"id: {ev['id']}\nevent: {ev['type']}\ndata: {data}\n\n".encode("utf-8"))
                    last = ev["id"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            return


def _port(netloc: str) -> str:
    n = netloc.strip()
    if n.startswith("["):
        return n[n.find("]") + 2:] if "]:" in n else ""
    return n.rsplit(":", 1)[1] if n.count(":") == 1 else ""
