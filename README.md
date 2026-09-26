# godotai — ذكاء اصطناعي خاص بك، متخصص في صناعة الألعاب بمحرك Godot Engine 4.7.2-stable

> **Your own AI agent, specialised in one thing only: building games with Godot Engine 4.7.2-stable**
> (GDScript, Linux/container environment, plan-before-act, real-engine verification, GitHub integration, Android APK export) —
> running on **your** model server by default, with no vendor model in the loop.

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

---

## فين المكان اللي أكلّم فيه الذكاء الاصطناعي وأقوله يعمل لعبة؟

**الجواب المباشر:** صفحة محادثة في المتصفح تشغّلها بأمر واحد — على جهازك، أو على خادمك كموقع عام محمي بـ Cloudflare
(`deploy/cloudflare/`). **لا يوجد موقع منشور من هذا المستودع**؛ النشر خطوة تنفّذها أنت بحسابك (الخطوات بالأسفل).

```bash
python3 -m godotai chat
#   افتح هذا العنوان في المتصفح:   http://127.0.0.1:8765/
```

في الصفحة: تنشئ «مشروعًا» (= مجلد لعبة)، تختار فكرة من الأزرار الجاهزة (Flappy Bird، Endless Runner، Match-3، Platformer،
Top-down shooter، 2048، HUD/إيقاف، تصدير APK…) أو تكتب أي لعبة بالعربي أو الإنجليزي وتضغط **«ابعت للذكاء الاصطناعي»**،
يظهر لك **خطة**، توافق عليها (أو تكتب ما تريد تغييره)، ثم يبني الملفات ويشغّل محرك Godot 4.7.2 للتحقق، وترى كل خطوة
وتقرير المحرك مباشرة في المحادثة. الرسالة التالية في نفس المشروع تعدّل نفس اللعبة («أضف زر إيقاف»، «صدّر APK»…).
أعلى الصفحة بطاقة **هوية النموذج** تقول بالضبط ما النموذج الذي يخدمك (الأساس، الترخيص، هل هو لك، هل مدرَّب من الصفر أم لا).

**خطوات التشغيل من الصفر (مرة واحدة على جهاز Linux أو WSL):**

```bash
git clone https://github.com/joknok72-ctrl/Ai-game-godot.git && cd Ai-game-godot

# 1) محرك Godot 4.7.2 (تنزيل رسمي مُتحقَّق منه بـ SHA-512، ~1.4 GB) — بدونه لا يستطيع التحقق من أي لعبة
python3 -m godotai install-godot
export PATH="$HOME/.local/bin:$PATH"

# 2) نموذجك أنت — خادم متوافق مع OpenAI على جهازك/خادمك؛ لا مفتاح شركة ولا اشتراك. الافتراضي يتوقع http://127.0.0.1:8000/v1
#    GPU (vLLM): النموذج المفتوح Qwen2.5-Coder-7B-Instruct (Apache-2.0) يُقدَّم باسم "godotai" مع استدعاء الأدوات
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --served-model-name godotai --enable-auto-tool-choice --tool-call-parser hermes
#    أو CPU (Ollama، بطيء):  ollama pull qwen2.5-coder:7b
#                            export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 GODOTAI_MODEL=qwen2.5-coder:7b

# 3) شغّل صفحة المحادثة وافتح العنوان الذي يطبعه (الحالة تقول: خادمك متاح؟ الاسم موجود في /models؟ المحرك موجود؟)
python3 -m godotai chat
```

- **ما هو «نموذجي»؟** اقرأ [`docs/MODEL.md`](docs/MODEL.md) — الفرق الدقيق بين **نشر خاص لنموذج مفتوح الوزن** (الافتراضي الآن:
  Qwen2.5-Coder، على خادمك، بلا أي شركة في الطريق)، و**تدريب خاص بك** (محوّل LoRA من `training/` على آثار متحقَّق منها بالمحرك)،
  و**تدريب من الصفر** (غير واقعي لفرد، ولا يُعلَن إلا بدليل)، و**نموذج شركة** (Claude وغيره: اختياري، مُعلَن، مرجع للمقارنة فقط).
