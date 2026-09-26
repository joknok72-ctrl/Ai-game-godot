# موقع للمحادثة مع ذكائك الاصطناعي — عبر Cloudflare Tunnel + Access

> **الحالة:** كل ما في هذا المجلد مكتوب ومفحوص **بلا اتصال** (dry-run + اختبارات وحدة بناقل مزيّف). **لم يُنشر أي شيء** من هذا
> المستودع على الإنترنت، ولم يُجرَ أي طلب حي إلى Cloudflare، ولم يُستخدم أي مفتاح من المستخدم. النشر خطوة تنفّذها أنت بحسابك —
> من جهازك (الطريقة 1) أو من GitHub Actions بسرّ محمي (الطريقة 2) — والخادم الذي يشغّل الموقع هو جهازك أنت.

> 🔐 **قبل أي شيء:** أي API Token لُصق في محادثة (كما حدث أثناء تطوير هذا المشروع — أكثر من مرة) يُعتبر **مكشوفًا** حتى لو لم
> يُستخدم. من dash.cloudflare.com → My Profile → **API Tokens** → القائمة (⋯) بجانب التوكن → **Roll** (يُبطل القديم ويُصدر جديدًا بنفس
> الصلاحيات) أو **Delete**. ثم أنشئ توكن جديد **بأقل صلاحيات** (الجدول أدناه) واستخدمه في الطرفية أو في سرّ GitHub محمي فقط.
> المستودع لا يحوي ولن يحوي أي مفتاح؛ `Account ID` ليس سرًّا لكنه أيضًا لا يُكتب في الملفات المرفوعة.

## ما الذي يحدث

```
المتصفح ──HTTPS──► Cloudflare (Access: تسجيل دخول بالبريد) ──Tunnel──► cloudflared (حاوية) ──► chat:8765 (يتحقق من JWT مرة أخرى + حصة تشغيل)
                                                                                            └──► model:8000  (خادم نموذجك: vLLM/Ollama)
                                                                                                 أو Workers AI (المسار B — نموذج مفتوح الوزن تستضيفه Cloudflare)
```

- لا يوجد منفذ مفتوح على خادمك: `cloudflared` يفتح اتصالًا **خارجًا** إلى Cloudflare فقط.
- **Cloudflare Access** يرفض أي زائر ليس في قائمة بريدك قبل أن يصل إلى خادمك (رمز لمرة واحدة على البريد افتراضيًا).
- صفحة المحادثة **تتحقق بنفسها** من توقيع Access JWT في كل طلب (`godotai/chat/access.py`)، كما توصي وثائق Cloudflare (ترويسة
  `Cf-Access-Jwt-Assertion` أولًا، ومفاتيح `/cdn-cgi/access/certs` التي تتبدّل كل ~6 أسابيع مع بقاء المفتاح السابق صالحًا ~7 أيام —
  الخادم يعيد تحميلها عند `kid` مجهول). ويُفعَّل *Protect with Access* في النفق نفسه (طبقة ثالثة).
- **حصة تشغيل** (`godotai/chat/quota.py`): افتراضيًا تشغيل واحد في الوقت نفسه على الخادم كله، و40 طلب بناء لكل بريد كل 24 ساعة؛
  ما يزيد يُرفض بـ **429 + Retry-After** قبل أن يلمس النموذج. هذا هو المكبح الثاني بعد Access ضد الاستهلاك والإساءة (0 = بلا حد).
- النموذج الذي تتكلم معه هو **خادمك أنت** (`OPENAI_BASE_URL=http://model:8000/v1`) — لا Claude ولا أي API لشركة أخرى في هذا المسار.
  (المسار B يستبدل خادمك بنموذج مفتوح الوزن تستضيفه Cloudflare؛ الواجهة تسمّيه حينها «عند مزوّد استضافة» لا «على خادمك».)

## المتطلبات — وما الذي *لا* تعطيه Cloudflare

