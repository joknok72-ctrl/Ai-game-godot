# كل شيء مجانًا؟ — Qwen عبر API، وخادم لينكس، وموقع عام: ما الممكن فعلًا في سبتمبر 2026

> السؤال الذي يجيب عنه هذا الملف (نص صاحب المشروع كما هو):
>
> طب بص عشان أنا مش معي فلوس اريد كل شي يكون مجانا ممكن نستخدم نموذج اللي هو Qwen من خلال api اخده من موقع معين و استخدمه مجانا فهمتني ده اول شي و ثاني شي السيرفر اللي هو لينكس المجاني ممكن اديلك ال api بتاعي من موقع railway السيرفر مواصفاته ده المعالج (vCPU Limit) 2.0 vCPU و الذاكرة العشوائية (Memory Limit) 1000 ميجابايت (1 جيجابايت) أو اعمل شي يكون افضل و اقوى و اذكي و منحتجش لي سيرفر لينكس أو جيب انت سيرفر لينكس أو اعمل سيرفر لينكس مجاني بدون ما نربطه بي railway أو api و كل ده فاهمني و كمان اهم شي أنا اريده يكون موقع عشان اعرف أكلمه بسهولة موقع عام فاهمني فا شوف برضه شي مجاني أو اعمل افضل و اقوى و اذكي حل ليه مجاني برضه و اعمل اعمق سيرش عندك في 2026 فيه هذا الشهر

كل رقم أدناه مقروء من صفحة المزوّد الرسمية يوم **2026-09-26** (الروابط في §7)، وكل شيء يتغيّر بلا إشعار. لا توجد هنا
خدمة «مجانية للأبد»؛ توجد **حصص مجانية محدودة** بشروط، وهذا الملف يقول بالضبط ما هي، وما تحتاجه من حساب/مفتاح/بطاقة،
وما يحدث لبياناتك، وأين خطر الفوترة. لم يُنشر أي شيء ولم يُستدعَ أي API من هذا المستودع؛ لا يوجد موقع عام بعد (§6).

## 0. الخلاصة في خمس نقاط

1. **النموذج (Qwen عبر API مجانًا): ممكن، لكن بحصة صغيرة.** أربعة مسارات جاهزة في الكود بمتغيّر واحد `GODOTAI_PRESET`
   (§2): OpenRouter (النسخة `:free` من Qwen3.8-27B — 50 طلبًا/يوم بلا رصيد)، Cloudflare Workers AI (10,000 نيورون/يوم)،
   Alibaba Model Studio (نحو مليون رمز لكل طراز، **90 يومًا فقط** ثم فوترة تلقائية ما لم تعطّلها)، Groq (حدود الحساب
   المجاني غير منشورة؛ تراها في حسابك). كلها تحتاج **حسابًا ومفتاح API** تضعه أنت في متغيّر بيئة — لا يُكتب في المستودع أبدًا.
2. **خادم Railway (2 vCPU / 1 GB) لا يستطيع تشغيل النموذج**، ولا أي نموذج Qwen مفيد لهذا العمل (§3). يستطيع تشغيل
   **التطبيق فقط** (صفحة المحادثة + تحقق المحرك) خلال فترة التجربة (30 يومًا / 5 دولارات)، بتشغيل واحد في كل مرة —
   قِسنا الذاكرة فعليًا: تحقق المحرك على قالب اللعبة يبلغ ذروة **≈ 640 MB** (§3.2). بعد التجربة تنزل الخطة المجانية إلى
   **0.5 GB** فلا يكفي حتى للتحقق: **لا نموذج ولا تطبيق**.
3. **أفضل خادم لينكس مجاني طويل الأمد وجدناه: Oracle Cloud Always Free** (ARM، حتى 2 OCPU + 12 GB، «لمدة غير محدودة»)
   — لكنه يتطلب بطاقة ائتمان/خصم للتحقق من الهوية، وقد يُستصلح الجهاز إن بقي خاملًا 7 أيام (§4). ما زال لا يشغّل النموذج
   بسرعة مفيدة؛ النموذج يبقى عبر API.