- **أقوى من Claude Fable 5.1 (Max)؟** لا يُقال هذا هنا إلا كنتيجة `python3 -m godotai eval compare` على نفس المهام وبحُكم المحرك؛
  بلا نتائج مشتركة يطبع **NO EVIDENCE**. لا وعود.
- **موقع عام لك (اختياري):** `deploy/cloudflare/` — Cloudflare Tunnel (لا منفذ مفتوح على خادمك) + Cloudflare Access (تسجيل دخول
  ببريدك فقط) + الخادم يتحقق من توقيع Access في كل طلب + حصة تشغيل (429 عند التجاوز)؛ `scripts/cloudflare_setup.py --dry-run` يعرض
  كل ما سيُنفَّذ بلا اتصال. التوكن يُقرأ من متغير بيئة في طرفيتك (أو من سرّ GitHub محمي في `cloudflare-provision.yml`) ولا يُخزَّن.
  **ما تحتاجه أنت ولا توفّره Cloudflare:** نطاق مضاف إلى Cloudflare + جهاز يعمل دائمًا للـ chat والنموذج (أو مسار Workers AI بلا GPU —
  «عند مزوّد استضافة» لا «على خادمك»). **لم يُنشر شيء من هذا المستودع.** وأي توكن لُصق في محادثة يجب عمل *Roll* له فورًا.
- **مش معي فلوس — كل شيء مجانًا؟** اقرأ [`docs/FREE_TIER.md`](docs/FREE_TIER.md) (بحث سبتمبر 2026 بمصادره). الخلاصة: بدون GPU
  يمكنك استخدام **Qwen عبر API بحصة مجانية** من مزوّد استضافة — `GODOTAI_PRESET=openrouter_free` أو `groq` أو `alibaba_model_studio`
  أو `workers_ai` (`python3 -m godotai presets` يعرض الحدود والمفتاح المطلوب وسياسة البيانات لكل واحد؛ المفتاح من حسابك أنت،
  في متغير بيئة فقط). هذا «نموذج مفتوح الوزن **عند مزوّد**» لا «خادمك الخاص»: طلباتك تُعالَج عندهم. خادم Railway ذو
  **2 vCPU / 1 GB**: يشغّل **التطبيق فقط** (صورة `deploy/paas/`، قياس 2026-09-26: التحقق بالمحرك يصل إلى ≈ 640 MB) ولا يشغّل أي
  نموذج Qwen؛ بعد التجربة (30 يومًا / 5$) تصير الخطة المجانية 0.5 GB وهي أقل من اللازم. أقوى خادم لينكس مجاني وجدناه:
  **Oracle Cloud Always Free** (ARM، 2 OCPU / 12 GB، ببطاقة للتحقق). **لا شيء منشور من هذا المستودع** ولا يمكن نشر موقع دائم
  بدون حسابك ومفتاحك وتوكن دخول (`GODOTAI_CHAT_TOKEN`) — [`deploy/paas/README.md`](deploy/paas/README.md) يشرح الخطوات.
  لا حصة مجانية تعني «مجاني للأبد»، ولا أحد هنا يزعم أن Qwen المجاني أقوى من Claude Fable 5.1 Max — الحكم فقط لـ `eval compare`.
- **بدون متصفح (سطر أوامر):** `python3 -m godotai run "اعمل لعبة Flappy Bird للموبايل" --workspace ./flappy`
  — نفس الوكيل تمامًا، والموافقة على الخطة تكون سؤالًا في الطرفية.
- **لو ناقص شيء:** الصفحة نفسها تكتب لك بالعربي ما الناقص بالضبط (خادم النموذج؟ المحرك؟) والأمر الذي تنفّذه، و
  `python3 -m godotai doctor` يعرض نفس الفحص في الطرفية.
