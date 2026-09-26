# Usage

## Requirements

- Linux x86_64 (the pinned editor build), Python **3.11+** (uses `tomllib`), `unzip`/`git` for GitHub tools.
- For Android export: OpenJDK 17 (`sudo apt install openjdk-17-jdk-headless`), ~3 GB disk for SDK + templates.
- **A model server of your own** (the default): any OpenAI-compatible server — vLLM (GPU), Ollama or llama.cpp (CPU) —
  serving the open-weight base (`Qwen/Qwen2.5-Coder-7B-Instruct`, Apache-2.0) or your fine-tuned adapter under the name
  `godotai` at `http://127.0.0.1:8000/v1`. No vendor key is needed. A vendor model (Claude) is an explicit, labelled
  opt-in used as a *reference* — see `docs/MODEL.md`.
  Credentials, when any exist, are read from environment variables only — never from files in the repo (see `SECURITY.md`).

## Commands

```
python3 -m godotai doctor                      # environment vs. the pin (+ your model server + model identity); exit 1 if something is missing
python3 -m godotai install-godot [--system] [--editor-only] [--bin-dir DIR] [--force]
python3 -m godotai setup-android [--sdk-root DIR] [--no-packages]
python3 -m godotai new --template mobile-2d --dest DIR --name "Name" [--package com.x.y] | --list
python3 -m godotai verify --project DIR [--frames N] [--no-lint] [--json report.json]
python3 -m godotai export --project DIR [--preset Android] [--out build/android/game.apk] [--release]
python3 -m godotai plan "task" --workspace DIR                 # THINK + PLAN only, writes .godotai/PLAN.md
python3 -m godotai run  "task" --workspace DIR [--yes] [--no-github]
python3 -m godotai chat [--port 8765] [--games-dir ./games] [--yes] [--no-github] [--open]   # browser chat UI (below)
python3 -m godotai chat --host 0.0.0.0 --public-host games.example.com \
                        --access-team-domain <team> --access-aud <aud>    # public website behind Cloudflare Tunnel + Access

python3 -m godotai apiref build [--docs-dir godot/doc/classes] [--force]   # ClassDB index from the pinned binary (~2 s)
python3 -m godotai apiref lookup CharacterBody2D move_and_slide           # exact signature, inherited members resolved
python3 -m godotai apiref search "change scene"                            # find the API when you only know the intent
python3 -m godotai apiref lint --project DIR                               # Godot-3 idioms / unknown classes & methods
python3 -m godotai eval list                                               # engine-verified task bank
python3 -m godotai eval run --task flappy-clone --workspace DIR [--yes]    # run the agent on a task, then score
python3 -m godotai eval score --task template-baseline --project DIR       # score an existing project (no model)
python3 -m godotai eval compare [--candidate MODEL] [--reference MODEL] [--results-dir DIR]   # two models, common tasks, engine as judge
python3 -m godotai dataset extract --runs DIR --out data/sft.jsonl [--include-failed]   # verified runs → SFT JSONL
python3 scripts/secret_scan.py [PATH]                                      # token-shaped strings in a tree (CI step)
python3 scripts/cloudflare_setup.py --dry-run --hostname H --team-domain T --emails E --account-id A --zone-id Z   # website plan, no network
```

`run` prints the plan and asks `Approve this plan? [y]es / [n]o + feedback`. Saying `n` sends your feedback
to the model and it re-plans. `--yes` auto-approves (for CI / unattended containers).

## Chat UI — where you talk to the AI

The chat runs on your own machine (default) or on your own server as a website behind Cloudflare Access
(`deploy/cloudflare/`), and talks to the model server *you* run. **There is no instance hosted by this repository.**
`chat` is the same agent as `run` behind a browser page, so everything below about configuration, plan approval,
verification and where files land applies unchanged.

```bash
python3 -m godotai install-godot            # once — the engine is what verifies every game
export PATH="$HOME/.local/bin:$PATH"
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --served-model-name godotai \
     --enable-auto-tool-choice --tool-call-parser hermes          # your model server (or Ollama: see "Your model server")
python3 -m godotai chat                     # → http://127.0.0.1:8765/
```

