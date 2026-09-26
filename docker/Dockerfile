# godotai execution environment: Linux + pinned Godot editor (headless) + export
# templates + OpenJDK 17 + Android SDK (per the official 4.7 Android export docs)
# + gdtoolkit. The agent runs inside this container and drives the engine directly.
#
#   docker build -t godotai -f docker/Dockerfile .
#   docker run --rm -it -e ANTHROPIC_API_KEY -e GITHUB_TOKEN -v "$PWD/games:/games" godotai \
#          run "make a 2D endless runner for Android" --workspace /games/runner --yes
#
#   # Browser chat UI (talk to the AI at http://127.0.0.1:8765 on the host). Inside a container the
#   # server must bind 0.0.0.0, which godotai only allows together with an access token:
#   docker run --rm -it -e ANTHROPIC_API_KEY -e GODOTAI_CHAT_TOKEN=<choose-a-secret> \
#          -p 127.0.0.1:8765:8765 -v "$PWD/games:/games" godotai chat --host 0.0.0.0 --games-dir /games
#
# Versions come from godot.toml (copied in before the install step), so retargeting
# the engine is a one-line change + rebuild. Not built in CI here (no Docker on the
# sandbox that produced this repo) — see docs/RESEARCH.md "Limitations".
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    GODOT_SILENCE_ROOT_WARNING=1 \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git git-lfs unzip zip xz-utils \
        python3 python3-pip python3-venv \
        openjdk-17-jdk-headless \
        libfontconfig1 libfreetype6 libgl1 libglu1-mesa libxcursor1 libxi6 libxinerama1 libxrandr2 libxkbcommon0 libdbus-1-3 libasound2t64 libpulse0 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:/root/.local/bin:${PATH}"

WORKDIR /opt/godotai
# Copy only what the installers need first (better layer caching).
COPY godot.toml pyproject.toml ./
COPY engine ./engine
COPY godotai ./godotai

# 1) Godot editor + export templates, SHA-512 verified against engine/checksums/.
RUN python3 -m godotai install-godot --system \
    && godot --headless --version

# 2) Android SDK packages from godot.toml, debug keystore, editor settings.
RUN python3 -m godotai setup-android --sdk-root ${ANDROID_HOME} \
    && rm -rf /root/.android/cache

# 3) GDScript linter/formatter (advisory in the verification pipeline).
RUN pip3 install --no-cache-dir --break-system-packages "gdtoolkit==4.*"

# The rest of the repo (knowledge base, templates, docs, tests).
COPY knowledge ./knowledge
COPY templates ./templates
COPY docs ./docs
COPY tests ./tests
COPY .github ./.github
COPY README.md ./

ENV GODOTAI_CONFIG=/opt/godotai/godot.toml
EXPOSE 8765
VOLUME ["/games"]
WORKDIR /games
ENTRYPOINT ["python3", "-m", "godotai"]
CMD ["doctor"]