4. **موقع عام مجاني:** يحتاج (أ) خادمًا دائمًا من §4 و(ب) طريقة دخول آمنة: توكن (`GODOTAI_CHAT_TOKEN`) أو Cloudflare
   Access (يحتاج نطاقًا). الرابط العام لا يُنشأ إلا من **حسابك أنت** — لا نستطيع فعله بدلًا منك دون بيانات دخولك، ولن
   نطلبها في محادثة (§6).
5. **«أفضل وأقوى وأذكى» مجانًا:** لا نزعم أن أي نموذج مجاني يتفوّق على Claude Fable 5.1 (Max). الحكم الوحيد المقبول في هذا
   المشروع هو `python3 -m godotai eval compare` على نفس المهام بتقييم المحرك (docs/MODEL.md §5) — ولم يُشغَّل بعد لأي
   مسار مجاني.

## 1. ما الذي يحتاجه هذا التطبيق أصلًا (حتى نقارن بصدق)

| المكوّن | ما يحتاجه | لماذا |
|---|---|---|
| **النموذج** | خادم استدلال: 7B بدقة 4-bit ≈ 5 GB ذاكرة + GPU أو CPU قوية؛ 27B ≈ 16+ GB | حلقة الوكيل تُرسل الطلب كاملًا (مطالبة النظام + الأدوات + السجل) في **كل** دورة |
| **التطبيق** | Python + محرك Godot 4.7.2 بلا واجهة: ≈ 50 MB للخادم، **≈ 640 MB ذروة** للتحقق الواحد (مقياس §3.2) | التحقق يستورد المشروع ويشغّله 120 إطارًا |
| **تصدير APK** | JDK 17 + Android SDK (عدة GB) | لا يلزم على الخادم: يتم في GitHub Actions (`.github/workflows/build-android.yml`) |
| **الموقع** | خادم يعمل دائمًا + HTTPS + مصادقة + حصة تشغيل | صفحة المحادثة تفتح جلسات طويلة (SSE) |

## 2. النموذج: Qwen عبر API بحصة مجانية — المسارات الجاهزة (`GODOTAI_PRESET`)

الأوزان **مفتوحة** (Apache-2.0 على Hugging Face) في المسارات الأربعة الافتراضية، لكن **الخادم ليس خادمك**: طلباتك
ومخرجاتها تُعالَج عند المزوّد وفق شروطه. لذلك تُسجَّل الهوية `serving = managed` تلقائيًا (لا `self_hosted`)، وتقولها
صفحة المحادثة و`doctor` صراحةً. `python3 -m godotai presets` يطبع الجدول التالي من الكود نفسه.