| ما تحتاجه | ملاحظات (وثائق Cloudflare، قُرئت 2026-09-26) |
| --- | --- |
| **نطاق (domain)** مضاف إلى Cloudflare وخوادم أسمائه عند Cloudflare | **شرط** للنفق المسمّى + Access على `games.example.com`؛ Cloudflare يصدر شهادة TLS تلقائيًا. بلا نطاق لا يوجد إلا «النفق السريع» المؤقت (أدناه) |
| حساب Zero Trust (خطة Free حتى 50 مستخدمًا) واسم فريق `<team>.cloudflareaccess.com` | من one.dash.cloudflare.com |
| API Token بأقل صلاحيات: **Account → Access: Apps and Policies: Edit**، **Account → Cloudflare Tunnel: Edit**، **Zone → DNS: Edit** (للنطاق فقط) | يُستخدم مرة واحدة ثم يُحذف؛ ضع له TTL. (صفحة مرجع الصلاحيات تسمّيها `Access: Apps and Policies Write` / `Cloudflare Tunnel Write` / `DNS Write` — نفس المعنى) |
| **جهاز Linux يعمل دائمًا مع Docker** (+ NVIDIA GPU للملفات `gpu-*`) | Cloudflare **لا تشغّل** هذا الخادم نيابةً عنك: النفق يوصل إلى جهاز *موجود*. بلا GPU: الملف `cpu` (Ollama، بطيء) أو المسار B |
| نموذج | افتراضيًا `Qwen/Qwen2.5-Coder-7B-Instruct` (Apache-2.0) على vLLM. قدرته على قيادة حلقة الأدوات هنا **غير مقاسة** — قِسها بـ `python3 -m godotai eval compare` |

**ما لا يوجد بعد:** لا خادم، لا نطاق، لا حساب Zero Trust، لا توكن سليم — كلها خطوات لك. عنوان `*.trycloudflare.com` المؤقت ليس موقعًا عامًّا
دائمًا (انظر «النفق السريع»).

## المسارات الممكنة (اختر واحدًا)

| | A — خادمك + GPU (الافتراضي) | B — خادم صغير + Workers AI | C — Cloudflare Workers/Containers (غير منفَّذ) | نفق سريع مؤقت |
| --- | --- | --- | --- | --- |
| أين يعمل الـ chat؟ | جهازك الدائم (Docker) | جهازك الدائم أو VPS صغير (Docker) | حاوية عند Cloudflare | جهازك، الآن فقط |
| أين النموذج؟ | vLLM/Ollama عندك — **خادمك أنت** | نموذج مفتوح الوزن **تستضيفه Cloudflare** («managed») | Workers AI | كما A أو B |
| نطاق مطلوب؟ | نعم | نعم | لا للـ Worker نفسه (Access يحمي `workers.dev`)، نعم للنفق | **لا** |
| الكلفة | خادم + GPU عليك؛ Tunnel وAccess (≤50) مجانيان | Workers AI: 10,000 Neuron/يوم مجانًا ثم $0.011 لكل 1,000 Neuron على Workers Paid ($5/شهر)؛ توليد النص 300 طلب/دقيقة، والنماذج التي تتطلب Workers Paid 20/دقيقة (50 مع رصيد AI Gateway مسبق) | Workers Paid $5/شهر + استهلاك؛ حتى 4 vCPU / 12 GiB / 20 GB لكل حاوية؛ **لا GPU** | مجاني |
| هوية النموذج في الواجهة | «على خادمك أنت» | «عند مزوّد استضافة (الأوزان مفتوحة)» | كما B | كما A/B |
| الحالة هنا | مكتوب ومفحوص بلا اتصال | متغيرات compose مجهّزة (`GODOTAI_ROUTE=workers_ai`)؛ بلا طلب حي | **غير منفَّذ** — فكرة موثّقة فقط | أمر واحد؛ للتجربة لا للنشر |

- **A** هو ما يطلبه المشروع: نموذجك على خادمك. **B** لمن لا يملك GPU: الـ chat + `cloudflared` على أي جهاز دائم، والنموذج عند Cloudflare
  بتوكن لا يستطيع إلا تشغيل Workers AI (`CF_WORKERS_AI_TOKEN`) — ليس «خادمك» لكنه أيضًا ليس Claude. أي نموذج من كتالوج Workers AI
  (Qwen، GLM، gpt-oss…) يجب **قياسه** بحلقة الأدوات قبل الاعتماد عليه؛ لا نفترض أنه بمستوى النموذج المرجعي.
