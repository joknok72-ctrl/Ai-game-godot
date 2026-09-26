"""Run quotas for the chat server — the cost/abuse brake in front of *your* model server.

Every message that starts an agent run spends GPU time on your server (or Workers AI neurons, or a vendor
key if you opted into one). Cloudflare Access decides **who** may open the site; this module decides
**how much** each of them may start once inside — a second line of defence that still holds when a
request reaches the origin without passing the edge (misconfigured tunnel, leaked origin address, a
visitor sharing a shared ``--token``):

* ``max_concurrent`` — agent runs that may be *in flight at once* on the whole server (0 = unlimited).
  A 7B model on one GPU serves one run well; a second concurrent run halves both. Also caps the blast
  radius of a runaway browser tab.
* ``per_day`` — runs one visitor may *start per rolling 24 hours* (0 = unlimited). Visitors are keyed by
  their Cloudflare Access identity (``email``, else ``sub``); by ``"token"`` in shared-token mode; by
  ``"local"`` on loopback. The window is in memory (restart = fresh window) — deliberately simple.

The server enforces both **before** ``Session.send`` and answers ``429 Too Many Requests`` with a
``Retry-After`` header and a bilingual hint. Approving a plan, cancelling, verifying with the engine and
reading files are *not* counted — they do not start a new model run.

This complements, and does not replace, Cloudflare's own knobs (the Access allow-list, a WAF rate-limiting
rule on ``/api/``, AI Gateway rate limits / spend limits for Workers AI — see ``deploy/cloudflare/README.md``).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Mapping

from .session import SessionError

ENV_MAX_CONCURRENT = "GODOTAI_MAX_CONCURRENT_RUNS"
ENV_PER_DAY = "GODOTAI_MAX_RUNS_PER_DAY"
DAY = 24 * 3600.0
MAX_IDENTITIES = 10_000           # hard cap on the in-memory table (a flood of distinct identities cannot grow it forever)


class QuotaExceeded(SessionError):
    """A quota said no. ``status`` is 429 and ``retry_after`` (seconds, ≥ 1) feeds the ``Retry-After`` header."""

    status = 429

    def __init__(self, message: str, hint: str, retry_after: float, kind: str):
        super().__init__(message, hint)
        self.retry_after = max(1, int(retry_after + 0.999))
        self.kind = kind                                  # "concurrent" | "daily"


def identity_of(viewer: Mapping[str, Any] | None, token_mode: bool) -> str:
    """Who is asking: the Access e-mail (or subject), the shared token holder, or the local user."""
    if viewer:
        for key in ("email", "sub"):
            v = viewer.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        return "access:unknown"
    return "token" if token_mode else "local"


class RunQuota:
    def __init__(self, max_concurrent: int = 0, per_day: int = 0, clock: Callable[[], float] = time.time):
        if max_concurrent < 0 or per_day < 0:
            raise ValueError("quota limits must be 0 (unlimited) or positive")
        self.max_concurrent = int(max_concurrent)
        self.per_day = int(per_day)
        self.clock = clock
        self._lock = threading.Lock()
        self._starts: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------ helpers
    @property
    def enabled(self) -> bool:
        return bool(self.max_concurrent or self.per_day)

    def _prune(self, identity: str, now: float) -> deque[float]:
        q = self._starts.get(identity)
        if q is None:
            if len(self._starts) >= MAX_IDENTITIES:
                # drop the identity whose newest start is the oldest — the least recently active one
                victim = min(self._starts, key=lambda k: self._starts[k][-1] if self._starts[k] else 0.0)
                del self._starts[victim]
            q = self._starts[identity] = deque()
        while q and now - q[0] >= DAY:
            q.popleft()
        return q

    def used_today(self, identity: str) -> int:
        with self._lock:
            return len(self._prune(identity, self.clock()))

    # ------------------------------------------------------------------ the gate
    def reserve(self, identity: str, running: int) -> float:
        """Admit one new run for *identity* (returns the start stamp) or raise :class:`QuotaExceeded`.

        *running* is the number of agent runs currently in flight on the server (the caller counts them).
        Call :meth:`release` with the stamp if the run could not actually be started afterwards.
        """
        now = self.clock()
        with self._lock:
            if self.max_concurrent and running >= self.max_concurrent:
                raise QuotaExceeded(
                    f"server busy: {running} agent run(s) in progress, limit {self.max_concurrent}",
                    "الخادم مشغول بطلب آخر الآن — انتظر حتى ينتهي (أو ألغِه من مشروعه) ثم أعد الإرسال.",
                    retry_after=30, kind="concurrent")
            q = self._prune(identity, now)
            if self.per_day and len(q) >= self.per_day:
                wait = DAY - (now - q[0])
                hours = max(1, int(wait // 3600 + (1 if wait % 3600 else 0)))
                raise QuotaExceeded(
                    f"daily quota reached: {self.per_day} run(s) per 24 h for this visitor",
                    f"وصلت إلى الحد اليومي ({self.per_day} طلب بناء كل 24 ساعة). حاول بعد نحو {hours} ساعة، "
                    f"أو غيّر GODOTAI_MAX_RUNS_PER_DAY على الخادم.",
                    retry_after=wait, kind="daily")
            q.append(now)
            return now

    def release(self, identity: str, stamp: float) -> None:
        """Undo a reservation whose run never started (e.g. the session turned out to be busy)."""
        with self._lock:
            q = self._starts.get(identity)
            if not q:
                return
            try:
                q.remove(stamp)
            except ValueError:
                pass

    def describe(self) -> dict[str, Any]:
        return {"max_concurrent": self.max_concurrent, "per_day": self.per_day, "enabled": self.enabled}


def from_env(env: Mapping[str, str] | None = None, max_concurrent: int | None = None,
             per_day: int | None = None) -> RunQuota | None:
    """Build a quota from explicit values, else from ``GODOTAI_MAX_CONCURRENT_RUNS`` / ``GODOTAI_MAX_RUNS_PER_DAY``.

    Returns ``None`` when both limits are 0/unset (the local default: no brake on your own laptop).
    """
    import os
    env = os.environ if env is None else env

    def pick(explicit: int | None, name: str) -> int:
        if explicit is not None:
            return int(explicit)
        raw = (env.get(name) or "").strip()
        if not raw:
            return 0
        try:
            value = int(raw)
        except ValueError:
            raise ValueError(f"{name} must be an integer (0 = unlimited), got {raw!r}") from None
        if value < 0:
            raise ValueError(f"{name} must be 0 or positive")
        return value

    mc, pd = pick(max_concurrent, ENV_MAX_CONCURRENT), pick(per_day, ENV_PER_DAY)
    if not mc and not pd:
        return None
    return RunQuota(mc, pd)