- **Claude كمرجع (اختياري، بمفتاحك):** `GODOTAI_PROVIDER=anthropic GODOTAI_MODEL=claude-fable-5-1 GODOTAI_MODEL_KIND=vendor_api
  GODOTAI_SERVING=vendor GODOTAI_BASE_MODEL=claude-fable-5-1 ANTHROPIC_API_KEY=…` — الإعداد **يرفض** تشغيل نموذج شركة دون هذا الإعلان.

> أين تظهر اللعبة؟ في `./games/<اسم-المشروع>/` بجانب المستودع (يمكن تغييره بـ `--games-dir`). افتحه بمحرر Godot 4.7.2
> العادي إن أردت، أو اطلب من الذكاء الاصطناعي تصدير APK.

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

**النموذج — والفكرة الأساسية:** الهدف ذكاء اصطناعي **لك أنت**، مخصص لصناعة ألعاب Godot، لا تشغيل نموذج شركة كمنتج نهائي.
لذلك:

- **الافتراضي = نموذجك على خادمك.** `provider = "openai_compat"` و`model = "godotai"`: نموذج مفتوح الوزن (Qwen2.5-Coder-7B-Instruct،
  ترخيص Apache-2.0) يعمل على خادمك عبر vLLM/Ollama/llama.cpp، ولا يُرسل شيء لأي شركة. قسم `[model]` في `godot.toml` يسجّل **ما هي
  الأوزان بالضبط** (`open_weight_deployment` الآن، `fine_tune` بعد أول محوّل تدرّبه، `from_scratch` فقط بدليل، `vendor_api` لنماذج
  الشركات)، والصفحة و`doctor` يعرضان الجملة نفسها — بما فيها ما **ليس** عليه («ليس مدرَّبًا من الصفر»). الكود يرفض تقديم نموذج شركة
  كنموذجك (`godotai/model_identity.py`). التفاصيل والقانون: [`docs/MODEL.md`](docs/MODEL.md).
- **التخصص الذي تملكه بالكامل:** فهرس API من المحرك نفسه، تحقق بالمحرك الحقيقي قبل أي «نجح»، قاعدة معرفة مثبَّتة على 4.7.2،
  بوابة خطة مفروضة بالكود، وبنك اختبارات يحكم فيه **المحرك** لا نموذج آخر (`evals/`). هذه الطبقة هي ما يجعل نموذجًا أصغر
  قادرًا — إن قاسه المحرك — على منافسة نموذج عام أكبر في هذا المجال الضيق.
- **طريق النموذج المخصص:** كل تشغيل ناجح يترك أثرًا كاملًا متحقَّقًا منه بالمحرك. أمر `dataset extract` يحوّل هذه الآثار إلى بيانات
  تدريب (بعد حذف التفكير الداخلي وإخفاء أي أسرار)، و`training/train_qlora.py` يدرّب محوّل LoRA على الأساس المفتوح — على GPU لديك أو
  على Kaggle (T4 ×2) كـ**مهمة دفعية** (Kaggle ليس خادمًا دائمًا). المحوّل يُقدَّم بنفس الاسم `godotai` (`vllm --enable-lora`) ويُقاس
  بنفس بنك الاختبارات. **ملاحظة صريحة:** مسار التدريب مكتوب ومفحوص بلا GPU ولم يُنفَّذ بعد؛ ولا تُستخدم آثار نموذج شركة كبيانات تدريب
  (شروط الاستخدام تقيّد ذلك) — آثار المرجع للمقارنة فقط.
- **Claude Fable 5.1 = مرجع، لا منتج.** درسنا من وثائقه *لماذا* يفكر جيدًا (تفكير تكيّفي يراجع نفسه، تفكير بين كل أداة وأخرى، معامل
  effort، ميزانيات المهام) ونقلنا *الطريقة* إلى الحلقة والإعدادات (`docs/RESEARCH.md` §3). المقارنة معه تكون فقط بـ
  `python3 -m godotai eval compare` على نفس المهام.
