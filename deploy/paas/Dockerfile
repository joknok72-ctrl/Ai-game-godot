# godotai — *app-only* image for small hosts (Railway trial 1 GB, Oracle Always Free A1, any 1–2 GB Linux VM).
#
# What is inside: Python + the pinned Godot editor (headless, for import / script check / smoke-run verification)
# + the chat server. What is NOT inside, on purpose: the model (a hosted preset serves it — GODOTAI_PRESET), the
# Android SDK / JDK / export templates (APK export happens in GitHub Actions: .github/workflows/build-android.yml).
# Measured on the mobile-2d template, 2026-09-26, x86_64: chat server ≈ 50 MB RSS, one engine verification peaks
# ≈ 640 MB RSS → fits a 1 GB host with GODOTAI_MAX_CONCURRENT_RUNS=1 (the default here), NOT a 512 MB one.
#
#   docker build -t godotai-app -f deploy/paas/Dockerfile .
#   export GODOTAI_CHAT_TOKEN="$(openssl rand -hex 32)"; export OPENROUTER_API_KEY=<your key>     # shell only, never a file
#   docker run --rm -p 127.0.0.1:8765:8765 -e GODOTAI_CHAT_TOKEN -e OPENROUTER_API_KEY \
#              -e GODOTAI_PRESET=openrouter_free godotai-app                                     # `-e NAME` passes the value through
#
# Every value that identifies *you* (token, key, account id, workspace id) is an environment variable set in the
# host's secret store — nothing is baked into the image and nothing is read from a file in this repository.
# Not built in the CI of this repository (no Docker there); the shell entrypoint and the files it relies on are
# unit-tested (tests/test_deploy_files.py).
FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    GODOT_SILENCE_ROOT_WARNING=1

# Runtime libraries the headless editor still links against (no X server is started).
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
        libfontconfig1 libfreetype6 libgl1 libglu1-mesa libxcursor1 libxi6 libxinerama1 libxrandr2 libxkbcommon0 libdbus-1-3 libasound2 libpulse0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/godotai
COPY godot.toml pyproject.toml ./
COPY engine ./engine
COPY godotai ./godotai

# Editor only (no export templates: ~0.1 GB instead of ~1.4 GB), SHA-512 verified against engine/checksums/.
# The asset follows the image architecture: linux.x86_64 (default) or linux.arm64 (Oracle Ampere A1 and other ARM hosts).
RUN if [ "$TARGETARCH" = "arm64" ]; then export GODOTAI_ENGINE_PLATFORM=linux.arm64; fi \
    && python3 -m godotai install-godot --system --editor-only \
    && godot --headless --version

COPY knowledge ./knowledge
COPY templates ./templates
COPY deploy/paas/entrypoint.sh /usr/local/bin/godotai-entrypoint
RUN chmod +x /usr/local/bin/godotai-entrypoint && mkdir -p /games

ENV GODOTAI_CONFIG=/opt/godotai/godot.toml \
    GODOTAI_GAMES_DIR=/games \
    GODOTAI_MAX_CONCURRENT_RUNS=1 \
    GODOTAI_MAX_RUNS_PER_DAY=40
# (the entrypoint sets GODOTAI_ENGINE_PLATFORM=linux.arm64 at run time on aarch64, matching the asset installed above)

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS "http://127.0.0.1:${PORT:-8765}/healthz" || exit 1
ENTRYPOINT ["godotai-entrypoint"]
