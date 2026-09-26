# النموذج: ما هو «ذكاؤك الاصطناعي الخاص» بالضبط — وما الذي لا نزعمه

> **الطلب:** ذكاء اصطناعي **مستقل، مخصص لك أنت فقط**، مجاله الوحيد صناعة أي لعبة على **Godot Engine 4.7.2-stable**، ليس
> نموذج شركة أخرى، ويكون في هذا المجال بمستوى أفضل النماذج العامة (المرجع: Claude Fable 5.1 بجهد `max`) أو أعلى.
>
> **الجواب الصادق في سطرين:** (1) نعم يمكنك امتلاك ذكاء اصطناعي خاص بك يعمل على خادمك بلا أي شركة في الطريق — وهذا هو
> **الافتراضي الآن** في المستودع؛ (2) «خاص بك» له ثلاث درجات مختلفة تمامًا في الكلفة والمعنى (نشر خاص، تدريب إضافي خاص،
> تدريب من الصفر)، وأي كلام عن «أقوى من Claude Fable 5.1» لا يُقال هنا إلا **كنتيجة قياس** يحكم فيها المحرك — لا كوعد.

هذا الملف هو المرجع الوحيد لهوية النموذج؛ الكود يفرض ما فيه (`godotai/model_identity.py`)، وصفحة المحادثة وأمر `doctor`
يعرضان الهوية نفسها حرفيًا.

---

## 1. الدرجات الثلاث لـ «نموذج خاص بي» (+ الحالة الرابعة التي ليست خاصة)

القيمة في `godot.toml` → `[model].kind` (أو المتغير `GODOTAI_MODEL_KIND`):

| `kind` | ما هو | مَن يملك الأوزان؟ | مَن يشغّله؟ | الكلفة الواقعية | الحالة في هذا المستودع |
| --- | --- | --- | --- | --- | --- |
| `open_weight_deployment` | نموذج **مفتوح الوزن** تنزّله وتشغّله على خادمك كما هو | الجهة التي نشرته (مثلًا فريق Qwen) — بترخيص يسمح لك بالاستخدام التجاري والتعديل | **أنت** | خادم + GPU (7B: بطاقة 16–24 GB؛ أو CPU بطيء) | **الافتراضي**: `Qwen/Qwen2.5-Coder-7B-Instruct` بترخيص **Apache-2.0** (بطاقة النموذج + ملف LICENSE، قراءة 2026-09-26) |
| `fine_tune` | النموذج المفتوح أعلاه **+ أوزان دربتها أنت** (محوّل LoRA) على آثار ألعاب Godot متحقَّق منها بالمحرك | الأساس لغيرك، **المحوّل لك** — والنتيجة نموذج لا يملكه أحد غيرك | **أنت** | نفس الخادم + جولة تدريب (Kaggle T4×2 أو GPU لديك: ساعات) + **بيانات**: مئات التشغيلات الناجحة | المسار كامل ومفحوص بلا GPU (`dataset extract` → `training/train_qlora.py` → vLLM `--enable-lora`)؛ **لم يُدرَّب محوّل بعد** لأن البيانات تأتي من تشغيل الوكيل فعليًا |
| `from_scratch` | تدريب مسبق من أوزان عشوائية: أوزان **100 % لك** ولا أساس لأحد | أنت | أنت | **غير واقعية لفرد**: نموذج برمجة تنافسي يحتاج تريليونات الرموز وآلاف GPU-hours على الأقل (عشرات ملايين الدولارات للمستوى الأعلى)، وحتى نموذج صغير يحتاج مئات GPU-hours ليكتب كودًا مفيدًا | الكود **يقبل** هذا النوع فقط مع `training_report` يُثبت أنه حدث — لا يمكن إعلانه بالكلام |
| `vendor_api` | Claude / GPT / … خلف API شركة | الشركة | الشركة | اشتراك/رصيد | **اختياري** و**ليس المنتج**: يُستخدم كـ**مرجع للمقارنة** فقط، ويُرفض تحميل الإعداد إن لم يُعلَن صراحةً (`provider = "anthropic"` يحتاج `kind = "vendor_api"` و`serving = "vendor"`) |