| المسار | الطراز الافتراضي | ما هو مجاني (مقروء 2026-09-26) | يلزمك | البيانات | الخطر |
|---|---|---|---|---|---|
| `openrouter_free` | `qwen/qwen3.8-27b:free` (سياق 262K، $0/مليون رمز) | حدود منصة على كل طراز `:free`: **20 طلبًا/دقيقة، 50 طلبًا/يوم** بلا رصيد مشترى؛ **1000/يوم** بعد شراء 10 دولارات رصيد مرة واحدة | حساب + `OPENROUTER_API_KEY` | يُمرَّر لمزوّد خارجي؛ الاحتفاظ/التدريب يختلف حسب المزوّد (صفحة الخصوصية تعرضه لكل مزوّد) ويمكن استثناء مزوّدين من إعدادات الحساب | 50 طلبًا ≈ **تشغيل واحد قصير** يوميًا (كل دورة أدوات = طلب)؛ قد يُزال الطراز المجاني |
| `workers_ai` | `@cf/qwen/qwen3-30b-a3b-fp8` | **10,000 نيورون/يوم** على خطة Workers المجانية (بلا بطاقة). هذا الطراز: 4,625 نيورون/مليون رمز إدخال، 30,475/مليون إخراج ≈ 2 مليون رمز إدخال أو 330 ألف إخراج يوميًا. `@cf/qwen/qwen3.8-27b`: 40,909 / 290,909 ≈ تشغيل قصير واحد | حساب Cloudflare + `CF_ACCOUNT_ID` + توكن `CF_WORKERS_AI_TOKEN` بصلاحية Workers AI فقط | Cloudflare: لا تستخدم محتوى العميل للتدريب أو تحسين الخدمات دون موافقة صريحة | فوق الحصة تتوقف الطلبات (Free) أو تُحاسَب 0.011$/1000 نيورون (Paid) |
| `alibaba_model_studio` | `qwen3-coder-30b-a3b-instruct` (بديل: `qwen3.5-27b`) | عند أول تفعيل في **سنغافورة**: حصة لكل طراز (عادةً **1,000,000 رمز**) صالحة **90 يومًا** من التفعيل | إكمال معلومات الحساب + `DASHSCOPE_API_KEY` + `GODOTAI_BASE_URL` بمعرّف مساحة عملك | صفحة الخصوصية الرسمية لم تُسترجع بشكل مقروء (4 محاولات؛ الصفحة تُرسم بالسكربت) → **غير مُثبت من مصدر أولي** | **فوترة تلقائية** بعد النفاد/الانتهاء ما لم تفعّل «Free Quota Only» (معطّل افتراضيًا) لكل طراز |
| `groq` | `qwen/qwen3.8-27b` (Preview، سياق 131K، إخراج ≤ 16,384) | حسابات بلا بطاقة موجودة، لكن الجدول المنشور (30 RPM / 1K RPD / 8K TPM / 200K TPD) موصوف بأنه «الحدود الأساسية لخطة Developer»؛ حدودك الحقيقية في صفحة Limits بحسابك | حساب + `GROQ_API_KEY` | لا عبارة أولية مختصرة؛ الشروط تُحيل إلى DPA. مصدر ثانوي (OpenRouter) يصنّفها «احتفاظ صفري/لا تدريب» | 8K رمز/دقيقة قد لا تكفي مطالبات هذا الوكيل الطويلة؛ الطراز معاينة |

**كيف تُشغّل مسارًا (مثال OpenRouter):**

```bash
export GODOTAI_PRESET=openrouter_free
export OPENROUTER_API_KEY=<من https://openrouter.ai/settings/keys — لا تلصقه في أي ملف أو محادثة>
python3 -m godotai doctor      # يقول: preset openrouter_free … keys set، ويطبع الحصة وسياسة البيانات
python3 -m godotai chat        # الصفحة تعرض الهوية: نموذج مفتوح الوزن عند مزوّد استضافة — الخادم ليس خادمك
```

قواعد الأسبقية: متغيّرات `GODOTAI_*` الصريحة تغلب المسار، والمسار يغلب `godot.toml`. المسار يضبط `provider/route/model/base_url`
وهوية `[model]` (النوع، الاستضافة، الأوزان، الرخصة). مع OpenRouter، اختيار طراز شركة مغلقة (`openai/…`, `anthropic/…`, `google/…`)
يُرفض ما لم تُعلن `GODOTAI_MODEL_KIND=vendor_api GODOTAI_SERVING=vendor` — نفس القاعدة القديمة: نموذج شركة لا يُقدَّم كنموذجك.

**ما يعنيه «50 طلبًا في اليوم» لهذا الوكيل:** كل دورة تفكير→استدعاء أداة→نتيجة = طلب واحد يحمل السجل كله؛ التشغيل النموذجي
لمهمة `flappy-clone` يستهلك عشرات الدورات. لذلك فالمسارات المجانية تكفي **للتجربة والقياس**، لا لاستخدام يومي كثيف. الحلقة
تحترم الآن ترويسة `Retry-After` التي تعيدها هذه الخدمات مع `429` (godotai/providers/base.py) فتنتظر ما يطلبه المزوّد بدل
إهدار الحصة.

**ما لم نختره ولماذا:** Cerebras (رصيد 5$ تجريبي، بطاقة، 30 يومًا ثم توقّف)؛ Ollama Cloud (لم نجد حصة مجانية دائمة موثّقة
بلا بطاقة)؛ Gemini / GitHub Models (ليست Qwen). ولم نختر «تشغيل Qwen صغير على الخادم المجاني» — انظر §3.

## 3. خادم Railway الذي ذكرته: 2 vCPU / 1000 MB — يشغّل النموذج؟ التطبيق؟ لا هذا ولا ذاك؟