- **C**: Containers أصبحت متاحة عمومًا في 2026-04-13، لكنها بلا GPU، وصورة هذا المشروع (Godot + Android SDK) ثقيلة؛ لم تُجرَّب ولم تُنفَّذ.
- **النفق السريع**: `cloudflared tunnel --url http://127.0.0.1:8765` يعطي عنوان `*.trycloudflare.com` عشوائيًا بلا حساب ولا DNS —
  توثّقه Cloudflare **للاختبار والتطوير فقط**: بلا ضمان تشغيل، حد 200 طلب متزامن (ما يزيد → 429)، **بلا Access**، ويزول مع إغلاق
  الأمر. لو استعملته، شغّل الـ chat بـ `--token` (رابط `#token=`) على الأقل — ولا تعتبره موقعًا عامًّا.

## الخطوات — الطريقة 1: من جهازك

```bash
# 0) انظر ما سيُنفَّذ بلا أي اتصال (لا يحتاج توكن) — نفس الأمر يعمل في CI
python3 scripts/cloudflare_setup.py --dry-run --hostname games.example.com --team-domain myteam \
    --emails you@example.com --account-id <account id> --zone-id <zone id>

# 1) نفّذ فعليًا: يتحقق أولًا أن التوكن فعّال (GET /user/tokens/verify — قراءة فقط)، ثم يبحث عن تطبيق Access/نفق/سجل CNAME
#    بالاسم نفسه ويعيد استخدامه إن وُجد (تشغيل ثانٍ لا يكرّر شيئًا)، ثم ينشئ الناقص بالترتيب: Access → النفق → مسار النفق → DNS —
#    ويكتب deploy/cloudflare/.env (صلاحيات 0600) وفيه TUNNEL_TOKEN الذي لا يُطبع أبدًا
export CLOUDFLARE_API_TOKEN=<التوكن الجديد>          # في هذه الطرفية فقط
python3 scripts/cloudflare_setup.py --hostname games.example.com --team-domain myteam \
    --emails you@example.com --account-id <account id> --zone-id <zone id> --write-env deploy/cloudflare/.env
unset CLOUDFLARE_API_TOKEN                            # ثم احذف التوكن من لوحة Cloudflare — لم تعد تحتاجه

# 2) أضف إعدادات النموذج إلى .env (انظر env.example) ثم شغّل الحاويات — اختر ملفًا واحدًا للنموذج
docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d       # vLLM + Qwen2.5-Coder-7B (Apache-2.0)
#   أو --profile gpu-lora  (النموذج الأساسي + محوّل LoRA الذي دربته في training/)
#   أو --profile cpu       ثم: docker compose -f deploy/cloudflare/compose.yml exec ollama ollama pull qwen2.5-coder:7b
#   أو (المسار B) بلا --profile مع GODOTAI_ROUTE=workers_ai GODOTAI_SERVING=managed CF_WORKERS_AI_TOKEN=… في .env

# 3) افتح https://games.example.com/ — تصلك صفحة تسجيل دخول Cloudflare، تكتب بريدك، يصلك رمز، ثم تظهر صفحة المحادثة
```

## الخطوات — الطريقة 2: من GitHub Actions (التوكن لا يلمس جهازك)

`.github/workflows/cloudflare-provision.yml` يدوي فقط (`workflow_dispatch`)، ولا يعمل عند أي push. قبل أول تشغيل:

1. GitHub → Settings → **Environments** → أنشئ بيئة باسم `cloudflare` بالضبط → *Required reviewers* (أنت) و*Deployment branches*: `main` فقط.
2. في البيئة نفسها أضف الأسرار: `CLOUDFLARE_API_TOKEN` (توكن **جديد** بالصلاحيات الثلاث أعلاه)، `CF_ACCOUNT_ID`، `CF_ZONE_ID`،
   `GODOTAI_ACCESS_EMAILS` (بريدك، مفصولًا بفواصل). كلها أسرار كي يحجبها سجل التشغيل — المستودع عام.
3. Actions → *Cloudflare: provision website* → Run workflow → اسم المضيف + اسم الفريق. اترك *dry run* مفعّلًا أول مرة (يطبع الخطة بلا توكن)،
   ثم أعد التشغيل بلا dry run.