Open the printed URL, create a project (a folder under `--games-dir`, default `./games/<slug>`), pick a quick prompt
(Flappy Bird, endless runner, match-3, platformer, top-down shooter, 2048, HUD + pause, APK export) or type what game
you want — Arabic or English — and press send. The page then shows, live: the model's messages, each tool call and
its result, the **plan** with *approve / reject-with-feedback* buttons (unless you tick *auto-approve*), the engine
verification report and the final summary. A project keeps its chat history in `<project>/.godotai/chat.jsonl`
(git-ignored), so reloading the page or restarting the server resumes where you were, and a follow-up message
("make the player faster") continues in the same workspace with the previous requests as context.

The header tells you what you are talking to: **your model server** (reachable? is the served name in `GET …/models`?),
the pinned engine, the hosting mode (💻 local · 🔑 token · 🔐 Cloudflare Access) and the **model identity card** —
base model, licence, whether it is yours, whether it is trained from scratch (it is not) — straight from `[model]` in
`godot.toml`. No quality claim is shown; the card points to `eval compare`.

Options:

| Flag | Meaning |
| --- | --- |
| `--host` / `--port` | Bind address (default `127.0.0.1:8765`; `--port 0` picks a free one). |
| `--games-dir DIR` | Parent folder for projects (default `./games`). |
| `--token SECRET` (or `GODOTAI_CHAT_TOKEN`) | Shared secret for non-loopback binds (Docker/LAN). Required on every `/api/*` request as `Authorization: Bearer …` (the page does this after you paste it or open `…/#token=…`). |
| `--public-host HOST` (or `GODOTAI_PUBLIC_HOST`) | Hostname the page is reached at through Cloudflare Tunnel / a reverse proxy (repeatable); accepted in the `Host` check. |
| `--access-team-domain TEAM` + `--access-aud AUD` (or `GODOTAI_ACCESS_TEAM_DOMAIN` / `GODOTAI_ACCESS_AUD`) | Require a valid **Cloudflare Access** JWT (`Cf-Access-Jwt-Assertion` header or `CF_Authorization` cookie) on the page, the assets and the API; verified locally against the team's JWKS (RS256, issuer, audience, expiry). The alternative to `--token` for a public website. |
| `--max-concurrent-runs N` / `--max-runs-per-day N` (or `GODOTAI_MAX_CONCURRENT_RUNS` / `GODOTAI_MAX_RUNS_PER_DAY`) | Run quota for shared/public servers: at most N agent runs in flight on the whole server, and N runs per visitor (Access e-mail, else the token holder, else `local`) per rolling 24 h; `0` = unlimited (the loopback default). Beyond the limit `POST …/messages` answers **429** with `Retry-After` and a bilingual hint before any model work starts; approve / cancel / verify / files are not counted. The compose file sets 1 / 40. |
| `--yes` | Tick *auto-approve the plan* by default in the page (same meaning as `run --yes`). |
| `--no-github` | Do not expose the GitHub tools to the model. |
| `--open` | Open the page in your default browser. |
| `--verbose` | Log every HTTP request (tokens are redacted). |
| `--check` | Start, print URL + readiness, stop — the CI smoke test. |

Readiness is shown in the page header and printed at startup: the pinned Godot binary (`❌` → `install-godot`)
and the model (`❌` → the page names the exact command: start your server, or export the variable a vendor route
needs; sending is refused with a **503** and nothing is spent until it is ready). The model-server probe is a single
`GET <base_url>/models` to *your* URL, cached for 5 s; `GODOTAI_SKIP_MODEL_PROBE=1` disables it (the page then says
"not probed", never "reachable"). Everything in `godot.toml` / the env overrides below (provider, model, effort,
route, identity) is honoured by `chat` exactly as by `run`.

Docker (image from `docker/Dockerfile`, entrypoint is already `python3 -m godotai`):

```bash
docker run --rm -it -e OPENAI_BASE_URL=http://host.docker.internal:8000/v1 -e GODOTAI_CHAT_TOKEN=<choose-a-secret> \
       -p 127.0.0.1:8765:8765 -v "$PWD/games:/games" godotai chat --host 0.0.0.0 --games-dir /games
```

