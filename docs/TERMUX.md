# تشغيل godotai من Termux على هاتف Android — بواجهة API مجانية، وبناء APK على GitHub (سبتمبر 2026)

> **طلب صاحب المشروع، بنصّه:**
>
> تمام خلاص الان هنشغله و لكن على ترميكس عندي فيه الهاتف بتاعي و لكن طبعا هنستخدم api مجاني عشان مش معنا GPU و كمان اديني الطريقة بالتفصيل كيف اشغله من ترميكس عندي على الهاتف و كمان هل لو قلتله يعمل اي لعبة مهما كانت اللي هو اي ذكاء اصطناعي هل لما يخلص هل اعرف اخليها apk لو انا ربطه بي مستودع على جيت هب ولا اي و كمان كيف اخليه apk و كيف اربطه و اخليه هو كمان اللي يعمل ال apk نفسه فاهمني

هذا الدليل يجيب على الأسئلة الأربعة بالترتيب، بأوامر تُنسخ كما هي، وبحدود حقيقية (ما يعمل، ما لا يعمل، ما لم يُجرَّب).
كل ما فيه يخصّ **Godot Engine 4.7.2-stable** فقط — هذا الذكاء الاصطناعي لا يصنع شيئًا خارج هذا المحرك.
المصادر التي قُرئت في 2026-09-26 مذكورة في §8، وسكربت مساعد يطبع/ينفّذ نفس الأوامر: `scripts/termux_setup.sh`.

---

## 0. الخلاصة — الإجابات المباشرة

| سؤالك | الجواب القصير |
|---|---|
| **هل يشتغل من Termux على الهاتف؟** | **نعم للجزء الذكي**: المحادثة، التخطيط، كتابة ملفات اللعبة، وأدوات GitHub — كلها Python فقط بلا مكتبات خارجية وتعمل في Termux مباشرةً (§2). **لا لمحرك Godot نفسه داخل Termux مباشرةً**: الملف الرسمي `Godot_v4.7.2-stable_linux.arm64` مبني على glibc، وTermux بيئة Android/Bionic وليست توزيعة لينكس، فلا يُنفَّذ هناك. الحلّان: **(أ)** Debian داخل `proot-distro` على نفس الهاتف يشغّل المحرك للتحقق (§2.5)، **(ب)** GitHub Actions يشغّل المحرك ويبني الـ APK على خادم GitHub (§3). المثبّت وصفحة المحادثة و`doctor` يكشفون Termux ويقولون لك هذا بأنفسهم. |
| **API مجاني بدون GPU؟** | **نعم**: `export GODOTAI_PRESET=openrouter_free` + مفتاح من حسابك على OpenRouter في متغير بيئة (§2.3). **الحدّ**: 20 طلبًا/دقيقة و**50 طلبًا/يوم** بلا رصيد (1000/يوم بعد شراء رصيد 10$ مرة واحدة). كل دورة في حلقة الوكيل = طلب واحد، ولعبة كاملة = عشرات الطلبات → **قد تنتهي الحصة في منتصف التشغيل**. بدائل بحصص أخرى: `groq`، `alibaba_model_studio`، `workers_ai` (`python3 -m godotai presets`). |
| **لو قلت له يعمل أي لعبة مهما كانت؟** | **لا، ولا يزعم ذلك أي ذكاء اصطناعي بصدق** (§5). المضمون: ألعاب 2D صغيرة/متوسطة بـ GDScript على 4.7.2، بأصول مؤقتة (أشكال ملوّنة)، تمرّ بخطة → موافقتك → تنفيذ → **تحقق بالمحرك الحقيقي** (`SMOKE_TEST_OK`). غير المضمون: 3D كبير، رسوم/أصوات حقيقية، لعب جماعي عبر الإنترنت، وأي شيء خارج Godot 4.7.2. النموذج المجاني أضعف من النماذج المدفوعة وقد يفشل ويعيد المحاولة (ويستهلك الحصة). الحكم الوحيد: هل مرّ التحقق أم لا. |
| **لما يخلص، أعرف أخليها APK؟ وهو اللي يعملها؟** | **نعم، عبر GitHub، وهو الذي ينفّذ كل الخطوات** (§3): بأدواته `github_create_repo` → `github_push_project` → `github_build_apk` → `github_build_status` ينشئ المستودع، يرفع اللعبة مع ملف الـ workflow، يشغّل البناء على خادم Ubuntu عند GitHub (Godot 4.7.2 + JDK 17 + Android SDK)، **يتحقق من اللعبة بالمحرك أولًا**، يصدّر الـ APK، ويعطيك رابطًا مباشرًا (`publish_release=true` → GitHub Release فيه `game-debug.apk`). المطلوب منك: حساب GitHub وتوكن في `GITHUB_TOKEN` (§3.1). ما لم يُجرَّب بعد: §7. |

---

## 1. ما تحتاجه قبل البدء