4. لا يحتفظ المشغّل بتوكن النفق أبدًا (`--discard-tunnel-token`). خذه أنت من اللوحة: **Networking → Tunnels → godotai-chat → Configure**،
   وضعه في `deploy/cloudflare/.env` على خادمك مع قيم `GODOTAI_PUBLIC_HOST` / `GODOTAI_ACCESS_TEAM_DOMAIN` / `GODOTAI_ACCESS_AUD` من ملخص المهمة
   (معرّفات لا أسرار)، ثم `docker compose … up -d` كما في الطريقة 1.

## كيف تتأكد أن الحماية تعمل

- `curl -I https://games.example.com/` من أي جهاز → **302** إلى `<team>.cloudflareaccess.com` (لم تصل الطلبات إلى خادمك أصلًا).
- من داخل الشبكة نفسها: `docker compose -f deploy/cloudflare/compose.yml exec cloudflared wget -qO- http://chat:8765/api/status`
  → **401 Cloudflare Access: no Cloudflare Access token** — أي طلب يتجاوز Cloudflare يُرفض من الخادم نفسه.
- `docker compose -f deploy/cloudflare/compose.yml logs chat` يطبع عند التشغيل: `🔐 Cloudflare Access required on every request — team …, aud ……`
  و`⏱ run quota: max 1 concurrent run(s), 40 run(s) per visitor per 24 h`.
- في الصفحة: شارة **«🔐 موقع عام محمي بـ Cloudflare Access»** وبريدك في الحالة، وبطاقة هوية النموذج تقول بالضبط ما هو النموذج الذي يخدمك.
- أرسل طلبين متزامنين من نافذتين → الثاني يتلقى **429** ورسالة «الخادم مشغول» — هذه الحصة تعمل.

## طبقات الحماية من الإساءة والكلفة (رتّبها هكذا)

1. **Access allow-list** — البريد فقط؛ الجلسة 24 ساعة افتراضيًا (`--session-duration`).
2. **حصة التشغيل في الخادم** — `GODOTAI_MAX_CONCURRENT_RUNS` / `GODOTAI_MAX_RUNS_PER_DAY` في `.env` (تصل إلى `python3 -m godotai chat
   --max-concurrent-runs/--max-runs-per-day`). تبقى فعّالة حتى لو تجاوز أحد Cloudflare.
3. **WAF Rate limiting** على `/api/` (Security → WAF → Rate limiting rules): خطة Free تعطي **قاعدة واحدة**، عدّ حسب IP، فترة 10 ثوانٍ —
   مكبح خشن جيد، لكنه لا يعرف بريد Access ولا يُعدّ دقيقًا.
4. **للمسار B فقط**: AI Gateway أمام Workers AI يضيف حدود طلبات (نافذة ثابتة/منزلقة → 429) و**حدود إنفاق بالدولار** (Spend limits،
   وُثّقت 2026-09-09؛ حتى 20 قاعدة لكل بوابة؛ التطبيق «متّسق في النهاية» فقد تتجاوز دفعة قصيرة الحد قليلًا). يحتاج إضافة ترويسة
   `cf-aig-gateway-id` أو مسار البوابة — غير موصول في الكود بعد.
5. لا تنشر منفذ خادم النموذج أبدًا؛ compose يعرضه لحاوية الـ chat فقط.

## حدود يجب أن تعرفها (2026-09-26)

- **مهلة Cloudflare للردّ من خادمك: 125 ثانية** (خطأ 524) — ليست 100 كما في وثائق أقدم. بثّ SSE في الصفحة يرسل الترويسات فورًا
  و`: keepalive` كل 15 ثانية فلا يسقط أثناء عمل النموذج. أما `POST /api/sessions/<id>/verify` (تشغيل المحرك للتحقق) فطلب متزامن قد
  يتجاوز 125 ثانية على خادم بطيء — حد معروف؛ الحل المستقبلي جعله غير متزامن.
- Access Free: ≤ 50 مستخدمًا. النفق السريع: 200 طلب متزامن، بلا SLA.
- Workers AI: 10,000 Neuron/يوم مجانًا؛ توليد النص 300 طلب/دقيقة، والنماذج التي تتطلب Workers Paid (مثل الحدودية الجديدة) 20 طلب/دقيقة لكل نموذج (50 مع رصيد مسبق عبر AI Gateway)؛ نقطة النهاية المتوافقة مع OpenAI هي
  `…/accounts/<account>/ai/v1/chat/completions` بترويسة Bearer.
