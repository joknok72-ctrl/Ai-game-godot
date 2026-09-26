# godotai — ذكاء اصطناعي متخصص في صناعة الألعاب بمحرك Godot Engine 4.7.2-stable

> **AI agent specialised in one thing only: building games with Godot Engine 4.7.2-stable**
> (GDScript, Linux/container environment, plan-before-act, GitHub integration, Android APK export).

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

---

## بالعربي — ما هذا المشروع؟

هذا وكيل ذكاء اصطناعي (agent) مجاله **واحد فقط**: صناعة ألعاب على محرك **Godot Engine 4.7.2-stable** بلغة GDScript.
ليس مساعدًا عامًا؛ إذا طُلب منه أي شيء خارج صناعة ألعاب Godot يرفض بجملة واحدة ويعرض المساعدة في لعبة Godot بدلًا من ذلك.

**كيف يعمل (باختصار):**

1. **يفكّر أولًا** — يقرأ المشروع الحالي، يراجع قاعدة المعرفة المثبَّتة على الإصدار 4.7.2، ويحدّد التصميم (النوع، حلقة اللعب، المشاهد، السكربتات، التحكم باللمس…).
2. **يقدّم خطة** عبر أداة `submit_plan` — الخطة تُفحص آليًا (الإصدار الصحيح، مسارات آمنة، خطوات متسلسلة S1..Sn، خطوة تحقق إلزامية) ثم تُعرض عليك للموافقة.
3. **بوابة الموافقة** — لا يمكنه إنشاء أو تعديل أي ملف قبل الموافقة. هذا **مفروض بالكود** (`godotai/tools/base.py`) وليس مجرد تعليمات في الـ prompt.
4. **ينفّذ** خطوة خطوة، وكل تعديل يجب أن يذكر رقم الخطوة التي ينفّذها من الخطة المعتمدة.
5. **يتحقق بالمحرك الحقيقي** — يشغّل Godot 4.7.2 بوضع headless: `--import`، ثم `--check-only` على كل سكربت، ثم اختبار تشغيل (smoke test). لا يُسمح له بإعلان النجاح إلا بعد أن يقول المحرك **PASS**.
6. **يصدّر APK** — محليًا داخل حاوية Linux (Godot + JDK 17 + Android SDK) أو عبر GitHub Actions بعد رفع اللعبة إلى مستودع على حسابك.

**تغيير الإصدار مستقبلًا:** كل شيء يقرأ الإصدار من ملف واحد `godot.toml`. لتغيير الإصدار لاحقًا: عدّل `[engine].version`، أضف ملف `SHA512-SUMS.txt` الرسمي للإصدار الجديد تحت `engine/checksums/`، ثم شغّل `python3 -m godotai doctor`.

**النموذج:** الافتراضي هو `claude-fable-5-1` بجهد `max` (أعلى مستوى تفكير) عبر Claude API، مع دعم بديل لأي خادم متوافق مع OpenAI (Ollama / vLLM / OpenRouter…) لتشغيل نماذج مفتوحة المصدر مثل Qwen3-Coder أو GLM أو DeepSeek إذا رغبت.

> ⚠️ **ما تم التحقق منه فعليًا وما لم يتم:** راجع قسم *Verification status* بالأسفل. لا نزعم أي قدرة لم نجرّبها.

---

## What is in this repository

| Path | Purpose |
| --- | --- |
| `godot.toml` | **Single source of truth**: engine pin (4.7.2-stable), Android SDK requirements, model/effort settings |
| `godotai/` | The agent (stdlib-only Python 3.11+, zero dependencies): CLI, agent loop, planner, tools, providers, verification, installer |
| `godotai/prompts/` | System prompt (strict scope + engineering standards) and planning-phase prompt |
| `knowledge/` | Version-pinned Godot 4.7 notes the model loads on demand (progressive disclosure) |
| `templates/mobile-2d/` | A real, verified mobile game project ("Tap Dodge") used as scaffold and as CI fixture |
| `engine/checksums/4.7.2-stable/SHA512-SUMS.txt` | Official checksums; every engine download is verified against them |
| `docker/Dockerfile` | Linux execution environment: Godot 4.7.2 headless + export templates + OpenJDK 17 + Android SDK |
| `.github/workflows/ci.yml` | Unit tests + real-engine verification of the template on every PR |
| `.github/workflows/build-android.yml` | Reusable APK build workflow; the agent copies it into every game repo it creates |
| `tests/` | 100+ offline unit tests + real-engine integration tests (auto-skipped without the binary) |
| `docs/` | [ARCHITECTURE](docs/ARCHITECTURE.md) · [USAGE](docs/USAGE.md) · [RESEARCH](docs/RESEARCH.md) |

