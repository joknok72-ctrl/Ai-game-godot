"""Workspace-sandboxed filesystem tools."""
from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from .base import ToolContext, ToolRegistry, ToolResult

MAX_READ_CHARS = 60_000
# Files the model may neither read nor write inside a project: signing material and
# credential stores (Android keystores, PEM/PKCS12, dotenv, Kaggle/Cloudflare/GitHub token files).
SECRET_GLOBS = ("*.keystore", "*.jks", "*.pem", "*.p12", ".env", ".env.*", "*.key", "*.token",
                "kaggle.json", "access_token", ".netrc", "credentials.json", "service-account*.json", "*.secret")
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".ogg", ".wav", ".mp3", ".ttf", ".otf", ".apk", ".aab", ".pck", ".zip", ".import"}


class PathEscape(ValueError):
    pass


def safe_path(workspace: Path, rel: str) -> Path:
    """Resolve *rel* inside *workspace*; raise if it escapes."""
    if not rel or rel.startswith(("/", "\\")):
        raise PathEscape(f"path must be relative to the project: {rel!r}")
    if rel.startswith("res://"):
        rel = rel[len("res://"):]
    target = (workspace / rel).resolve()
    ws = workspace.resolve()
    if target != ws and ws not in target.parents:
        raise PathEscape(f"path escapes the project directory: {rel!r}")
    return target


def is_secret_name(name: str) -> bool:
    return any(fnmatch.fnmatch(name, g) for g in SECRET_GLOBS)


def register(reg: ToolRegistry) -> None:
    @reg.add(
        "list_files",
        "List files in the project (recursive). Hidden dirs, .godot/ and .import caches are skipped.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Sub-directory, relative. Default: project root."},
            "glob": {"type": "string", "description": "Optional glob such as *.gd or scenes/*.tscn"},
        }, "required": []},
    )
    def list_files(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        base = safe_path(ctx.workspace, args.get("path") or ".")
        if not base.exists():
            return ToolResult.error(f"{args.get('path')!r} does not exist")
        pattern = args.get("glob")
        lines: list[str] = []
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(ctx.workspace).as_posix()
            if any(part.startswith(".") for part in rel.split("/")):
                continue
            if p.is_dir():
                continue
            if pattern and not fnmatch.fnmatch(rel, pattern) and not fnmatch.fnmatch(p.name, pattern):
                continue
            lines.append(f"{rel}  ({p.stat().st_size} B)")
            if len(lines) >= 2000:
                lines.append("… truncated")
                break
        return ToolResult.success("\n".join(lines) or "(empty)")

    @reg.add(
        "read_file",
        "Read a text file from the project (GDScript, .tscn, .tres, project.godot, export_presets.cfg, .md …).",
        {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        }, "required": ["path"]},
    )
    def read_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        p = safe_path(ctx.workspace, args["path"])
        if not p.is_file():
            return ToolResult.error(f"{args['path']!r} is not a file")
        if is_secret_name(p.name):
            return ToolResult.error("refusing to read secret material")
        if p.suffix.lower() in BINARY_SUFFIXES:
            return ToolResult.success(f"(binary file, {p.stat().st_size} bytes)")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        start = int(args.get("start_line") or 1)
        end = int(args.get("end_line") or len(lines))
        chunk = lines[start - 1:end]
        numbered = "\n".join(f"{i:5d}| {l}" for i, l in enumerate(chunk, start))
        if len(numbered) > MAX_READ_CHARS:
            numbered = numbered[:MAX_READ_CHARS] + "\n… truncated; request a smaller line range"
        return ToolResult.success(numbered or "(empty file)")

    @reg.add(
        "write_file",
        "Create or overwrite a text file in the project. Use edit_file for small changes to existing files.",
        {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
        mutating=True,
    )
    def write_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        p = safe_path(ctx.workspace, args["path"])
        if is_secret_name(p.name):
            return ToolResult.error("refusing to write secret material; keystores/passwords come from env vars")
        if ".godot" in p.relative_to(ctx.workspace).parts:
            return ToolResult.error("never write inside the .godot/ cache directory")
        content = str(args["content"])
        if p.suffix == ".gd" and "\t" not in content and "    " in content:
            # GDScript is indentation-sensitive; the engine's style guide uses tabs.
            content = _spaces_to_tabs(content)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        ctx.log(f"  ✎ {'updated' if existed else 'created'} {p.relative_to(ctx.workspace)}")
        return ToolResult.success(f"{'updated' if existed else 'created'} {args['path']} ({len(content)} chars)")

    @reg.add(
        "edit_file",
        "Replace an exact text snippet in an existing file (targeted edit). old_text must occur exactly once.",
        {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        }, "required": ["path", "old_text", "new_text"]},
        mutating=True,
    )
    def edit_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        p = safe_path(ctx.workspace, args["path"])
        if not p.is_file():
            return ToolResult.error(f"{args['path']!r} does not exist; use write_file to create it")
        if is_secret_name(p.name):
            return ToolResult.error("refusing to edit secret material")
        text = p.read_text(encoding="utf-8")
        old, new = str(args["old_text"]), str(args["new_text"])
        count = text.count(old)
        if count == 0:
            return ToolResult.error("old_text not found; read the file again and copy the snippet exactly")
        if count > 1:
            return ToolResult.error(f"old_text occurs {count} times; include more context to make it unique")
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        ctx.log(f"  ✎ edited {p.relative_to(ctx.workspace)}")
        return ToolResult.success(f"edited {args['path']}")

    @reg.add(
        "delete_file",
        "Delete a file from the project.",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        mutating=True,
    )
    def delete_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        p = safe_path(ctx.workspace, args["path"])
        if not p.is_file():
            return ToolResult.error(f"{args['path']!r} is not a file")
        p.unlink()
        ctx.log(f"  ✂ deleted {p.relative_to(ctx.workspace)}")
        return ToolResult.success(f"deleted {args['path']}")


def _spaces_to_tabs(content: str, width: int = 4) -> str:
    out = []
    for line in content.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        n = len(line) - len(stripped)
        out.append("\t" * (n // width) + " " * (n % width) + stripped)
    return "".join(out)