A token is mandatory because `0.0.0.0` is not loopback; `-p 127.0.0.1:8765:8765` keeps the port on the host
only. Do not expose it to the internet with just a token: use the Cloudflare Tunnel + Access path below (or another
authenticating reverse proxy with TLS); the server is a small `http.server`, not a hardened web app.

### Public website: Cloudflare Tunnel + Access (optional)

```bash
python3 scripts/cloudflare_setup.py --dry-run --hostname games.example.com --team-domain myteam \
    --emails you@example.com --account-id <account id> --zone-id <zone id>       # the 4 API calls, printed, no network
export CLOUDFLARE_API_TOKEN=<new token: Access Apps+Policies Edit, Cloudflare Tunnel Edit, DNS Edit>   # this shell only
python3 scripts/cloudflare_setup.py --hostname games.example.com --team-domain myteam --emails you@example.com \
    --account-id <account id> --zone-id <zone id> --write-env deploy/cloudflare/.env
unset CLOUDFLARE_API_TOKEN
docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d      # or --profile gpu-lora / --profile cpu
```

The script verifies the token read-only first (`GET /user/tokens/verify`), looks up an existing Access application /
tunnel / CNAME with the same hostname or name and reuses them (a second run converges; `--no-reuse-existing` forbids it),
then creates what is missing: the Access application (e-mail allow-list) *first*, then the tunnel, its ingress
(`games.example.com → http://chat:8765`, protected with Access, `http_status:404` catch-all) and the proxied CNAME;
it writes the tunnel token only to the `.env` (mode 0600) and never prints it. `--discard-tunnel-token` +
`--facts-json PATH` is the CI mode used by the manual `cloudflare-provision.yml` workflow (no copy of the connector token
is kept; take it from Networking → Tunnels on the server). Prerequisites Cloudflare does not provide: a domain on
Cloudflare and an always-on machine for the chat and the model. The compose file publishes no port —
`cloudflared` dials out — and starts the chat with `--public-host/--access-*` so every request is re-verified. Details,
verification steps and what is *not* tested live: `deploy/cloudflare/README.md`.

HTTP API (what the page uses; useful for scripting):

| Route | Purpose |
| --- | --- |
| `GET /healthz` | `{"ok": true}` — the only route that answers without Access/token (container health probe). |
| `GET /api/status` | Version, engine + model readiness, model-server probe (`reachable`, `models`, `model_listed`), model identity, games dir, hosting mode (`local`/`token`/`access`), public hosts, and — behind Access — the signed-in viewer's e-mail. |
| `GET/POST /api/sessions` | List projects / create one (`{"name": "…"}` → slug folder). |
| `GET /api/sessions/<id>` | Snapshot: state, history, pending plan. |
| `GET /api/sessions/<id>/events?since=N` | Server-sent events (`Last-Event-ID` resumes). |
| `POST /api/sessions/<id>/messages` | `{"text", "auto_approve"?, "plan_only"?}` → **202**, the run happens in a background thread. |
| `POST /api/sessions/<id>/approve` | `{"approved": true|false, "feedback"?}` for the pending plan. |
| `POST /api/sessions/<id>/cancel` | Cooperative stop between model turns / at the approval gate. |
| `POST /api/sessions/<id>/verify` | Engine-only verification of the project (no model call). |
| `GET /api/sessions/<id>/files[?path=…]` | Read-only listing / viewer, sandboxed to the project (secret-named files refused). |

Guards: unexpected `Host` header → 403; cross-origin `POST` → 403; missing/wrong token → 401; missing/invalid Access
JWT → 401 (page, assets and API alike); strict `Content-Security-Policy`, no inline script, no external assets; one
run per project at a time (**409** while busy).

## Configuration

`godot.toml` (searched upward from the current directory, or `GODOTAI_CONFIG=/path/godot.toml`):

