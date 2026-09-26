# موقع للمحادثة مع ذكائك الاصطناعي — عبر Cloudflare Tunnel + Access

> **الحالة:** كل ما في هذا المجلد مكتوب ومفحوص **بلا اتصال** (dry-run + اختبارات وحدة). **لم يُنشر أي شيء** من هذا المستودع
> على الإنترنت، ولم يُجرَ أي طلب حي إلى Cloudflare، ولم يُستخدم أي مفتاح من المستخدم. النشر خطوة تنفّذها أنت من جهازك.

> 🔐 **قبل أي شيء:** أي API Token لُصق في محادثة (كما حدث أثناء تطوير هذا المشروع) يُعتبر **مكشوفًا** — أعد إنشاءه (*Roll*)
> من dash.cloudflare.com → My Profile → API Tokens، ثم أنشئ توكن جديد **بأقل صلاحيات** (الجدول أدناه) واستخدمه في الطرفية
> فقط. المستودع لا يحوي ولن يحوي أي مفتاح؛ `Account ID` ليس سرًّا لكنه أيضًا لا يُكتب في الملفات المرفوعة.

## ما الذي يحدث

```
المتصفح ──HTTPS──► Cloudflare (Access: تسجيل دخول بالبريد) ──Tunnel──► cloudflared (حاوية) ──► chat:8765 (يتحقق من JWT مرة أخرى)
                                                                                            └──► model:8000  (خادم نموذجك: vLLM/Ollama)
```

- لا يوجد منفذ مفتوح على خادمك: `cloudflared` يفتح اتصالًا **خارجًا** إلى Cloudflare فقط.
- **Cloudflare Access** يرفض أي زائر ليس في قائمة بريدك قبل أن يصل إلى خادمك (رمز لمرة واحدة على البريد افتراضيًا).
- صفحة المحادثة **تتحقق بنفسها** من توقيع Access JWT في كل طلب (`godotai/chat/access.py`)، كما توصي وثائق Cloudflare، فلو
  أُخطئ في إعداد النفق أو انكشف عنوان الخادم يبقى الرفض قائمًا. كما يُفعَّل *Protect with Access* في النفق نفسه (طبقة ثالثة).
- النموذج الذي تتكلم معه هو **خادمك أنت** (`OPENAI_BASE_URL=http://model:8000/v1`) — لا Claude ولا أي API لشركة أخرى في هذا المسار.

## المتطلبات

| ما تحتاجه | ملاحظات |
| --- | --- |
| نطاق (domain) مضاف إلى Cloudflare وخوادم أسمائه عند Cloudflare | مطلوب لـ `games.example.com`؛ Cloudflare يصدر شهادة TLS تلقائيًا |
| حساب Zero Trust (خطة Free تكفي حتى 50 مستخدمًا) واسم فريق `<team>.cloudflareaccess.com` | من one.dash.cloudflare.com |
| API Token بأقل صلاحيات: **Account → Access: Apps and Policies: Edit**، **Account → Cloudflare Tunnel: Edit**، **Zone → DNS: Edit** (للنطاق فقط) | يُستخدم مرة واحدة من طرفيتك ثم يمكن حذفه؛ ضع له TTL |
| خادم Linux يعمل دائمًا مع Docker (+ NVIDIA GPU للملفات `gpu-*`) | بلا GPU استخدم الملف `cpu` (Ollama) — بطيء لكنه يعمل |

## الخطوات

