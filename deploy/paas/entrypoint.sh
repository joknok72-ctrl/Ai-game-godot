#!/bin/sh
# godotai app-only entrypoint for PaaS hosts (Railway / Render-like platforms / a plain VM running the image).
#
# Refuses to start a *public* server without an access token, binds 0.0.0.0 on the platform's PORT, accepts the
# platform-assigned hostname in the Host header, and keeps the run quota on. Nothing here reads or writes a
# credential file: every secret comes from the environment of the process (the host's secret store).
set -eu

# ARM hosts (Oracle Ampere A1 …): the editor asset installed in the image is linux.arm64 — say so to the config.
if [ -z "${GODOTAI_ENGINE_PLATFORM:-}" ] && [ "$(uname -m)" = "aarch64" ]; then
  export GODOTAI_ENGINE_PLATFORM=linux.arm64
fi

if [ "${1:-}" = "chat" ] || [ -z "${1:-}" ]; then
  [ $# -gt 0 ] && shift
  if [ -z "${GODOTAI_CHAT_TOKEN:-}" ] && [ -z "${GODOTAI_ACCESS_AUD:-}" ]; then
    echo "refusing to start a public chat server without authentication:" >&2
    echo "  set GODOTAI_CHAT_TOKEN=<long random secret> in the platform's variables (openssl rand -hex 32), or" >&2
    echo "  put Cloudflare Access in front and set GODOTAI_ACCESS_TEAM_DOMAIN + GODOTAI_ACCESS_AUD (deploy/cloudflare/)." >&2
    exit 64
  fi
  if [ -z "${GODOTAI_PRESET:-}" ] && [ -z "${GODOTAI_BASE_URL:-}" ] && [ -z "${OPENAI_BASE_URL:-}" ]; then
    echo "no model endpoint: set GODOTAI_PRESET (python3 -m godotai presets) with its key variable, or GODOTAI_BASE_URL." >&2
    exit 64
  fi
  # PORT is injected by the platform (Railway, Render …); the chat command reads it itself (GODOTAI_PORT → PORT → 8765).
  exec python3 -m godotai chat --host 0.0.0.0 --games-dir "${GODOTAI_GAMES_DIR:-/games}" "$@"
fi
exec python3 -m godotai "$@"