### 3.1 ما يقوله Railway نفسه (2026-09-26)

- الجدول الذي أرسلته هو **خطة التجربة (Trial)**: مستخدم جديد، **حتى 30 يومًا**، منحة **5 دولارات لمرة واحدة**؛ لكل خدمة: 1 GB RAM،
  2 vCPU مشتركة، 1 GB تخزين مؤقت، **0.5 GB حجم دائم**، صورة ≤ 4 GB. تجربة «محدودة» (بلا تحقق عبر GitHub) لها **قيود على
  الاتصال الخارجي** — قد تمنع الوصول إلى API النموذج أصلًا.
- بعد 30 يومًا أو نفاد الـ5$: خطة **Free** = رصيد **1$ شهريًا** لا يتراكم، **0.5 GB RAM، 1 vCPU**. Hobby = 5$/شهر (ليست مجانية).
- الفوترة بالاستخدام الفعلي: RAM **10$/GB/شهر**، CPU 20$/vCPU/شهر. خادم محادثة خامل (≈ 50–80 MB) ≈ 0.5–0.8$/شهر — ينسجم مع الـ5$ ثم
  الـ1$؛ التحقق بالمحرك يرفع الاستهلاك مؤقتًا.
- الأحجام الدائمة لحسابات التجربة **تُحذف بعد 30 يومًا من انتهاء الرصيد** — أي أن مجلد الألعاب `/games` يضيع ما لم تنسخه إلى GitHub
  (أداة GitHub في الوكيل تفعل ذلك).

### 3.2 القياس الذي أجريناه (لا تخمين)

على x86_64 بمحرك Godot 4.7.2-stable بلا واجهة، قالب `mobile-2d` بعد `python3 -m godotai new`:

| العملية | الذروة (RSS) | المدة |
|---|---|---|
| `godot --headless --version` | 39 MB | فوري |
| `python3 -m godotai verify --project …` (استيراد + فحص السكربتات + تشغيل 120 إطارًا) | **≈ 642 MB** | 6.8 ث |
| `python3 -m godotai chat --check` (الخادم يبدأ ويتوقف) | ≈ 50 MB | 0.5 ث |

(الأرقام من `resource.getrusage(RUSAGE_CHILDREN)`؛ ألعاب أكبر تستهلك أكثر. القياس محفوظ خارج المستودع مع أبحاث هذا الملف.)

### 3.3 الحكم

| السؤال | الجواب | السبب |
|---|---|---|
| يستضيف **نموذج** Qwen؟ | **لا** | 7B بدقة 4-bit يحتاج ≈ 5 GB ذاكرة وحدها؛ 1 GB لا يحمل حتى أصغر طراز مفيد لاستدعاء الأدوات، وبلا GPU تكون السرعة أجزاء رمز/ثانية على `max_tokens = 64000` |
| يستضيف **التطبيق فقط**؟ | **نعم خلال التجربة، بشرطين** | صورة `deploy/paas/Dockerfile` (بلا Android SDK/JDK/قوالب تصدير) + `GODOTAI_MAX_CONCURRENT_RUNS=1`: 50 MB + 642 MB < 1 GB؛ تشغيلان متزامنان = OOM |
| بعد التجربة (0.5 GB)؟ | **لا هذا ولا ذاك** | التحقق وحده (642 MB) > 0.5 GB؛ تبقى المحادثة بلا تحقق — أي بلا قيمة المشروع |
| النموذج + التطبيق معًا؟ | **لا** | حتى Hobby المدفوعة لا تعطي GPU |

الطريقة (بلا ملف إعداد خاص بـ Railway — «Config as Code» أُعلن إهماله في 2026 حتى 2026-12-01، والبديل IaC يحتاج CLI وتوكن مشروع):
في إعدادات الخدمة على لوحة Railway ضع المتغيّرات `RAILWAY_DOCKERFILE_PATH=deploy/paas/Dockerfile`، `GODOTAI_CHAT_TOKEN=<سر طويل>`،
`GODOTAI_PRESET=<مسار>` ومفتاحه، ومسار الفحص الصحي `/healthz`؛ الخادم يقرأ `PORT` الذي يحقنه Railway ويقبل `RAILWAY_PUBLIC_DOMAIN`
في ترويسة Host تلقائيًا. التفاصيل في `deploy/paas/README.md`. **لن نطلب توكن Railway الخاص بك** ولن نستخدمه: النشر يتم من حسابك.

