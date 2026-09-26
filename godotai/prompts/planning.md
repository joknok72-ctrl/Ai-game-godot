# Task

{{TASK}}

# Environment

- Project directory: `{{WORKSPACE}}` (all paths are relative to it)
- Existing files: {{FILE_COUNT}} — {{PROJECT_STATE}}
- Pinned engine: Godot {{GODOT_TAG}} — `{{GODOT_BINARY_STATUS}}`
- API index (exact ClassDB of the pinned binary): {{API_INDEX_STATUS}}
- Knowledge base index:
{{KNOWLEDGE_INDEX}}

# What to do now

Phase 1 — THINK & PLAN only. Investigate the project and the knowledge base, look up the
exact signatures of the nodes and methods your design relies on (`api_lookup` — batch them),
decide the design, then call `submit_plan`. Do not write any file yet (writes are blocked
until the plan is approved). If the task is outside Godot {{GODOT_TAG}} game development,
say so instead of planning.