- هاتف Android **7 أو أحدث** (Termux لا يدعم الأقدم). على **Android 12+** النظام قد يقتل عمليات Termux الطويلة (§4.1) — يعمل، لكن بحذر.
- **Termux من F-Droid أو من GitHub Releases** (`https://f-droid.org/en/packages/com.termux/` أو `https://github.com/termux/termux-app/releases`، الإصدار الحالي 0.118.3). **ليس** نسخة Google Play: فرع تجريبي ناقص الوظائف بحسب صفحة Termux نفسها. لا تخلط مصدرين (توقيع مختلف → فشل التثبيت).
- مساحة: Termux + Python + git ≈ 0.5 GB. مع Debian والمحرك (§2.5) أضِف ≈ 1 GB. الـ APK **لا** يُبنى على الهاتف فلا حاجة لـ Android SDK ولا لقوالب التصدير (1.4 GB) هنا.
- إنترنت، حساب على **OpenRouter** (أو أحد البدائل)، حساب على **GitHub**. لا حاجة لحاسوب.
- معالج الهاتف: كل هواتف Android الحديثة `aarch64` → ملف المحرك `linux.arm64` (مثبَّت في قائمة SHA-512 الموقَّعة بالمستودع `engine/checksums/4.7.2-stable/`). السكربت و`doctor` يكشفان المعالج ويختاران الملف الصحيح؛ ملف `x86_64` على هاتف ARM يفشل بـ `Exec format error`.

---

## 2. التشغيل خطوة بخطوة (على الهاتف، داخل Termux)

### 2.1 الحزم

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git proot-distro
termux-setup-storage        # يطلب إذن التخزين مرة واحدة → ~/storage/downloads = مجلد Download في الهاتف
```

(`proot-distro` لازم فقط إن أردت المحرك على الهاتف §2.5؛ تثبيته الآن لا يضرّ.)

### 2.2 المستودع وفحص البيئة

```bash
git clone https://github.com/joknok72-ctrl/Ai-game-godot.git
cd Ai-game-godot
python3 -m godotai doctor
```

`doctor` في Termux يطبع صفًا `host` يقول صراحةً: `Termux (Android, Bionic), aarch64 → linux.arm64 — the glibc editor cannot run
directly in Termux`، وصفًا `editor binary ✗` يشرح الحلّين (أ) و(ب). هذا **متوقَّع** وليس خطأً في التثبيت. لو جرّبت
`python3 -m godotai install-godot` هنا فسيرفض **قبل** تنزيل أي شيء بنفس الرسالة (`GODOTAI_INSTALL_ANYWAY=1` يتجاوز الرفض
لو كنت تنزّل ملفًا لجهاز آخر عن قصد).

سكربت يطبع الخطة كلها بلا تنفيذ، أو ينفّذ خطوات الحزم نيابةً عنك:

```bash
sh scripts/termux_setup.sh --print     # يعرض فقط
sh scripts/termux_setup.sh             # pkg install … ثم يطبع الخطوات التالية
```

### 2.3 مفتاح النموذج المجاني — كيف تحصل عليه وأين تضعه (وأين لا تضعه)

1. أنشئ حسابًا على `https://openrouter.ai`، ثم من `https://openrouter.ai/settings/keys` اضغط **Create key** وانسخ المفتاح مرة واحدة.
2. في طرفية Termux (وليس في أي ملف):

```bash
export GODOTAI_PRESET=openrouter_free
export OPENROUTER_API_KEY='<الصق مفتاحك هنا>'
python3 -m godotai presets     # يطبع لكل مسار: الحدود، المفتاح المطلوب، ماذا يحدث لبياناتك، المصادر
```

**قواعد المفتاح (غير قابلة للتفاوض):** المفتاح متغير بيئة في طرفيتك أو *GitHub Secret* — **أبدًا** في `godot.toml`، ولا في أي ملف داخل
المستودع، ولا في رسالة للذكاء الاصطناعي، ولا في Issue/PR/لقطة شاشة. لو لُصق مفتاح في أي مكان عام فاعمل له **Roll/Delete** فورًا من
صفحة المفاتيح. المستودع يفحص نفسه (`python3 scripts/secret_scan.py .`) ويرفض ملفات المفاتيح في أدوات الوكيل.
إن أردت ألّا تكتب `export` في كل جلسة فالمكان الوحيد المقبول هو `~/.bashrc` **داخل Termux** (مجلد خاص بالتطبيق لا تقرأه تطبيقات أخرى
بلا صلاحية root) — وليس أي ملف داخل `Ai-game-godot/`.

**ماذا تعني «مجاني» فعلًا (قُرئ 2026-09-26، يتغيّر بلا إشعار):**