- **Cloudflare:** مسار النشر الاختياري (`deploy/cloudflare/`): Tunnel + Access + تحقق من JWT في الخادم، بمتغيرات بيئة فقط.
  AI Gateway/Workers AI ما زالا خيارين اختياريين للتوجيه. لا يُخزَّن أي مفتاح أو معرّف حساب في المستودع، ولم يُجرَ أي طلب حي إلى Cloudflare أو Kaggle.

> 🔐 **أمان:** أي مفتاح API لُصق في محادثة يجب اعتباره مكشوفًا — **أعد إنشاءه (rotate) الآن** من لوحة Cloudflare/Kaggle. راجع `SECURITY.md`. المستودع يحوي فاحص أسرار يعمل في CI ويمنع تسريب أي مفتاح بالخطأ.

> ⚠️ **ما تم التحقق منه فعليًا وما لم يتم:** راجع قسم *Verification status* بالأسفل. لا نزعم أي قدرة لم نجرّبها.

---

## What is in this repository

| Path | Purpose |
| --- | --- |
| `godot.toml` | **Single source of truth**: engine pin (4.7.2-stable), Android SDK requirements, agent settings, and `[model]` — *what the weights behind the AI are* |
| `godotai/` | The agent (stdlib-only Python 3.11+, zero dependencies): CLI, agent loop, planner, tools, providers, verification, installer |
| `godotai/model_identity.py` + `docs/MODEL.md` | Honest model identity: private open-weight deployment / your fine-tune / from-scratch / vendor API — shown in the UI and `doctor`, enforced by the config loader; no quality claim without `eval compare` evidence |
| `godotai/prompts/` | System prompt (strict scope + engineering standards) and planning-phase prompt |
| `godotai/chat/` | **The place to talk to the AI**: `python3 -m godotai chat` → browser page (Arabic-first, quick game prompts, identity card, model-server status) + JSON/SSE API over the *same* agent, plan gate and engine verification as `run` |
| `godotai/chat/access.py` | Cloudflare Access JWT verification (JWKS, RS256, iss/aud/exp) for the public-website mode; `/healthz` is the only unauthenticated route |
| `godotai/chat/quota.py` | Run quota for shared/public servers: concurrency cap for the whole server + rolling-24 h cap per visitor (Access e-mail); `429` + `Retry-After` before any model work; the cost/abuse brake behind the login |
| `deploy/cloudflare/` + `scripts/cloudflare_setup.py` | Optional public website: Access app → Tunnel → ingress → DNS via the Cloudflare API (dry-run available), compose file with your model server (vLLM base / vLLM + LoRA / Ollama) and `cloudflared`; no port published, no credential stored |
| `godotai/presets.py` + `docs/FREE_TIER.md` + `deploy/paas/` | **No budget, no GPU**: `GODOTAI_PRESET=openrouter_free\|groq\|alibaba_model_studio\|workers_ai` switches the agent to a hosted Qwen endpoint with a free allowance (limits, key variable *name*, data-handling notes and sources per preset, `python3 -m godotai presets`); the identity card then says *managed hosting of open weights*, never "your server". `deploy/paas/` is the app-only image (≈ 50 MB idle, ≈ 640 MB during one verification) for a 1 GB host such as the Railway trial or Oracle Always Free — auth token mandatory, quota on, no secret in any file. September-2026 research with sources in `docs/FREE_TIER.md` |
| `godotai/apiref.py` + `tools/apiref_tools.py` | **ClassDB index generated from the pinned binary** (`--doctool`): `api_lookup`, `api_search`, `api_lint`; advisory lint in every verification |
| `godotai/evals.py` + `evals/tasks/` | Engine-verified task bank (6 tasks incl. a Godot-3 migration trap and an out-of-scope refusal); scored by the engine + structural checks, never by a model; `eval compare` puts two models side by side on common tasks |
| `godotai/dataset.py` + `training/` | Verified run logs → redacted SFT JSONL → optional QLoRA fine-tune of the open-weight base (Kaggle T4×2 script kernel included) |
| `godotai/secrets.py` + `scripts/secret_scan.py` + `SECURITY.md` | Secret detection/redaction in transcripts and datasets; CI secret scan (incl. tunnel/chat tokens); credential handling and rotation rules |
| `godotai/providers/` | `openai_compat` (your server: vLLM / Ollama / llama.cpp — the default), `anthropic` (opt-in vendor reference), Cloudflare AI Gateway / Workers AI routing from environment variable names only |
| `knowledge/` | Version-pinned Godot 4.7 notes the model loads on demand (progressive disclosure) |
| `templates/mobile-2d/` | A real, verified mobile game project ("Tap Dodge") used as scaffold and as CI fixture |
| `engine/checksums/4.7.2-stable/SHA512-SUMS.txt` | Official checksums; every engine download is verified against them |
| `docker/Dockerfile` | Linux execution environment: Godot 4.7.2 headless + export templates + OpenJDK 17 + Android SDK |
| `.github/workflows/ci.yml` | Unit tests, byte-compile, secret scan, Cloudflare setup dry-run, JS syntax check, training dry-run, Kaggle self-check, then real-engine API index + template verification + baseline eval on every PR |
| `.github/workflows/cloudflare-provision.yml` | Manual (`workflow_dispatch`) provisioning of the Access app / tunnel / DNS from a protected GitHub environment secret — dry run by default, tunnel token never stored, identifiers only in the job summary; deploys nothing |
| `.github/workflows/build-android.yml` | Reusable APK build workflow; the agent copies it into every game repo it creates |
| `tests/` | 360+ offline unit tests + real-engine integration tests (auto-skipped without the binary); `tests/ui/jsdom_smoke.mjs` drives the chat page's real JavaScript in a DOM against the real server with the scripted model (needs `jsdom`, see its header) |
| `docs/` | [MODEL](docs/MODEL.md) · [ARCHITECTURE](docs/ARCHITECTURE.md) · [USAGE](docs/USAGE.md) · [RESEARCH](docs/RESEARCH.md) · [FREE_TIER](docs/FREE_TIER.md) |