**ما لا نفعله أبدًا:** تسمية نموذج شركة أخرى أو نموذج مفتوح الوزن غير معدَّل «نموذجًا أصليًا بالكامل». الصفحة تكتب بالعربية
والإنجليزية: *«نشر خاص لنموذج مفتوح الوزن (الأساس Qwen/Qwen2.5-Coder-7B-Instruct، Apache-2.0)، على خادمك أنت — يخصك؛ ليس
مدرَّبًا من الصفر»* — وتتغير الجملة تلقائيًا إلى «+ تدريب خاص بك (LoRA)» عندما تضبط `adapter`، وإلى «نموذج شركة أخرى — ليس
لك» عند اختيار مزوّد.

### لماذا الافتراضي «نشر خاص» وليس «تدريب خاص» الآن؟

لأن التدريب الإضافي يحتاج بيانات لا تُخترع: آثار تشغيل كاملة (تفكير ← أداة ← نتيجة **المحرك الحقيقي** ← تصحيح ← PASS) لألعاب
Godot 4.7.2 حقيقية. المستودع يولّد هذه البيانات من كل تشغيل ناجح (`python3 -m godotai dataset extract`)، فالترتيب الصادق هو:
شغّل النموذج المفتوح على خادمك → اجمع مئات التشغيلات المتحقَّق منها → درِّب المحوّل → قِسه → ثم قل ما قاله القياس.

---

## 2. من أين يأتي «التخصص في Godot» اليوم — الجزء الذي هو لك 100 %

النماذج العامة (بما فيها Claude Fable 5.1) لم تُبنَ لمحرك Godot 4.7.2 تحديدًا. ما يجعل هذا المشروع **مخصصًا** ليس الأوزان
وحدها، بل الطبقة التي تحيط بها، وكلها في هذا المستودع وتحت سيطرتك:

1. **فهرس API مولَّد من المحرك نفسه** (`apiref build` بأمر `--doctool` على النسخة 4.7.2 بالضبط: 1 076 صنفًا، 10 731 دالة) —
   أدوات `api_lookup` / `api_search` / `api_lint`؛ أي دالة من Godot 3 أو غير موجودة تظهر كخطأ، مهما كان النموذج.
2. **التحقق بالمحرك الحقيقي** قبل إعلان أي نجاح (`--import` → `--check-only` → smoke test → lint) — هذا هو «المدرّس» الذي
   يعيد النموذج إلى الصواب، وهو نفسه حكم بنك الاختبارات.
3. **قاعدة معرفة مثبَّتة على الإصدار** (`knowledge/`) تُحمَّل عند الحاجة، ومطالبات نظام محددة النطاق ترفض أي شيء خارج ألعاب Godot.
4. **بوابة الخطة والسياسة في الكود** (لا كتابة ملف قبل موافقة، وكل تعديل يحمل رقم خطوة).
5. **بنك اختبارات يحكم فيه المحرك** (`evals/tasks/`) — به تُقاس أي أوزان: مفتوحة، أو مدرَّبة، أو مرجعية.
6. **دولاب البيانات**: كل تشغيل ناجح = مثال تدريب متحقَّق منه → المحوّل الخاص بك (القسم 4).

بهذا يكون «ذكاؤك الاصطناعي» = **أوزان تملك تشغيلها (وتدريبها لاحقًا) + طبقة تخصص Godot تملكها بالكامل**. ولهذا السبب بالذات
يمكن لنموذج مفتوح أصغر أن يتفوق على نموذج عام أكبر **في هذه المهمة الضيقة** — لكن ذلك احتمال يُثبت بالقياس (القسم 5)، لا حقيقة
تُعلن مسبقًا.

---

