/* godotai chat UI — plain JS, no dependencies. Talks to the JSON/SSE API in godotai/chat/server.py. */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  };

  // ---------------------------------------------------------------- token (only when the server was started with one)
  const hashParams = new URLSearchParams(location.hash.replace(/^#/, ""));
  if (hashParams.get("token")) {
    sessionStorage.setItem("godotai_token", hashParams.get("token"));
    hashParams.delete("token");
    history.replaceState(null, "", location.pathname + (hashParams.toString() ? "#" + hashParams.toString() : ""));
  }
  const token = () => sessionStorage.getItem("godotai_token") || "";

  async function api(path, opts, retried) {
    opts = opts || {};
    const headers = { "Accept": "application/json" };
    if (token()) headers["Authorization"] = "Bearer " + token();
    const init = { method: opts.method || "GET", headers, credentials: "same-origin" };
    if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (_) { data = { error: "bad response" }; }
    if (res.status === 401 && !retried) {
      const t = prompt("هذا الخادم يحتاج رمز دخول (token). الصقه هنا (طبعه الخادم عند التشغيل):");
      if (t) { sessionStorage.setItem("godotai_token", t.trim()); return api(path, opts, true); }
    }
    if (!res.ok) { const e = new Error(data.error || ("HTTP " + res.status)); e.status = res.status; e.hint = data.hint || ""; throw e; }
    return data;
  }

  // ---------------------------------------------------------------- minimal, safe markdown (plan / reports)
  function inline(s) {
    return s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
            .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
            .replace(/_([^_]+)_/g, "<em>$1</em>");
  }
  function escapeHtml(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function renderMd(text) {
    const out = []; let inList = false;
    for (const raw of (text || "").split("\n")) {
      const line = escapeHtml(raw);
      const m = /^(#{1,3})\s+(.*)$/.exec(line);
      const li = /^\s*[-*]\s+(.*)$/.exec(line);
      if (li) { if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${inline(li[1])}</li>`); continue; }
      if (inList) { out.push("</ul>"); inList = false; }
      if (m) out.push(`<h${m[1].length === 1 ? 4 : 5}>${inline(m[2])}</h${m[1].length === 1 ? 4 : 5}>`);
      else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
    if (inList) out.push("</ul>");
    return out.join("");
  }

  // ---------------------------------------------------------------- state
  const state = { status: null, sessions: [], current: null, es: null, lastId: 0, activity: null, planCard: null, snapshot: null };
  const EMPTY_HINT = $("empty-hint").cloneNode(true);   // template: the timeline is cleared on every project switch
  const STATUS_AR = { success: "✅ تمت اللعبة — المحرك قال PASS", failed: "❌ لم يمرّ التحقق بالمحرك", aborted: "⏹ توقّف التشغيل", refused: "🚫 النموذج رفض الطلب" };

  // ---------------------------------------------------------------- status / setup banner
  function chip(label, cls, title) { const c = el("span", "chip " + cls); c.title = title || ""; c.appendChild(el("span", "dot")); c.appendChild(el("span", "", label)); return c; }
  function renderStatus(st) {
    state.status = st;
    $("engine-tag").textContent = st.engine.pinned;
    $("version").textContent = st.version || "";
    $("games-dir").textContent = st.games_dir || "";
    const chips = $("chips"); chips.innerHTML = "";
    chips.appendChild(chip(st.engine.ok ? `Godot ${st.engine.version || st.engine.pinned}` : `Godot ${st.engine.pinned} غير مثبّت`, st.engine.ok ? "ok" : "bad", st.engine.binary || st.engine.error || ""));
    const m = st.model, id = m.identity || {};
    const modelLabel = m.private ? `نموذجك «${id.name || m.model}» · ${m.ready ? "الخادم يعمل" : "الخادم غير متاح"}`
                                 : `${m.provider}/${m.model} · effort=${m.effort}`;
    const modelTitle = m.private ? (m.base_url || "") + (m.server && m.server.models && m.server.models.length ? " · يقدّم: " + m.server.models.slice(0, 5).join(", ") : "")
                                 : (m.ready ? `مفتاح النموذج (${m.key_env || ""}) موجود` : `مفتاح النموذج (${m.key_env || ""}) مفقود`);
    chips.appendChild(chip(modelLabel, m.ready ? "ok" : "bad", modelTitle));
    // hosted free-allowance preset (godotai/presets.py): say where the server is and what the allowance really is
    if (m.preset) chips.appendChild(chip(`🆓 ${m.preset.label_ar}`, m.ready ? "ok" : "warn",
                                         `الخادم عند المزوّد، ليس خادمك. الحصة (قُرئت ${m.preset.verified}): ${m.preset.free_ar}\n\nالبيانات: ${m.preset.data_ar}`));
    if (m.private && m.server && m.server.model_listed === false) chips.appendChild(chip(`⚠ الاسم «${m.model}» غير موجود في الخادم`, "warn", "المتاح: " + m.server.models.join(", ") + " — اضبط GODOTAI_MODEL أو --served-model-name"));
    const hosting = st.hosting || (st.token_required ? "token" : "local");
    chips.appendChild(chip(hosting === "access" ? `🔐 موقع عام محمي بـ Cloudflare Access${st.public_hosts && st.public_hosts.length ? " · " + st.public_hosts[0] : ""}` : hosting === "token" ? "🔑 وصول برمز (token)" : "💻 محلي على جهازك فقط",
                           hosting === "local" ? "warn" : "ok",
                           hosting === "access" ? ("كل طلب يُتحقق من توقيع Cloudflare Access" + (st.access ? ` · ${st.access.team_domain}` : "") + (st.viewer && st.viewer.email ? ` · أنت: ${st.viewer.email}` : "")) : hosting === "token" ? "كل طلب يحتاج الرمز الذي طبعه الخادم" : "لا يوجد موقع عام؛ للنشر انظر deploy/cloudflare/"));
    chips.appendChild(chip(st.android.export_possible ? "تصدير APK محلي متاح" : "تصدير APK: غير مهيّأ", st.android.export_possible ? "ok" : "warn", "يحتاج قوالب التصدير + JDK 17 + Android SDK"));
    chips.appendChild(chip(st.github_token_set ? "GitHub متصل" : "GitHub غير متصل", st.github_token_set ? "ok" : "warn", "GITHUB_TOKEN لبناء APK على GitHub Actions"));
    renderIdentity(st);

    const setup = $("setup"); setup.innerHTML = ""; setup.className = "setup hidden";
    const blocks = [];
    if (!m.ready) blocks.push({ cls: "bad", title: m.private ? "① خادم نموذجك (إلزامي قبل إرسال أي رسالة)" : "① مفتاح النموذج (إلزامي قبل إرسال أي رسالة)", text: m.hint_ar });
    if (!st.engine.ok) blocks.push({ cls: "warn", title: "② محرك Godot (إلزامي حتى يمرّ التحقق ويُبنى شيء حقيقي)", text: st.engine.hint_ar });
    if (blocks.length) {
      setup.className = "setup " + blocks[0].cls;
      setup.appendChild(el("h3", "", "قبل أن تبدأ — الإعداد الناقص:"));
      for (const b of blocks) { setup.appendChild(el("p", "", b.title)); const pre = el("pre", "", b.text); setup.appendChild(pre); }
      const row = el("div", "row");
      row.appendChild(el("span", "dim", "بعد ضبط ما سبق أعد تشغيل الخادم ثم اضغط:"));
      const btn = el("button", "btn", "تحديث الحالة"); btn.type = "button"; btn.onclick = loadStatus; row.appendChild(btn);
      setup.appendChild(row);
    }
  }
  function renderIdentity(st) {
    const id = (st.model && st.model.identity) || null;
    const box = $("identity");
    if (!id) { box.classList.add("hidden"); return; }
    box.classList.remove("hidden");
    $("identity-name").textContent = id.name;
    $("identity-kind").textContent = id.kind_label_ar || id.kind;
    const yours = $("identity-yours");
    yours.textContent = id.kind === "vendor_api" ? "ليس نموذجك — مرجع للمقارنة" : (id.yours ? "ملكك: الأوزان والخادم تحت سيطرتك" : "أوزان مفتوحة عند مزوّد استضافة");
    yours.className = "badge " + (id.kind === "vendor_api" ? "bad" : (id.yours ? "ok" : "warn"));
    $("identity-text").textContent = id.disclosure_ar || "";
  }
  function renderHosting(st) {
    const note = $("hosting-note");
    const hosting = st.hosting || (st.token_required ? "token" : "local");
    if (hosting === "access") note.textContent = `موقع عام${st.public_hosts && st.public_hosts.length ? " على " + st.public_hosts.join(", ") : ""} خلف Cloudflare Tunnel + Access — كل طلب موقَّع ومُتحقَّق منه في الخادم.`;
    else if (hosting === "token") note.textContent = "الخادم يعمل خلف رمز وصول (token). لا تعرضه على الإنترنت بدون Cloudflare Access أو وكيل مُصادِق.";
    else note.textContent = "الخادم يعمل على جهازك فقط (لا يوجد موقع عام). للنشر الآمن: deploy/cloudflare/README.md. أوقفه بـ Ctrl+C في الطرفية.";
  }
  async function loadStatus() { try { const st = await api("/api/status"); renderStatus(st); renderHosting(st); } catch (e) { toast("تعذر الاتصال بالخادم: " + e.message, true); } }

  // ---------------------------------------------------------------- projects
  function renderProjects() {
    const ul = $("projects"); ul.innerHTML = "";
    if (!state.sessions.length) { const li = el("li", "hint", "لا توجد مشاريع بعد — أنشئ واحدًا بالأسفل."); ul.appendChild(li); }
    for (const s of state.sessions) {
      const li = el("li", s.id === state.current ? "active" : "");
      const b = el("button"); b.type = "button";
      b.appendChild(el("span", "mono", s.id));
      const meta = el("span", "meta", (s.state === "idle" ? "" : (s.state === "running" ? "⏳ يعمل" : "⏸ ينتظر موافقتك")) + (s.has_project ? " · مشروع" : " · فارغ") + ` · ${s.messages} رسالة`);
      b.appendChild(meta);
      b.onclick = () => selectSession(s.id);
      li.appendChild(b); ul.appendChild(li);
    }
  }
  async function loadSessions() {
    const d = await api("/api/sessions");
    state.sessions = d.sessions; renderProjects();
  }
  $("new-project").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("new-name").value.trim();
    try {
      const snap = await api("/api/sessions", { method: "POST", body: { name } });
      $("new-name").value = "";
      await loadSessions();
      selectSession(snap.id);
    } catch (e) { toast(e.message + (e.hint ? " — " + e.hint : ""), true); }
  });

  // ---------------------------------------------------------------- session view
  function setComposerEnabled(on) {
    $("message").disabled = !on; $("btn-send").disabled = !on;
    $("btn-verify").disabled = !on || !(state.snapshot && state.snapshot.has_project);
    $("btn-files").disabled = !state.current;
  }
  function setRunState(s) {
    const rs = $("run-state");
    rs.className = "state" + (s === "running" ? " running" : "");
    rs.textContent = s === "running" ? "الذكاء الاصطناعي يعمل…" : (s === "awaiting_approval" ? "⏸ ينتظر موافقتك على الخطة" : "");
    $("btn-cancel").disabled = s === "idle";
    setComposerEnabled(s === "idle");
    const cur = state.sessions.find((x) => x.id === state.current); if (cur) { cur.state = s; renderProjects(); }
  }

  async function selectSession(id) {
    if (state.es) { state.es.close(); state.es = null; }
    state.current = id; state.activity = null; state.planCard = null;
    location.hash = "p=" + id;
    renderProjects();
    const tl = $("timeline"); tl.innerHTML = "";
    try {
      const snap = await api(`/api/sessions/${id}`);
      state.snapshot = snap;
      $("session-title").textContent = "مشروع: " + snap.id;
      $("session-path").textContent = snap.workspace;
      if (!snap.history.length) tl.appendChild(EMPTY_HINT.cloneNode(true));
      for (const ev of snap.history) handleEvent(ev, true);
      state.lastId = snap.last_event_id;
      if (snap.pending_plan && snap.state === "awaiting_approval" && !state.planCard) {
        addPlanCard({ markdown: snap.pending_plan.markdown, auto: false });
      }
      if (state.planCard && snap.state === "awaiting_approval") enablePlanControls(true);
      setRunState(snap.state);
      subscribe(id, state.lastId);
    } catch (e) { toast(e.message, true); }
  }

  function subscribe(id, since) {
    const url = `/api/sessions/${id}/events?since=${since}` + (token() ? `&token=${encodeURIComponent(token())}` : "");
    const es = new EventSource(url);
    state.es = es;
    for (const type of ["user", "assistant", "system", "log", "tool", "progress", "plan", "approval", "done", "error"]) {
      es.addEventListener(type, (m) => { const ev = JSON.parse(m.data); if (ev.id <= state.lastId) return; state.lastId = ev.id; handleEvent(ev, false); });
    }
    es.onerror = () => { /* EventSource reconnects by itself with Last-Event-ID */ };
  }

  // ---------------------------------------------------------------- timeline rendering
  function scrollDown() { const tl = $("timeline"); tl.scrollTop = tl.scrollHeight; }
  function add(node) { const tl = $("timeline"); const empty = tl.querySelector(".empty"); if (empty) empty.remove(); tl.appendChild(node); scrollDown(); return node; }
  function bubble(role, text, label) { const m = el("div", "msg " + role); m.appendChild(el("span", "role", label)); m.appendChild(document.createTextNode(text)); return m; }
  function activity() {
    if (!state.activity) {
      const d = el("details", "activity"); d.open = true;
      d.appendChild(el("summary", "", "نشاط الوكيل (أدوات، فحص المحرك، تقدّم)"));
      state.activity = add(d);
    }
    return state.activity;
  }
  function activityLine(text, cls, preview) {
    const act = activity();
    act.appendChild(el("div", "line " + (cls || ""), text));
    if (preview) { const det = el("details"); det.appendChild(el("summary", "", "التفاصيل")); det.appendChild(el("pre", "", preview)); act.appendChild(det); }
    scrollDown();
  }

  function addPlanCard(ev) {
    const card = el("div", "card plan");
    const h = el("h3"); h.appendChild(document.createTextNode("📋 الخطة المقترحة")); const badge = el("span", "badge", ev.auto ? "موافقة تلقائية" : "تنتظر موافقتك"); h.appendChild(badge); card.appendChild(h);
    const md = el("div", "md"); md.innerHTML = renderMd(ev.markdown); card.appendChild(md);
    if (!ev.auto) {
      const actions = el("div", "actions");
      const ok = el("button", "btn ok", "✅ موافق — ابدأ التنفيذ"); ok.type = "button";
      const no = el("button", "btn danger", "✏️ عدّل الخطة"); no.type = "button";
      const fb = el("textarea"); fb.rows = 2; fb.placeholder = "اكتب ما تريد تغييره في الخطة…"; fb.className = "hidden";
      const send = el("button", "btn", "إرسال التعديل"); send.type = "button"; send.className = "btn hidden";
      ok.onclick = () => decide(true, "");
      no.onclick = () => { fb.classList.remove("hidden"); send.classList.remove("hidden"); fb.focus(); };
      send.onclick = () => decide(false, fb.value.trim() || "revise the plan");
      actions.append(ok, no); card.appendChild(actions); card.appendChild(fb); card.appendChild(send);
      card._controls = [ok, no, send, fb];
    }
    card._badge = badge;
    state.planCard = add(card);
    return card;
  }
  function enablePlanControls(on) { if (state.planCard && state.planCard._controls) for (const c of state.planCard._controls) c.disabled = !on; }
  async function decide(approved, feedback) {
    try { await api(`/api/sessions/${state.current}/approve`, { method: "POST", body: { approved, feedback } }); enablePlanControls(false); }
    catch (e) { toast(e.message + (e.hint ? " — " + e.hint : ""), true); }
  }

  function addResult(ev) {
    const card = el("div", "card result " + ev.status);
    const h = el("h3", "", STATUS_AR[ev.status] || ev.status); card.appendChild(h);
    card.appendChild(el("p", "", ev.message || ""));
    const usage = ev.usage || {}; const toks = Object.entries(usage).filter(([k, v]) => typeof v === "number" && /tokens/.test(k)).map(([k, v]) => `${k}=${v}`).join(" · ");
    card.appendChild(el("p", "dim", `دورات: ${ev.iterations || 0}${toks ? " · " + toks : ""}${ev.log_path ? " · السجل: " + ev.log_path : ""}`));
    if (ev.has_project) card.appendChild(el("p", "dim", "مكان اللعبة على جهازك: " + (state.snapshot ? state.snapshot.workspace : "")));
    const actions = el("div", "actions");
    if (ev.plan_path) { const b = el("button", "btn", "عرض الخطة"); b.type = "button"; b.onclick = () => viewFile(ev.plan_path); actions.appendChild(b); }
    if (ev.verification_path) { const b = el("button", "btn", "عرض تقرير المحرك"); b.type = "button"; b.onclick = () => viewFile(ev.verification_path); actions.appendChild(b); }
    if (ev.has_project) { const b = el("button", "btn", "ملفات اللعبة"); b.type = "button"; b.onclick = showFiles; actions.appendChild(b); }
    card.appendChild(actions);
    if (ev.status === "success" && ev.has_project) {
      card.appendChild(el("p", "hint", "للحصول على APK: اكتب في الرسالة التالية «صدّر APK» (يحتاج قوالب التصدير + JDK 17 + Android SDK، أو GITHUB_TOKEN لبنائه على GitHub Actions)، أو من الطرفية: python3 -m godotai export --project " + (state.snapshot ? state.snapshot.workspace : "<المجلد>")));
    }
    if (state.snapshot) state.snapshot.has_project = !!ev.has_project;
    add(card);
  }

  function handleEvent(ev, replay) {
    switch (ev.type) {
      case "user": state.activity = null; state.planCard = null; add(bubble("user", ev.text, "أنت")); break;
      case "assistant": add(bubble("assistant", ev.text, "godotai")); break;
      case "system":
        add(el("div", "note", ev.text_ar || ev.text));
        if (ev.markdown) { const c = el("div", "card"); c.appendChild(el("h3", "", "تقرير المحرك")); const md = el("div", "md"); md.innerHTML = renderMd(ev.markdown); c.appendChild(md); add(c); }
        break;
      case "log": activityLine(ev.text, ""); break;
      case "progress": activityLine("◦ " + ev.text, "progress"); break;
      case "tool": activityLine(`${ev.ok ? "✅" : "❌"} ${ev.name}(${ev.args})${ev.step_id ? " [" + ev.step_id + "]" : ""}`, ev.ok ? "ok" : "bad", (!ev.ok || /godot_verify|godot_check_script/.test(ev.name)) ? ev.preview : ""); break;
      case "plan": addPlanCard(ev); if (!replay && !ev.auto) { setRunState("awaiting_approval"); enablePlanControls(true); } else if (replay) enablePlanControls(false); break;
      case "approval":
        if (state.planCard) { state.planCard._badge.textContent = ev.approved ? "✅ تمت الموافقة" : "✏️ طُلب تعديل: " + (ev.feedback || ""); enablePlanControls(false); }
        if (!replay) setRunState("running");
        break;
      case "done": addResult(ev); if (!replay) { setRunState("idle"); loadSessions(); } break;
      case "error": { const m = bubble("error", ev.message, "خطأ"); if (ev.hint_ar) m.appendChild(el("div", "hint", ev.hint_ar)); add(m); if (!replay) setRunState("idle"); break; }
      default: break;
    }
  }

  // ---------------------------------------------------------------- composer / actions
  $("composer").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.current) { toast("اختر مشروعًا أو أنشئ واحدًا أولًا.", true); return; }
    const text = $("message").value.trim();
    if (!text) return;
    try {
      setRunState("running");
      await api(`/api/sessions/${state.current}/messages`, { method: "POST", body: { text, auto_approve: $("auto-approve").checked, plan_only: $("plan-only").checked } });
      $("message").value = "";
    } catch (e) {
      setRunState("idle");
      const m = bubble("error", e.message, "لم تُرسل الرسالة"); if (e.hint) m.appendChild(el("div", "hint", e.hint)); add(m);
      if (e.status === 503) loadStatus();
    }
  });
  $("message").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("composer").requestSubmit(); } });
  // quick prompts: fill the box (never auto-send — the user reads, edits, then presses send)
  for (const b of document.querySelectorAll(".quick-btn")) {
    b.addEventListener("click", () => {
      if (!state.current) { toast("اختر مشروعًا أو أنشئ واحدًا أولًا، ثم اضغط الطلب الجاهز.", true); return; }
      const box = $("message");
      if (box.disabled) { toast("انتظر انتهاء الطلب الجاري.", true); return; }
      box.value = b.dataset.prompt || "";
      box.focus();
    });
  }
  $("btn-cancel").addEventListener("click", async () => { try { await api(`/api/sessions/${state.current}/cancel`, { method: "POST", body: {} }); } catch (e) { toast(e.message, true); } });
  $("btn-verify").addEventListener("click", async () => {
    $("btn-verify").disabled = true;
    try { const r = await api(`/api/sessions/${state.current}/verify`, { method: "POST", body: {} }); toast(r.passed ? "✅ المحرك: PASS" : "❌ المحرك: FAIL — التقرير في المحادثة", !r.passed); }
    catch (e) { toast(e.message + (e.hint ? " — " + e.hint : ""), true); }
    finally { $("btn-verify").disabled = false; }
  });
  $("btn-files").addEventListener("click", showFiles);

  async function viewFile(path) {
    try {
      const f = await api(`/api/sessions/${state.current}/files?path=${encodeURIComponent(path)}`);
      $("viewer-title").textContent = f.path; $("viewer-body").textContent = f.content; $("viewer").showModal();
    } catch (e) { toast(e.message, true); }
  }
  async function showFiles() {
    try {
      const d = await api(`/api/sessions/${state.current}/files`);
      $("viewer-title").textContent = "ملفات " + state.current + ` (${d.files.length})`;
      const body = $("viewer-body"); body.textContent = "";
      if (!d.files.length) body.textContent = "(لا ملفات بعد)";
      const ul = el("ul", "file-list");
      for (const f of d.files) { const li = el("li"); const b = el("button", "", f); b.type = "button"; b.onclick = () => viewFile(f); li.appendChild(b); ul.appendChild(li); }
      body.appendChild(ul); $("viewer").showModal();
    } catch (e) { toast(e.message, true); }
  }
  $("viewer-close").addEventListener("click", () => $("viewer").close());

  function toast(text, bad) { const n = el("div", "note", text); if (bad) n.style.color = "#f0a3a3"; add(n); }

  // ---------------------------------------------------------------- boot
  (async function boot() {
    await loadStatus();
    try {
      await loadSessions();
      const wanted = hashParams.get("p");
      if (wanted && state.sessions.some((s) => s.id === wanted)) selectSession(wanted);
      else if (state.sessions.length === 1) selectSession(state.sessions[0].id);
    } catch (e) { toast("تعذر تحميل المشاريع: " + e.message, true); }
    if (state.status && state.status.auto_approve_default) $("auto-approve").checked = true;
  })();
})();