## 4. خوادم لينكس مجانية أخرى — مقارنة (2026-09-26)

| الخيار | ما تحصل عليه مجانًا | يلزمك | الدوام / الثبات | يشغّل التطبيق؟ | النموذج؟ |
|---|---|---|---|---|---|
| **Oracle Cloud Always Free** | Ampere A1 (ARM): حتى **2 OCPU + 12 GB** (1,500 ساعة OCPU + 9,000 GB·ساعة شهريًا)، أو حتى 2 × E2.1.Micro (1 GB)، 200 GB تخزين؛ «لمدة غير محدودة» | حساب + **بطاقة ائتمان/خصم** للتحقق (لا مسبقة الدفع) | قد يُستصلح الجهاز إن كان خاملًا 7 أيام (CPU/شبكة/ذاكرة < 20%)؛ الحساب الخامل 30 يومًا قد يُعلَّق؛ «out of host capacity» شائعة | **نعم** (A1؛ الصورة تدعم `linux.arm64` — غير مجرَّب بعد على ARM) | 7B على 2 نوى ARM ≈ رمز واحد/ث أو أقل — غير عملي |
| **Google Cloud Free Tier** | 1 × `e2-micro` (≈ 1 GB) في `us-west1`/`us-central1`/`us-east1`، 30 GB قرص، 1 GB خروج/شهر | حساب فوترة (بطاقة) | دائم ضمن الحدود؛ الخروج فوق 1 GB يُحاسَب | هامشي (1 GB مثل Railway، بلا حدّ زمني) | لا |
| **Railway** (§3) | تجربة 30 يومًا/5$ ثم 1$/شهر و0.5 GB | حساب؛ GitHub للتحقق | مؤقت | خلال التجربة فقط | لا |
| **Render Free** | خدمة ويب **512 MB**، 750 ساعة/شهر | حساب | **تنام بعد 15 دقيقة** بلا زيارات وتستيقظ في ≈ دقيقة؛ **نظام الملفات يُفقد** عند كل نوم/نشر؛ لا أقراص دائمة؛ Postgres يُحذف بعد 30 يومًا | المحادثة فقط؛ التحقق (642 MB) > 512 MB → **لا** | لا |
| **Hugging Face Spaces** | CPU Basic 2 vCPU/16 GB… **لكن** Docker/Gradio Spaces تحتاج خطة PRO/Team مدفوعة؛ المجاني: Static وحتى Space-ين Gradio على ZeroGPU | حساب (+PRO للـDocker) | ينام عند الخمول؛ قرص غير دائم | **لا** (Docker ليس مجانيًا) | لا |
| **GitHub Actions** (عام) | `ubuntu-latest`: 4 CPU/16 GB، غير محدود للمستودعات العامة، 6 ساعات/مهمة | المستودع عام | ليس خادمًا: يعمل عند الطلب فقط | لا كموقع؛ **نعم** للتحقق وبناء APK (موجود) | لا |
| **Fly.io / Koyeb** | لا خطة «مجانية دائمًا» مؤكدة في الصفحات المقروءة (Fly تطلب بطاقة وتحاسب بالاستخدام) | بطاقة | — | غير موصى به دون تحقق إضافي | لا |
| **Cloudflare Tunnel (Quick)** | نفق `*.trycloudflare.com` بلا حساب | لا شيء | **للتجربة فقط**: عنوان عشوائي يزول بإغلاق العملية، 200 طلب متزامن، بلا Access | يوصل جهازك أنت مؤقتًا | — |
| **Cloudflare Tunnel + Access** (deploy/cloudflare/) | الحماية والدخول بالبريد مجانًا | **نطاق** على Cloudflare + خادم دائم من هذا الجدول | دائم ما دام الخادم يعمل | يحمي التطبيق، لا يستضيفه | لا |