- مفاتيح توقيع Access تتبدّل كل ~6 أسابيع؛ الخادم يتعامل مع ذلك تلقائيًا.
- لا يضيف `cloudflared` حماية للمنافذ الأخرى على جهازك — لا تفتحها.

## ما لا يفعله هذا المجلد (بصراحة)

- لا ينشر شيئًا نيابة عنك ولا يخزّن أي توكن؛ وما لم تنفّذ الخطوات بنفسك فلا يوجد موقع.
- لا يجعل النموذج أذكى: الموقع هو *باب* إلى نفس الوكيل ونفس خادم النموذج. جودة النموذج تُقاس بـ `python3 -m godotai eval compare`
  وليس بالوعود (راجع `docs/MODEL.md`). لا دليل هنا على أن نموذجك أفضل من أي نموذج آخر.
- لم يُجرَّب مع GPU/Docker حقيقيين هنا (لا Docker في بيئة التطوير) — ملف compose مفحوص بنيويًا فقط (`tests/test_deploy_files.py`)،
  والسكربت مفحوص بناقل مزيّف فقط (`tests/test_cloudflare_setup.py`, 33 اختبارًا). أسماء معاملات الاستعلام (`domain`, `name`,
  `is_deleted`, `type`) مأخوذة من مرجع API؛ أول تشغيل حي هو أول اختبار حقيقي لها — لذلك ابدأ بـ `--dry-run`.
- كلفة التشغيل عليك: خادم دائم + GPU (أو صبر مع CPU، أو Neurons في المسار B). Cloudflare Tunnel وAccess (≤ 50 مستخدمًا) مجانيان
  حاليًا حسب صفحات الأسعار (2026-09-26).

---

## English summary

`scripts/cloudflare_setup.py` first verifies the API token read-only (`GET /user/tokens/verify`), then looks for an existing
Access application (same hostname), tunnel (same name) and CNAME (same hostname) and **reuses** them — a second run
converges instead of failing with "already exists" (`--no-reuse-existing` to forbid that) — and creates what is missing in
the order the docs recommend (Access application with an e-mail allow-policy **first**, then the remotely-managed tunnel,
its ingress with *Protect with Access*, then the proxied CNAME). Locally it writes `deploy/cloudflare/.env` (mode 0600) with
the tunnel token, which it never prints; in the manual GitHub Actions workflow (`cloudflare-provision.yml`,
`workflow_dispatch` only, environment `cloudflare` with required reviewers) it runs with `--discard-tunnel-token` so a
public repository's log never holds a connector token — you copy it from Networking → Tunnels on the server.

`compose.yml` runs the chat (no published port; `--public-host`, `--access-team-domain`, `--access-aud`, a run quota of
1 concurrent / 40 per visitor per day → 429), `cloudflared` (`TUNNEL_TOKEN` from the env file) and exactly one model
server profile (`gpu-base` vLLM + Qwen2.5-Coder-7B-Instruct, `gpu-lora` base + your adapter, `cpu` Ollama) — or no profile
with `GODOTAI_ROUTE=workers_ai` + `CF_WORKERS_AI_TOKEN` (Workers AI, labelled *managed*, not your own server; 10k
Neurons/day free, then $0.011 per 1k Neurons; 300 req/min for text generation, 20 req/min — 50 with prepaid AI Gateway credits — for models that require Workers Paid). A named tunnel needs a domain on Cloudflare and an
always-on machine of yours; a Quick Tunnel (`*.trycloudflare.com`) needs neither but is documented by Cloudflare for
testing only (no SLA, 200 in-flight requests, no Access). Cloudflare Containers (GA 2026-04-13, no GPU) are a possible
future host for the chat container — not implemented. Cloudflare's origin response timeout is 125 s (524); the SSE stream
keeps alive every 15 s, the synchronous `/verify` route may exceed it on a slow origin.

Nothing has been deployed by this repository; no live Cloudflare call was made; any token pasted into a chat must be rolled.
Token permissions needed once, from your own terminal or a protected GitHub environment: *Access: Apps and Policies: Edit*,
*Cloudflare Tunnel: Edit*, *DNS: Edit* (zone-scoped). Test offline with `--dry-run`; the unit tests drive the script with
a fake transport (token check, lookups, reuse, update, discard mode, failure paths, no secret in any output).
