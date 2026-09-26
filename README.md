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
5. **يتحقق بالمحرك الحقيقي** — يشغّل Godot 4.7.2 بوضع headless: `--import`، ثم `--check-only` على كل سكربت، ثم اختبار تشغيل (smoke test)، ثم فحص كل API مستخدم مقابل قاعدة الأصناف (ClassDB) المولَّدة من نفس المحرك. لا يُسمح له بإعلان النجاح إلا بعد أن يقول المحرك **PASS**.
6. **يصدّر APK** — محليًا داخل حاوية Linux (Godot + JDK 17 + Android SDK) أو عبر GitHub Actions بعد رفع اللعبة إلى مستودع على حسابك.
7. **لا يعتمد على ذاكرته في أسماء الدوال** — أدوات `api_lookup` / `api_search` / `api_lint` تقرأ التوقيعات الحقيقية من فهرس مولَّد بأمر `godot --doctool` على النسخة 4.7.2 بالضبط (1076 صنفًا، 10731 دالة). أي استخدام لـ API من Godot 3 أو دالة غير موجودة يظهر كخطأ يجب إصلاحه.

**تغيير الإصدار مستقبلًا:** كل شيء يقرأ الإصدار من ملف واحد `godot.toml`. لتغيير الإصدار لاحقًا: عدّل `[engine].version`، أضف ملف `SHA512-SUMS.txt` الرسمي للإصدار الجديد تحت `engine/checksums/`، ثم شغّل `python3 -m godotai doctor`.

**النموذج — والفكرة الأساسية:** الهدف هو ذكاء اصطناعي **مخصص** لصناعة ألعاب Godot، وليس تشغيل Claude كنموذج نهائي. Claude Fable 5.1 هنا **مرجع** درسنا منه لماذا يفكر جيدًا، ونقلنا *الطريقة* إلى هذا المشروع:

- **لماذا Fable 5.1 قوي؟ (من الوثائق الرسمية، قراءة 2026-09-26):** ليس لأنه «يفكر وقتًا طويلًا» فقط. التفكير عنده **تكيّفي دائم** (يقرر متى وبأي عمق يفكر)، وهو تفكير **يراجع نفسه** (يعيد صياغة المطلوب، يجرّب طرقًا، يتحقق من النتائج الوسيطة ويتخلى عمّا لا يصمد)، ويفكر **بين كل استدعاء أداة وآخر** (interleaved thinking) أي يقرأ نتيجة كل أداة قبل أن يقرر، ومعامل **effort** يوسّع هذا كله حتى `max`، ويملك ذاكرة سياق 1M وحدة و128K إخراج، مع أدوات لعمل طويل النفس (ميزانية المهمة، تحديثات التقدم، تغيير الجهد أثناء المحادثة، والحفاظ على تسلسل التفكير بشكل append-only).
- **ما نقلناه إلى godotai:** حلقة «فكّر → استدعِ أداة → اقرأ نتيجة *المحرك الحقيقي* → فكّر مجددًا» أصبحت أساس الوكيل؛ فهرس API مولَّد من المحرك بدل الاعتماد على الذاكرة؛ مفاتيح تشغيل لكل ما تقدم (`effort`, `act_effort`, `task_budget_tokens`, `progress_updates`, `turn_scoped_system`) في `godot.toml`؛ وبنك اختبارات (`evals/`) يحكم فيه **المحرك** لا نموذج آخر.
- **طريق النموذج المخصص:** كل تشغيل ناجح يترك أثرًا كاملًا متحقَّقًا منه بالمحرك. أمر `dataset extract` يحوّل هذه الآثار إلى بيانات تدريب (بعد حذف التفكير الداخلي وإخفاء أي أسرار)، و`training/train_qlora.py` يدرّب نموذجًا مفتوح الوزن (QLoRA) عليها — يمكن تشغيله على Kaggle (GPU T4 ×2) كـ**مهمة دفعية** فقط، لأن Kaggle ليس خادمًا دائم التشغيل (حد 12 ساعة، ويتوقف بعد 20 دقيقة خمول). النموذج الناتج يُشغَّل عبر أي خادم متوافق مع OpenAI، ويُقاس بنفس بنك الاختبارات. **ملاحظة صريحة:** مسار التدريب مكتوب ومفحوص بلا GPU، ولم يُنفَّذ فعليًا بعد؛ والـ GPU/TPU لا تجعل الوكيل «أذكى» بذاتها — الذكاء يأتي من النموذج الأساسي وطريقة قيادة الحلقة.
- **Cloudflare:** اختياري كـ AI Gateway (سجلات، تخزين مؤقت، حدود معدل، لوحة تكلفة) أو Workers AI لنماذج مفتوحة الوزن — بمتغيرات بيئة فقط، ولا يُخزَّن أي مفتاح أو معرّف حساب في المستودع. لم يُجرَ أي طلب حي إلى Cloudflare أو Kaggle من هذا المشروع.

