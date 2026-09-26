#!/usr/bin/env sh
# godotai on an Android phone — Termux helper. Companion of docs/TERMUX.md (Arabic guide, English summary at the end).
#
#   sh scripts/termux_setup.sh            # in Termux: install python/git/proot-distro, then print the next steps
#   sh scripts/termux_setup.sh --proot    # in Termux: also install the Debian guest (proot-distro install debian:bookworm)
#   sh scripts/termux_setup.sh --guest    # inside the Debian guest: apt runtime libs for the headless editor + print the
#                                         #   install-godot command for THIS CPU (GODOTAI_ENGINE_PLATFORM=linux.arm64 …)
#   sh scripts/termux_setup.sh --guest --install-godot   # … and run install-godot --editor-only right away (~100 MB download)
#   sh scripts/termux_setup.sh --print    # anywhere: print the whole plan, change nothing (what the tests run)
#
# What this script never does: ask for, read, print or store an API key, a GitHub token or a keystore. Those are typed
# by you as `export NAME=…` in your own terminal (or as GitHub repository secrets) — the placeholders below are
# placeholders. It never runs the model, never pushes anything, never claims the engine works on your phone: the
# honest status comes from `python3 -m godotai doctor` afterwards.
#
# Why two halves (host / guest): Termux is Android's Bionic userland, not a Linux distribution — the official Godot
# editor (a glibc build) cannot execute there. Chat, planning and the GitHub tools run in Termux directly; the engine
# runs inside a Debian guest started by proot-distro on the same phone, or on GitHub Actions (build-android.yml).
set -eu

MODE=host
INSTALL_GODOT=0
PRINT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --host) MODE=host ;;
    --proot) MODE=proot ;;
    --guest) MODE=guest ;;
    --install-godot) INSTALL_GODOT=1 ;;
    --print|--dry-run|-n) PRINT_ONLY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

# Same list as deploy/paas/Dockerfile (Debian bookworm): what the headless editor still links against. Kept in sync by
# tests/test_termux.py.
GUEST_LIBS="libfontconfig1 libfreetype6 libgl1 libglu1-mesa libxcursor1 libxi6 libxinerama1 libxrandr2 libxkbcommon0 libdbus-1-3 libasound2 libpulse0"
GUEST_TOOLS="ca-certificates curl git python3 unzip"
DEBIAN_IMAGE="debian:bookworm"
DEBIAN_NAME="debian"

machine="$(uname -m 2>/dev/null || echo unknown)"
case "$machine" in
  aarch64|arm64) ENGINE_PLATFORM=linux.arm64 ;;
  x86_64|amd64) ENGINE_PLATFORM=linux.x86_64 ;;
  armv7l|armv8l|armv6l) ENGINE_PLATFORM=linux.arm32 ;;
  i386|i486|i586|i686) ENGINE_PLATFORM=linux.x86_32 ;;
  *) ENGINE_PLATFORM="" ;;
esac

in_proot_guest() {
  [ -n "${PD_CONTAINER:-}" ] && return 0
  [ "${container:-}" = "proot-distro" ] && return 0
  uname -r 2>/dev/null | grep -qi proot
}
in_termux() {
  in_proot_guest && return 1
  [ -n "${TERMUX_VERSION:-}" ] && return 0
  case "${PREFIX:-}" in */com.termux/files/usr*) return 0 ;; esac
  return 1
}

say() { printf '%s\n' "$@"; }
run() {                       # print the command; execute it unless --print
  say "+ $*"
  [ "$PRINT_ONLY" = 1 ] || "$@"
}

say "godotai — Termux helper · mode=$MODE · cpu=$machine → ${ENGINE_PLATFORM:-no official Godot build for this CPU}"
if in_termux; then say "  where: Termux itself (Android/Bionic) — the glibc editor cannot run here; the Debian guest can host it.";
elif in_proot_guest; then say "  where: PRoot guest (${PD_CONTAINER:-debian}) — glibc userland, the editor can run here.";
else say "  where: a regular Linux host (not Termux) — the plan is printed; nothing here is Termux-specific to run."; fi
say ""

