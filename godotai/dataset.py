"""Turn engine-verified agent runs into training data.

Every ``run`` writes ``.godotai/runs/<stamp>.json`` with the complete, append-only
transcript (system prompt, tool list, every tool call and every engine report).
Runs whose verification passed are *ground-truth-correct by construction*: the
real Godot {tag} accepted the project. Those transcripts — including the
fix-after-failed-verification stretches, which teach repair — are the raw
material for supervised fine-tuning of an open-weight model (see ``training/``).

Output format (one JSON object per line, provider-agnostic):

    {"id": "...", "task": "...", "engine": "4.7.2-stable", "source_model": "...", "status": "success",
     "system": "<system prompt>", "tools": [{"name": ..., "description": ..., "input_schema": ...}],
     "messages": [{"role": "user", "content": "..."},
                  {"role": "assistant", "content": "...", "tool_calls": [{"id":..., "name":..., "args": {...}}]},
                  {"role": "tool", "tool_call_id": "...", "content": "...", "is_error": false}, ...]}

Thinking blocks are never exported (they are redacted by the API anyway), harness
scaffolding (turn-scoped system nudges, the batching sentence next to tool results)
is dropped, and every string is passed through :func:`godotai.secrets.redact`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .secrets import redact_obj


def _anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role, content = m.get("role"), m.get("content")
        if role == "system":                       # turn-scoped nudges: not part of the trajectory
            continue
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text_parts, calls = [], []
            for b in content or []:
                t = b.get("type")
                if t == "text":
                    text_parts.append(b.get("text", ""))
                elif t == "tool_use":
                    calls.append({"id": b.get("id"), "name": b.get("name"), "args": b.get("input") or {}})
                # thinking / redacted_thinking: dropped on purpose
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts).strip()}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        else:  # user: text blocks and/or tool results
            texts = []
            has_results = any(b.get("type") == "tool_result" for b in content or [])
            for b in content or []:
                t = b.get("type")
                if t == "tool_result":
                    c = b.get("content")
                    if isinstance(c, list):
                        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
                    out.append({"role": "tool", "tool_call_id": b.get("tool_use_id"), "content": c or "",
                                "is_error": bool(b.get("is_error"))})
                elif t == "text" and not has_results:
                    # text next to tool results is harness scaffolding (the documented batching
                    # nudge), identical in every run — not part of the trajectory
                    texts.append(b.get("text", ""))
            if texts:
                out.append({"role": "user", "content": "\n".join(texts).strip()})
    return out


def _openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            c = m.get("content") or ""
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": c.removeprefix("ERROR: "),
                        "is_error": c.startswith("ERROR: ")})
        elif role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.get("content") or ""}
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": fn.get("arguments")}
                calls.append({"id": tc.get("id"), "name": fn.get("name"), "args": args})
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        elif role == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = "\n".join(p.get("text", "") for p in c if isinstance(p, dict))
            out.append({"role": "user", "content": c or ""})
    return out


def normalise_run(run: dict[str, Any]) -> dict[str, Any] | None:
    msgs = run.get("messages") or []
    if not msgs:
        return None
    provider = run.get("provider") or ("anthropic" if isinstance(msgs[0].get("content"), list) else "openai_compat")
    conv = _anthropic_messages(msgs) if provider == "anthropic" else _openai_messages(msgs)
    if not any(m["role"] == "assistant" for m in conv):
        return None
    digest = hashlib.sha256(json.dumps(conv, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return {
        "id": digest,
        "task": run.get("task", ""),
        "engine": run.get("engine", ""),
        "source_model": run.get("model", ""),
        "source_provider": provider,
        "status": run.get("status", ""),
        "verification_passed": bool(run.get("verification_passed")),
        "iterations": run.get("iterations", 0),
        "system": run.get("system"),
        "tools": run.get("tools"),
        "messages": conv,
    }


def iter_runs(runs_dir: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for p in sorted(Path(runs_dir).rglob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "messages" in data and "task" in data:
            yield p, data


def extract(runs_dir: Path, out_path: Path, include_failed: bool = False, min_iterations: int = 2) -> dict[str, Any]:
    stats = {"files": 0, "kept": 0, "skipped_failed": 0, "skipped_short": 0, "examples": 0, "redactions": 0,
             "approx_tokens": 0, "out": str(out_path)}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    with out_path.open("w", encoding="utf-8") as fh:
        for _, run in iter_runs(runs_dir):
            stats["files"] += 1
            if not include_failed and not run.get("verification_passed"):
                stats["skipped_failed"] += 1
                continue
            if int(run.get("iterations") or 0) < min_iterations:
                stats["skipped_short"] += 1
                continue
            ex = normalise_run(run)
            if ex is None or ex["id"] in seen:
                continue
            seen.add(ex["id"])
            counter = [0]
            ex = redact_obj(ex, counter)
            stats["redactions"] += counter[0]
            line = json.dumps(ex, ensure_ascii=False)
            stats["approx_tokens"] += len(line) // 4
            fh.write(line + "\n")
            stats["kept"] += 1
            stats["examples"] += 1
    return stats