> 🔐 **أمان:** أي مفتاح API لُصق في محادثة يجب اعتباره مكشوفًا — **أعد إنشاءه (rotate) الآن** من لوحة Cloudflare/Kaggle. راجع `SECURITY.md`. المستودع يحوي فاحص أسرار يعمل في CI ويمنع تسريب أي مفتاح بالخطأ.

> ⚠️ **ما تم التحقق منه فعليًا وما لم يتم:** راجع قسم *Verification status* بالأسفل. لا نزعم أي قدرة لم نجرّبها.

---

## What is in this repository

| Path | Purpose |
| --- | --- |
| `godot.toml` | **Single source of truth**: engine pin (4.7.2-stable), Android SDK requirements, model/effort settings |
| `godotai/` | The agent (stdlib-only Python 3.11+, zero dependencies): CLI, agent loop, planner, tools, providers, verification, installer |
| `godotai/prompts/` | System prompt (strict scope + engineering standards) and planning-phase prompt |
| `godotai/apiref.py` + `tools/apiref_tools.py` | **ClassDB index generated from the pinned binary** (`--doctool`): `api_lookup`, `api_search`, `api_lint`; advisory lint in every verification |
| `godotai/evals.py` + `evals/tasks/` | Engine-verified task bank (6 tasks incl. a Godot-3 migration trap and an out-of-scope refusal); scored by the engine + structural checks, never by a model |
| `godotai/dataset.py` + `training/` | Verified run logs → redacted SFT JSONL → optional QLoRA fine-tune of an open-weight model (Kaggle T4×2 script kernel included) |
| `godotai/secrets.py` + `scripts/secret_scan.py` + `SECURITY.md` | Secret detection/redaction in transcripts and datasets; CI secret scan; credential handling and rotation rules |
| `godotai/providers/cloudflare.py` | Optional Cloudflare AI Gateway / Workers AI routing built from environment variable names only |
| `knowledge/` | Version-pinned Godot 4.7 notes the model loads on demand (progressive disclosure) |
| `templates/mobile-2d/` | A real, verified mobile game project ("Tap Dodge") used as scaffold and as CI fixture |
| `engine/checksums/4.7.2-stable/SHA512-SUMS.txt` | Official checksums; every engine download is verified against them |
| `docker/Dockerfile` | Linux execution environment: Godot 4.7.2 headless + export templates + OpenJDK 17 + Android SDK |
| `.github/workflows/ci.yml` | Unit tests, byte-compile, secret scan, training dry-run, Kaggle self-check, then real-engine API index + template verification + baseline eval on every PR |
| `.github/workflows/build-android.yml` | Reusable APK build workflow; the agent copies it into every game repo it creates |
| `tests/` | 200+ offline unit tests + real-engine integration tests (auto-skipped without the binary) |
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