## Quick start (Linux)

```bash
git clone <this repo> && cd Ai-game-godot

# 1. Engine: download Godot 4.7.2-stable editor + export templates, SHA-512 verified (~1.4 GB)
python3 -m godotai install-godot            # → ~/.local/bin/godot ; add it to PATH
python3 -m godotai doctor                   # shows what is present / missing, incl. your model server + model identity

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

# 5. Your model server (default endpoint http://127.0.0.1:8000/v1, served name "godotai") — no vendor key needed
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --served-model-name godotai --enable-auto-tool-choice --tool-call-parser hermes
#    or: ollama pull qwen2.5-coder:7b && export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 GODOTAI_MODEL=qwen2.5-coder:7b

# 6. Let the AI build a game (plan → your approval → act → verify)
python3 -m godotai chat                     # ← browser chat at http://127.0.0.1:8765 — the user-facing way to ask for a game
python3 -m godotai plan "لعبة 2D endless runner للموبايل بالتحكم باللمس" --workspace ./runner   # plan only (CLI)
python3 -m godotai run  "make a 2D endless runner for Android with touch controls" --workspace ./runner

# 7. Measure, collect, (optionally) train your specialised adapter — see docs/MODEL.md and training/README.md
python3 -m godotai eval list && python3 -m godotai eval run --task flappy-clone --workspace ./flappy --yes
python3 -m godotai eval compare             # your model vs. the reference on common engine-scored tasks (NO EVIDENCE until both ran)
python3 -m godotai dataset extract --runs . --out data/sft.jsonl
python3 training/train_qlora.py --data data/sft.jsonl --dry-run
```