case "$MODE" in
  host)
    say "١) الحزم في Termux (بايثون + git + proot-distro) / 1) Termux packages"
    if in_termux && [ "$PRINT_ONLY" = 0 ]; then
      run pkg update -y
      run pkg install -y python git proot-distro
    else
      say "+ pkg update -y && pkg install -y python git proot-distro"
    fi
    say ""
    say "٢) الوصول إلى ذاكرة الهاتف (مجلد Download لملف الـ APK) / 2) phone storage (for the downloaded APK)"
    say "+ termux-setup-storage        # يطلب إذنًا مرة واحدة؛ ثم ~/storage/downloads"
    say ""
    say "٣) المستودع / 3) the repository"
    say "+ git clone https://github.com/joknok72-ctrl/Ai-game-godot.git && cd Ai-game-godot"
    say "+ python3 -m godotai doctor   # يقول بصراحة: host = Termux، المحرك لا يعمل هنا مباشرة"
    say ""
    say "٤) مفتاح النموذج المجاني — في طرفيتك فقط، ليس في أي ملف / 4) the free model key — this shell only, never a file"
    say "+ export GODOTAI_PRESET=openrouter_free"
    say "+ export OPENROUTER_API_KEY='<paste your key from openrouter.ai/settings/keys>'     # 20 req/min · 50 req/day without credits"
    say "+ python3 -m godotai presets  # البدائل وحدودها: groq · alibaba_model_studio · workers_ai"
    say ""
    say "٥) المحادثة على الهاتف / 5) chat on the phone"
    say "+ termux-wake-lock            # يقلّل قتل العملية في الخلفية (لا يمنعه تمامًا على Android 12+)"
    say "+ python3 -m godotai chat     # ثم افتح http://127.0.0.1:8765/ في متصفح الهاتف (شاشة منقسمة أفضل)"
    say ""
    say "٦) المحرك (التحقق الحقيقي) داخل Debian على نفس الهاتف / 6) the engine inside Debian on the same phone"
    say "+ sh scripts/termux_setup.sh --proot"
    say "   أو بدون محرك على الهاتف: اترك بناء الـ APK لـ GitHub Actions (docs/TERMUX.md §3) — لكن التحقق بالمحرك لن يعمل محليًا."
    ;;
  proot)
    if ! in_termux && [ "$PRINT_ONLY" = 0 ]; then
      say "this mode installs the guest with proot-distro and only makes sense inside Termux (use --print elsewhere)" >&2; exit 3
    fi
    say "١) تثبيت Debian bookworm (نحو 100 MB) / 1) install the Debian guest"
    if [ "$PRINT_ONLY" = 0 ] && proot-distro login "$DEBIAN_NAME" -- /bin/true >/dev/null 2>&1; then
      say "= guest '$DEBIAN_NAME' already installed"
    else
      # Termux ships proot-distro 5.x (Python rewrite, Docker-Hub images; read 2026-09-26). The second form is the
      # pre-5.0 alias syntax, tried only if the first is rejected.
      run proot-distro install "$DEBIAN_IMAGE" --name "$DEBIAN_NAME" || run proot-distro install "$DEBIAN_NAME"
    fi
    say ""
    say "٢) الدخول مع مشاركة مجلد المنزل (نفس ملفات اللعبة داخل وخارج Debian) / 2) log in, sharing the Termux home"
    say "+ proot-distro login $DEBIAN_NAME --shared-home"
    say "   داخل Debian بعدها / then, inside Debian:"
    say "+ cd ~/Ai-game-godot && sh scripts/termux_setup.sh --guest --install-godot"
    say "   ملاحظة: متغيرات البيئة لا تنتقل إلى الداخل — أعد كتابة export للمفتاح داخل Debian إن شغّلت المحادثة من هناك."
    ;;
  guest)
    if ! in_proot_guest && [ "$PRINT_ONLY" = 0 ] && in_termux; then
      say "you are in Termux itself; run this mode inside the Debian guest: proot-distro login $DEBIAN_NAME --shared-home" >&2; exit 3
    fi
    say "١) مكتبات التشغيل للمحرك بلا واجهة (نفس قائمة deploy/paas/Dockerfile) / 1) runtime libs for the headless editor"
    if command -v apt-get >/dev/null 2>&1 && [ "$PRINT_ONLY" = 0 ]; then
      run apt-get update
      # shellcheck disable=SC2086
      run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $GUEST_TOOLS $GUEST_LIBS
    else
      say "+ apt-get update && apt-get install -y --no-install-recommends $GUEST_TOOLS $GUEST_LIBS"
    fi
    say ""
    say "٢) المحرك لهذه المعالج بالضبط (يُتحقق من SHA-512) / 2) the editor for THIS CPU (SHA-512 verified)"
    if [ -z "$ENGINE_PLATFORM" ]; then
      say "!! no official Godot build for cpu '$machine' — engine verification must happen on GitHub Actions (docs/TERMUX.md §3)"
    else
      say "+ export GODOTAI_ENGINE_PLATFORM=$ENGINE_PLATFORM"
      say "+ python3 -m godotai install-godot --editor-only    # ~100 MB; templates are NOT needed on the phone (APK is built on GitHub)"
      say "+ export PATH=\"\$HOME/.local/bin:\$PATH\""
      say "+ GODOTAI_ENGINE_PLATFORM=$ENGINE_PLATFORM python3 -m godotai doctor"
      if [ "$INSTALL_GODOT" = 1 ] && [ "$PRINT_ONLY" = 0 ]; then
        export GODOTAI_ENGINE_PLATFORM="$ENGINE_PLATFORM"
        run python3 -m godotai install-godot --editor-only
        PATH="$HOME/.local/bin:$PATH"; export PATH
        run python3 -m godotai doctor || true
      fi
    fi
    say ""
    say "٣) بعدها كل أمر يحتاج المتغيّر نفسه / 3) every later command needs the same variable:"
    say "+ export GODOTAI_ENGINE_PLATFORM=${ENGINE_PLATFORM:-linux.arm64}   # أضِفه إلى ~/.bashrc داخل Debian"
    say "+ export GODOTAI_PRESET=openrouter_free && export OPENROUTER_API_KEY='<your key>'   # again: the guest does not inherit Termux variables"
    say "+ python3 -m godotai chat     # http://127.0.0.1:8765/ — الآن التحقق بالمحرك يعمل داخل Debian"
    ;;
esac
say ""
say "docs/TERMUX.md — الدليل الكامل بالعربي (الحصص، القيود، APK عبر GitHub Actions، ما جُرّب وما لم يُجرَّب)."