| Key | Meaning | Env override |
| --- | --- | --- |
| `engine.version` / `engine.release` | The pin, e.g. `4.7.2` / `stable` | `GODOTAI_ENGINE_VERSION`, `GODOTAI_ENGINE_RELEASE` |
| `engine.flavor` | `standard` (GDScript) or `mono` | — |
| `android.*` | JDK major, cmdline-tools URL, sdkmanager packages, preset name | — |
| `agent.provider` | `openai_compat` (default — **your** server) or `anthropic` (vendor, requires `[model].kind = "vendor_api"`) | `GODOTAI_PROVIDER` |
| `agent.model` | `godotai` (default: the *served* name on your server) or any model id | `GODOTAI_MODEL` |
| `agent.base_url` | Your server's OpenAI-compatible endpoint. Precedence: this key → `GODOTAI_BASE_URL` → `OPENAI_BASE_URL` → `https://api.openai.com/v1` only if `OPENAI_API_KEY` is set → `http://127.0.0.1:8000/v1` | `GODOTAI_BASE_URL` |
| `agent.effort` | `low|medium|high|xhigh|max` (default `max`); forwarded to open-weight servers as `reasoning_effort` only with `GODOTAI_SEND_REASONING_EFFORT=1` | `GODOTAI_EFFORT` |
| `agent.max_tokens` | per-turn output cap (thinking + text) | — |
| `agent.max_iterations` / `max_verify_rounds` | hard stops | — |
| `agent.require_plan_approval` | human gate on/off | — |
| `agent.strict_tools` | send `strict: true` on tool schemas | — |
| `agent.route` | `direct` (default) · `cf_gateway` (Cloudflare AI Gateway) · `workers_ai` (Cloudflare Workers AI, needs `openai_compat`) | `GODOTAI_ROUTE` |
| `agent.act_effort` | effort from plan approval onward (Claude per-message effort, beta); unset = same as `effort` | `GODOTAI_ACT_EFFORT` |
| `agent.batch_nudge` | one-line reminder after tool results to batch independent calls (default on) | `GODOTAI_BATCH_NUDGE` |
| `agent.long_output_note` | tell the model the real `max_tokens` at `xhigh`/`max` (default on) | `GODOTAI_LONG_OUTPUT_NOTE` |
| `agent.progress_updates` | Claude beta: short status lines between tool calls in the run log | `GODOTAI_PROGRESS_UPDATES` |
| `agent.turn_scoped_system` | Claude beta: nudges as turn-scoped `system` messages instead of text | `GODOTAI_TURN_SCOPED_SYSTEM` |
| `agent.task_budget_tokens` | Claude beta: advisory whole-task token budget (≥ 20000; 0/unset = off) | `GODOTAI_TASK_BUDGET` |
| `agent.prefix_binding_drop` | Claude beta debugging aid for preserved-thinking prefix mismatches | `GODOTAI_PREFIX_BINDING_DROP` |
| `model.name` | what the page calls your model (default `godotai`) | `GODOTAI_MODEL_NAME` |
| `model.kind` | `open_weight_deployment` (default) · `fine_tune` · `from_scratch` · `vendor_api` — see `docs/MODEL.md` | `GODOTAI_MODEL_KIND` |
| `model.base_model` / `model.base_license` | the weights you start from and their licence (default `Qwen/Qwen2.5-Coder-7B-Instruct`, `Apache-2.0`) | `GODOTAI_BASE_MODEL`, `GODOTAI_BASE_LICENSE` |
| `model.adapter` | your LoRA adapter name/path (required for `fine_tune`) | `GODOTAI_ADAPTER` |
| `model.serving` | `self_hosted` (default) · `managed` · `vendor` | `GODOTAI_SERVING` |
| `model.reference_model` | default counterpart of `eval compare` (`claude-fable-5-1`) | `GODOTAI_REFERENCE_MODEL` |

All of these are documented (commented) in `godot.toml`; the Claude betas add the corresponding `anthropic-beta`
header only when enabled and only for the vendor provider. The loader refuses contradictory identities (a vendor
model labelled as yours, a `fine_tune` without an adapter, a `from_scratch` without a training report).

### Your model server (the default)