# 4. Exact engine API without guessing (also available to the model as tools)
python3 -m godotai apiref build                       # ClassDB index from the pinned binary, ~2 s, cached per engine tag
python3 -m godotai apiref lookup CharacterBody2D move_and_slide
python3 -m godotai apiref lint --project ./tapdodge   # Godot-3 idioms / unknown APIs

# 5. Let the AI build a game (plan → your approval → act → verify)
export ANTHROPIC_API_KEY=...                # or set GODOTAI_PROVIDER=openai_compat + OPENAI_BASE_URL for local models
python3 -m godotai plan "لعبة 2D endless runner للموبايل بالتحكم باللمس" --workspace ./runner   # plan only
python3 -m godotai run  "make a 2D endless runner for Android with touch controls" --workspace ./runner

# 6. Measure, collect, (optionally) train the specialised model — see training/README.md
python3 -m godotai eval list && python3 -m godotai eval run --task flappy-clone --workspace ./flappy --yes
python3 -m godotai dataset extract --runs . --out data/sft.jsonl
python3 training/train_qlora.py --data data/sft.jsonl --dry-run
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
- ✅ 200+ unit/integration tests pass (`python3 -m unittest discover -s tests`), with and without the engine binary.
- ✅ `ci.yml` passed on a clean GitHub-hosted Ubuntu runner (PR #1): installer downloaded + checksum-verified the
  pinned editor, `doctor` ran, the template was scaffolded and verified with the real engine.
- ✅ ClassDB API index generated from the pinned binary with `--doctool`: 1 076 classes, 10 731 methods,
  6 999 properties, 503 signals, 6 012 constants; lookups (`CharacterBody2D.move_and_slide`, inherited
  `Node.queue_free`, `Area2D.body_entered`, `@GlobalScope.randf_range`, …) and Godot-3 rename suggestions verified.
- ✅ `eval score --task template-baseline` passes on the template with the real engine (import, check-only,
  smoke test, API lint, structural checks, ≥ 90 % static typing).
- ✅ Dataset extraction → `train_qlora.py --dry-run` round-trip on synthetic verified runs; repo-wide secret scan clean.
- ✅ `ci.yml` passed again on a GitHub-hosted runner for PR #2 (run #5): 201 tests without the engine, then the
  runner installed the pinned editor, built the ClassDB index from it (same 1 076 / 10 731 / 6 999 / 503 / 6 012
  counts), verified the template, ran the 201 tests *with* the engine, and `eval score --task template-baseline`
  reported **PASS**; secret scan, training dry-run and `kaggle_push.py --check` all passed credential-free.

Not yet verified (implemented, but no evidence of success — treat as untested):

- ❌ Live model calls to `claude-fable-5-1` / OpenAI-compatible servers (request shapes — including the opt-in
  Fable 5.1 betas: per-message effort, task budgets, progress updates, turn-scoped system messages — are
  unit-tested against the documentation only).
- ❌ Cloudflare AI Gateway / Workers AI and Kaggle: URL/header/metadata construction is unit-tested; **no live
  request or kernel push was made**, and no credential from the user was used anywhere.
- ❌ QLoRA training itself (`training/train_qlora.py` without `--dry-run`) — written against the TRL v1.14 /
  transformers docs, never executed (no GPU here). The eval tasks that need a model (`eval run`) have not been
  run against any model yet; only the no-LLM `template-baseline` score has.
- ❌ GitHub *tools* against a real token (`github_create_repo`/`push`/`build_apk`); the `build-android.yml` workflow
  has not been run yet; Docker image build (no Docker in the sandbox).
- ❌ Release-signed APK / AAB, Gradle builds (needs NDK + CMake + Android build template), Play Store compliance.
- ❌ Behaviour on a physical Android device (touch feel, performance, audio).

See [docs/RESEARCH.md](docs/RESEARCH.md) for sources and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design decisions.

## License

MIT.
