# Research notes (September 2026)

All web research was done on **2026-09-26**. Each claim is tagged:
**[verified]** = read directly from an official/primary source (URL given);
**[secondary]** = blog/aggregator, plausible but not primary; **[assumption]** = our design choice.
Raw copies of every page read are kept outside the repo (they are large); the URLs below are the sources.

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
  `--export-debug <preset> <path>`, `--export-release`, `--install-android-build-template`,
  and **`--doctool <path>`** (dumps the engine's own class reference as XML — used by `godotai/apiref.py`).
- **[verified in our sandbox]** `godot --headless --doctool` on the pinned binary yields **1 076 classes,
  10 731 methods, 6 999 properties, 503 signals, 6 012 constants** (ClassDB only; descriptions are empty in the
  ClassDB dump — the engine's `doc/classes` tree can be supplied with `--docs-dir` for descriptions).
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

## 3. Claude Fable 5.1 — *why* it reasons well, and what we copied into this project

The user asked: is Fable 5.1 strong "because it thinks for a long time, or because of what?" — and to build
the answer into the specialised Godot AI. Sources (all read 2026-09-26):
https://platform.claude.com/docs/en/models/fable-5-1/whats-new-fable-5-1 ,
https://platform.claude.com/docs/en/models/fable-5-1/overview ,
https://platform.claude.com/docs/en/build-with-claude/thinking ,
https://platform.claude.com/docs/en/build-with-claude/effort ,
https://platform.claude.com/docs/en/build-with-claude/task-budgets ,
https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages ,
https://platform.claude.com/docs/en/build-with-claude/preserved-thinking ,
https://platform.claude.com/docs/en/api/beta-headers .

### 3.1 What the documentation actually says

- **[verified]** API id `claude-fable-5-1`; **1M-token context** (default and maximum), **128K max output**;
  price $10 / $50 per MTok (input / output), cache reads $0.25 / MTok; default effort `high`; knowledge cutoff
  Jun 2026. Positioned as "successor to Claude Fable 5, for long-running agentic coding, knowledge work, and
  research".
- **[verified]** It is *not* "thinks for a fixed long time". **Adaptive thinking is always on**: the model
  decides *whether* and *how deeply* to think per request; `thinking: {type: enabled, budget_tokens}` and
  `{type: disabled}` both return 400. Thinking "restates what is being asked, tries approaches, checks
  intermediate results, and abandons paths that do not hold up" — i.e. deliberate reasoning with
  self-checking, not just longer output.
- **[verified]** **Interleaved thinking is automatic**: "reasoning between tool calls appears in thinking blocks",
  so the model reasons about *each tool result* before acting on it, and the thinking allocation can span the
  whole assistant turn. This is the property that matters most for an engine-in-the-loop agent: read the
  engine error → think → fix → verify again.
- **[verified]** **Effort** (`output_config.effort`, levels `low|medium|high|xhigh|max`) "affects all tokens in
  the response", including tool calls and how often/deeply it thinks; `max` = "absolute maximum capability
  with no constraints on token spending". Fable 5.1 "improves on Fable 5, and the gap is widest at higher
  effort levels". Docs advise re-tuning effort and **changing it mid-conversation** (beta: a `{"role":
  "system"}` message carrying `output_config.effort`).
- **[verified]** **Task budgets** (beta `task-budgets-2026-03-13`): `output_config.task_budget = {type:
  "tokens", total: N}` gives the model a running countdown over the whole agentic loop (thinking + tool calls +
  tool results + output) so it "prioritizes work and finishes gracefully"; documented minimum 20 000.
- **[verified]** **Progress updates** (beta `thinking-display-updates-2026-08-18`): `thinking.display:
  "updates"` returns the short status lines the model writes between tool calls as readable text.
- **[verified]** **Mid-conversation system messages**: append `{"role": "system"}` at the point an instruction
  becomes relevant instead of editing the top-level `system` (which would invalidate the prompt cache);
  optional `clear_at` scoping. Mid-conversation *tool* changes (beta `inline-tools-2026-09-15`) likewise avoid
  touching the `tools` array.
- **[verified]** **Preserved thinking**: thinking blocks are signed and bound to their prefix (`system`,
  `tools`, earlier `messages`); changing the prefix invalidates them (400 or drop, selectable via
  `thinking.block_binding.prefix_mismatch_behavior`). Accounts created on/after 2026-08-31 are enforced by
  default → "make your integration append-only regardless of your account's age". Allowed without
  invalidation: removing a leading run of thinking blocks, server-side compaction, moving `cache_control`,
  changing `effort` between requests.
- **[verified]** Cache economics: Fable 5.1 cache reads cost 0.025 × base input (vs 0.1 on other Claude models);
  512-token minimum cacheable prompt.

### 3.2 So, why is it "smart"? (our synthesis — **[assumption]** where it goes beyond the docs)

Not a single cause. The docs support four contributors: (1) **deliberate, self-checking reasoning that
scales with effort** — the model spends more when the task is hard and the gains concentrate at high effort;
(2) **reasoning at every tool boundary** (interleaved thinking) — decisions are made *after* seeing real
results, not from a plan made blind; (3) **long-horizon controls** — 1M context, 128K output, task budgets and
per-message effort so it can finish long jobs without cutting off; (4) **a harness that keeps its own past
reasoning valid** — append-only history, constant prefix, cached system/tools. None of this is "GPU makes it
smart"; it is *how the loop is driven* plus a strong base model.

### 3.3 What this repository does with that (concrete, tested)

| Fable 5.1 property | godotai counterpart | Where |
| --- | --- | --- |
| Adaptive thinking + effort | `effort = "max"` default, `act_effort` (per-message effort for the ACT phase), no `thinking` param ever sent | `config.py`, `providers/anthropic.py` |
| Interleaved thinking on tool results | the loop feeds *engine* results (import / check-only / smoke test / API lint) back as tool results, so every reasoning step is grounded in the real 4.7.2 binary | `agent.py`, `verify.py`, `tools/godot_tools.py` |
| Real signatures instead of recall | **ClassDB index generated from the pinned binary** (`--doctool`): `api_lookup`, `api_search`, `api_lint`; advisory lint in every verification | `apiref.py`, `tools/apiref_tools.py` |
| Task budgets | `task_budget_tokens` (beta header sent only when set) | `config.py`, `providers/anthropic.py` |
| Progress updates | `progress_updates` → `thinking.display: "updates"`, surfaced in run logs | same |
| Mid-conversation system messages | `turn_scoped_system` → nudges as `{"role":"system"}` + `clear_at` instead of text | same |
| Preserved thinking / append-only | transcript is append-only; constant `system`+`tools`; `prefix_binding_drop` debugging aid | `agent.py`, `providers/anthropic.py` |
| Batching independent tool calls | one-line nudge after tool results (`batch_nudge`), prompt guidance | `agent.py`, `prompts/system.md` |
| Learn from the strong model | verified trajectories → dataset → QLoRA → same eval bank | `dataset.py`, `evals.py`, `training/` |

## 4. Cloudflare — what an "API + Account ID" can and cannot do here

Sources (read 2026-09-26): https://developers.cloudflare.com/ai-gateway/usage/providers/anthropic/ ,
https://developers.cloudflare.com/ai-gateway/configuration/authentication/ ,
https://developers.cloudflare.com/ai-gateway/usage/chat-completion/ ,
https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/ ,
https://developers.cloudflare.com/fundamentals/api/get-started/create-token/ .

- **[verified]** AI Gateway provider-native endpoint
  `https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway_id}/anthropic/v1/messages`; the Anthropic key
  travels in `x-api-key` as usual. An **authenticated gateway** adds `cf-aig-authorization: Bearer <token>`;
  with **BYOK** (provider key stored in the gateway) the provider header can be omitted. → `route =
  "cf_gateway"` in `godot.toml`; the provider omits `x-api-key` when only `CF_AIG_TOKEN` is present.
- **[verified]** OpenAI-compatible endpoints: gateway `…/{gateway_id}/compat/chat/completions`; **Workers AI**
  `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions` with `Authorization:
  Bearer <Cloudflare API token>` → `route = "workers_ai"` (requires `provider = "openai_compat"`).
- **[verified]** API tokens are scoped (permissions, resources) and can carry a TTL → create a token with only
  the AI Gateway / Workers AI permissions it needs; rotate the one that was pasted into chat (see `SECURITY.md`).
- **[assumption]** What the gateway buys us: request logs, caching, rate limits and per-gateway cost dashboards
  with zero code change. What it does **not** do: make the model smarter. Whether any Workers AI open-weight
  model can drive this tool loop must be **measured** with `python3 -m godotai eval run` — not assumed.
- **Status:** URL/header construction is unit-tested; **no request was sent to Cloudflare** from this repo.

## 5. Kaggle — a batch GPU, not a server

Sources (read 2026-09-26): https://www.kaggle.com/docs/notebooks , https://www.kaggle.com/docs/tpu ,
https://www.kaggle.com/docs/api , https://github.com/Kaggle/kaggle-api/blob/main/docs/README.md (auth),
https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md and
https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels_metadata.md (CLI + metadata).

- **[verified]** Notebook sessions: **12 h** (CPU/GPU), **9 h** (TPU); interactive sessions stop after **20 min
  idle**; `/kaggle/working` **20 GB** persisted with the version; "GPU T4 ×2" = 2 × Tesla T4 (16 GB each),
  4 CPU cores, 29 GB RAM; TPU quota 20 h/week.
- **[verified]** CLI/metadata: `kaggle kernels push -p DIR [--accelerator NvidiaTeslaT4|TpuV5E8|…] [--no-run]`,
  `kernels status`, `kernels output`; `kernel-metadata.json` fields `id, title, code_file, language,
  kernel_type, is_private, enable_gpu/enable_internet, machine_shape, dataset_sources, …`.
  The older TPU docs page describes **v3-8**; the user's account offers **v5e-8** (`TpuV5E8`).
- **[verified]** Auth: `KAGGLE_API_TOKEN` env var or `~/.kaggle/access_token`; legacy `kaggle.json`
  (`KAGGLE_USERNAME` + `KAGGLE_KEY`). OAuth access tokens expire after 3 h. Secrets *inside* a notebook come from
  Add-ons → Secrets (`kaggle_secrets.UserSecretsClient`) — the kernel never needs the API token itself.
- **[assumption, deliberately not built]** A Kaggle notebook is not an always-on inference endpoint; using it as
  the agent's model server would violate the idle/12 h limits. It is used here **only** as a place to run the
  optional QLoRA batch job (`training/kaggle/`). No TPU path: the bitsandbytes/QLoRA stack is CUDA-only.
- **Status:** metadata rendering, staging and command construction are unit-tested and run in CI
  (`scripts/kaggle_push.py --check`); **no kernel was pushed** to Kaggle from this repo.

## 6. Fine-tuning stack (for the optional specialised open-weight model)

Sources (read 2026-09-26): https://huggingface.co/docs/trl/main/en/sft_trainer (stable **v1.14.0**),
https://huggingface.co/docs/transformers/en/main_classes/model , https://huggingface.co/Qwen/Qwen3.5-9B ,
https://huggingface.co/Qwen/Qwen3.6-27B , https://huggingface.co/Qwen/Qwen3-Coder-Next .

- **[verified]** TRL `SFTConfig`: `max_length` (default 1024), `dataset_text_field` (default `"text"`),
  `assistant_only_loss` (conversational datasets; needs `{% generation %}` markers in the chat template — TRL
  patches known families e.g. Qwen3), `completion_only_loss`, `packing`; defaults that differ from
  `TrainingArguments`: `gradient_checkpointing=True`, **`bf16=True` unless `fp16` is set** (T4 has no bf16 →
  the script sets `fp16`/`bf16` explicitly), `learning_rate=2e-5`, `logging_steps=10`.
  `SFTTrainer(model, args, train_dataset, eval_dataset, processing_class, peft_config, quantization_config, …)`.
- **[verified]** transformers: `from_pretrained(dtype=…)` is current; `torch_dtype` is deprecated
  (kept for backwards compatibility) → the script tries `dtype` first and falls back.
- **[verified]** Base-model landscape: `Qwen3.5-9B` (9 B, 262 144 context, thinking on by default, tool calling,
  VLM with hybrid Gated-DeltaNet/MoE architecture, needs latest transformers); `Qwen3.6-27B` (April 2026, dense);
  `Qwen3-Coder-Next` (80 B, coding agents). **[assumption]** For 2 × T4 QLoRA the low-risk default stays
  `Qwen2.5-Coder-7B-Instruct`; 4-bit/PEFT support for the Qwen3.5 hybrid architecture is unverified here.
- **Status:** dataset extraction, validation, chat-template conversion and `--dry-run` are unit-tested and run in
  CI; **the training path has not been executed** (no GPU in the build environment).

## 7. What makes a specialised coding agent strong (design inputs)

- **[verified]** Anthropic, "Effective context engineering for AI agents" and "Agent Skills": keep the
  always-on prompt small, load specialised knowledge on demand (SKILL.md / progressive disclosure), give the
  agent real tools and a real environment. → `knowledge/` + `knowledge_search`, engine in the container,
  exact API via `api_lookup` instead of a giant prompt.
- **[secondary]** "From Plan to Action: How Well Do Agents Follow the Plan?" (arXiv 2604.12147) and the
  plan-then-execute pattern literature: agents drift from their plans unless following is enforced.
  → mandatory `submit_plan`, human gate, `step_id` on every mutation.
- **[secondary]** "Agentic Agile-V" (arXiv 2605.20456), Addy Osmani's "The 80% problem", Sonar/Futurum on
  verifiers: **testing must be inside the loop**; verification, not generation, is the bottleneck.
  → the run cannot succeed without `godot_verify` PASS; broken scripts are proven to fail; the eval bank
  (`evals/tasks/*.json`) scores *only* with the engine + structural checks, never with a model judge.
- **[secondary]** Open-source agents surveyed: OpenHands (MIT), Aider, SWE-agent, Goose, OpenCode, Cline/Roo,
  Qwen Code, Kimi CLI. All are general-purpose; none ships engine-backed verification for Godot.
  **[assumption]** A small purpose-built agent with a hard scope is more reliable for this one job than
  configuring a general agent, and is trivially auditable. Any of those agents can still be pointed at this
  repo's CLI (`godotai verify/export/apiref`) as tools.
- **[secondary]** Godot MCP servers (Coding-Solo/godot-mcp, GDAI MCP, godot-ai, …) expose scene/script
  editing to chat assistants but don't verify by running the game; several authors state they "can't one-shot
  a whole game". We chose direct headless engine control instead of an MCP dependency.
- **[secondary]** Open-weight coding models in 2026 (Qwen3.5/3.6, Qwen3-Coder-Next, GLM, DeepSeek, Kimi K2.6)
  are usable via OpenAI-compatible servers → `openai_compat` provider, effort mapped to `reasoning_effort`.
- **[verified]** GDScript tooling: gdtoolkit 4.x (`gdlint`, `gdformat`) — used as an *advisory* step.
  Godot 4 headless CI images (barichello/godot-ci) initialise the editor once with `--editor --quit` and place
  templates under `~/.local/share/godot/export_templates/<version>` — mirrored by `install.py`.

## 8. Limitations discovered while building

- Godot prints RID/ObjectDB leak messages when a scene is quit from a script; treated as noise, not errors.
- `--check-only` needs the project imported first for `class_name` globals to resolve → pipeline imports first.
- The ClassDB `--doctool` dump has no descriptions; deprecated/experimental flags and full signatures are
  present. Unknown methods on user classes are *warnings*, only unambiguous Godot-3 idioms are *errors*.
- ZIP extraction with Python drops Unix mode bits → `sdkmanager` must be re-chmodded (fixed in `android.py`).
- `adb` may print `cannot connect to daemon` after export when `shutdown_adb_on_exit` is on — harmless.
- Docker was not available in the build sandbox → the image is untested; the CI job installs the engine
  directly on the runner instead and verifies the template with it.
- CPython constant-folds `"a" + "b"`, so a "split" fake token in a test still appears whole in the `.pyc`;
  fixtures use `str.join` at runtime and the secret scanner skips `__pycache__`.