```bash
# GPU — vLLM serves the open-weight base under the name the config expects, with tool calling enabled
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --served-model-name godotai --enable-auto-tool-choice --tool-call-parser hermes
# GPU — your fine-tuned adapter from training/ under the same name (and say so: kind = fine_tune, adapter = godotai-lora)
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --enable-lora --lora-modules godotai=training/output/godotai-lora \
     --enable-auto-tool-choice --tool-call-parser hermes
GODOTAI_MODEL_KIND=fine_tune GODOTAI_ADAPTER=godotai-lora python3 -m godotai chat
# CPU — Ollama (slow, for trying things out)
ollama pull qwen2.5-coder:7b
OPENAI_BASE_URL=http://127.0.0.1:11434/v1 GODOTAI_MODEL=qwen2.5-coder:7b python3 -m godotai chat
# containers — the compose file used for the website also works locally (model server only)
docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d model
```

`GODOTAI_SEND_REASONING_EFFORT=1` forwards the effort level as `reasoning_effort` for servers that support it.
`python3 -m godotai eval run` + `eval compare` is how you find out whether a given server/model/adapter is actually
good at this job — see `docs/MODEL.md` §5.

### Claude as a labelled reference (opt-in)

```bash
GODOTAI_PROVIDER=anthropic GODOTAI_MODEL=claude-fable-5-1 GODOTAI_MODEL_KIND=vendor_api GODOTAI_SERVING=vendor \
GODOTAI_BASE_MODEL=claude-fable-5-1 ANTHROPIC_API_KEY=<your key> \
    python3 -m godotai eval run --task flappy-clone --workspace /tmp/ref/flappy --yes
python3 -m godotai eval compare --candidate godotai --reference claude-fable-5-1
```

Without `GODOTAI_MODEL_KIND=vendor_api` + `GODOTAI_SERVING=vendor` the config loader exits with an error: a vendor
model is never presented as your private one (the UI then shows "third-party vendor model via API — not yours").

### Routing through Cloudflare (optional)

Nothing about your Cloudflare account is stored in the repository; set variables and pick a route:

```bash
# A vendor model via Cloudflare AI Gateway (logs, caching, rate limits, cost dashboard) — still a vendor model:
export CF_ACCOUNT_ID=<account id>            # identifier, not a secret
export CF_AIG_GATEWAY=<gateway name>         # created in the Cloudflare dashboard → AI → AI Gateway
export ANTHROPIC_API_KEY=...                 # pass-through; or store the key in the gateway (BYOK) and
export CF_AIG_TOKEN=...                      #   send only the authenticated-gateway token instead
GODOTAI_ROUTE=cf_gateway GODOTAI_PROVIDER=anthropic GODOTAI_MODEL=claude-fable-5-1 GODOTAI_MODEL_KIND=vendor_api \
    GODOTAI_SERVING=vendor GODOTAI_BASE_MODEL=claude-fable-5-1 python3 -m godotai run "..." --workspace ./g

# Cloudflare-hosted open-weight model (Workers AI, OpenAI-compatible endpoint) — open weights, managed hosting:
export CF_WORKERS_AI_TOKEN=...               # a SEPARATE token with only the Workers AI Read permission (never the Tunnel/DNS setup token)
GODOTAI_ROUTE=workers_ai GODOTAI_PROVIDER=openai_compat GODOTAI_MODEL=<workers-ai model id> GODOTAI_SERVING=managed \
    GODOTAI_BASE_MODEL=<workers-ai model id> python3 -m godotai eval run --task template-baseline --workspace /tmp/eval
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
- The quick-prompt buttons in the chat page are complete examples of this style; edit them before sending.

## Where things land

- Plan: `<workspace>/.godotai/PLAN.md`; engine report: `<workspace>/.godotai/last_verification.md`;
  full transcript: `<workspace>/.godotai/runs/*.json` (git-ignored — it contains the whole conversation).
- Chat: projects under `./games/<slug>/` (git-ignored); each keeps its conversation in `.godotai/chat.jsonl`.
- API index: `~/.cache/godotai/apiref/<engine-tag>/index.json` (rebuilt from the binary in ~2 s).
- Eval results: `evals/results/*.json` (each carries the model id that produced it — what `eval compare` groups by);
  extracted datasets: `data/*.jsonl` (both git-ignored).
- Website: `deploy/cloudflare/.env` (git-ignored, mode 0600, holds the tunnel token) — never commit or paste it.
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
| `Your model server 'godotai' is not reachable at http://127.0.0.1:8000/v1` (page / `doctor`) | Start your server (the hint prints the exact `vllm serve …` / Ollama / compose command) or point `OPENAI_BASE_URL` / `GODOTAI_BASE_URL` at it, then press *refresh status*. |
| Page: the served model list does not contain `godotai` | Your server runs but under another name: add `--served-model-name godotai` (vLLM), or set `GODOTAI_MODEL=<the name it lists>`. |
| `The model server at … rejected the key (HTTP 401/403)` | Your server expects an API key: `export OPENAI_API_KEY=<what your server expects>`. |
| `[agent] uses Claude (Anthropic), a third-party vendor model, but [model].kind = … would present it as your own model` (config error) | You chose a vendor model: declare it (`GODOTAI_MODEL_KIND=vendor_api GODOTAI_SERVING=vendor GODOTAI_BASE_MODEL=<id>`) — or return to the private default. |
| `eval compare` prints `NO EVIDENCE` | Neither model has engine-scored results on a common task yet: run `eval run --task <id>` for both, then compare. |
| `CF_ACCOUNT_ID is not set (needed for this route)` | `route = cf_gateway`/`workers_ai` needs the variables listed above; use `route = direct` otherwise. |
| Chat page: **429** `server busy` / `daily quota reached` (with `Retry-After`) | The run quota (`--max-concurrent-runs` / `--max-runs-per-day`, compose defaults 1 / 40) said no: wait for the running build to finish (or cancel it), or raise the limits in `deploy/cloudflare/.env`. `/api/status` shows `quota.running` and `quota.used_today`. |
| `CF_WORKERS_AI_TOKEN is not set` | `route = workers_ai` needs a Cloudflare token with the Workers AI Read permission in `CF_WORKERS_AI_TOKEN` (legacy name `CLOUDFLARE_API_TOKEN` still works, but keep the setup token out of containers). |
| `secret-scan: N finding(s)` in CI | A token-shaped string was committed. Rotate it, remove it, or mark a deliberate placeholder line with `secret-scan:allow`. |
| Chat: `cannot listen on 127.0.0.1:8765` | Another process has the port: `python3 -m godotai chat --port 8766` (the page URL changes accordingly). |
| Chat: `refusing to listen on '0.0.0.0' without a token or Cloudflare Access` | Non-loopback binds need `--token <secret>` / `GODOTAI_CHAT_TOKEN` (Docker, LAN) or `--access-team-domain` + `--access-aud` (website). |
| Chat: `Cloudflare Access needs both the team domain and the AUD tag` | Pass both flags (or both `GODOTAI_ACCESS_*` variables); the AUD is on the application's Overview page in Zero Trust. |
| Chat page: **401** `Cloudflare Access: no Cloudflare Access token` | You reached the server without going through Cloudflare (e.g. by IP). Open the public URL; only allowed e-mails can sign in. |
| Chat page: **401** `Cloudflare Access: audience mismatch (token is for another Access application)` / `issuer mismatch` | `--access-aud` / `--access-team-domain` do not match the Access application in front; re-copy them from Zero Trust (or re-run `cloudflare_setup.py --reuse-tunnel … --reuse-aud …`). |
| Chat page: **503** `model provider is not configured` / not ready | Follow the hint in the page (start your server / export the named variable) and press *refresh status*. Nothing was sent to the model. |
| Chat page: **403** `unexpected Host header` | Open exactly the URL the server printed (`http://127.0.0.1:<port>/`), or add the hostname with `--public-host`. |
| Chat page: **409** `this project is busy with a previous request` | One run per project at a time — wait, press *Stop* (cancel), or create another project. |
| `docker compose up`: `required variable GODOTAI_PUBLIC_HOST is missing a value` (or `TUNNEL_TOKEN`) | The `.env` next to the compose file is incomplete: run `scripts/cloudflare_setup.py --write-env deploy/cloudflare/.env` and append the model lines from `env.example`. |
