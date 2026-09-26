"""Progressive-disclosure knowledge base (SKILL.md style).

The ``knowledge/`` folder holds curated, version-pinned notes about Godot
4.7.2. Only the index is in the system prompt; the model pulls sections on
demand, which keeps the context small and the facts current.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import ToolContext, ToolRegistry, ToolResult

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_WORD_RE = re.compile(r"[a-z0-9_]+")


def knowledge_dir(ctx: ToolContext) -> Path:
    override = ctx.extra.get("knowledge_dir")
    if override:
        return Path(override)
    return ctx.cfg.root / "knowledge"


def split_sections(text: str, source: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    title, buf = f"{source}", []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if buf and "".join(buf).strip():
                sections.append({"source": source, "title": title, "body": "\n".join(buf).strip()})
            title, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    if buf and "".join(buf).strip():
        sections.append({"source": source, "title": title, "body": "\n".join(buf).strip()})
    return sections


def search(kdir: Path, query: str, limit: int = 5) -> list[dict[str, Any]]:
    terms = [t for t in _WORD_RE.findall(query.lower()) if len(t) > 2]
    if not terms or not kdir.is_dir():
        return []
    scored: list[tuple[float, dict[str, str]]] = []
    for md in sorted(kdir.rglob("*.md")):
        for sec in split_sections(md.read_text(encoding="utf-8", errors="replace"), md.relative_to(kdir).as_posix()):
            hay_title = sec["title"].lower()
            hay_body = sec["body"].lower()
            score = 0.0
            for t in terms:
                score += 3.0 * hay_title.count(t) + hay_body.count(t)
            if score > 0:
                scored.append((score, sec))
    scored.sort(key=lambda s: -s[0])
    return [dict(sec, score=score) for score, sec in scored[:limit]]


def register(reg: ToolRegistry) -> None:
    @reg.add(
        "knowledge_search",
        "Search the curated Godot 4.7.2 knowledge base (GDScript rules, node patterns, Android export, "
        "verification checklist). Prefer this over memory for version-specific API details.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
        }, "required": ["query"]},
    )
    def knowledge_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        hits = search(knowledge_dir(ctx), str(args["query"]), int(args.get("limit") or 5))
        if not hits:
            return ToolResult.success("no matching sections; try knowledge_read on INDEX.md")
        chunks = [f"### [{h['source']}] {h['title']}\n{h['body'][:2500]}" for h in hits]
        return ToolResult.success("\n\n".join(chunks))

    @reg.add(
        "knowledge_read",
        "Read a whole knowledge file, e.g. INDEX.md, godot-4.7-essentials.md, android-export.md.",
        {"type": "object", "properties": {"file": {"type": "string"}}, "required": ["file"]},
    )
    def knowledge_read(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        kdir = knowledge_dir(ctx)
        name = str(args["file"]).strip().lstrip("/")
        p = (kdir / name).resolve()
        if kdir.resolve() not in p.parents or not p.is_file():
            files = ", ".join(sorted(f.name for f in kdir.glob("*.md"))) if kdir.is_dir() else "(none)"
            return ToolResult.error(f"unknown knowledge file {name!r}; available: {files}")
        return ToolResult.success(p.read_text(encoding="utf-8", errors="replace"))