### Docker (the recommended execution environment)

```bash
docker build -t godotai -f docker/Dockerfile .
docker run --rm -it -e OPENAI_BASE_URL=http://host.docker.internal:8000/v1 -e GITHUB_TOKEN -v "$PWD/games:/games" godotai \
       run "make a match-3 game for Android" --workspace /games/match3
```

The image contains everything the official 4.7 Android export documentation requires (OpenJDK 17, Build-Tools 35.0.1,
Platform 35, CMake 3.10.2.4988404, NDK r28b) plus the pinned engine, all installed from `godot.toml`. `OPENAI_BASE_URL`
points the container at your model server (the compose file in `deploy/cloudflare/` wires this up for you).

Chat UI from the container on a LAN (the server must bind `0.0.0.0` inside Docker, which godotai only permits with a
token or Cloudflare Access):

```bash
docker run --rm -it -e OPENAI_BASE_URL=http://host.docker.internal:8000/v1 -e GODOTAI_CHAT_TOKEN=<choose-a-secret> \
       -p 127.0.0.1:8765:8765 -v "$PWD/games:/games" godotai chat --host 0.0.0.0 --games-dir /games
# then open http://127.0.0.1:8765/#token=<choose-a-secret>
```

### Public website (optional): Cloudflare Tunnel + Access

```bash
python3 scripts/cloudflare_setup.py --dry-run --hostname games.example.com --team-domain myteam \
    --emails you@example.com --account-id <account id> --zone-id <zone id>            # prints the 4 API calls, no network
export CLOUDFLARE_API_TOKEN=<new least-privilege token>                                # this shell only; never in files
python3 scripts/cloudflare_setup.py --hostname games.example.com --team-domain myteam --emails you@example.com \
    --account-id <account id> --zone-id <zone id> --write-env deploy/cloudflare/.env    # Access app → tunnel → ingress → DNS
docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d               # chat + cloudflared + your vLLM server
```

Only the e-mail addresses you list can pass Cloudflare's login page; the chat re-validates the Access JWT on every request,
publishes no port and applies a run quota (1 run at a time, 40 per visitor per day by default → `429`). The setup script
checks the token read-only first, reuses an existing Access app / tunnel / CNAME on a second run, and can also be run
from the manual GitHub Actions workflow `cloudflare-provision.yml` (environment-protected secret, tunnel token never
stored). Prerequisites that Cloudflare does **not** provide: a domain on Cloudflare and an always-on machine of yours for
the chat + model (or the Workers AI route for a machine without a GPU — labelled *managed*, not your own server). A
Quick Tunnel (`*.trycloudflare.com`) is for testing only. See [`deploy/cloudflare/README.md`](deploy/cloudflare/README.md).
Nothing has been deployed by this repository.

### No money, no GPU: hosted Qwen with a free allowance + a small free host (read `docs/FREE_TIER.md` first)

```bash
python3 -m godotai presets                       # the 4 presets: limits, key variable, data handling, sources (read 2026-09-26)
export OPENROUTER_API_KEY=<from openrouter.ai/settings/keys>     # your account, this shell only — never a file in the repo
GODOTAI_PRESET=openrouter_free python3 -m godotai doctor         # identity: "open-weight model hosted by a provider" (managed)
GODOTAI_PRESET=openrouter_free python3 -m godotai chat           # 20 req/min, 50 req/day without purchased credits (≈ 1 short run)
# alternatives: GODOTAI_PRESET=groq GROQ_API_KEY=…  |  GODOTAI_PRESET=alibaba_model_studio DASHSCOPE_API_KEY=… GODOTAI_BASE_URL=https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
#               GODOTAI_PRESET=workers_ai CF_ACCOUNT_ID=… CF_WORKERS_AI_TOKEN=…   (10,000 Neurons/day)
```

