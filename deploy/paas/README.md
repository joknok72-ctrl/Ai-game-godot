# deploy/paas — the *app-only* image for small free hosts (Railway trial, Render, Oracle Always Free, any 1–2 GB Linux VM)

> **لم يُنشر أي شيء من هذا المستودع.** هذا الدليل والملفات المرفقة تُجهّز النشر فقط؛ الموقع العام لا يمكن إنشاؤه إلا من حسابك
> أنت (Railway/Render/Oracle/Cloudflare)، بمفاتيحك أنت، ولا يوجد في المستودع أي مفتاح أو معرّف حساب.
> **Nothing has been deployed by this repository.** These files only *prepare* a deployment; the public site can only
> be created from your own account, with your own keys — none of which exist in this repository.

This directory is the counterpart of [`deploy/cloudflare/`](../cloudflare/README.md) (your own GPU/CPU box + your own
model server + Cloudflare Tunnel/Access) for the situation described in [`docs/FREE_TIER.md`](../../docs/FREE_TIER.md):
**no money, no GPU** — the model is a hosted Qwen endpoint with a free allowance (`GODOTAI_PRESET`), and the host runs
*only* the chat server + the headless Godot editor for verification.

| file | what it is |
|---|---|
| `Dockerfile` | Python 3.12 + the pinned Godot **editor only** (SHA-512 verified from `engine/checksums/`) + the chat server. No model weights, no Android SDK/JDK/export templates (APK export stays in GitHub Actions). x86_64 and ARM64 (`TARGETARCH`). |
| `entrypoint.sh` | refuses to start a public server without authentication, refuses to start without a model endpoint, binds `0.0.0.0` on the platform's `PORT`, keeps the run quota on. |

Measured on the `mobile-2d` template (x86_64, 2026-09-26, see `docs/FREE_TIER.md` §3): chat server ≈ 50 MB RSS,
**one** engine verification peaks ≈ 640 MB RSS. Hence `GODOTAI_MAX_CONCURRENT_RUNS=1` is the default and a **512 MB**
host (Render Free, Railway Free-after-trial) is *not* enough for the verification path.

## 1. What you must have (nothing here can be provided by this repository)

| you need | why | where it goes |
|---|---|---|
| an account on the host (Railway / Render / Oracle Cloud …) | the site runs *in your account*; the repository cannot create one | — |
| `GODOTAI_CHAT_TOKEN` — a long random secret (`openssl rand -hex 32`) | the only thing standing between the public internet and your model quota | host's **secret** variables |
| `GODOTAI_PRESET` + its key variable (`python3 -m godotai presets`) | the model — `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `DASHSCOPE_API_KEY` (+ `GODOTAI_BASE_URL`), or `CF_ACCOUNT_ID` + `CF_WORKERS_AI_TOKEN` | host's **secret** variables |
| (optional) `GODOTAI_MAX_RUNS_PER_DAY`, `GODOTAI_MAX_CONCURRENT_RUNS` | cost/abuse cap; defaults `40` / `1` | plain variables |

The entrypoint exits with status `64` when the token or the model endpoint is missing — on purpose: a public chat
server with no auth and a free-tier key behind it would let strangers burn your quota within the hour.

## 2. Railway (2 vCPU / 1 GB trial service)

Facts read on 2026-09-26 from Railway's pricing/limits pages (they change — re-read them):

* **Trial**: up to 30 days, one-time `$5` credit; per service **2 vCPU, 1 GB RAM, 1 GB ephemeral disk, 0.5 GB volume, 4 GB image**.
  The *Limited* trial may block outbound network — then the hosted model API is unreachable and the app is useless there.
* **After the trial**: the Free plan gives `$1/month` (no rollover) at **0.5 GB RAM / 1 vCPU** — below the measured
  verification peak. Trial volumes are deleted 30 days after the credit expires. Beyond the credit the service stops
  (or you add a card). Usage price: `$10/GB·month` RAM, `$20/vCPU·month`.
* Railway injects `PORT` and `RAILWAY_PUBLIC_DOMAIN`; the chat command reads both (`--port` → `GODOTAI_PORT` → `PORT`
  → 8765; the domain is accepted in the `Host` header like `--public-host`).

Steps (all in *your* Railway account — nothing here needs a Railway token):

1. New project → **Deploy from GitHub repo** → this repository (your fork).
2. Service → *Settings → Build*: set the variable `RAILWAY_DOCKERFILE_PATH=deploy/paas/Dockerfile` (Railway then uses this
   image instead of Nixpacks/the root `docker/Dockerfile`, which is the *full* Android build image and far too large here).
3. *Variables* (mark the secrets as sealed/secret):
   ```
   GODOTAI_CHAT_TOKEN=<openssl rand -hex 32>
   GODOTAI_PRESET=openrouter_free            # or groq | alibaba_model_studio | workers_ai
   OPENROUTER_API_KEY=<from https://openrouter.ai/settings/keys>
   GODOTAI_MAX_CONCURRENT_RUNS=1
   GODOTAI_MAX_RUNS_PER_DAY=40
   ```
4. *Settings → Networking → Generate Domain* → the public URL (`https://<name>.up.railway.app`). Open it, paste the token.
5. Healthcheck path: `/healthz` (unauthenticated liveness only; `/api/*` needs the token).

