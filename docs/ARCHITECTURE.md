# Architecture

godotai is a small, auditable agent: ~2 000 lines of stdlib-only Python around one
idea — **the model is never trusted; the engine is.** Every claim of success has to
come from running the pinned Godot 4.7.2-stable binary headless.

```
 user task ──► Agent.run()
               │  THINK  (read project, knowledge_search)           ── read-only tools only
               │  PLAN   submit_plan → validate_plan()              ── schema + version + path checks
               │  GATE   human approves / rejects with feedback     ── CLI prompt or --yes
               │  ACT    write_file / edit_file / run_command …     ── every call needs step_id ∈ plan
               │  VERIFY godot_verify → verify_project()            ── real engine: import, check-only, smoke test
               └─ REPORT RunSummary + .godotai/runs/<ts>.json
```

## Modules

| Module | Responsibility |
| --- | --- |
| `config.py` | Loads `godot.toml`; `EngineConfig` derives every official asset name/URL from `version`+`release`; env overrides (`GODOTAI_*`); strict — unknown keys are errors. |
| `godot.py` | `Godot` wrapper: finds the binary, **refuses any version other than the pin**, runs `--import`, `--check-only`, `--quit-after`, `--script`, `--export-debug/--export-release`. |
| `verify.py` | The verification pipeline → `VerificationReport` (markdown + JSON). `classify_output()` turns raw engine output into errors/warnings, ignoring known headless noise (root warning, RID leaks at forced quit, dummy renderer). |
| `planner.py` | Plan schema (strict JSON schema), `validate_plan()` (correct engine version, sequential S1..Sn ids, safe relative paths, no secret files, a final `verify` step, mandatory `godot_import`/`check_scripts`/`smoke_test`), persistence to `.godotai/plan.json` + `PLAN.md`. |
| `tools/base.py` | Tool registry + **policy**: mutating tools return `POLICY:` errors before approval, need a known `step_id` during ACT, and are frozen when DONE. Handler exceptions never crash the loop. |
| `tools/fs.py` | Workspace-sandboxed `list_files`/`read_file`/`write_file`/`edit_file`/`delete_file`; refuses path escapes, secret files (`*.keystore`, `.env`, …) and writes into `.godot/`; converts 4-space indentation to tabs in `.gd`. |
| `tools/godot_tools.py` | `godot_info`, `godot_verify`, `godot_check_script`, `godot_run_headless`, `godot_export`, allow-listed `run_command` (no network binaries; secrets stripped from the child environment). |
| `tools/github_tools.py` | `github_create_repo`, `github_push_project`, `github_build_apk`, `github_build_status` over the REST API + git (token via `GIT_ASKPASS`, never in URLs). `sync_workflow()` copies `build-android.yml` into game repos with the pin applied. |
| `tools/knowledge.py` | Progressive-disclosure knowledge base: only `knowledge/INDEX.md` bullets are in the prompt; `knowledge_search`/`knowledge_read` pull sections on demand. |
| `providers/anthropic.py` | Claude Messages API tuned for **Claude Fable 5.1**: `tool_choice: auto` only, no `thinking`/`temperature`, `output_config.effort`, constant `system`+`tools` prefix with `cache_control`, thinking blocks replayed verbatim, `stop_reason: refusal` handled. |
| `providers/openai_compat.py` | Any OpenAI-compatible chat-completions server (Ollama, vLLM, OpenRouter, llama.cpp) → open-weight models. |
| `agent.py` | The loop above; append-only transcript; nudges the model if it stops without a plan or a passing verification; hard limits `max_iterations` / `max_verify_rounds`. |
| `install.py` / `android.py` / `scaffold.py` | Engine installer (SHA-512 verified), Android SDK + editor settings + debug keystore, project scaffolding from `templates/`. |
| `__main__.py` | CLI: `doctor`, `install-godot`, `setup-android`, `new`, `verify`, `export`, `plan`, `run`. |

## Design decisions (and why)

1. **Version pin as data, not code.** `godot.toml` is read by the agent, installer, Dockerfile, CI and templates.
   Changing the pin = edit one file + add the official `SHA512-SUMS.txt`. Tests (`tests/test_infra_sync.py`)
   fail if the workflows drift from the config.
2. **Policy in code, not in prose.** Prompts ask the model to plan first; the registry *enforces* it. A model
   that ignores instructions still cannot write a file before approval or outside the approved plan.
3. **Constant prefix.** Claude Fable 5.1 binds thinking blocks to the `system`/`tools` prefix and requires an
   append-only history. We therefore keep one tool list for the whole run and gate by phase instead of
   swapping tool sets (which is also what makes prompt caching effective).
4. **Engine-backed verification, not self-assessment.** `--import` catches malformed `.tscn`/`.tres` and
   missing resources, `--check-only` is the engine's own static analysis for GDScript, and the smoke test
   (`tests/smoke_test.gd`, a `SceneTree` script) drives the real main scene for N frames and asserts state.
5. **Human gate with feedback.** Rejecting a plan sends the reviewer's text back to the model and returns
   to the PLAN phase. Mid-execution re-planning is allowed but audited (`.godotai/plan.json` is rewritten).
6. **Secrets never touch files or the model.** Keystore paths/passwords come from the
   `GODOT_ANDROID_KEYSTORE_*` environment variables Godot reads natively; the model cannot read or write
   `*.keystore`, `*.jks`, `*.pem`, `.env`; `run_command` children get a scrubbed environment; the GitHub token
   is passed via `GIT_ASKPASS`.
7. **Stdlib only.** No SDKs, no framework lock-in: the whole agent is readable in one sitting and runs in the
   minimal Docker image. Open-source agent frameworks (OpenHands, Aider, SWE-agent, Goose, OpenCode, Cline)
   were evaluated and are far more general than needed; their generality is exactly what a single-purpose
   agent should *not* carry. See RESEARCH.md.

## Data written to a game workspace

```
<workspace>/
  .godotai/plan.json, PLAN.md         the approved plan (audit trail)
  .godotai/last_verification.md       last engine report
  .godotai/runs/<timestamp>.json      full transcript + usage (git-ignored by default)
  .github/workflows/build-android.yml added by github_push_project if missing
  .gitignore                          added by github_push_project if missing
```

## Extending

- **New template:** add `templates/<name>/` with `project.godot`, `export_presets.cfg`, `tests/smoke_test.gd`,
  using the `__GAME_NAME__`, `__PACKAGE__`, `__GODOT_FEATURE__`, `__GODOT_TAG__` placeholders.
- **New knowledge:** add a `.md` under `knowledge/` and a bullet in `INDEX.md` (tests enforce the index).
- **New tool:** register it in `godotai/tools/*.py` with `mutating=True` if it changes state — the policy
  layer then applies automatically.
- **New engine version:** change `godot.toml`, add `engine/checksums/<tag>/SHA512-SUMS.txt`, review
  `knowledge/` against the new release notes, run `python3 -m godotai doctor` and the test suite.