A hosted preset is *not* your private server: the open weights are Qwen's (Apache-2.0), the machine is the provider's, and
your prompts are processed under its terms (each preset states what is and is not known about retention/training). The
Railway service you may have (2 vCPU / 1 GB, trial) can run **the app only** with `deploy/paas/Dockerfile`
(`RAILWAY_DOCKERFILE_PATH=deploy/paas/Dockerfile`, `GODOTAI_CHAT_TOKEN` + preset key as secret variables; measured peak
≈ 640 MB per verification, so concurrency stays 1) — it cannot run a Qwen model, and the post-trial 0.5 GB plan is too
small even for the app's verification step. The longest-lived free Linux box found (2026-09-26) is Oracle Cloud Always
Free (Ampere A1, 2 OCPU / 12 GB, card verification). Steps and limits: [`deploy/paas/README.md`](deploy/paas/README.md).
None of this is "free forever", every path needs *your* account and key, and no free model is claimed to beat Claude
Fable 5.1 Max — `python3 -m godotai eval compare` on the same engine-scored tasks is the only judge.

## Talking to the AI (chat UI)

`python3 -m godotai chat` starts a stdlib-only HTTP server on `127.0.0.1:8765` serving a browser page and a small
JSON + Server-Sent-Events API (`docs/USAGE.md` lists the routes). It is **not a demo**: every message becomes a real
`Agent.run()` in `./games/<project>/` with the same tool registry, policy layer and engine verification as the CLI.
The plan gate is the same too — the plan appears as a card with *approve* / *revise with feedback* buttons, and nothing is
written until you approve (or tick *auto-approve*). Tool calls, `godot_verify` reports and the final summary stream in
live; the conversation is persisted in `<project>/.godotai/chat.jsonl` and the full transcript in `.godotai/runs/`.

The page header shows what you are talking to: whether *your* model server is reachable (`GET …/v1/models`), whether the
configured served name is in its list, the pinned engine, the hosting mode (local / token / Cloudflare Access) and the
model identity card (base model, licence, yours or not, trained from scratch or not). Quick-prompt buttons fill the box
with a complete game request (Flappy Bird, endless runner, match-3, platformer, top-down shooter, 2048, HUD + pause, APK
export) — you still press send.

Safety defaults: loopback only (any other `--host` requires `--token`/`GODOTAI_CHAT_TOKEN` or `--access-team-domain` +
`--access-aud`), `Host`/`Origin` checks, strict CSP with no inline script, file viewer restricted to the project directory
and never to secret files, and the status endpoint reports only whether a key is *set* — never its value. Optional run
quota for shared/public servers: `--max-concurrent-runs N` / `--max-runs-per-day N` (env `GODOTAI_MAX_CONCURRENT_RUNS` /
`GODOTAI_MAX_RUNS_PER_DAY`) — the server answers `429` + `Retry-After` before any model work starts; approving a plan,
cancelling and verifying do not count.

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
- ✅ 290+ unit/integration tests pass (`python3 -m unittest discover -s tests`), with and without the engine binary.
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
- ✅ Chat UI: offline tests drive the real HTTP server with a scripted model through create-project → message → plan
  over SSE → reject-with-feedback → approve → tool events → engine-verified success → persisted history → follow-up
  message with context, plus cancel, provider-error recovery, plan-only, token/Host/Origin guards,
  path-traversal/secret-file refusals and server start/stop lifecycle (Ctrl+C, `--check`, embedded); the page's
  real JavaScript is driven in a DOM (`tests/ui/jsdom_smoke.mjs`, jsdom + the live server + the scripted model, also a
  CI step): status chips, the model-identity card (names the third-party base, no superiority claim), quick prompts
  (fill, never auto-send), project creation through the form, message → plan card → approve → tool events → engine
  PASS → composer re-enabled — 23 checks, **zero JS errors** (last run: this PR).