| المسار (`GODOTAI_PRESET`) | المفتاح | الحصة المعلنة | ملاحظات |
|---|---|---|---|
| `openrouter_free` — `qwen/qwen3.8-27b:free` | `OPENROUTER_API_KEY` | 0$ للرموز؛ **20 طلب/دقيقة**، **50 طلب/يوم** بلا رصيد، **1000/يوم** بعد شراء رصيد 10$ مرة واحدة | الطراز المجاني قد يُزال أو يُبطَّأ أي يوم |
| `groq` — `qwen/qwen3.8-27b` (Preview) | `GROQ_API_KEY` | الجدول المنشور: 30 طلب/دقيقة، 1000/يوم، 8 آلاف رمز/دقيقة — حدود **خطة Developer الأساسية**؛ حدودك الحقيقية في صفحة Limits بحسابك | 8K رمز/دقيقة قد لا تكفي لمطالبات هذا الوكيل الطويلة |
| `alibaba_model_studio` — `qwen3-coder-30b-a3b-instruct` | `DASHSCOPE_API_KEY` + `GODOTAI_BASE_URL` | حصة أول تفعيل (عادةً مليون رمز لكل طراز) لمدة **90 يومًا** | **فوترة تلقائية** بعد النفاد إلا إن فعّلت Free Quota Only |
| `workers_ai` — `@cf/qwen/qwen3-30b-a3b-fp8` | `CF_ACCOUNT_ID` + `CF_WORKERS_AI_TOKEN` | **10,000 نيورون/يوم** على خطة Workers المجانية (≈ 2 مليون رمز إدخال أو 330 ألف إخراج) | فوق الحصة تتوقف الطلبات، لا خصم |

- **كل دورة في حلقة الوكيل (تفكير → أداة → نتيجة) = طلب واحد**. الحدّ الأقصى في `godot.toml` هو 60 دورة و4 جولات إصلاح/تحقق؛
  لعبة من القالب مع تعديل صغير قد تحتاج 10–30 طلبًا، ولعبة من الصفر أكثر. مع 50 طلبًا/يوم توقّع **تشغيلًا واحدًا قصيرًا في اليوم**
  وربما رسالة `429` (تجاوز الحصة) في منتصفه؛ الملفات المكتوبة حتى تلك اللحظة تبقى في `./games/<المشروع>/` وتُكمل غدًا برسالة
  «أكمل من حيث توقفت».
- **توفير الحصة:** ابدأ من القالب الجاهز بلا أي طلب (`python3 -m godotai new --dest games/x --name "X" --package com.you.x`) ثم اطلب
  تعديلات صغيرة؛ اطلب شيئًا واحدًا في الرسالة؛ ولا تعِد التشغيل بلا محرك (§2.4) لأن كل جولة تحقق فاشلة طلبٌ ضائع.
- **الخصوصية:** هذه «Qwen مفتوح الوزن **عند مزوّد**» لا «خادمك الخاص»: كل ما تكتبه **وكل كود اللعبة الذي يُنتَج** يُرسَل إلى المزوّد
  (وعند OpenRouter إلى مزوّد أعلى قد يحتفظ أو يدرّب — يمكنك استثناء المزوّدين الذين يدرّبون من إعدادات حسابك). لا تكتب في المحادثة
  أسرارًا أو بيانات شخصية. التفاصيل لكل مزوّد في `docs/FREE_TIER.md` و`python3 -m godotai presets`.

### 2.4 تشغيل المحادثة على الهاتف

```bash
termux-wake-lock             # يطلب من Android عدم تعليق Termux (لا يمنع القتل تمامًا على Android 12+، §4.1)
python3 -m godotai chat      # يطبع العنوان — افتحه في متصفح الهاتف (Chrome/Firefox): http://127.0.0.1:8765/
```

- الأفضل **شاشة منقسمة**: Termux فوق، المتصفح تحت. أو انتقل بين التطبيقين — Termux يبقى يعمل عبر إشعاره الدائم. من إعدادات
  الهاتف: **البطارية → Termux → بلا قيود** (وإلا يُجمَّد في الخلفية).
- في الصفحة: أنشئ مشروعًا (= مجلد `games/<اسم>`)، اختر فكرة جاهزة أو اكتب لعبتك بالعربي، **ابعت** → تظهر **خطة** → وافق (أو اطلب
  تعديلها) → يبني الملفات → **يشغّل المحرك للتحقق**. بطاقة أعلى الصفحة تقول ما النموذج (Qwen عند OpenRouter: «managed»، ليس خاصًا).
- **بلا محرك على الهاتف (Termux فقط) — ماذا يحدث بالضبط:** الخطة والملفات تُكتب، لكن خطوة `godot_verify` تفشل بـ «Termux… the glibc editor
  cannot run here»، فيُخبَر النموذج بالمشكلة كل جولة وتنتهي التشغيلة بحالة **failed** بعد جولات التحقق الأربع — حتى لو كانت اللعبة
  صحيحة. الملفات موجودة، ويمكنك بعدها طلب **الرفع إلى GitHub وبناء الـ APK** (§3): الـ workflow **يتحقق بالمحرك على خادم GitHub**
  قبل التصدير ويعطي تقريرًا بالملف والسطر. لكن الأصحّ والأوفر للحصة: المحرك داخل Debian على الهاتف (§2.5) فتصير التشغيلة `success`
  محليًا مثل أي حاسوب.
- من سطر الأوامر بدل المتصفح: `python3 -m godotai run "اعمل لعبة تجنّب مكعبات بلمسة واحدة" --workspace games/dodge` — نفس الوكيل،
  والموافقة سؤال في الطرفية؛ وبلا محرك يطبع تحذيرًا **قبل** أول طلب للنموذج حتى لا تُحرَق الحصة.
