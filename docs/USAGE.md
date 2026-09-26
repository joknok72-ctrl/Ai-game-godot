# Usage

## Requirements

- Linux x86_64 (the pinned editor build), Python **3.11+** (uses `tomllib`), `unzip`/`git` for GitHub tools.
- For Android export: OpenJDK 17 (`sudo apt install openjdk-17-jdk-headless`), ~3 GB disk for SDK + templates.
- A model API key: `ANTHROPIC_API_KEY` (default provider) **or** an OpenAI-compatible server.

## Commands

```
python3 -m godotai doctor                      # environment vs. the pin; exit 1 if something is missing
python3 -m godotai install-godot [--system] [--editor-only] [--bin-dir DIR] [--force]
python3 -m godotai setup-android [--sdk-root DIR] [--no-packages]
python3 -m godotai new --template mobile-2d --dest DIR --name "Name" [--package com.x.y] | --list
python3 -m godotai verify --project DIR [--frames N] [--no-lint] [--json report.json]
python3 -m godotai export --project DIR [--preset Android] [--out build/android/game.apk] [--release]
python3 -m godotai plan "task" --workspace DIR                 # THINK + PLAN only, writes .godotai/PLAN.md
python3 -m godotai run  "task" --workspace DIR [--yes] [--no-github]
```

`run` prints the plan and asks `Approve this plan? [y]es / [n]o + feedback`. Saying `n` sends your feedback
to the model and it re-plans. `--yes` auto-approves (for CI / unattended containers).

## Configuration

`godot.toml` (searched upward from the current directory, or `GODOTAI_CONFIG=/path/godot.toml`):

| Key | Meaning | Env override |
| --- | --- | --- |
| `engine.version` / `engine.release` | The pin, e.g. `4.7.2` / `stable` | `GODOTAI_ENGINE_VERSION`, `GODOTAI_ENGINE_RELEASE` |
| `engine.flavor` | `standard` (GDScript) or `mono` | — |
| `android.*` | JDK major, cmdline-tools URL, sdkmanager packages, preset name | — |
| `agent.provider` | `anthropic` or `openai_compat` | `GODOTAI_PROVIDER` |
| `agent.model` | `claude-fable-5-1` (default) or any model id | `GODOTAI_MODEL` |
| `agent.effort` | `low|medium|high|xhigh|max` (default `max`) | `GODOTAI_EFFORT` |
| `agent.max_tokens` | per-turn output cap (thinking + text) | — |
| `agent.max_iterations` / `max_verify_rounds` | hard stops | — |
| `agent.require_plan_approval` | human gate on/off | — |
| `agent.strict_tools` | send `strict: true` on tool schemas | — |
| `agent.base_url` | custom endpoint | `GODOTAI_BASE_URL` |

### Using an open-source model instead of Claude

```bash
export GODOTAI_PROVIDER=openai_compat GODOTAI_MODEL=qwen3-coder OPENAI_BASE_URL=http://localhost:11434/v1
python3 -m godotai run "make a flappy-bird clone for Android" --workspace ./flappy --yes
```
`GODOTAI_SEND_REASONING_EFFORT=1` forwards the effort level as `reasoning_effort` for servers that support it.

## Android export

Debug APK (local): `python3 -m godotai export --project ./game` → `build/android/game.apk`, signed with the
debug keystore created by `setup-android`.

Release APK (local):

```bash
keytool -v -genkey -keystore ~/keys/release.keystore -alias mygame -keyalg RSA -validity 10000
export GODOT_ANDROID_KEYSTORE_RELEASE_PATH=~/keys/release.keystore
export GODOT_ANDROID_KEYSTORE_RELEASE_USER=mygame
export GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD=...
python3 -m godotai export --project ./game --release --out build/android/game-release.apk
```

Release APK (GitHub Actions): add repository secrets `ANDROID_KEYSTORE_BASE64` (`base64 -w0 release.keystore`),
`ANDROID_KEYSTORE_USER`, `ANDROID_KEYSTORE_PASSWORD`, then run the *Build Android APK* workflow with
`build_type=release` (or let the agent call `github_build_apk` with `build_type: release`).

## Writing tasks the agent does well

- State the genre, platform and controls: *"2D endless runner for Android, one-thumb tap to jump, portrait"*.
- Mention constraints: *"no external assets, placeholder shapes, 60 fps on low-end phones"*.
- Ask for an APK explicitly if you want one: *"…and export a debug APK"*.
- Arabic works: *"اعمل لعبة سباق سيارات 2D للموبايل بالتحكم بالسحب"* — the model answers in Arabic, code stays English.

## Where things land

- Plan: `<workspace>/.godotai/PLAN.md`; engine report: `<workspace>/.godotai/last_verification.md`;
  full transcript: `<workspace>/.godotai/runs/*.json` (git-ignored — it contains the whole conversation).
- APK: `<workspace>/build/android/*.apk` (git-ignored). GitHub artifacts: `android-apk-debug|release`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Godot binary … reports '4.x.y' but this AI is pinned to 4.7.2.stable` | Install the pinned build: `python3 -m godotai install-godot --force`, or set `GODOT_BIN`. |
| `export templates … are not installed` | `python3 -m godotai install-godot` (without `--editor-only`). |
| Export fails with Java/SDK path errors | `python3 -m godotai setup-android`; check `~/.config/godot/editor_settings-4.7.tres`. |
| `POLICY: no file/system changes are allowed before a plan is approved` in the transcript | Expected — the model tried to skip planning and was blocked. |
| Plan rejected: `godot_version must be 4.7.2-stable` | The model targeted another version; it is told to fix it automatically. |
