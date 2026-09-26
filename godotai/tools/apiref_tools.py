"""API-reference tools: exact signatures from the pinned engine's ClassDB.

Read-only, never blocked by the plan gate. The index is built lazily from the
pinned Godot binary (``--doctool``, ~2 s) and cached on disk, so the first call
in a fresh environment pays the build once.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import apiref
from ..godot import GodotNotFound, GodotVersionMismatch
from ..verify import gd_scripts
from .base import ToolContext, ToolRegistry, ToolResult
from .fs import safe_path


def get_index(ctx: ToolContext) -> apiref.ApiIndex | None:
    """Cached index for the pinned engine; built from the binary when missing."""
    idx = ctx.extra.get("apiref")
    if idx is not None:
        return idx
    idx = apiref.load_index(ctx.cfg.engine)
    if idx is None:
        try:
            from .godot_tools import _godot
            idx = apiref.build_index(_godot(ctx))
            ctx.log(f"  📚 built API index for Godot {idx.engine_tag}: {idx.stats()['classes']} classes")
        except (GodotNotFound, GodotVersionMismatch, apiref.ApiRefError) as exc:
            ctx.extra["apiref_error"] = str(exc)
            return None
    ctx.extra["apiref"] = idx
    return idx


def _unavailable(ctx: ToolContext) -> ToolResult:
    return ToolResult.error("API index unavailable: " + ctx.extra.get("apiref_error", "pinned Godot binary not found")
                            + ". Fall back to knowledge_search and godot_check_script.")


def project_known_classes(ctx: ToolContext) -> set[str]:
    scripts = [(p.as_posix(), p.read_text(encoding="utf-8", errors="replace")) for p in gd_scripts(ctx.workspace)]
    return apiref.user_classes(scripts)


def register(reg: ToolRegistry) -> None:
    @reg.add(
        "api_lookup",
        "Exact API of the pinned Godot version, generated from the engine binary itself (ClassDB). Give a class "
        "to list its properties/methods/signals/enums, or class + member for one exact signature (searches the "
        "inheritance chain). Use `@GlobalScope` for free functions/global enums (lerp, randf_range, Error, Key) "
        "and `@GDScript` for built-ins/annotations (preload, range, @export_range). Unknown names return "
        "did-you-mean suggestions and Godot-3→4 renames. Call this BEFORE using any API you are not certain "
        "about, and batch several lookups in one turn.",
        {"type": "object", "properties": {
            "class_name": {"type": "string", "description": "e.g. CharacterBody2D, Tween, @GlobalScope"},
            "member": {"type": "string", "description": "optional method/property/signal/constant/annotation name"},
            "enum": {"type": "string", "description": "optional enum name inside the class, e.g. ProcessMode"},
        }, "required": ["class_name"]},
    )
    def api_lookup(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        idx = get_index(ctx)
        if idx is None:
            return _unavailable(ctx)
        cls = str(args["class_name"]).strip()
        if args.get("enum"):
            text = idx.render_enum(cls, str(args["enum"]).strip())
            return ToolResult.success(text) if text else ToolResult.error(
                f"{cls} has no enum {args['enum']!r} in Godot {idx.engine_tag}")
        member = (args.get("member") or "").strip() or None
        text = idx.render_class(cls, member)
        missing = "does not exist" in text or "has no member" in text
        if missing and member and idx.resolve(cls) is None:
            g = idx.find_global(member)
            if g:
                text += "\n" + idx.render_member(*g)
        return ToolResult(not missing, text, is_error=missing)

    @reg.add(
        "api_search",
        "Full-text search over all class, method, property, signal, constant and annotation names of the pinned "
        "Godot version (e.g. 'change scene', 'screen touch', 'tween property', 'collision layer value'). Returns "
        "exact signatures. Use it when you know what you want to do but not the exact API name.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        }, "required": ["query"]},
    )
    def api_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        idx = get_index(ctx)
        if idx is None:
            return _unavailable(ctx)
        hits = idx.search(str(args["query"]), int(args.get("limit") or 20))
        if not hits:
            return ToolResult.success("no matches; try other words or api_lookup on a parent class")
        return ToolResult.success("\n".join(hits))

    @reg.add(
        "api_lint",
        "Static check of GDScript files against the pinned engine: Godot-3 idioms (yield, export var, .instance(), "
        "KinematicBody2D, connect(\"sig\", self, \"fn\") …) and class names that do not exist in this version. "
        "Cheaper than godot_check_script and catches runtime-only mistakes on untyped code. Omit `path` to lint "
        "the whole project.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "one .gd file, relative; omit for all scripts"},
        }, "required": []},
    )
    def api_lint(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        idx = get_index(ctx)  # may be None → legacy-pattern lint only
        if args.get("path"):
            p = safe_path(ctx.workspace, args["path"])
            if not p.is_file():
                return ToolResult.error(f"{args['path']!r} not found")
            text = p.read_text(encoding="utf-8", errors="replace")
            findings = {p.relative_to(ctx.workspace).as_posix(): apiref.lint_script(text, idx, project_known_classes(ctx))}
            findings = {k: v for k, v in findings.items() if v}
        else:
            findings = apiref.lint_project(ctx.workspace, idx)
        if not findings:
            note = "" if idx else " (index unavailable — only Godot-3 idiom patterns were checked)"
            return ToolResult.success("api_lint: no findings" + note)
        lines = apiref.format_findings(findings)
        has_error = any(f.severity == "error" for fs in findings.values() for f in fs)
        return ToolResult(not has_error, "\n".join(lines), is_error=has_error)


__all__ = ["register", "get_index", "project_known_classes"]
