// Drive the chat page's real JavaScript in a DOM (jsdom) against the real HTTP server with the scripted model.
//
//   mkdir -p /tmp/ui-deps && (cd /tmp/ui-deps && npm init -y >/dev/null && npm install --no-audit --no-fund jsdom@24)
//   GODOTAI_UI_DEPS=/tmp/ui-deps node tests/ui/jsdom_smoke.mjs     # spawns tests/ui/serve_scripted.py itself
//
// jsdom is the only dependency and lives outside the repository tree (GODOTAI_UI_DEPS = a directory that holds
// node_modules/); without it the script exits 2 with instructions. Nothing is committed for it.
//
// Exit 0 only if ALL of: zero JS errors (jsdomError / window.onerror / unhandledrejection / console.error),
// the identity card renders honestly (names the third-party base, no superiority claim), the hosting chip says
// "local only", a project is created through the form, a quick prompt fills the box without auto-sending, and
// message → plan card → approve → tool events → engine PASS result → composer re-enabled.
// jsdom has neither fetch nor EventSource: both are routed to the live server through Node's http/fetch.
import { spawn } from "node:child_process";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const require = createRequire(process.env.GODOTAI_UI_DEPS ? path.join(process.env.GODOTAI_UI_DEPS, "/") : path.join(repo, "/"));
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require("jsdom")); }
catch (e) { console.error("jsdom is not installed — see the header of tests/ui/jsdom_smoke.mjs (npm install jsdom@24 in a directory, then GODOTAI_UI_DEPS=<that dir>)"); process.exit(2); }

// ---------------------------------------------------------------- server (scripted model, ephemeral port)
const server = spawn("python3", [path.join(here, "serve_scripted.py")],
                     { cwd: repo, env: { ...process.env, GODOTAI_SKIP_MODEL_PROBE: "1" }, stdio: ["pipe", "pipe", "inherit"] });
const port = await new Promise((resolve, reject) => {
  let buf = "";
  const timer = setTimeout(() => reject(new Error("server did not print its port within 30 s")), 30000);
  server.stdout.on("data", (d) => {
    buf += d;
    const nl = buf.indexOf("\n");
    if (nl >= 0) { clearTimeout(timer); resolve(JSON.parse(buf.slice(0, nl)).port); }
  });
  server.on("exit", (code) => reject(new Error("server exited early with code " + code)));
});
const base = `http://127.0.0.1:${port}`;
const stopServer = () => new Promise((resolve) => { server.once("exit", resolve); server.stdin.end(); setTimeout(() => server.kill(), 5000).unref(); });

// ---------------------------------------------------------------- browser shims
const jsErrors = [];
const consoleErrors = [];
const requests = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (e) => jsErrors.push(String(e && (e.stack || e.message || e))));
virtualConsole.on("error", (...a) => consoleErrors.push(a.map(String).join(" ")));
virtualConsole.on("warn", () => {});
virtualConsole.on("log", () => {});

function makeEventSource() {
  return class EventSource {
    constructor(url) {
      this.listeners = new Map();
      this.onerror = null;
      this.req = http.get(new URL(url, base).toString(), { headers: { Accept: "text/event-stream" } }, (res) => {
        if (res.statusCode !== 200) { this.onerror && this.onerror(new Error("HTTP " + res.statusCode)); return; }
        let buf = "", cur = {};
        res.setEncoding("utf8");
        res.on("data", (chunk) => {
          buf += chunk;
          let idx;
          while ((idx = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, idx).replace(/\r$/, "");
            buf = buf.slice(idx + 1);
            if (line.startsWith(":")) continue;
            if (line === "") {
              if (cur.data !== undefined) for (const fn of this.listeners.get(cur.event || "message") || []) fn({ data: cur.data, lastEventId: cur.id || "" });
              cur = {};
              continue;
            }
            const k = line.slice(0, line.indexOf(":"));
            const v = line.slice(line.indexOf(":") + 1).replace(/^ /, "");
            cur[k] = k === "data" && cur.data !== undefined ? cur.data + "\n" + v : v;
          }
        });
        res.on("error", (e) => this.onerror && this.onerror(e));
      });
      this.req.on("error", (e) => this.onerror && this.onerror(e));
    }
    addEventListener(type, fn) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(fn); }
    close() { this.req.destroy(); }
  };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(pred, what, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { try { if (pred()) return; } catch (_) {} await sleep(25); }
  throw new Error("timeout waiting for: " + what);
}
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail });

// ---------------------------------------------------------------- the flow, as a user would do it
const dom = await JSDOM.fromURL(base + "/", {
  runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole,
  beforeParse(window) {
    window.EventSource = makeEventSource();
    window.fetch = async (p, init) => {
      const entry = { method: (init && init.method) || "GET", path: String(p), status: "pending" };
      requests.push(entry);
      try { const r = await fetch(new URL(String(p), base).toString(), init); entry.status = r.status; return r; }
      catch (e) { entry.status = "ERR " + e; throw e; }
    };
    window.prompt = () => null;   // a token prompt would mean the page thinks it is not on a local server
    if (window.HTMLDialogElement) {
      window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
      window.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
    }
    window.addEventListener("error", (e) => jsErrors.push("window.onerror: " + ((e.error && e.error.stack) || e.message)));
    window.addEventListener("unhandledrejection", (e) => jsErrors.push("unhandledrejection: " + ((e.reason && e.reason.stack) || e.reason)));
  },
});
const { window } = dom;
const { document } = window;
const $ = (id) => document.getElementById(id);
const txt = (id) => ($(id) ? $(id).textContent : "");