**Verdict for 2 vCPU / 1 GB**: it can host the **app only** (this image) with concurrency 1, for the trial period; it
cannot host any Qwen inference model (the smallest coder-class Qwen needs several GB of RAM even at 4-bit and is far
too slow on 2 vCPU to drive an agent loop). Persistence: the games directory is *ephemeral* on Railway unless you
attach the 0.5 GB volume at `/games` — commit finished games to GitHub through the built-in GitHub tools instead.

## 3. Render (Free web service — for the UI only)

Render Free: **512 MB RAM**, sleeps after 15 min idle (~1 min cold start), ephemeral filesystem, 750 instance-hours/month.
The chat page + hosted model *work*, but `godot_verify` (≈ 640 MB peak) will be OOM-killed → the agent cannot verify
its own output there. Use Render Free only to try the UI; `RENDER_EXTERNAL_HOSTNAME` is accepted automatically.
Dockerfile path: `deploy/paas/Dockerfile`; health check path: `/healthz`.

## 4. Oracle Cloud Always Free — Ampere A1 (ARM, the only free host with real headroom)

Oracle's Always Free page (read 2026-09-26): **2 OCPUs and 12 GB of memory total** for `VM.Standard.A1.Flex` (1,500
OCPU-hours + 9,000 GB-hours a month; one instance of 2 OCPU/12 GB or two of 1 OCPU), 200 GB block storage, free for the
life of the account (card verification at sign-up). Idle instances (7 days below 20 % CPU *and* network *and* memory)
may be reclaimed, and A1 capacity in a home region is regularly "out of host capacity" for days. This is the only
free option where **the app + verification fit comfortably** (12 GB ≫ 640 MB peak), and a small local model could even
be *tried* — but on 2 ARM cores a 7B-class model yields a few tokens per second: too slow for the agent loop. Keep the
hosted preset as the model.

```sh
git clone https://github.com/<you>/Ai-game-godot.git && cd Ai-game-godot
docker build -t godotai-app -f deploy/paas/Dockerfile .        # TARGETARCH=arm64 is set by Docker on the A1 host
docker run -d --name godotai --restart unless-stopped -p 127.0.0.1:8765:8765 \
   -e GODOTAI_CHAT_TOKEN -e GODOTAI_PRESET=groq -e GROQ_API_KEY -v godotai-games:/games godotai-app
```

Do **not** open port 8765 in the VCN security list. Put Cloudflare Tunnel + Access in front exactly as in
[`deploy/cloudflare/README.md`](../cloudflare/README.md) (then `GODOTAI_ACCESS_TEAM_DOMAIN`/`GODOTAI_ACCESS_AUD`
replace the shared token), or at least a reverse proxy with TLS. The ARM editor asset is pinned in
`engine/checksums/4.7.2-stable/SHA512-SUMS.txt`; the entrypoint sets `GODOTAI_ENGINE_PLATFORM=linux.arm64` on `aarch64`.

## 5. What this image deliberately does not do

* **No model inside.** `GODOTAI_PRESET` (hosted) or `GODOTAI_BASE_URL` (your own server elsewhere) is mandatory.
* **No Android export.** APKs are built by `.github/workflows/build-android.yml` on GitHub's runners (free for public
  repositories), not on the 1 GB host.
* **No secret files.** Every identifying value is an environment variable of the process; `.env*` is git-ignored.
* **No deployment from CI.** The repository's workflows never touch Railway/Render/Oracle; there is no Railway
  config file here either (Railway's *Config as Code* is deprecated and its replacement needs a project token).

## 6. Local check without Docker

```sh
GODOTAI_SKIP_MODEL_PROBE=1 PORT=0 RAILWAY_PUBLIC_DOMAIN=example.up.railway.app \
  python3 -m godotai chat --games-dir /tmp/games --check --max-concurrent-runs 1 --max-runs-per-day 40
python3 -m unittest discover -s tests -p "test_deploy_files.py"     # entrypoint refusal paths, copied paths, quotas
```

`tests/test_deploy_files.py::PaasImageTests` runs `entrypoint.sh` with a stub `python3` to prove the two refusals
(no token → exit 64, no endpoint → exit 64) and the exact command line it executes when both are present.
