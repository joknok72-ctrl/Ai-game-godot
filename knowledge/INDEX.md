# Knowledge base index (Godot 4.7.2-stable)

Curated, version-pinned notes the agent loads on demand with `knowledge_search` /
`knowledge_read`. Keep entries short, factual and sourced. When the engine pin in
`godot.toml` changes, review every file here against the new release notes.
These notes are *patterns*; exact class/method signatures come from the engine-generated
ClassDB index (`api_lookup`, `api_search`, `api_lint`), never from memory.

- godot-4.7-essentials.md — GDScript 2 rules, node/scene conventions, project.godot keys, common pitfalls, 4.7 highlights
- game-architecture-patterns.md — scene composition, state machines, signals, spawning/pooling, mobile input, save data
- android-export.md — verified Android export requirements (JDK 17, SDK 35 packages, env vars, keystores, presets, CLI)
- verification-checklist.md — what "done" means: engine-backed checks the agent must pass before claiming success
- scene-file-format.md — how to hand-write valid .tscn/.tres files that survive `--import`