try {
  await waitFor(() => $("chips") && $("chips").children.length >= 4, "status chips rendered");
  const chips = [...$("chips").children].map((c) => c.textContent);
  check("model chip: your private model, server up", chips.some((c) => c.includes("نموذجك") && c.includes("الخادم يعمل")), chips.join(" | "));
  check("hosting chip: local only", chips.some((c) => c.includes("محلي على جهازك")), chips.join(" | "));
  check("hosting note points to deploy/cloudflare", txt("hosting-note").includes("deploy/cloudflare"), txt("hosting-note"));

  await waitFor(() => !$("identity").classList.contains("hidden"), "identity card visible");
  check("identity: name", txt("identity-name").trim().length > 0, txt("identity-name"));
  check("identity: kind label", txt("identity-kind").trim().length > 0, txt("identity-kind"));
  check("identity: badge = yours (weights + server under your control)", txt("identity-yours").includes("ملكك"), txt("identity-yours"));
  check("identity: disclosure names the third-party base (Qwen), not 'from scratch'", /Qwen/i.test(txt("identity-text")), txt("identity-text").slice(0, 160));
  const claim = txt("identity-claim").trim();
  check("identity: no unverified superiority claim", !/أفضل من|أقوى من|better than|outperform/i.test(claim), claim || "(empty)");
  check("no missing-model setup banner (probe says the server is up)", $("setup").classList.contains("hidden") || !$("setup").textContent.includes("①"), $("setup").textContent.slice(0, 120));

  const quick = document.querySelectorAll(".quick-btn");
  check("quick prompts present (>= 6)", quick.length >= 6, String(quick.length));
  quick[0].click(); await sleep(50);
  check("quick prompt without a project leaves the box empty", $("message").value === "", $("message").value);

  $("new-name").value = "Flappy Test";
  $("new-project").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await waitFor(() => txt("session-title").includes("flappy-test"), "project selected after creation");
  check("project created and selected through the form", true);

  quick[0].click(); await sleep(50);
  check("quick prompt fills the composer with its data-prompt", $("message").value === quick[0].dataset.prompt && $("message").value.length > 20);
  check("quick prompt never auto-sends", $("run-state").textContent === "" && !$("message").disabled);

  $("message").value = "اعمل لعبة Flappy Bird للموبايل";
  $("composer").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  await waitFor(() => $("run-state").textContent.includes("ينتظر موافقتك"), "plan awaiting approval");
  const approve = document.querySelector("#timeline .card.plan .actions button.btn.ok");
  check("plan card with an enabled approve button", approve && !approve.disabled);
  check("plan markdown rendered in the timeline", $("timeline").textContent.includes("Plan"));
  check("composer locked while awaiting approval", $("message").disabled && $("btn-send").disabled);

  approve.click();
  await waitFor(() => /تمت اللعبة|لم يمرّ|توقّف|رفض/.test($("timeline").textContent), "run finished", 20000);
  check("approve → build → engine PASS result shown", $("timeline").textContent.includes("تمت اللعبة — المحرك قال PASS"), $("timeline").textContent.slice(-200));
  check("tool events rendered (write_file / godot_verify)", /write_file/.test($("timeline").textContent) && /godot_verify/.test($("timeline").textContent));
  await waitFor(() => $("run-state").textContent === "" && !$("message").disabled, "composer re-enabled after done");
  check("composer re-enabled after done", true);
  check("approve went to the server", requests.some((r) => r.method === "POST" && /\/approve$/.test(r.path) && r.status === 200), requests.map((r) => `${r.method} ${r.path} → ${r.status}`).join("; "));
} catch (e) {
  check("flow completed", false, String((e && e.stack) || e) + "\n  requests: " + requests.map((r) => `${r.method} ${r.path} → ${r.status}`).join("; ")
        + "\n  timeline tail: " + ($("timeline") ? $("timeline").textContent.slice(-300) : "(none)"));
} finally {
  await sleep(200);
  window.close();
  await stopServer();
}

check("zero JS errors (jsdomError / window.onerror / unhandledrejection)", jsErrors.length === 0, jsErrors.join("\n---\n"));
check("zero console.error", consoleErrors.length === 0, consoleErrors.join("\n"));

const failed = checks.filter((c) => !c.ok);
for (const c of checks) console.log(`${c.ok ? "PASS" : "FAIL"}  ${c.name}${c.ok || !c.detail ? "" : "  :: " + c.detail}`);
console.log(`\nui smoke: ${checks.length - failed.length}/${checks.length} checks passed; JS errors: ${jsErrors.length}`);
process.exit(failed.length ? 1 : 0);
