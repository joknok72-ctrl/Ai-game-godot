# Usage

## Requirements

- Linux x86_64 (the pinned editor build), Python **3.11+** (uses `tomllib`), `unzip`/`git` for GitHub tools.
- For Android export: OpenJDK 17 (`sudo apt install openjdk-17-jdk-headless`), ~3 GB disk for SDK + templates.
- A model API key: `ANTHROPIC_API_KEY` (default provider) **or** an OpenAI-compatible server.
  Credentials are read from environment variables only — never from files in the repo (see `SECURITY.md`).

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

python3 -m godotai apiref build [--docs-dir godot/doc/classes] [--force]   # ClassDB index from the pinned binary (~2 s)
python3 -m godotai apiref lookup CharacterBody2D move_and_slide           # exact signature, inherited members resolved
python3 -m godotai apiref search "change scene"                            # find the API when you only know the intent
python3 -m godotai apiref lint --project DIR                               # Godot-3 idioms / unknown classes & methods
python3 -m godotai eval list                                               # engine-verified task bank
python3 -m godotai eval run --task flappy-clone --workspace DIR [--yes]    # run the agent on a task, then score
python3 -m godotai eval score --task template-baseline --project DIR       # score an existing project (no model)
python3 -m godotai dataset extract --runs DIR --out data/sft.jsonl [--include-failed]   # verified runs → SFT JSONL
python3 scripts/secret_scan.py [PATH]                                      # token-shaped strings in a tree (CI step)
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
| `agent.route` | `direct` (default) · `cf_gateway` (Cloudflare AI Gateway) · `workers_ai` (Cloudflare Workers AI, needs `openai_compat`) | `GODOTAI_ROUTE` |
| `agent.act_effort` | effort from plan approval onward (per-message effort, beta); unset = same as `effort` | `GODOTAI_ACT_EFFORT` |
| `agent.batch_nudge` | one-line reminder after tool results to batch independent calls (default on) | `GODOTAI_BATCH_NUDGE` |
| `agent.long_output_note` | tell the model the real `max_tokens` at `xhigh`/`max` (default on) | `GODOTAI_LONG_OUTPUT_NOTE` |
| `agent.progress_updates` | beta: short status lines between tool calls in the run log | `GODOTAI_PROGRESS_UPDATES` |
| `agent.turn_scoped_system` | beta: nudges as turn-scoped `system` messages instead of text | `GODOTAI_TURN_SCOPED_SYSTEM` |
| `agent.task_budget_tokens` | beta: advisory whole-task token budget (≥ 20000; 0/unset = off) | `GODOTAI_TASK_BUDGET` |
| `agent.prefix_binding_drop` | beta debugging aid for preserved-thinking prefix mismatches | `GODOTAI_PREFIX_BINDING_DROP` |

All of these are documented (commented) in `godot.toml`; the beta ones add the corresponding `anthropic-beta`
header only when enabled, so a default configuration sends a plain, non-beta request.

### Using an open-source model instead of Claude

```bash
export GODOTAI_PROVIDER=openai_compat GODOTAI_MODEL=qwen3-coder OPENAI_BASE_URL=http://localhost:11434/v1
python3 -m godotai run "make a flappy-bird clone for Android" --workspace ./flappy --yes
```
`GODOTAI_SEND_REASONING_EFFORT=1` forwards the effort level as `reasoning_effort` for servers that support it.
A LoRA adapter produced by `training/train_qlora.py` is served the same way (vLLM/llama.cpp/Ollama) — see
`training/README.md`; `python3 -m godotai eval run` is how you find out whether it is actually better.

### Routing through Cloudflare (optional)

Nothing about your Cloudflare account is stored in the repository; set variables and pick a route:

```bash
# Claude via Cloudflare AI Gateway (logs, caching, rate limits, cost dashboard):
export CF_ACCOUNT_ID=<account id>            # identifier, not a secret
export CF_AIG_GATEWAY=<gateway name>         # created in the Cloudflare dashboard → AI → AI Gateway
export ANTHROPIC_API_KEY=...                 # pass-through; or store the key in the gateway (BYOK) and
export CF_AIG_TOKEN=...                      #   send only the authenticated-gateway token instead
GODOTAI_ROUTE=cf_gateway python3 -m godotai run "..." --workspace ./g

# Cloudflare-hosted open-weight model (Workers AI, OpenAI-compatible endpoint):
export CLOUDFLARE_API_TOKEN=...              # scoped token with Workers AI permission only
GODOTAI_ROUTE=workers_ai GODOTAI_PROVIDER=openai_compat GODOTAI_MODEL=<workers-ai model id> \
    python3 -m godotai eval run --task template-baseline --workspace /tmp/eval
```

Endpoints/headers follow the Cloudflare docs read on 2026-09-26 (`docs/RESEARCH.md` §4). The gateway does not
make a model smarter; treat a Workers AI model as a candidate to *measure*, not a drop-in.

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
- API index: `~/.cache/godotai/apiref/<engine-tag>/index.json` (rebuilt from the binary in ~2 s).
- Eval results: `evals/results/*.json`; extracted datasets: `data/*.jsonl` (both git-ignored).
- APK: `<workspace>/build/android/*.apk` (git-ignored). GitHub artifacts: `android-apk-debug|release`.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Godot binary … reports '4.x.y' but this AI is pinned to 4.7.2.stable` | Install the pinned build: `python3 -m godotai install-godot --force`, or set `GODOT_BIN`. |
| `export templates … are not installed` | `python3 -m godotai install-godot` (without `--editor-only`). |
| Export fails with Java/SDK path errors | `python3 -m godotai setup-android`; check `~/.config/godot/editor_settings-4.7.tres`. |
| `POLICY: no file/system changes are allowed before a plan is approved` in the transcript | Expected — the model tried to skip planning and was blocked. |
| Plan rejected: `godot_version must be 4.7.2-stable` | The model targeted another version; it is told to fix it automatically. |
| `api_lint`: `KinematicBody2D → CharacterBody2D` or `unknown method … on <Class>` | Godot-3 API or a typo. Errors block the eval task; warnings on user-defined symbols are advisory. |
| `CF_ACCOUNT_ID is not set (needed for this route)` | `route = cf_gateway`/`workers_ai` needs the variables listed above; use `route = direct` otherwise. |
| `secret-scan: N finding(s)` in CI | A token-shaped string was committed. Rotate it, remove it, or mark a deliberate placeholder line with `secret-scan:allow`. |
