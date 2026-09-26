# Research notes (September 2026)

All web research was done on **2026-09-26**. Each claim is tagged:
**[verified]** = read directly from an official/primary source (URL given);
**[secondary]** = blog/aggregator, plausible but not primary; **[assumption]** = our design choice.

## 1. Godot Engine 4.7.2-stable

- **[verified]** 4.7.2-stable is a maintenance release published **18 August 2026**
  (https://godotengine.org/article/maintenance-release-godot-4-7-2/). 4.7 "Lights, Camera, Action!" is the
  current stable minor; 4.7.1 shipped 14 July 2026 (https://godotengine.org/releases/4.7/).
- **[verified]** Official binaries and `SHA512-SUMS.txt`:
  https://github.com/godotengine/godot-builds/releases/tag/4.7.2-stable — asset names
  `Godot_v4.7.2-stable_linux.x86_64.zip`, `Godot_v4.7.2-stable_export_templates.tpz`.
  We downloaded both, verified the checksums and the binary prints `4.7.2.stable.official.ed1daf0bf`.
  The checksum file is committed under `engine/checksums/4.7.2-stable/`.
- **[verified]** Command line (https://docs.godotengine.org/en/latest/tutorials/editor/command_line_tutorial.html):
  `--headless`, `--path`, `--import`, `--script … --check-only`, `--quit-after N`,
  `--export-debug <preset> <path>`, `--export-release`, `--install-android-build-template`.
- **[verified]** 4.6 → 4.7 migration notes (https://docs.godotengine.org/en/4.7/tutorials/migrating/upgrading_to_godot_4.7.html):
  small API tweaks only (`Object.is_class` takes `StringName`, `RichTextLabel` image-width enum renamed, …).
  Summarised in `knowledge/godot-4.7-essentials.md`.
- **[verified]** Export templates are stored under `~/.local/share/godot/export_templates/4.7.2.stable/`; editor
  settings per minor version in `~/.config/godot/editor_settings-4.7.tres`.

## 2. Android APK export requirements

Source: https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html (stable = 4.7 on
2026-09-26) and https://docs.godotengine.org/en/stable/tutorials/export/android_gradle_build.html.

- **[verified]** OpenJDK **17** is the recommended JDK.
- **[verified]** SDK packages: Platform-Tools 35.0.0+, **Build-Tools 35.0.1**, **Platform 35**, command-line
  tools (latest), **CMake 3.10.2.4988404**, **NDK r28b (28.1.13356709)**. These are the values in `godot.toml`.
- **[verified]** Editor settings `export/android/java_sdk_path` and `export/android/android_sdk_path` must point
  at the JDK and SDK; a debug keystore is generated with `keytool` (alias `androiddebugkey`, password `android`).
- **[verified]** Keystore credentials can come from environment variables
  `GODOT_ANDROID_KEYSTORE_DEBUG_PATH/USER/PASSWORD` and `GODOT_ANDROID_KEYSTORE_RELEASE_PATH/USER/PASSWORD`
  (https://docs.godotengine.org/en/stable/classes/class_editorexportplatformandroid.html). We rely on this so no
  password is ever written into `export_presets.cfg`.
- **[verified in our sandbox]** With `gradle_build/use_gradle_build=false` a debug APK exports with only
  `platform-tools`, `build-tools;35.0.1`, `cmdline-tools;latest` + JDK 17 present (27.6 MiB, arm64-v8a).
  The Gradle path (custom builds, AAB, plugins) additionally needs NDK/CMake/build template — **not tested**.
- **[verified]** Command-line tools download used in the workflow/Dockerfile:
  `https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip`
  (https://developer.android.com/studio → "Command line tools only", read 2026-09-26). This URL rotates with
  new releases; it is a single value in `godot.toml`.

## 3. Claude Fable 5.1 — what matters for an agent built on it

Sources: https://platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1 ,
https://platform.claude.com/docs/en/models/fable-5-1/overview , https://platform.claude.com/docs/en/build-with-claude/effort .

- **[verified]** API id `claude-fable-5-1`; 1M context, 128K max output; adaptive thinking always on;
  default effort `high`; knowledge cutoff Jun 2026.
- **[verified]** Effort is set with `output_config: {"effort": …}`; levels `low|medium|high|xhigh|max`,
  all five supported by Fable 5.1. `max` = "absolute maximum capability with no constraints on token spending".
  Docs: "At `high` and above, set a large `max_tokens`" → we use `effort="max"`, `max_tokens=64000`
  (the user explicitly asked for Fable 5.1 at MAX).
- **[verified]** Breaking rules we implement: `tool_choice` `any`/`tool` → 400 (we always send `auto`);
  `thinking: {type: enabled, budget_tokens}` or `disabled` → 400 (we send no `thinking`); thinking blocks
  are bound to the model and to the `system`/`tools` prefix, and editing earlier turns invalidates them
  (we keep an append-only transcript, constant system prompt + tool list, and replay assistant content
  verbatim including signatures). `stop_reason: "refusal"` must be handled (we do).
- **[verified]** Fewer user-facing progress updates at higher effort → our system prompt explicitly asks for one
  short line per step; strict tool use (`strict: true`) is available and is a config switch (`strict_tools`).
- **[verified]** Prompt-cache guidance: hold top-level effort constant within a conversation (we do); per-message
  effort change is beta (`mid-conversation-output-config-2026-07-01`) — not used.
- **[secondary]** Independent write-ups (Artificial Analysis, AWS/GCP model pages) describe Fable 5.1 as the
  strongest option for long-horizon agentic coding; we did not run our own eval.

## 4. What makes a specialised coding agent strong (design inputs)

- **[verified]** Anthropic, "Effective context engineering for AI agents" and "Agent Skills": keep the
  always-on prompt small, load specialised knowledge on demand (SKILL.md / progressive disclosure), give the
  agent real tools and a real environment. → `knowledge/` + `knowledge_search`, engine in the container.
- **[secondary]** "From Plan to Action: How Well Do Agents Follow the Plan?" (arXiv 2604.12147) and the
  plan-then-execute pattern literature: agents drift from their plans unless following is enforced.
  → mandatory `submit_plan`, human gate, `step_id` on every mutation.
- **[secondary]** "Agentic Agile-V" (arXiv 2605.20456), Addy Osmani's "The 80% problem", Sonar/Futurum on
  verifiers: **testing must be inside the loop**; verification, not generation, is the bottleneck.
  → the run cannot succeed without `godot_verify` PASS; broken scripts are proven to fail.
- **[secondary]** Open-source agents surveyed: OpenHands (MIT), Aider, SWE-agent, Goose, OpenCode, Cline/Roo,
  Qwen Code, Kimi CLI. All are general-purpose; none ships engine-backed verification for Godot.
  **[assumption]** A ~2k-line purpose-built agent with a hard scope is more reliable for this one job than
  configuring a general agent, and is trivially auditable. Any of those agents can still be pointed at this
  repo's CLI (`godotai verify/export`) as tools.
- **[secondary]** Godot MCP servers (Coding-Solo/godot-mcp, GDAI MCP, godot-ai, …) expose scene/script
  editing to chat assistants but don't verify by running the game; several authors state they "can't one-shot
  a whole game". We chose direct headless engine control instead of an MCP dependency.
- **[secondary]** Open-weight coding models in 2026 (GLM-5.2, Qwen3-Coder, DeepSeek V4, Kimi K2.6/K3) are
  usable via OpenAI-compatible servers → `openai_compat` provider, effort mapped to `reasoning_effort`.
- **[verified]** GDScript tooling: gdtoolkit 4.x (`gdlint`, `gdformat`) — used as an *advisory* step.
  Godot 4 headless CI images (barichello/godot-ci) initialise the editor once with `--editor --quit` and place
  templates under `~/.local/share/godot/export_templates/<version>` — mirrored by `install.py`.

## 5. Limitations discovered while building

- Godot prints RID/ObjectDB leak messages when a scene is quit from a script; treated as noise, not errors.
- `--check-only` needs the project imported first for `class_name` globals to resolve → pipeline imports first.
- ZIP extraction with Python drops Unix mode bits → `sdkmanager` must be re-chmodded (fixed in `android.py`).
- `adb` may print `cannot connect to daemon` after export when `shutdown_adb_on_exit` is on — harmless.
- Docker was not available in the build sandbox → the image is untested; the CI job installs the engine
  directly on the runner instead and verifies the template with it.