## 3. الترخيص والقانون (بدون تجميل)

| المسألة | الحقيقة | ما يفعله المستودع |
| --- | --- | --- |
| ترخيص `Qwen/Qwen2.5-Coder-7B-Instruct` | **Apache-2.0** (بطاقة النموذج وملف LICENSE على Hugging Face، قراءة 2026-09-26): استخدام تجاري، تعديل، إعادة توزيع، مع الإبقاء على إشعار الترخيص/الحقوق. لا يحق لك ادعاء أنك أنشأت الأوزان الأصلية | `base_license = "Apache-2.0"` في `godot.toml`؛ الإفصاح في الصفحة يذكر الأساس دائمًا |
| أحجام/عائلات أخرى | ليست كل نماذج Qwen بنفس الترخيص (بعض الأحجام لها «Qwen License»/«Research License»); عائلة **Llama** لها ترخيص مجتمعي خاص وليس Apache | اقرأ بطاقة كل نموذج قبل تغيير `base_model`، وحدّث `base_license` — الاختبارات تتحقق من تطابق التوثيق مع الإعداد |
| التدريب على مخرجات نماذج الشركات | شروط استخدام Anthropic (وغيرها) **تقيّد استخدام مخرجات الخدمة لتطوير أو تدريب نماذج منافسة**. أي آثار تشغيل أُنتجت بـ `provider = "anthropic"` **لا** تُستخدم كبيانات تدريب إلا إن كانت الشروط السارية تسمح | الملف `training/README.md` يوضّح ذلك؛ درِّب على آثار نموذجك الخاص (الافتراضي)، والآثار المرجعية للمقارنة فقط |
| كود الألعاب المولَّد | تشغيل نموذج مفتوح الوزن على خادمك لا يمنح أحدًا حقًا في ألعابك؛ ألعابك لك | لا شيء يُرسل لأي طرف ثالث في المسار الافتراضي |
| «نموذج خاص بالكامل» | صحيح **قانونيًا وتشغيليًا** (على خادمك، بلا اشتراك، بلا إرسال بيانات) — و**غير صحيح** إذا قُصد به «أوزان من الصفر» | نميّز الدرجات الثلاث أعلاه ونعرض الدرجة الحقيقية دائمًا |

---

## 4. طريق النموذج الخاص فعليًا (خطوات، وما تحقق من كل خطوة)

```bash
# 0) خادم نموذجك (خيار GPU): يقدّم الأساس المفتوح باسم "godotai" مع استدعاء الأدوات (Qwen2.5 = محلل hermes في vLLM)
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --served-model-name godotai --enable-auto-tool-choice --tool-call-parser hermes
#    خيار CPU (بطيء): ollama pull qwen2.5-coder:7b   ثم  export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 GODOTAI_MODEL=qwen2.5-coder:7b
#    أو الحاويات الجاهزة: docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d

# 1) تكلّم معه (محليًا) — الافتراضي يتوقع http://127.0.0.1:8000/v1
python3 -m godotai chat            # يعرض في الحالة: هل خادمك متاح، وهل "godotai" ضمن /models، وما هوية النموذج

# 2) اجمع آثارًا متحقَّق منها (كل لعبة نجحت = مثال)
python3 -m godotai eval run --task flappy-clone --workspace games/flappy --yes
python3 -m godotai dataset extract --runs games --out data/sft.jsonl

# 3) درِّب المحوّل الخاص بك (GPU لديك أو Kaggle T4×2 كمهمة دفعية — training/README.md)
python3 training/train_qlora.py --data data/sft.jsonl --model Qwen/Qwen2.5-Coder-7B-Instruct --out training/output/godotai-lora

# 4) قدّمه بنفس الاسم وقل الحقيقة في الإعداد
vllm serve Qwen/Qwen2.5-Coder-7B-Instruct --enable-lora --lora-modules godotai=training/output/godotai-lora \
    --enable-auto-tool-choice --tool-call-parser hermes
export GODOTAI_MODEL_KIND=fine_tune GODOTAI_ADAPTER=godotai-lora      # الصفحة تكتب الآن: «+ تدريب خاص بك (LoRA)»

# 5) قِس — ثم فقط قل ما قاله المحرك (القسم 5)
python3 -m godotai eval compare
```