- الصفحة على `127.0.0.1` لا يراها إلا هاتفك. لا تفتحها على الشبكة (`--host 0.0.0.0`) بلا `--token`؛ الخادم نفسه يرفض ذلك.

### 2.5 المحرك على الهاتف نفسه: Debian داخل proot-distro (موصى به للتحقق الحقيقي)

`proot-distro` (الإصدار 5.x في Termux، 2026) يثبّت توزيعة Debian حقيقية داخل مجلد Termux بلا root؛ داخلها glibc، فالملف الرسمي
`linux.arm64` **يُنفَّذ** — بلا واجهة رسومية (`--headless`)، وهو كل ما يحتاجه التحقق (`--import`، `--check-only`، اختبار الدخان).

```bash
# في Termux
sh scripts/termux_setup.sh --proot            # = proot-distro install debian:bookworm  (نحو 100 MB)
proot-distro login debian --shared-home       # نفس مجلد المنزل داخل وخارج Debian → نفس ~/Ai-game-godot

# داخل Debian (الطرفية تتغيّر إلى root@localhost)
cd ~/Ai-game-godot
sh scripts/termux_setup.sh --guest --install-godot
#   = apt-get install مكتبات التشغيل (نفس قائمة deploy/paas/Dockerfile)
#   + export GODOTAI_ENGINE_PLATFORM=linux.arm64 + python3 -m godotai install-godot --editor-only  (المحرر فقط، SHA-512 مُتحقَّق)
export PATH="$HOME/.local/bin:$PATH"
export GODOTAI_ENGINE_PLATFORM=linux.arm64     # ضعه في ~/.bashrc داخل Debian؛ بدونه يُتوقَّع ملف x86_64 ويفشل التحقق
export GODOTAI_PRESET=openrouter_free
export OPENROUTER_API_KEY='<مفتاحك>'           # من جديد: Debian لا يرث متغيرات Termux
python3 -m godotai doctor                      # host ✓ PRoot guest, aarch64 → linux.arm64 · editor binary ✓ 4.7.2.stable
python3 -m godotai chat                        # http://127.0.0.1:8765/ — الآن التحقق بالمحرك يعمل على الهاتف
```

ما يجب أن تعرفه قبل الاعتماد عليه:

- **لم يُجرَّب على هاتف فعلي في هذا المستودع** (§7). ما جُرّب: نفس الملف الرسمي بنفس الأوامر على لينكس x86_64، وكشف بيئة PRoot
  بمتغيراتها. مشروع Godot نفسه يقول إن Termux **ليست منصة مستهدفة رسميًا** (godotengine/godot#82677) — التقارير هناك عن الواجهة
  الرسومية/Vulkan؛ الوضع `--headless` لا يحتاجهما.
- **أبطأ** من الحاسوب (proot يترجم استدعاءات النظام)، والاستيراد الأول قد يأخذ دقائق. الذاكرة: التحقق على القالب بلغ ≈ 640 MB على
  x86_64؛ هاتف بأقل من 4 GB RAM قد يقتل العملية.
- `proot-distro` **لا يعمل داخل proot آخر**، والمتغيرات لا تنتقل تلقائيًا من Termux إلى Debian (لذلك تعيد `export`).
- المحرر فقط (`--editor-only`): قوالب التصدير وAndroid SDK **غير مطلوبة** على الهاتف لأن الـ APK يُبنى على GitHub.
- بديل غير مُختبَر هنا: **محرّر Godot الرسمي لأندرويد** (`Godot_v4.7.2-stable_android_editor.apk` في نفس صفحة الإصدار) — تطبيق
  يعمل باللمس بمرحلة «وصول مبكّر»، لفتح المشروع وتجربته على الهاتف يدويًا؛ هذا الوكيل لا يتحدث معه.

---

## 3. الـ APK: كيف يُبنى، كيف تربط GitHub، وكيف يفعل الذكاء الاصطناعي ذلك بنفسه

الفكرة: **الهاتف يقود، وGitHub يبني**. الـ APK يحتاج JDK 17 + Android SDK (build-tools 35.0.1، platform 35) + Godot 4.7.2 مع قوالب
التصدير — كلها تُثبَّت في دقائق على خادم Ubuntu مجاني عند GitHub بملف `.github/workflows/build-android.yml` الذي **يُنسخ آليًا** إلى
مستودع كل لعبة يرفعها الوكيل.

### 3.1 الربط: توكن GitHub في متغير بيئة (مرة واحدة)

1. GitHub → صورتك → **Settings → Developer settings → Personal access tokens**.
   - الأبسط: **Tokens (classic)** → Generate new token → الصلاحيات `repo` + `workflow`.
   - أو **Fine-grained**: Repository access = All repositories (الوكيل ينشئ مستودعات جديدة) والصلاحيات **Contents / Actions /
     Administration = Read and write**.
2. في Termux (أو داخل Debian إن كنت تشغّل المحادثة من هناك) — قبل `python3 -m godotai chat`:

```bash
export GITHUB_TOKEN='<التوكن>'      # متغير بيئة فقط؛ الوكيل لا يكتبه في ملف ولا في رابط ولا يعيده للنموذج
python3 -m godotai chat             # الصفحة تعرض github_token_set = true
```

بلا التوكن تظهر أدوات GitHub للنموذج لكنها ترجع «GITHUB_TOKEN is not set». مع `--no-github` تُخفى تمامًا.

### 3.2 ما يفعله الذكاء الاصطناعي عندما تطلب APK

اكتب في نفس مشروع اللعبة، مثلًا:

> ارفع اللعبة إلى مستودع جديد باسم `tap-dodge` على GitHub، وابنِ APK تجريبي (debug)، وانشره كـ Release لأحمّله من متصفح الهاتف.

الوكيل يخطّط ثم (بعد موافقتك) ينفّذ بأدواته:

| الأداة | ما تفعله | ما تراه أنت |
|---|---|---|
| `github_create_repo` | ينشئ المستودع (خاص افتراضيًا) | رابط المستودع |
| `github_push_project` | يضيف `.gitignore` آمنًا (لا keystore، لا apk، لا .env) + `build-android.yml` ثم يرفع الملفات | الـ commit |
| `github_build_apk` بـ `publish_release: true` | يطلق الـ workflow (`workflow_dispatch`) على خادم GitHub | «workflow dispatched…» |
| `github_build_status` | آخر التشغيلات (حالة، نتيجة، رابط) + الـ artifacts + **قائمة `apk_releases` برابط تنزيل مباشر لكل .apk** | الرابط الذي تفتحه على الهاتف |

النموذج **ممنوع** (في تعليماته) من وصف الـ APK بأنه «جاهز» قبل أن يُظهر `github_build_status` تشغيلًا مكتملًا بنجاح.

### 3.3 ما يحدث على خادم GitHub (build-android.yml)

1. JDK 17 (Temurin) + Android SDK: `platform-tools`, `build-tools;35.0.1`, `platforms;android-35`.
2. تنزيل Godot 4.7.2-stable + قوالب التصدير من `godotengine/godot-builds` مع **تحقق SHA-512**.
3. مفتاح توقيع debug: من السرّ `ANDROID_DEBUG_KEYSTORE_BASE64` إن وُجد (§3.5)، وإلا مفتاح جديد لكل تشغيلة.
4. **التحقق بالمحرك (`verify`، مفعّل افتراضيًا):** `--import` لكل الموارد، `--check-only` لكل ملف `.gd`، ثم `tests/smoke_test.gd`
   ويجب أن يطبع `SMOKE_TEST_OK` (أو تشغيل 120 إطارًا بلا أخطاء إن لم يوجد اختبار). أي فشل **يوقف البناء** مع الملف والسطر في السجل
   وجدول في ملخص التشغيلة — هذه هي «كلمة المحرك» عندما لا يكون عندك محرك على الهاتف. (`verify=false` يتخطاها.)
5. `godot --headless --export-debug "Android" build/android/game-debug.apk` (أو `--export-release` مع مفاتيحك §3.6).
6. **Artifact** `android-apk-debug` (zip، يبقى 30 يومًا، تنزيله يحتاج تسجيل دخول على GitHub في المتصفح).
7. مع `publish_release=true`: وظيفة منفصلة (الوحيدة التي تملك صلاحية `contents: write`) تنشئ **Release** باسم `apk-debug-<رقم التشغيلة>`
   وترفع `game-debug.apk` ملفًا مباشرًا، وتعلّمه **latest**. الروابط:
   - الدقيق (يطبعه `github_build_status` كـ `download_url`): `https://github.com/<owner>/<repo>/releases/download/apk-debug-<N>/game-debug.apk`
   - الأحدث دائمًا: `https://github.com/<owner>/<repo>/releases/latest/download/game-debug.apk`

**الكلفة والوقت:** الـ workflow يشغّله GitHub Actions على runner قياسي: **مجاني بلا حدّ دقائق للمستودعات العامة**؛ للمستودعات
**الخاصة** على خطة Free: **2,000 دقيقة/شهر** و**500 MB** تخزين للـ artifacts (ملفات الـ Releases لها حدودها المنفصلة: أقل من 2 GiB للملف). التشغيلة تنزّل ≈ 1.5 GB
(SDK + المحرك + القوالب) وتستغرق عادةً عدة دقائق — لم نقِس مدة تشغيلة بهذه النسخة من الملف بالضبط (§7).

### 3.4 التنزيل والتثبيت على الهاتف

1. افتح رابط الـ Release في متصفح الهاتف (مستودع خاص → سجّل الدخول في المتصفح نفسه، لا داخل تطبيق GitHub) ونزّل `game-debug.apk`.
2. عند التثبيت اسمح للمتصفح/مدير الملفات بـ **تثبيت تطبيقات من مصادر غير معروفة** (Android يطلب ذلك مرة).
3. لو ظهر **«App not installed / لا يمكن التثبيت»** والتطبيق مثبّت من تشغيلة سابقة: التوقيع مختلف (مفتاح debug جديد) → احذف القديم ثم ثبّت،
   أو اضبط مفتاحًا ثابتًا (§3.5).

### 3.5 مفتاح debug ثابت (اختياري) حتى يقبل الهاتف التحديث فوق النسخة القديمة

أنشئ keystore مرة واحدة (داخل Debian: `apt-get install -y openjdk-17-jre-headless`؛ أو في Termux: `pkg install openjdk-17`):

```bash
keytool -genkeypair -v -keystore debug.keystore -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android -dname "CN=Android Debug,O=Android,C=US"
base64 -w0 debug.keystore      # انسخ الناتج
```

ثم في مستودع اللعبة على GitHub: **Settings → Secrets and variables → Actions → New repository secret** باسم
`ANDROID_DEBUG_KEYSTORE_BASE64` والقيمة هي الناتج. احذف `debug.keystore` من الهاتف بعدها أو احفظه خارج أي مستودع (الـ `.gitignore`
يرفض `*.keystore` لكن لا تختبر ذلك). السرّ يصل إلى الـ workflow كمتغير بيئة فقط ولا يُطبع.

### 3.6 نسخة release (للنشر على المتاجر)

تحتاج مفتاح توقيع **خاصًا بك** كأسرار في المستودع: `ANDROID_KEYSTORE_BASE64`، `ANDROID_KEYSTORE_USER`، `ANDROID_KEYSTORE_PASSWORD`، ثم
اطلب من الوكيل `build_type: release` (أو اختره يدويًا). ملف release بلا هذه الأسرار يفشل مبكرًا برسالة واضحة. النشر على Google Play
(حساب مطوّر، AAB، سياسات) خارج نطاق هذا المستودع.

### 3.7 بلا الذكاء الاصطناعي: تشغيل البناء يدويًا من الهاتف

- من المتصفح: صفحة المستودع → **Actions → Build Android APK → Run workflow** → اختر `publish_release` ✔ → Run.
- من Termux بـ GitHub CLI (`pkg install gh` ثم `gh auth login`):

```bash
gh workflow run build-android.yml --repo <owner>/<repo> -f publish_release=true
gh run list --repo <owner>/<repo> --workflow build-android.yml
gh run download <run-id> --repo <owner>/<repo> -n android-apk-debug -D ~/storage/downloads/   # الـ artifact بديلًا عن الـ Release
```

- لو رجع GitHub **422 «Unexpected inputs provided»** فمستودع اللعبة يحمل نسخة قديمة من الـ workflow بلا المدخل الجديد: اطلب من الوكيل
  إعادة `github_push_project` (يحدّث الملف) أو انسخ الملف يدويًا. الوكيل نفسه لا يرسل المدخلات الاختيارية إلا عند تغييرها عن الافتراضي،
  فالنسخ القديمة تظل تعمل معه في الحالة الافتراضية.

---

## 4. مشاكل متوقَّعة وحلولها

### 4.1 `[Process completed (signal 9)]` أو توقّف صامت في الخلفية
Android 12+ يقتل «العمليات الوهمية» (phantom processes — حدّ 32 عملية لكل التطبيقات معًا) والعمليات ذات الاستهلاك العالي، بحسب تحذير Termux الرسمي.
ما يقلّل المشكلة: `termux-wake-lock`، استثناء Termux من تحسين البطارية، إبقاء الشاشة على Termux أثناء التحقق، وعدم تشغيل أكثر من
تشغيلة معًا (`GODOTAI_MAX_CONCURRENT_RUNS=1`). ما يزيله (على بعض الأجهزة من Android 12L/13): **خيارات المطوّر → تعطيل قيود العمليات
الفرعية** ("Disable child process restrictions")، أو أمر `device_config` عبر ADB من حاسوب (رابط Termux في §8). بلا حاسوب وبلا الخيار:
اترك المحرك لـ GitHub Actions (§3).

### 4.2 `Exec format error` أو `cannot execute Godot binary`
ملف لمعالج آخر (x86_64 على هاتف ARM) أو تشغيل الملف داخل Termux مباشرةً. الحل: داخل Debian مع `GODOTAI_ENGINE_PLATFORM=linux.arm64`
(§2.5). رسالة الخطأ نفسها تقول لك السبب المرجّح.

### 4.3 `429` / `rate limit` من OpenRouter
حصة 20/دقيقة أو 50/يوم انتهت. انتظر دقيقة، أو غدًا، أو بدّل المسار (`groq`، `workers_ai`)، أو اشترِ رصيد 10$ مرة واحدة لرفع الحدّ اليومي
إلى 1000. الملفات المكتوبة لا تُفقد.

### 4.4 «App not installed» على الهاتف
§3.4 و§3.5. وتأكّد أنك نزّلت من الـ Release (ملف .apk) لا من الـ artifact (ملف .zip يجب فكّه أولًا).

### 4.5 الصفحة لا تفتح على `127.0.0.1:8765`
تأكّد أن `python3 -m godotai chat` ما زال يعمل في Termux (لم يُقتل، §4.1) وأنك تفتح العنوان في متصفح الهاتف نفسه. منفذ آخر:
`--port 8080` أو `GODOTAI_PORT`.

---

## 5. بصراحة: ما الذي يستطيعه، وما لا يستطيعه، وما يكلّفك

- **ليس «أي لعبة مهما كانت».** النطاق: Godot 4.7.2-stable، GDScript، ألعاب 2D للهاتف من نوع القالب (تجنّب/جمع/قفز/ألغاز بسيطة/HUD/إيقاف)
  وتعديلاتها. أصول فنية وصوتية حقيقية، 3D معقّد، شبكات، نقود داخل اللعبة، Godot 3 أو محرّكات أخرى: خارج النطاق أو غير مضمون، ويقول لك ذلك.
- **الحكم للمحرك لا للنموذج.** التشغيلة `success` فقط عندما يمرّ `--import` و`--check-only` واختبار الدخان على المحرك الحقيقي؛
  بلا محرك (Termux فقط) لا توجد `success` محليًا مهما بدت الملفات جميلة — و«ما لم يُجرَّب» في §7 يشمل الهاتف نفسه.
- **النموذج المجاني** (Qwen 27B مجاني عند OpenRouter) ليس نموذج هذا المستودع «الخاص» ولا هو أقوى من النماذج المدفوعة؛ لا يُقال هنا إنه
  أفضل من أي شيء إلا بنتيجة `python3 -m godotai eval compare` على نفس المهام. يفشل أحيانًا في استدعاء الأدوات بشكل صحيح، وكل محاولة
  فاشلة تستهلك من الحصة.
- **الحصص المجانية صغيرة ومتغيّرة**: 50 طلبًا/يوم ≈ تشغيلة واحدة قصيرة؛ 2,000 دقيقة Actions/شهر للمستودعات الخاصة؛ 500 MB للـ artifacts.
  لا شيء من هذا «مجاني للأبد».
- **خصوصيتك**: كل رسالة وكل كود يُولَّد يذهب إلى مزوّد النموذج. مفاتيحك وتوكن GitHub لا يذهبان إلى النموذج (الأدوات تستخدمها من البيئة
  فقط) — لكن لا تلصقهما أنت في المحادثة.
- **الـ APK التجريبي** موقَّع بمفتاح debug: للتجربة على هاتفك فقط، وليس للمتاجر.

---

## 6. ملخّص تسلسل العمل اليومي (بعد الإعداد الأول)

```bash
# Termux
cd ~/Ai-game-godot && termux-wake-lock
export GODOTAI_PRESET=openrouter_free OPENROUTER_API_KEY='<مفتاحك>' GITHUB_TOKEN='<توكنك>'
proot-distro login debian --shared-home          # (لو أردت التحقق المحلي؛ وإلا شغّل chat هنا مباشرةً)
# Debian
cd ~/Ai-game-godot && export PATH="$HOME/.local/bin:$PATH" GODOTAI_ENGINE_PLATFORM=linux.arm64 \
   GODOTAI_PRESET=openrouter_free OPENROUTER_API_KEY='<مفتاحك>' GITHUB_TOKEN='<توكنك>'
python3 -m godotai chat                          # المتصفح: http://127.0.0.1:8765/
#   1) «اعمل لعبة …» → خطة → موافقة → بناء → تحقق بالمحرك
#   2) «ارفعها إلى GitHub وابنِ APK وانشره كـ Release» → github_build_status يعطيك رابط game-debug.apk
#   3) نزّل الرابط في المتصفح → ثبّت
```

---

## 7. ما جُرّب وما لم يُجرَّب (2026-09-26) — بلا تجميل

**جُرّب فعلًا (في صندوق لينكس x86_64، بالمحرك الرسمي `4.7.2.stable.official.ed1daf0bf`):**
- `tests/test_termux.py` (40 اختبارًا) تمرّ: كشف Termux/PRoot/المعالج ببيئة مُحاكاة؛ المثبّت يرفض على Termux **قبل** أي تنزيل ويرفض
  ملف معالج مختلف إلا بـ `GODOTAI_INSTALL_ANYWAY=1`; ملف غير قابل للتنفيذ يعطي رسالة مفهومة لا traceback؛ `doctor` و`/api/status` يطبعان
  صف `host` والتلميحات بالعربي؛ السكربت `scripts/termux_setup.sh` صحيح بـ `sh -n`/`bash -n` ويطبع الخطة ولا يتعامل مع أي سرّ؛
  قائمة مكتبات Debian مطابقة لصورة `deploy/paas/Dockerfile`.
- **كتلة التحقق في `build-android.yml` نُفِّذت بالفعل** (نفس نص الـ shell، بـ bash كما على runner) على قالب `mobile-2d`: نجاح مع
  `SMOKE_TEST_OK`؛ ومع سكربت مكسور → فشل مع `::error file=…broken.gd`؛ ومع اختبار دخان فاشل → فشل. CI هذا المستودع يعيد نفس الاختبارات
  على runner حقيقي عند GitHub في وظيفة `godot-verify-template`.
- الحقائق المنقولة هنا (Termux/Bionic، proot-distro 5.x وأوامره، حدود OpenRouter، حدود GitHub Actions، ترتيب «latest» في Releases)
  قُرئت من صفحاتها الأصلية في التاريخ المذكور (§8).

**لم يُجرَّب (لا تعتبره مضمونًا حتى تجرّبه أنت):**
- تشغيل أي شيء من هذا على **هاتف Android فعلي**: لا Termux، ولا proot-distro، ولا المحرك `linux.arm64` داخل Debian، ولا سرعة/ذاكرة ذلك.
- **استدعاء حيّ للنموذج المجاني** من هاتف (لا مفتاح لأي مزوّد في هذا المستودع، عن قصد).
- تشغيل **`build-android.yml` بعد هذه التعديلات** على GitHub (يعمل بـ `workflow_dispatch` فقط؛ أول تشغيل حقيقي سيكون منك أو من الوكيل)،
  وبالتالي لم يُنشأ أي **Release** ولم يُنزَّل أي APK من هاتف ولم يُثبَّت. النسخة السابقة من الـ workflow صدّرت APK فعليًا في CI هذا
  المستودع (README «Verification status»)؛ الخطوات المضافة اختُبرت كما في الفقرة السابقة فقط.
- **محرّر Godot لأندرويد** الرسمي (§2.5) لم يُفتح به أي مشروع من هنا.

---

## 8. المصادر (قُرئت 2026-09-26)

- Termux — README الرسمي (مصادر التثبيت، تحذير Android 12+ وقتل العمليات، Play فرع تجريبي): `https://github.com/termux/termux-app`
- Termux Wiki — الفروق عن لينكس (Bionic، بادئة واحدة، لا glibc): `https://wiki.termux.com/wiki/Differences_from_Linux`
- proot-distro (الإصدار 5.x، `install debian:bookworm`، `login --shared-home`، عدم انتقال متغيرات المضيف، منع proot داخل proot):
  `https://github.com/termux/proot-distro`
- Godot — تشغيل على Termux ليس منصة مستهدفة (godotengine/godot#82677): `https://github.com/godotengine/godot/issues/82677`
- Godot 4.7.2-stable — الملفات الرسمية وقائمة SHA-512 (بما فيها `linux.arm64` و`android_editor.apk`):
  `https://github.com/godotengine/godot-builds/releases/tag/4.7.2-stable`
- Godot — تصدير أندرويد (JDK 17، Build-Tools 35.0.1، Platform 35):
  `https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html`
- OpenRouter — حدود الطرازات المجانية (20/دقيقة، 50/يوم، 1000/يوم بعد 10 رصيد): `https://openrouter.ai/docs/api-reference/limits`
- OpenRouter — الخصوصية والتسجيل لكل مزوّد: `https://openrouter.ai/docs/features/privacy-and-logging`
- GitHub Actions — الفوترة (عام: مجاني؛ خاص على Free: 2,000 دقيقة و500 MB): `https://docs.github.com/en/billing/concepts/product-billing/github-actions`
- GitHub REST — Releases («latest» يُرتَّب بتاريخ الـ commit لا تاريخ النشر؛ `make_latest`): `https://docs.github.com/en/rest/releases/releases`
- GitHub Actions — مدخلات `workflow_dispatch` من نوع boolean وسياق `inputs`: `https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions`
- Godot — سطر الأوامر (`--headless`، `--import`, `--check-only`, `--export-debug`): `https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html`

---

## English summary

**Phone (Termux) = the brain, GitHub Actions = the engine + APK factory.** The agent is stdlib-only Python and runs directly in
Termux (chat, planning, file writing, GitHub tools). The official Godot 4.7.2 Linux editor is a **glibc** binary and cannot execute
in Termux's **Bionic** userland — the installer, `doctor` and `/api/status` detect Termux and say so instead of failing obscurely.
Two sound options: **(a)** run the `linux.arm64` editor inside a Debian guest via `proot-distro` on the same phone
(`scripts/termux_setup.sh --proot`, then `--guest --install-godot`), which gives real local verification; **(b)** let
`build-android.yml` do it on a GitHub runner — the workflow now **verifies the project with the engine first** (`--import`,
`--check-only` per script, smoke test → `SMOKE_TEST_OK`) and fails with file/line before exporting. With `publish_release=true` the
`.apk` is attached to a GitHub Release (`apk-<type>-<run>`, marked latest) so a phone browser can download it directly;
`github_build_status` lists the exact `download_url`. An optional `ANDROID_DEBUG_KEYSTORE_BASE64` secret keeps the debug signature
stable across builds so Android accepts updates. Free model: `GODOTAI_PRESET=openrouter_free` (20 req/min, **50 req/day** without
credits; one agent iteration = one request) — quotas and privacy are the provider's, not ours. **Not tested on a physical Android
device**, no live free-model call, no post-change workflow run, no APK installed — see §7. Everything here is limited to Godot Engine
4.7.2-stable game development.