## Quick start (Linux)

```bash
git clone <this repo> && cd Ai-game-godot

# 1. Engine: download Godot 4.7.2-stable editor + export templates, SHA-512 verified (~1.4 GB)
python3 -m godotai install-godot            # → ~/.local/bin/godot ; add it to PATH
python3 -m godotai doctor                   # shows what is present / missing

# 2. (optional, for local APK export) OpenJDK 17 + Android SDK + debug keystore + editor settings
sudo apt install openjdk-17-jdk-headless
python3 -m godotai setup-android            # installs the packages listed in godot.toml

# 3. Scaffold and verify a game with the real engine — no LLM involved
python3 -m godotai new --template mobile-2d --dest ./tapdodge --name "Tap Dodge" --package com.studio.tapdodge
python3 -m godotai verify --project ./tapdodge
python3 -m godotai export --project ./tapdodge --out build/android/game.apk   # debug APK

# 4. Let the AI build a game (plan → your approval → act → verify)
export ANTHROPIC_API_KEY=...                # or set GODOTAI_PROVIDER=openai_compat + OPENAI_BASE_URL for local models
python3 -m godotai plan "لعبة 2D endless runner للموبايل بالتحكم باللمس" --workspace ./runner   # plan only
python3 -m godotai run  "make a 2D endless runner for Android with touch controls" --workspace ./runner
```

### Docker (the recommended execution environment)

```bash
docker build -t godotai -f docker/Dockerfile .
docker run --rm -it -e ANTHROPIC_API_KEY -e GITHUB_TOKEN -v "$PWD/games:/games" godotai \
       run "make a match-3 game for Android" --workspace /games/match3
```

The image contains everything the official 4.7 Android export documentation requires (OpenJDK 17, Build-Tools 35.0.1,
Platform 35, CMake 3.10.2.4988404, NDK r28b) plus the pinned engine, all installed from `godot.toml`.

## GitHub integration

With `GITHUB_TOKEN` set (classic `repo`+`workflow`, or a fine-grained token with Contents/Actions/Administration write),
the agent can:

- `github_create_repo` — create a repository for the game on your account;
- `github_push_project` — commit and push the project (adds a safe `.gitignore` and the APK workflow);
- `github_build_apk` — dispatch `build-android.yml`, which installs Godot 4.7.2 + templates (checksum-verified),
  JDK 17 and the SDK on a GitHub runner, exports the APK and uploads it as an artifact;
- `github_build_status` — report run status and artifact download links.

The token is only ever sent as an `Authorization` header / `GIT_ASKPASS` helper — never written to files, URLs or model output.

## Verification status (honest accounting)

Verified on 2026-09-26 in a Linux sandbox with the official `Godot_v4.7.2-stable_linux.x86_64` binary
(`4.7.2.stable.official.ed1daf0bf`, SHA-512 verified):

- ✅ Installer downloads editor + templates and verifies both against the pinned checksums.
- ✅ Template scaffolds, `--import`s, all scripts pass `--check-only`, smoke test prints `SMOKE_TEST_OK`.
- ✅ A deliberately broken script makes the pipeline **FAIL** with file/line details (negative test).
- ✅ Android SDK setup (`platform-tools`, `build-tools;35.0.1`, `cmdline-tools;latest`) + JDK 17 + debug keystore + editor settings.
- ✅ **Real debug APK exported** from the template (`arm64-v8a`, ~27 MiB, `com.example.tapdodge`, signed with the debug key).
- ✅ 110 unit/integration tests pass (`python3 -m unittest discover -s tests`).

Not yet verified (implemented, but no evidence of success — treat as untested):

- ❌ Live model calls to `claude-fable-5-1` / OpenAI-compatible servers (request shapes are unit-tested against the docs only).
- ❌ GitHub tools against a real token; GitHub Actions workflow runs; Docker image build (no Docker in the sandbox).
- ❌ Release-signed APK / AAB, Gradle builds (needs NDK + CMake + Android build template), Play Store compliance.
- ❌ Behaviour on a physical Android device (touch feel, performance, audio).

See [docs/RESEARCH.md](docs/RESEARCH.md) for sources and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design decisions.

## License

MIT.