**التوصية العملية بدون أي إنفاق:** Oracle Always Free (A1) كخادم دائم + Cloudflare Access إن كان لديك نطاق (وإلا توكن) + مسار
`workers_ai` أو `openrouter_free` للنموذج. إن لم تملك بطاقة إطلاقًا: Railway لمدة التجربة فقط، ثم لا يوجد بديل مجاني دائم يفي بـ 1 GB
بلا بطاقة في ما قرأناه — وهذا يجب أن يُقال بوضوح لا أن يُجمَّل.

## 5. الأمان والكلفة على موقع عام (إلزامي، لا اختياري)

- **مصادقة على كل طلب:** `GODOTAI_CHAT_TOKEN` (يرفض الخادم البدء على 0.0.0.0 بدونه؛ `deploy/paas/entrypoint.sh` يوقف الحاوية قبل
  ذلك برسالة واضحة) أو Cloudflare Access. صفحة `/healthz` وحدها بلا مصادقة (فحص الحياة).
- **حصة تشغيل:** `GODOTAI_MAX_CONCURRENT_RUNS=1` (حدّ الذاكرة على 1 GB) و`GODOTAI_MAX_RUNS_PER_DAY` (حدّ حصة API النموذج المجانية
  — مع OpenRouter مثلًا لا معنى لأكثر من 1–2 تشغيل/يوم).
- **الأسرار خارج الكود:** المفاتيح في متغيّرات بيئة/مخزن أسرار المنصة فقط؛ `scripts/secret_scan.py` وفحوص `tests/` تفشل إن ظهر توكن في
  المستودع؛ أي مفتاح لُصق يومًا في محادثة يُعدّ مكشوفًا ويُلغى (SECURITY.md).
- **حصة الاستضافة:** على Cloudflare Workers Free لا فوترة (تتوقف الطلبات)؛ على Alibaba فعّل «Free Quota Only» قبل أول طلب؛ على Railway
  راقب الاستخدام في اللوحة؛ لا تفعّل الدفع التلقائي في أي منصة إن كان الهدف صفر إنفاق.

## 6. حالة الموقع العام الآن — بصراحة

- **لا يوجد رابط عام.** لم يُنشر شيء من هذا المستودع، ولا يمكن نشر موقع دائم بدون حسابك على المنصة (Railway/Oracle/…)، ومفتاح API
  النموذج، و— لمسار Cloudflare Access — نطاقك. هذه كلها أشياء تملكها أنت وتدخلها في لوحة المنصة؛ لن نطلبها في محادثة ولن نستخدم توكنًا
  لُصق سابقًا.
- ما يمكنك فعله اليوم بخطوات قليلة: (1) أنشئ حساب OpenRouter ومفتاحًا؛ (2) على Railway: خدمة من هذا المستودع مع المتغيّرات في
  `deploy/paas/README.md`؛ (3) افتح `https://<اسم-الخدمة>.up.railway.app/#token=<GODOTAI_CHAT_TOKEN>`؛ (4) أول رسالة تجريبية صغيرة، ثم
  `python3 -m godotai eval run` لقياس ما يفعله الطراز المجاني قبل أي حكم.

## 7. المصادر (قُرئت 2026-09-26)

- OpenRouter: https://openrouter.ai/docs/api-reference/limits · https://openrouter.ai/qwen/qwen3.8-27b:free · https://openrouter.ai/docs/features/privacy-and-logging
- Groq: https://console.groq.com/docs/model/qwen/qwen3.8-27b · https://console.groq.com/docs/rate-limits · https://groq.com/terms-of-use
- Alibaba Cloud Model Studio: https://www.alibabacloud.com/help/en/model-studio/new-free-quota · https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope · https://www.alibabacloud.com/help/en/model-studio/text-generation-model
- Cloudflare Workers AI: https://developers.cloudflare.com/workers-ai/platform/pricing/ · https://developers.cloudflare.com/workers-ai/platform/limits/ · https://developers.cloudflare.com/workers-ai/privacy/
- Qwen weights/licences: https://huggingface.co/Qwen/Qwen3.8-27B · https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct · https://huggingface.co/Qwen/Qwen3.5-27B (كلها Apache-2.0)
- Railway: https://docs.railway.com/reference/pricing/free-trial · https://docs.railway.com/reference/pricing/plans · https://docs.railway.com/reference/variables · https://docs.railway.com/reference/healthchecks · https://docs.railway.com/builds/dockerfiles · https://docs.railway.com/reference/config-as-code (مُهمل) · https://docs.railway.com/infrastructure-as-code
- Render: https://render.com/docs/free · https://render.com/pricing · https://render.com/docs/environment-variables
- Oracle: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm · https://www.oracle.com/cloud/free/
- Google Cloud: https://cloud.google.com/free/docs/free-cloud-features
- Hugging Face: https://huggingface.co/pricing · https://huggingface.co/docs/hub/spaces-overview · https://huggingface.co/docs/hub/spaces-sdks-docker
- GitHub Actions: https://docs.github.com/en/actions/reference/runners/github-hosted-runners · https://docs.github.com/en/actions/reference/limits
- Cerebras: https://inference-docs.cerebras.ai/support/rate-limits · Ollama Cloud: https://docs.ollama.com/cloud
- القياس: `verify` على قالب mobile-2d، Godot 4.7.2-stable headless x86_64، 2026-09-26 (ذروة 642 MB).