```bash
# 0) انظر ما سيُنفَّذ بلا أي اتصال (لا يحتاج توكن) — نفس الأمر يعمل في CI
python3 scripts/cloudflare_setup.py --dry-run --hostname games.example.com --team-domain myteam \
    --emails you@example.com --account-id <account id> --zone-id <zone id>

# 1) نفّذ فعليًا: ينشئ تطبيق Access + سياسة السماح لبريدك، ثم النفق، ثم مسار النفق، ثم سجل DNS —
#    ويكتب deploy/cloudflare/.env (صلاحيات 0600) وفيه TUNNEL_TOKEN الذي لا يُطبع أبدًا
export CLOUDFLARE_API_TOKEN=<التوكن الجديد>          # في هذه الطرفية فقط
python3 scripts/cloudflare_setup.py --hostname games.example.com --team-domain myteam \
    --emails you@example.com --account-id <account id> --zone-id <zone id> --write-env deploy/cloudflare/.env
unset CLOUDFLARE_API_TOKEN

# 2) أضف إعدادات النموذج إلى .env (انظر env.example) ثم شغّل الحاويات — اختر ملفًا واحدًا للنموذج
docker compose -f deploy/cloudflare/compose.yml --profile gpu-base up -d       # vLLM + Qwen2.5-Coder-7B (Apache-2.0)
#   أو --profile gpu-lora  (النموذج الأساسي + محوّل LoRA الذي دربته في training/)
#   أو --profile cpu       ثم: docker compose -f deploy/cloudflare/compose.yml exec ollama ollama pull qwen2.5-coder:7b

# 3) افتح https://games.example.com/ — تصلك صفحة تسجيل دخول Cloudflare، تكتب بريدك، يصلك رمز، ثم تظهر صفحة المحادثة
```

## كيف تتأكد أن الحماية تعمل

- `curl -I https://games.example.com/` من أي جهاز → **302** إلى `<team>.cloudflareaccess.com` (لم تصل الطلبات إلى خادمك أصلًا).
- من داخل الشبكة نفسها: `docker compose -f deploy/cloudflare/compose.yml exec cloudflared wget -qO- http://chat:8765/api/status`
  → **401 Cloudflare Access: no Cloudflare Access token** — أي طلب يتجاوز Cloudflare يُرفض من الخادم نفسه.
- `docker compose -f deploy/cloudflare/compose.yml logs chat` يطبع عند التشغيل: `🔐 Cloudflare Access required on every request — team …, aud ……`
- في الصفحة: شارة **«🔐 موقع عام محمي بـ Cloudflare Access»** وبريدك في الحالة، وبطاقة هوية النموذج تقول بالضبط ما هو النموذج الذي يخدمك.

## ما لا يفعله هذا المجلد (بصراحة)

- لا ينشر شيئًا نيابة عنك ولا يخزّن أي توكن؛ وما لم تنفّذ الخطوات بنفسك فلا يوجد موقع.
- لا يجعل النموذج أذكى: الموقع هو *باب* إلى نفس الوكيل ونفس خادم النموذج. جودة النموذج تُقاس بـ `python3 -m godotai eval compare`
  وليس بالوعود (راجع `docs/MODEL.md`).
- لم يُجرَّب مع GPU/Docker حقيقيين هنا (لا Docker في بيئة التطوير) — ملف compose مفحوص بنيويًا فقط (`tests/test_deploy_files.py`).
- كلفة التشغيل عليك: خادم دائم + GPU (أو صبر مع CPU). Cloudflare Tunnel وAccess (≤ 50 مستخدمًا) مجانيان حاليًا حسب صفحات الأسعار (2026-09-26).

---

## English summary

`scripts/cloudflare_setup.py` makes four Cloudflare API calls in the order the docs recommend (Access application with an
e-mail allow-policy **first**, then the remotely-managed tunnel, its ingress with *Protect with Access*, then the proxied
CNAME) and writes `deploy/cloudflare/.env` (mode 0600) with the tunnel token — which it never prints. `compose.yml` runs the
chat (no published port; `--public-host`, `--access-team-domain`, `--access-aud`), `cloudflared` (`TUNNEL_TOKEN` from the
env file) and exactly one model server profile (`gpu-base` vLLM + Qwen2.5-Coder-7B-Instruct, `gpu-lora` base + your
adapter, or `cpu` Ollama). The chat validates the Access JWT on every request itself (`godotai/chat/access.py`).

Nothing has been deployed by this repository; no live Cloudflare call was made; any token pasted into a chat must be rolled.
Token permissions needed once, from your own terminal: *Access: Apps and Policies: Edit*, *Cloudflare Tunnel: Edit*,
*DNS: Edit* (zone-scoped). Test offline with `--dry-run`; the unit tests drive the script with a fake transport.