| الخطوة | متحقَّق منه في هذا المستودع | غير متحقَّق منه (بصراحة) |
| --- | --- | --- |
| إعدادات الافتراضي الخاص، فحص الخادم (`GET /models`)، الإفصاح في الصفحة و`doctor` | اختبارات وحدة بخادم مزيّف | تشغيل حي مع vLLM/Ollama حقيقيين (لا GPU/Docker في بيئة التطوير) |
| قدرة Qwen2.5-Coder-7B على قيادة حلقة الأدوات هذه | — | **لم تُقَس**؛ قد تحتاج نموذجًا أكبر (Qwen2.5-Coder-14B/32B-Instruct — راجع ترخيص كل حجم) أو المحوّل |
| استخراج البيانات، `--dry-run` للتدريب، بيانات Kaggle | اختبارات + CI | التدريب نفسه (`train_qlora.py` بلا `--dry-run`) |
| `eval compare` | اختبارات وحدة على نتائج مصنوعة | لا توجد نتائج حقيقية لأي نموذج بعد؛ الأمر يطبع **NO EVIDENCE** حتى تُشغَّل المهام |

---

## 5. «أقوى من Claude Fable 5.1 (Max)؟» — كيف يُقال هذا بصدق

**القاعدة:** لا يوجد في الكود ولا في الوثائق ولا في الصفحة أي إعلان عن جودة النموذج مقابل نموذج آخر (`quality_claim` في
`ModelIdentity` هو دائمًا `None`). الجملة الوحيدة المسموح بها هي مخرجات هذا الأمر:

```bash
# الجانب المرجعي (اختياري، بمفتاحك أنت، ويُعلَن كنموذج شركة):
GODOTAI_PROVIDER=anthropic GODOTAI_MODEL=claude-fable-5-1 GODOTAI_MODEL_KIND=vendor_api GODOTAI_SERVING=vendor \
GODOTAI_BASE_MODEL=claude-fable-5-1 ANTHROPIC_API_KEY=<your key> \
    python3 -m godotai eval run --task flappy-clone --workspace /tmp/ref/flappy --yes
# جانبك (الافتراضي):
python3 -m godotai eval run --task flappy-clone --workspace /tmp/mine/flappy --yes
# الحكم: نفس المهام، نفس المحرك، نفس الفحوص البنيوية — لا نموذج يحكم على نموذج
python3 -m godotai eval compare --candidate godotai --reference claude-fable-5-1
```

- كل نتيجة في `evals/results/*.json` تحمل اسم النموذج الذي أنتجها؛ `compare` يقارن **المهام المشتركة فقط**.
- بلا نتائج مشتركة يطبع **NO EVIDENCE** ويخرج برمز 1 — لا يوجد «افتراضيًا أفضل».
- بأقل من ثلاث مهام مشتركة يكتب أن النتيجة **ملاحظة على مهام بعينها** وليست حكمًا عامًا.
- المرجع `claude-fable-5-1` هو قيمة `reference_model` في `godot.toml` ويمكن تغييره لأي نموذج آخر.
- إن أظهر القياس تفوق المرجع في مهمة، فهذا هو **مؤشر التدريب التالي**: أضف مهام من هذا النوع إلى `evals/tasks/`، اجمع
  آثارًا ناجحة، درِّب، قِس مجددًا. هذه هي الطريقة الوحيدة التي يصبح بها نموذج مخصص أقوى من نموذج عام في مجاله.

---

## 6. الإعداد الذي يفرض كل ما سبق

