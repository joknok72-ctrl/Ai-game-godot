# Security notes

## ⚠️ If you pasted a credential into a chat, rotate it now

During the development of this project, a Cloudflare API token (more than once)
and a Kaggle API token were pasted into an assistant conversation. **Anything
typed into a chat, a ticket, a screenshot or a commit must be treated as
leaked**, even if it was never used (it was not used, tested, logged, stored or
committed by the tooling that produced this repository — `scripts/cloudflare_setup.py`
reads a token from the environment of *your* terminal only). Rotate both immediately:

| Credential | Where to revoke / re-create | Notes |
| --- | --- | --- |
| Cloudflare API token | dash.cloudflare.com → My Profile → **API Tokens** → *Roll* or *Delete* | Re-create with the **least privilege** the task needs — for the website setup: *Account → Access: Apps and Policies: Edit*, *Account → Cloudflare Tunnel: Edit*, *Zone → DNS: Edit* (that zone only); for AI Gateway / Workers AI: their `Run`/`Read` permission — and set an optional **TTL** and client-IP filter — both are documented options on the token creation screen ([docs](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/), read 2026-09-26). The setup token is needed once; delete it afterwards. |
| Cloudflare **Tunnel token** (`TUNNEL_TOKEN`) | Zero Trust → Networks → Tunnels → your tunnel → *Refresh token* (or delete and re-create the tunnel) | Written by `cloudflare_setup.py` only into `deploy/cloudflare/.env` (mode 0600, git-ignored), never printed. Whoever holds it can attach a connector to your hostname. |
| Kaggle API token | kaggle.com → Settings → **API** → revoke the token, create a new one | Kaggle documents API tokens for "automated scripts, notebook workflows, CI/CD environments" ([docs](https://www.kaggle.com/docs/api#auth), read 2026-09-26). |
| Anthropic / OpenAI / GitHub keys | the respective console | Same rule: chat = leaked. |

The Cloudflare **Account ID** is an identifier, not a secret — it is fine in
configuration, but this repository still keeps it out of git and reads it from
the environment (`CF_ACCOUNT_ID`) so that forks work unchanged.

## How this project handles secrets

* **Environment variables only.** Every credential is read by *name* at run time
  — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_TOKEN`, `CF_AIG_TOKEN`,
  `CLOUDFLARE_API_TOKEN`, `TUNNEL_TOKEN`, `GODOTAI_CHAT_TOKEN`, `KAGGLE_API_TOKEN`,
  `HF_TOKEN`, `GODOT_ANDROID_KEYSTORE_RELEASE_*`.
  No value is stored in the repository, in `godot.toml`, in `deploy/cloudflare/compose.yml`,
  in the Docker image or in the run logs. In GitHub Actions they are `${{ secrets.NAME }}`
  and are passed to steps through `env:` only. The compose stack reads them from an
  untracked `deploy/cloudflare/.env` (`.env`/`.env.*` are git-ignored) and fails fast
  (`${VAR:?…}`) instead of starting half-configured.
* **The default model path needs no credential at all.** The chat talks to *your*
  OpenAI-compatible server (vLLM/Ollama); nothing is sent to a vendor unless you
  opt in and declare it (`docs/MODEL.md`).
* **Public website = Cloudflare Access, verified twice.** With `--access-team-domain` +
  `--access-aud` the server accepts a request only with a valid Access JWT
  (`Cf-Access-Jwt-Assertion` header or `CF_Authorization` cookie): RS256 signature against
  the team's JWKS (`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`, cached,
  refreshed on an unknown `kid`), issuer, audience (your application's AUD), `exp`/`nbf`/`iat`
  with 60 s skew. Raw tokens never appear in errors, logs or `/api/status`; only the
  signed-in e-mail/subject is exposed to the page. The tunnel route itself is also
  created with *Protect with Access*, and `/healthz` (the only unauthenticated route)
  returns `{"ok": true}` and nothing else. Never publish the chat with just
  `--token` on the open internet.
* **The model never sees them.** The agent's tool sandbox refuses to read or
  write credential-shaped files (`*.keystore`, `*.pem`, `.env*`, `kaggle.json`,
  `access_token`, `.netrc`, `*.token`, …) and strips every environment variable
  whose name contains `TOKEN`, `SECRET`, `PASSWORD`, `API_KEY`, `KAGGLE`,
  `CLOUDFLARE`, `CF_AIG`, `CREDENTIAL` … before starting the engine, Gradle or
  `run_command` (`godotai/tools/fs.py`, `godotai/tools/godot_tools.py`).
* **Transcripts are scrubbed.** Run logs under `.godotai/runs/` are git-ignored,
  and the training-data extractor (`python3 -m godotai dataset extract`) passes
  every string through `godotai.secrets.redact` before writing a JSONL line.
* **CI scans the tree.** `python3 scripts/secret_scan.py` fails the build when a
  file contains an Anthropic/OpenAI/GitHub/AWS/Google/Slack token shape, a PEM
  private key, a `Bearer …` value or an assignment to one of the variable names
  above (including `TUNNEL_TOKEN`, `CF_TUNNEL_TOKEN`, `CLOUDFLARE_TUNNEL_TOKEN` and
  `GODOTAI_CHAT_TOKEN`). `tests/test_secrets.py::test_repository_is_clean` runs the
  same scan, and `tests/test_deploy_files.py` additionally refuses any 32-hex
  account/zone-looking identifier or non-placeholder value in `deploy/cloudflare/`.
  A line that must show a token *format* can carry `secret-scan:allow`.
* **Downloads are pinned.** The Godot editor and export templates are verified
  against the SHA-512 sums committed under `engine/checksums/<tag>/`.
* **Workflow inputs are not interpolated into shell.** `build-android.yml`
  passes `workflow_dispatch` inputs through `env:` to avoid script injection.

## Least-privilege guidance for the optional integrations

* **Cloudflare website (Tunnel + Access)**: one API token with exactly *Access: Apps
  and Policies: Edit*, *Cloudflare Tunnel: Edit* and zone-scoped *DNS: Edit*, with a
  TTL, exported in the terminal that runs `scripts/cloudflare_setup.py` and deleted
  afterwards (`--dry-run` needs no token at all). Keep the Access allow-list to the
  e-mail addresses that should build games; the chat can write files and run your
  model server. Do not publish the model server's port; the compose file exposes it
  to the chat container only.
* **Cloudflare AI Gateway** (`GODOTAI_ROUTE=cf_gateway`): create a dedicated
  gateway, turn on *Authenticated Gateway* and use its token as `CF_AIG_TOKEN`;
  prefer *stored keys (BYOK)* so the Anthropic key lives in the gateway instead
  of on the machine that runs the agent ([docs](https://developers.cloudflare.com/ai-gateway/usage/providers/anthropic/)).
* **Cloudflare Workers AI** (`GODOTAI_ROUTE=workers_ai`): a token with only the
  Workers AI `Read`/`Run` permission, TTL-limited.
* **Kaggle**: the token is only needed on the machine that *pushes* a training
  kernel (`scripts/kaggle_push.py`); the kernel itself must not receive it.
  Use Kaggle *Secrets* (notebook add-on) for anything the training run needs.
* **GitHub Actions**: repository secrets, never organisation-wide ones, and
  `permissions:` blocks scoped per job.

## Reporting a vulnerability

Open a GitHub issue titled `security:` **without** the sensitive detail, or
contact the repository owner directly; do not post credentials or exploit
payloads in the issue body.