- ✅ Private-model default (this PR): config precedence (explicit `base_url` → `OPENAI_BASE_URL` → OpenAI only with
  `OPENAI_API_KEY` → local `http://127.0.0.1:8000/v1`), `GET /models` probe with a fake server (unreachable /
  unauthorized / model missing / present), identity disclosure in `doctor` and `/api/status`, refusal of
  `provider = "anthropic"` without `kind = "vendor_api"`, `eval compare` on synthetic results (NO EVIDENCE / common
  tasks / < 3-task caveat) — all unit-tested offline.
- ✅ Cloudflare Access path (this PR): RS256 JWT verification against a JWKS (rotation, unknown `kid`, iss/aud/exp/nbf,
  cookie fallback) with a stdlib-only test RSA fixture; the full HTTP server behind Access (page, assets and API →
  401 without a token, `/healthz` open); `cloudflare_setup.py` dry-run and a fake Cloudflare transport (call order,
  bodies, `.env` mode 0600, token never printed, failure paths); compose/env hygiene (no `ports:`, `TUNNEL_TOKEN`
  from the environment only, no credential or account id in the tree).
- ✅ Deployment hardening (PR #5): run quota (`godotai/chat/quota.py` — concurrency + rolling 24 h per identity,
  fake-clock rollover, 429 + `Retry-After`, reservation rollback, bounded identity table, CLI/env wiring — 13 tests) and
  the HTTP server behind it; `cloudflare_setup.py` token pre-check, idempotent lookups/reuse/CNAME update,
  `--discard-tunnel-token` / `--facts-json` CI mode, UUID validation before any call (33 offline tests); the manual
  `cloudflare-provision.yml` workflow is structurally tested (manual-only, environment-gated, no artifact, no token echo,
  no input interpolation) and its shell/Python steps were executed locally against synthetic facts; the `CF_WORKERS_AI_TOKEN`
  split so the setup token never reaches a container. All 320+ tests pass; secret scan clean.

Not yet verified (implemented, but no evidence of success — treat as untested):

- ❌ **Live model calls** — to your own vLLM/Ollama server *or* to `claude-fable-5-1`: request shapes, tool-call parsing
  and the `/models` probe are unit-tested against fake servers and the documentation only. Whether
  `Qwen2.5-Coder-7B-Instruct` can drive this tool loop well enough is **unmeasured**; a larger open-weight model or
  your fine-tuned adapter may be needed (`docs/MODEL.md` §4).
- ❌ **No public website is deployed.** `scripts/cloudflare_setup.py` has never been run live; no Cloudflare API request
  was made (the lookup query parameters `domain` / `name` / `is_deleted` / `type` follow the API reference but have not
  been exercised against the real API); the `cloudflare-provision.yml` workflow has never been dispatched; the compose
  stack was never started (no Docker/GPU in the development sandbox); `cloudflared` + Access end-to-end (login page →
  JWT → chat) is untested. No credential from the user was used anywhere — the token pasted into the chat must be rolled.
- ❌ `eval compare` has no real data yet: no eval task has been run against any model, so there is **no evidence** about
  the private model's quality relative to Claude Fable 5.1 or anything else.
- ❌ QLoRA training itself (`training/train_qlora.py` without `--dry-run`) — written against the TRL v1.14 /
  transformers docs, never executed (no GPU here); Kaggle kernel never pushed.
- ❌ Cloudflare AI Gateway / Workers AI routing: URL/header construction is unit-tested; no live request.
- ❌ GitHub *tools* against a real token (`github_create_repo`/`push`/`build_apk`); the `build-android.yml` workflow
  has not been run yet; Docker image build (no Docker in the sandbox).
- ❌ Release-signed APK / AAB, Gradle builds (needs NDK + CMake + Android build template), Play Store compliance.
- ❌ Behaviour on a physical Android device (touch feel, performance, audio).

See [docs/MODEL.md](docs/MODEL.md) for what the model is, [docs/RESEARCH.md](docs/RESEARCH.md) for sources and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design decisions.

## License

MIT.
