"""Chat sessions: one game project directory ⇄ one conversation with the agent.

A :class:`Session` owns a workspace under the games directory and runs the real
:class:`godotai.agent.Agent` in a background thread — one task at a time, exactly
like ``python3 -m godotai run`` — while publishing an append-only event stream
(assistant text, tool calls, progress lines, the plan awaiting approval, the
final summary). The plan gate becomes asynchronous: the agent thread blocks in
``approve()`` until the browser posts a decision (or the run is cancelled).

Follow-up messages start a *new* run in the *same* directory (the agent reads the
existing project); the task text carries a short, labelled summary of the earlier
exchanges so the model knows what "add a pause button" refers to.

Chat-level events (user, assistant, plan, approval, done, error, system) are
persisted to ``<workspace>/.godotai/chat.jsonl`` so a page reload — or a server
restart — shows the conversation again. Tool/log lines are not persisted here;
they are already in the full run log ``.godotai/runs/<ts>.json``.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..agent import Agent, RunSummary, _short_args
from ..config import AgentConfig, Config
from ..planner import Plan
from ..providers import Provider, ProviderError, ToolCall, make_provider
from ..tools import ToolRegistry, ToolResult, build_registry
from ..tools.fs import PathEscape, is_secret_name, safe_path
from .status import model_ready

SESSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
HISTORY_FILE = Path(".godotai") / "chat.jsonl"
PERSISTED_TYPES = frozenset({"user", "assistant", "plan", "approval", "done", "error", "system"})
MAX_EVENTS = 5000            # in-memory ring per session (ids keep growing; older ones are dropped)
MAX_HISTORY_LINES = 2000
MAX_MESSAGE_CHARS = 20_000
MAX_FILE_CHARS = 200_000
CONTEXT_EXCHANGES = 6        # earlier request/outcome pairs quoted to the model on a follow-up
PREVIEW_CHARS = 1500

ProviderFactory = Callable[[AgentConfig], Provider]
RegistryFactory = Callable[[], ToolRegistry]
ReadyCheck = Callable[[], tuple[bool, str, str]]


class SessionError(Exception):
    status = 400

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class SessionBusy(SessionError):
    status = 409


class SessionNotReady(SessionError):
    status = 503


class NoPendingPlan(SessionError):
    status = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    """Folder-safe session id: ascii lower-case, ``-``/``_`` allowed, ≤ 48 chars; '' if nothing usable remains."""
    text = unicodedata.normalize("NFKD", (name or "").strip().lower())
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9_-]+", "-", text).strip("-_")
    text = re.sub(r"-{2,}", "-", text)[:48].rstrip("-_")
    return text if SESSION_ID_RE.match(text) else ""


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class Session:
    def __init__(self, sid: str, workspace: Path, cfg: Config, provider_factory: ProviderFactory,
                 registry_factory: RegistryFactory, ready_check: ReadyCheck):
        self.id = sid
        self.workspace = Path(workspace).resolve()
        self.cfg = cfg
        self.provider_factory = provider_factory
        self.registry_factory = registry_factory
        self.ready_check = ready_check
        self.cond = threading.Condition()
        self.state = "idle"                       # idle | running | awaiting_approval
        self.pending_plan: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.next_id = 1
        self.history: list[dict[str, Any]] = []
        self._decision: queue.Queue[tuple[bool, str]] = queue.Queue()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._auto_approve = False
        self.last_summary: RunSummary | None = None
        self._load_history()

    # ------------------------------------------------------------------ events
    def emit(self, type_: str, **data: Any) -> dict[str, Any]:
        with self.cond:
            ev = {"id": self.next_id, "type": type_, "ts": _now(), **data}
            self.next_id += 1
            self.events.append(ev)
            if len(self.events) > MAX_EVENTS:
                del self.events[: len(self.events) - MAX_EVENTS]
            if type_ in PERSISTED_TYPES:
                self.history.append(ev)
                self._append_history(ev)
            self.cond.notify_all()
        return ev

    def events_since(self, since: int) -> list[dict[str, Any]]:
        with self.cond:
            return [e for e in self.events if e["id"] > since]

    def wait_for_events(self, since: int, timeout: float) -> list[dict[str, Any]]:
        with self.cond:
            if not any(e["id"] > since for e in self.events):
                self.cond.wait(timeout)
            return [e for e in self.events if e["id"] > since]

    def snapshot(self) -> dict[str, Any]:
        with self.cond:
            return {
                "id": self.id,
                "workspace": str(self.workspace),
                "state": self.state,
                "has_project": (self.workspace / "project.godot").is_file(),
                "pending_plan": self.pending_plan,
                "history": list(self.history),
                "last_event_id": self.next_id - 1,
                "messages": sum(1 for h in self.history if h["type"] == "user"),
            }

    # ------------------------------------------------------------------ history
    def _history_path(self) -> Path:
        return self.workspace / HISTORY_FILE

    def _load_history(self) -> None:
        p = self._history_path()
        if not p.is_file():
            return
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-MAX_HISTORY_LINES:]:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict) and ev.get("type") in PERSISTED_TYPES:
                ev["id"] = self.next_id
                self.next_id += 1
                self.history.append(ev)
        # replayed history is also visible on the live stream (so a fresh page shows it once, in order)
        self.events.extend(self.history)

    def _append_history(self, ev: dict[str, Any]) -> None:
        p = self._history_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")

    # ------------------------------------------------------------------ run control
    def send(self, text: str, auto_approve: bool = False, plan_only: bool = False) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise SessionError("empty message", "اكتب وصف اللعبة أو التعديل المطلوب أولًا.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise SessionError(f"message too long (> {MAX_MESSAGE_CHARS} chars)")
        ready, hint_ar, hint_en = self.ready_check()
        if not ready:
            raise SessionNotReady("model provider is not configured", hint_ar or hint_en)
        with self.cond:
            if self.state != "idle":
                raise SessionBusy("this project is busy with a previous request",
                                  "انتظر انتهاء الطلب الحالي أو اضغط «إيقاف».")
            self.state = "running"
            self._cancel.clear()
            self._auto_approve = bool(auto_approve)
            while not self._decision.empty():        # stale decisions from an earlier run
                self._decision.get_nowait()
        user_ev = self.emit("user", text=text, auto_approve=bool(auto_approve), plan_only=bool(plan_only))
        self._thread = threading.Thread(target=self._run, args=(text, plan_only), name=f"godotai-chat-{self.id}",
                                        daemon=True)
        self._thread.start()
        return user_ev

    def approve(self, approved: bool, feedback: str = "") -> None:
        with self.cond:
            if self.state != "awaiting_approval" or self.pending_plan is None:
                raise NoPendingPlan("no plan is waiting for approval")
        self._decision.put((bool(approved), (feedback or "").strip()))

    def cancel(self) -> bool:
        with self.cond:
            if self.state == "idle":
                return False
        self._cancel.set()
        self.emit("system", text="⏹ stop requested — finishing the current step, then aborting.",
                  text_ar="⏹ تم طلب الإيقاف — سيتوقف بعد الخطوة الجارية.")
        return True

    def wait(self, timeout: float | None = None) -> bool:
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    # ------------------------------------------------------------------ the run itself
    def _task_with_context(self, text: str) -> str:
        exchanges: list[tuple[str, str]] = []
        pending_user: str | None = None
        for h in self.history[:-1]:                 # the last entry is the message we are about to run
            if h["type"] == "user":
                pending_user = h.get("text", "")
            elif h["type"] in ("done", "error") and pending_user is not None:
                if h["type"] == "done":
                    outcome = f"{h.get('status')}: {_clip(h.get('message', ''), 200)}"
                else:
                    outcome = f"error: {_clip(h.get('message', ''), 200)}"
                exchanges.append((pending_user, outcome))
                pending_user = None
        if not exchanges:
            return text
        lines = [text, "", "---",
                 "Context — earlier requests in this same project directory (oldest first); the files they "
                 "produced are already in the workspace, so read them before planning:"]
        for i, (req, outcome) in enumerate(exchanges[-CONTEXT_EXCHANGES:], 1):
            lines.append(f"{i}. \"{_clip(req, 300)}\" → {outcome}")
        return "\n".join(lines)

    def _gate(self, plan: Plan) -> tuple[bool, str]:
        self.emit("plan", markdown=plan.to_markdown(), plan=plan.to_dict(), auto=self._auto_approve)
        if self._auto_approve:
            self.emit("approval", approved=True, feedback="", auto=True)
            return True, ""
        with self.cond:
            self.state = "awaiting_approval"
            self.pending_plan = {"markdown": plan.to_markdown(), "plan": plan.to_dict()}
            self.cond.notify_all()
        decision: tuple[bool, str]
        while True:
            if self._cancel.is_set():
                decision = (False, "cancelled by the user")
                break
            try:
                decision = self._decision.get(timeout=0.5)
                break
            except queue.Empty:
                continue
        with self.cond:
            self.state = "running"
            self.pending_plan = None
            self.cond.notify_all()
        if not self._cancel.is_set():
            self.emit("approval", approved=decision[0], feedback=decision[1], auto=False)
        return decision

    def _on_tool(self, call: ToolCall, res: ToolResult) -> None:
        preview = res.content if call.name in ("godot_verify", "godot_check_script", "submit_plan") else res.content[:400]
        self.emit("tool", name=call.name, args=_short_args(call.args, limit=160), ok=res.ok,
                  step_id=str(call.args.get("step_id", "")) if isinstance(call.args, dict) else "",
                  preview=preview[:PREVIEW_CHARS] + ("…" if len(preview) > PREVIEW_CHARS else ""))

    def _run(self, text: str, plan_only: bool) -> None:
        try:
            provider = self.provider_factory(self.cfg.agent)
            registry = self.registry_factory()
            agent = Agent(self.cfg, self.workspace, provider, registry, approve=self._gate,
                          log=lambda line: self.emit("log", text=line.strip()),
                          on_text=lambda t: self.emit("assistant", text=t),
                          on_progress=lambda t: self.emit("progress", text=t),
                          on_tool=self._on_tool, should_stop=self._cancel.is_set, plan_only=plan_only)
            self.emit("system", text=f"{provider.name}/{provider.model} · effort={self.cfg.agent.effort} · "
                                     f"Godot {self.cfg.engine.tag} · {'plan only' if plan_only else 'plan → approve → build → verify'}")
            summary = agent.run(self._task_with_context(text))
            self.last_summary = summary
            report = self.workspace / ".godotai" / "last_verification.md"
            self.emit("done", status=summary.status, message=summary.message, iterations=summary.iterations,
                      usage=summary.usage, verification_passed=summary.verification_passed,
                      plan_path=".godotai/PLAN.md" if summary.plan else None,
                      log_path=str(summary.log_path.relative_to(self.workspace)) if summary.log_path else None,
                      verification_path=".godotai/last_verification.md" if report.is_file() else None,
                      has_project=(self.workspace / "project.godot").is_file())
        except ProviderError as exc:
            self.emit("error", message=f"model provider error: {exc}",
                      hint_ar="تحقق من مفتاح API أو عنوان الخادم ثم أعد المحاولة.",
                      hint_en="Check the API key / server URL and try again.")
        except Exception as exc:  # never leave the session stuck in "running"
            self.emit("error", message=f"{type(exc).__name__}: {exc}")
        finally:
            with self.cond:
                self.state = "idle"
                self.pending_plan = None
                self.cond.notify_all()

    # ------------------------------------------------------------------ engine-only actions (no model)
    def verify(self) -> dict[str, Any]:
        from ..verify import verify_project
        with self.cond:
            if self.state != "idle":
                raise SessionBusy("this project is busy with a previous request")
        if not (self.workspace / "project.godot").is_file():
            raise SessionError("no project.godot in this workspace yet — ask the AI to build the game first",
                               "لا يوجد مشروع بعد في هذا المجلد؛ اطلب من الذكاء الاصطناعي أن يبني اللعبة أولًا.")
        report = verify_project(self.workspace, self.cfg)
        (self.workspace / ".godotai").mkdir(exist_ok=True)
        (self.workspace / ".godotai" / "last_verification.md").write_text(report.to_markdown(), encoding="utf-8")
        self.emit("system", text=f"engine verification (no model): {'PASS' if report.passed else 'FAIL'}",
                  markdown=report.to_markdown())
        return {"passed": report.passed, "markdown": report.to_markdown(), "report": report.to_dict()}

    def list_files(self, limit: int = 500) -> list[str]:
        out: list[str] = []
        for p in sorted(self.workspace.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(self.workspace).as_posix()
            parts = rel.split("/")
            hidden = any(part.startswith(".") for part in parts)
            if hidden and rel not in (".godotai/PLAN.md", ".godotai/last_verification.md"):
                continue
            if is_secret_name(p.name):
                continue
            out.append(rel)
            if len(out) >= limit:
                break
        return out

    def read_file(self, rel: str) -> dict[str, Any]:
        try:
            target = safe_path(self.workspace, rel)
        except PathEscape as exc:
            raise SessionError(str(exc)) from None
        if is_secret_name(target.name):
            raise SessionError("refusing to read secret material")
        if not target.is_file():
            raise SessionError(f"{rel!r} does not exist")
        if target.stat().st_size > MAX_FILE_CHARS:
            raise SessionError(f"{rel!r} is too large to display")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise SessionError(f"{rel!r} is not a text file") from None
        return {"path": target.relative_to(self.workspace).as_posix(), "content": content}


class SessionManager:
    """All sessions live under one games directory: ``<games_dir>/<session-id>/`` is the Godot project."""

    def __init__(self, cfg: Config, games_dir: Path, provider_factory: ProviderFactory | None = None,
                 registry_factory: RegistryFactory | None = None, ready_check: ReadyCheck | None = None,
                 include_github: bool = True):
        self.cfg = cfg
        self.games_dir = Path(games_dir).resolve()
        self.games_dir.mkdir(parents=True, exist_ok=True)
        self.provider_factory = provider_factory or make_provider
        self.registry_factory = registry_factory or (lambda: build_registry(include_github=include_github))
        self.ready_check = ready_check or (lambda: model_ready(cfg))
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _load(self, sid: str) -> Session:
        return Session(sid, self.games_dir / sid, self.cfg, self.provider_factory, self.registry_factory,
                       self.ready_check)

    def list(self) -> list[dict[str, Any]]:
        ids = set(self._sessions)
        for p in self.games_dir.iterdir():
            if p.is_dir() and SESSION_ID_RE.match(p.name):
                ids.add(p.name)
        out = []
        for sid in sorted(ids):
            s = self.get(sid)
            if s is None:
                continue
            snap = s.snapshot()
            out.append({"id": sid, "state": snap["state"], "has_project": snap["has_project"],
                        "messages": snap["messages"], "workspace": snap["workspace"]})
        return out

    def running_count(self) -> int:
        """Agent runs in flight right now (model turns / tool calls) — plans waiting for approval are not counted."""
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.state == "running")

    def get(self, sid: str) -> Session | None:
        if not SESSION_ID_RE.match(sid or ""):
            return None
        with self._lock:
            s = self._sessions.get(sid)
            if s is None and (self.games_dir / sid).is_dir():
                s = self._sessions[sid] = self._load(sid)
            return s

    def create(self, name: str) -> Session:
        sid = slugify(name)
        if not sid:
            with self._lock:
                n = 1
                while (self.games_dir / f"game-{n}").exists():
                    n += 1
                sid = f"game-{n}"
        with self._lock:
            if sid in self._sessions:
                return self._sessions[sid]
            (self.games_dir / sid).mkdir(parents=True, exist_ok=True)
            s = self._sessions[sid] = self._load(sid)
            return s

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for s in sessions:
            s.cancel()
        deadline = time.time() + timeout
        for s in sessions:
            s.wait(max(0.0, deadline - time.time()))
