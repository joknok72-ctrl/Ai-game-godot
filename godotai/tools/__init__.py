"""Tool registry assembly."""
from __future__ import annotations

from .base import Phase, Tool, ToolContext, ToolRegistry, ToolResult


def build_registry(include_github: bool = True) -> ToolRegistry:
    from . import fs, github_tools, godot_tools, knowledge, plan_tool

    reg = ToolRegistry()
    knowledge.register(reg)
    fs.register(reg)
    plan_tool.register(reg)
    godot_tools.register(reg)
    if include_github:
        github_tools.register(reg)
    return reg


__all__ = ["Phase", "Tool", "ToolContext", "ToolRegistry", "ToolResult", "build_registry"]