```toml
[agent]
provider = "openai_compat"     # خادمك (vLLM/llama.cpp/Ollama). anthropic = نموذج شركة، اختياري ومُعلَن
model = "godotai"              # الاسم المقدَّم على الخادم

[model]
name = "godotai"
kind = "open_weight_deployment"                # | fine_tune | from_scratch | vendor_api
base_model = "Qwen/Qwen2.5-Coder-7B-Instruct"
base_license = "Apache-2.0"
adapter = ""                                   # اسم محوّل LoRA عند kind = "fine_tune"
owner = "you"
serving = "self_hosted"                        # | managed | vendor
reference_model = "claude-fable-5-1"           # الطرف الآخر الافتراضي في eval compare
```

قواعد `check_consistency` (تُرفض بخطأ إعداد، لا بتحذير): `provider = "anthropic"` أو عنوان API شركة (OpenAI، أو AI Gateway أمام
مزوّد `openai/`, `anthropic/`, …) يستلزم `kind = "vendor_api"` + `serving = "vendor"`؛ `fine_tune` يستلزم `adapter`؛ `from_scratch`
يستلزم `training_report` و`base_model = ""`; النموذج الخاص لا يُوجَّه إلى API شركة. المتغيرات البيئية المناظرة:
`GODOTAI_MODEL_NAME`, `GODOTAI_MODEL_KIND`, `GODOTAI_BASE_MODEL`, `GODOTAI_BASE_LICENSE`, `GODOTAI_ADAPTER`, `GODOTAI_SERVING`,
`GODOTAI_REFERENCE_MODEL`.

---

## English summary

The owner asked for an AI that is *theirs alone*, specialised in building any game on Godot Engine 4.7.2-stable, not a
vendor's model, and at least as strong in that domain as Claude Fable 5.1 at `max` effort. This repository answers
with a precise, enforced vocabulary (`[model].kind` in `godot.toml`, `godotai/model_identity.py`):

- **`open_weight_deployment`** — the default: `Qwen/Qwen2.5-Coder-7B-Instruct` (Apache-2.0 per its model card and
  LICENSE, read 2026-09-26) served privately by *you* (vLLM `--served-model-name godotai`, Ollama, llama.cpp). No
  vendor model or key at run time. The weights are third-party open weights — the UI says so and never calls them
  "wholly original".
- **`fine_tune`** — the same base plus *your* LoRA adapter trained on engine-verified Godot trajectories
  (`dataset extract` → `training/train_qlora.py` → vLLM `--enable-lora`). The pipeline exists and is tested offline;
  no adapter has been trained yet because the data comes from running the agent.
- **`from_scratch`** — pre-training from random initialisation. Honestly out of reach for an individual at
  competitive quality (trillions of tokens, thousands of GPU-hours or far more); the loader accepts this kind only
  with a `training_report`, so it cannot be claimed by editing a label.
- **`vendor_api`** — Claude / GPT behind a vendor API: optional, labelled "not yours", used only as a *reference*
  for `eval compare`. `provider = "anthropic"` is refused unless declared this way.

The Godot specialisation that is 100 % yours today lives in the harness: the ClassDB index generated from the pinned
binary, real-engine verification before any success claim, the version-pinned knowledge base, the plan gate, the
engine-judged eval bank and the verified-trajectory data flywheel. Legal notes: keep Apache-2.0 notices; other
sizes/families (e.g. Llama) carry different licences; vendor terms (Anthropic's included) restrict using outputs to
train competing models, so train on your own model's runs, and use vendor runs only as comparison references.

**No superiority claim is made anywhere.** The only permitted statement is the output of
`python3 -m godotai eval compare --candidate godotai --reference claude-fable-5-1`, computed from engine-scored
results on the *same* tasks: it prints `NO EVIDENCE` without common results, and flags fewer than three common
tasks as a task-specific observation rather than a general verdict. Live model runs, training and real
comparisons have **not** been performed in this repository yet; everything above is implemented and unit-tested
offline (see the tables in sections 4 and 5).