---

## English summary

**Question:** everything for $0 — Qwen through a free API, a free Linux server (the user's Railway box: 2 vCPU / 1 GB), and a
public website to chat with the agent; "or something better, stronger, smarter, also free".

**Model:** four hosted Qwen endpoints with a free allowance are wired as `GODOTAI_PRESET=…` (`godotai/presets.py`, listed by
`python3 -m godotai presets`): OpenRouter `qwen/qwen3.8-27b:free` (20 req/min, **50 req/day** without purchased credits, 1,000/day
after a one-time $10 purchase), Cloudflare Workers AI `@cf/qwen/qwen3-30b-a3b-fp8` (**10,000 Neurons/day** ≈ 2M input tokens),
Alibaba Model Studio (Singapore; ~1M tokens per model for **90 days**, then automatic pay-as-you-go unless "Free Quota Only" is
enabled), Groq `qwen/qwen3.8-27b` (Preview; the published table is the Developer plan's, your free limits are in your console).
Each needs an account and an API key that lives only in an environment variable. Weights are open (Apache-2.0); the server is
the provider's, so the identity card says `serving = managed`. Data handling is quoted where a primary source exists
(Cloudflare: no training on customer content without consent; OpenRouter: per-provider table) and marked "not established" where
it does not (Groq, Alibaba). One agent iteration = one request, so 50/day ≈ one short run. The transport now honours `Retry-After`.
None of this is forever-free, and no quality claim is made — `eval compare` decides.

**Railway 2 vCPU / 1 GB:** the *Trial* tier (30 days, one-time $5; after that Free = $1/month, **0.5 GB**, 1 vCPU). Measured:
one engine verification of the template peaks at **≈ 642 MB RSS**, the chat server ≈ 50 MB. Verdict: **no** for hosting a Qwen model
(a 7B at 4-bit needs ≈ 5 GB), **yes for the app only during the trial** with `GODOTAI_MAX_CONCURRENT_RUNS=1` (slim image
`deploy/paas/Dockerfile`, no Android SDK), **neither** after the trial (0.5 GB < 642 MB). Limited (unverified) trials also restrict
outbound network, which can block the model API. Trial volumes are deleted 30 days after the credit expires.

**Other free hosts:** Oracle Cloud Always Free Ampere A1 (up to 2 OCPU / 12 GB, "unlimited time", card required for identity, idle
instances may be reclaimed after 7 days) is the strongest long-lived option — the image supports `linux.arm64`, untested on ARM.
Google e2-micro (1 GB) is marginal; Render Free (512 MB, sleeps after 15 min, ephemeral disk) cannot run the verification; Hugging Face
Docker Spaces require a paid plan; GitHub Actions is not a server (it already builds APKs); Fly/Koyeb showed no confirmed free tier.

**Public site:** none exists and none can be created without the user's own platform account, model key and (for Cloudflare
Access) domain. Nothing was deployed; no credential was requested or used. Security is mandatory: `GODOTAI_CHAT_TOKEN` or Cloudflare
Access on every request, run quotas, secrets only in the platform's variable store.
