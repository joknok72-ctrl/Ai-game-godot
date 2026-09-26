"""godotai command line.

  python3 -m godotai doctor                          # check engine pin, templates, JDK, SDK, keys
  python3 -m godotai install-godot [--system]        # download pinned editor + templates (sha512 verified)
  python3 -m godotai setup-android                   # Android SDK + editor settings + debug keystore
  python3 -m godotai new --template mobile-2d --dest ./my-game --name "My Game"
  python3 -m godotai verify --project ./my-game      # engine-backed verification, no LLM
  python3 -m godotai export --project ./my-game --out build/game.apk [--release]
  python3 -m godotai plan "make a 2D endless runner" --workspace ./my-game
  python3 -m godotai run  "make a 2D endless runner" --workspace ./my-game [--yes]
  python3 -m godotai chat [--port 8765] [--games-dir ./games]   # browser chat UI → http://127.0.0.1:8765
  python3 -m godotai apiref build [--docs-dir godot/doc/classes] [--force]   # ClassDB index from the pinned binary
  python3 -m godotai apiref lookup CharacterBody2D move_and_slide             # exact signature
  python3 -m godotai apiref search "change scene"                              # name search
  python3 -m godotai apiref lint --project ./my-game                           # Godot-3 idioms / unknown classes
  python3 -m godotai eval list | run <task-id> --workspace DIR [--yes] | score --task <id> --project DIR
  python3 -m godotai dataset extract --runs ./my-game/.godotai/runs --out data/train.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError, load_config
from .godot import Godot, GodotNotFound, GodotVersionMismatch, editor_settings_path, export_templates_dir, find_godot_binary


def _cfg(args: argparse.Namespace) -> Config:
    try:
        return load_config(Path(args.config) if getattr(args, "config", None) else None)
    except ConfigError as exc:
        sys.exit(f"config error: {exc}")


# ----------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    eng = cfg.engine
    ok = True

    def row(label: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok &= good
        print(f"{'✅' if good else '❌'} {label}: {detail}")

    print(f"godotai {__version__} — pinned engine: Godot {eng.tag} ({eng.flavor}, {eng.platform})")
    print(f"config: {cfg.path}")
    binary = find_godot_binary(eng)
    if binary is None:
        row("editor binary", False, "not found (run: python3 -m godotai install-godot)")
    else:
        try:
            g = Godot(eng, binary)
            row("editor binary", True, f"{g.version()} at {binary}")
            row("export templates", g.templates_installed(), str(export_templates_dir(eng)))
        except GodotVersionMismatch as exc:
            row("editor binary", False, str(exc))
    row("pinned checksums", cfg.checksums_file().is_file(), str(cfg.checksums_file()))
    java = shutil.which("java")
    row("java (JDK %d needed for Android)" % cfg.android.jdk_major, java is not None, java or "not found")
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    row("ANDROID_HOME", bool(sdk and Path(sdk).is_dir()), sdk or "not set (python3 -m godotai setup-android)")
    es = editor_settings_path(eng)
    has_paths = es.is_file() and "export/android/android_sdk_path" in es.read_text(encoding="utf-8", errors="replace")
    row("editor settings for Android export", has_paths, str(es))
    from . import apiref
    idx = apiref.load_index(eng)
    print(f"{'✅' if idx else '➖'} API index (ClassDB of {eng.tag}): "
          + (f"{idx.stats()['classes']} classes at {idx.path}" if idx else
             f"not built yet — built on first api_lookup, or: python3 -m godotai apiref build"))
    print(f"{'✅' if shutil.which('gdlint') else '➖'} gdlint (optional): {shutil.which('gdlint') or 'not installed (pip install gdtoolkit)'}")
    key_env = "ANTHROPIC_API_KEY" if cfg.agent.provider == "anthropic" else "OPENAI_API_KEY"
    print(f"{'✅' if os.environ.get(key_env) else '➖'} {key_env}: {'set' if os.environ.get(key_env) else 'not set (needed for plan/run)'}")
    print(f"{'✅' if os.environ.get('GITHUB_TOKEN') else '➖'} GITHUB_TOKEN: {'set' if os.environ.get('GITHUB_TOKEN') else 'not set (needed for GitHub tools)'}")
    print(f"model: {cfg.agent.provider}/{cfg.agent.model} effort={cfg.agent.effort}")
    return 0 if ok else 1


def cmd_install(args: argparse.Namespace) -> int:
    from .install import InstallError, ensure_editor_initialised, install_godot
    cfg = _cfg(args)
    try:
        target = install_godot(cfg, bin_dir=Path(args.bin_dir) if args.bin_dir else None, system=args.system,
                               templates=not args.editor_only, force=args.force)
        ensure_editor_initialised(target)
    except InstallError as exc:
        sys.exit(f"install failed: {exc}")
    print(f"done: {target} --version → {Godot(cfg.engine, target).version()}")
    return 0


def cmd_setup_android(args: argparse.Namespace) -> int:
    from .android import AndroidSetupError, setup
    cfg = _cfg(args)
    try:
        info = setup(cfg, sdk=Path(args.sdk_root) if args.sdk_root else None, install_packages=not args.no_packages)
    except AndroidSetupError as exc:
        sys.exit(f"android setup failed: {exc}")
    print(json.dumps(info, indent=2))
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    from .scaffold import ScaffoldError, create_project, list_templates
    cfg = _cfg(args)
    if args.list:
        print("\n".join(list_templates(cfg)))
        return 0
    try:
        dest = create_project(cfg, args.template, Path(args.dest), args.name, args.package)
    except ScaffoldError as exc:
        sys.exit(str(exc))
    print(f"created {dest} from template {args.template}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .verify import verify_project
    cfg = _cfg(args)
    report = verify_project(Path(args.project), cfg, frames=args.frames, run_lint=not args.no_lint)
    print(report.to_markdown())
    if args.json:
        Path(args.json).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return 0 if report.passed else 1


def cmd_export(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        g = Godot(cfg.engine)
    except (GodotNotFound, GodotVersionMismatch) as exc:
        sys.exit(str(exc))
    if not g.templates_installed():
        sys.exit(f"export templates missing at {export_templates_dir(cfg.engine)} — run install-godot")
    project = Path(args.project).resolve()
    out = Path(args.out)
    if not out.is_absolute():
        out = project / out
    res = g.export(project, args.preset or cfg.android.preset_name, out, debug=not args.release)
    print(res.output[-4000:])
    if res.ok and out.is_file() and out.stat().st_size > 0:
        print(f"\n✅ exported {out} ({out.stat().st_size // 1024} KiB)")
        return 0
    print(f"\n❌ export failed (exit {res.returncode}, file exists: {out.is_file()})")
    return 1


def cmd_apiref(args: argparse.Namespace) -> int:
    from . import apiref
    cfg = _cfg(args)
    if args.action == "build":
        try:
            g = Godot(cfg.engine)
            idx = apiref.build_index(g, docs_dir=Path(args.docs_dir) if args.docs_dir else None, force=args.force)
        except (GodotNotFound, GodotVersionMismatch, apiref.ApiRefError) as exc:
            sys.exit(str(exc))
        st = idx.stats()
        print(f"API index for Godot {idx.engine_tag} ({idx.engine_version}) → {idx.path}")
        print(f"  {st['classes']} classes, {st['methods']} methods, {st['properties']} properties, "
              f"{st['signals']} signals, {st['constants']} constants; descriptions: {'yes' if idx.has_descriptions else 'no (ClassDB only)'}")
        return 0
    idx = apiref.load_index(cfg.engine)
    if idx is None:
        try:
            idx = apiref.build_index(Godot(cfg.engine))
        except (GodotNotFound, GodotVersionMismatch, apiref.ApiRefError) as exc:
            sys.exit(f"API index not built and cannot build it: {exc}")
    if args.action == "lookup":
        text = idx.render_class(args.class_name, args.member)
        print(text)
        return 1 if ("does not exist" in text or "has no member" in text) else 0
    if args.action == "search":
        hits = idx.search(args.query, args.limit)
        print("\n".join(hits) or "no matches")
        return 0 if hits else 1
    if args.action == "lint":
        findings = apiref.lint_project(Path(args.project), idx)
        lines = apiref.format_findings(findings, limit=500)
        print("\n".join(lines) or "api_lint: no findings")
        return 1 if any(f.severity == "error" for fs in findings.values() for f in fs) else 0
    return 2


def cmd_eval(args: argparse.Namespace) -> int:
    from . import evals
    cfg = _cfg(args)
    if args.action == "list":
        for t in evals.load_tasks(cfg.root):
            print(f"{t.id:24} {t.title}  [{', '.join(t.tags)}]")
        return 0
    task = evals.get_task(cfg.root, args.task)
    if task is None:
        sys.exit(f"unknown task {args.task!r}; see: python3 -m godotai eval list")
    if args.action == "score":
        result = evals.score_project(task, Path(args.project), cfg)
        print(result.to_markdown())
        return 0 if result.passed else 1
    if args.action == "run":
        from .agent import Agent
        from .providers import make_provider
        from .tools import build_registry
        provider = make_provider(cfg.agent)
        approve = (lambda p: (True, "")) if (args.yes or not cfg.agent.require_plan_approval) else _approve_interactive
        agent = Agent(cfg, Path(args.workspace), provider, build_registry(include_github=False), approve=approve,
                      on_text=lambda t: print(f"\n🤖 {t}\n"))
        result = evals.run_task(task, agent, cfg, results_dir=Path(args.results_dir) if args.results_dir else None)
        print(result.to_markdown())
        return 0 if result.passed else 1
    return 2


def cmd_dataset(args: argparse.Namespace) -> int:
    from . import dataset
    stats = dataset.extract(Path(args.runs), Path(args.out), include_failed=args.include_failed,
                            min_iterations=args.min_iterations)
    print(json.dumps(stats, indent=2))
    return 0 if stats["examples"] else 1


def _approve_interactive(plan) -> tuple[bool, str]:
    print("\n" + plan.to_markdown() + "\n")
    while True:
        ans = input("Approve this plan? [y]es / [n]o + feedback: ").strip()
        if ans.lower() in ("y", "yes"):
            return True, ""
        if ans.lower().startswith("n"):
            fb = input("Feedback for the planner: ").strip()
            return False, fb
        print("please answer y or n")


def cmd_run(args: argparse.Namespace, plan_only: bool = False) -> int:
    from .agent import Agent
    from .providers import make_provider
    from .tools import build_registry
    cfg = _cfg(args)
    provider = make_provider(cfg.agent)
    approve = (lambda p: (True, "")) if (args.yes or not cfg.agent.require_plan_approval) else _approve_interactive
    agent = Agent(cfg, Path(args.workspace), provider, build_registry(include_github=not args.no_github),
                  approve=approve, plan_only=plan_only, on_text=lambda t: print(f"\n🤖 {t}\n"))
    print(f"godotai — Godot {cfg.engine.tag} — {provider.name}/{provider.model} effort={cfg.agent.effort}")
    summary = agent.run(args.task)
    print("\n" + "=" * 70)
    print(f"status: {summary.status} — {summary.message}")
    print(f"iterations: {summary.iterations}  usage: {summary.usage}")
    if summary.plan:
        print(f"plan: {agent.workspace / '.godotai' / 'PLAN.md'}")
    print(f"log: {summary.log_path}")
    return 0 if summary.status == "success" else 1


def cmd_chat(args: argparse.Namespace) -> int:
    """Browser chat UI on top of the same agent as `run` (see godotai/chat/)."""
    from .chat import ChatServer, ChatServerError, environment_status
    cfg = _cfg(args)
    token = args.token or os.environ.get("GODOTAI_CHAT_TOKEN") or None
    try:
        server = ChatServer(cfg, Path(args.games_dir), host=args.host, port=args.port, token=token,
                            auto_approve_default=args.yes or not cfg.agent.require_plan_approval,
                            include_github=not args.no_github, quiet=not args.verbose)
    except ChatServerError as exc:
        sys.exit(str(exc))
    except OSError as exc:
        sys.exit(f"cannot listen on {args.host}:{args.port}: {exc} (try --port <other>)")
    st = environment_status(cfg)
    url = server.url
    print(f"godotai chat {__version__} — Godot {cfg.engine.tag} — {cfg.agent.provider}/{cfg.agent.model} effort={cfg.agent.effort}")
    print(f"games directory: {server.manager.games_dir}")
    print(f"\n  افتح هذا العنوان في المتصفح:   {url}")
    print(f"  Open this URL in your browser: {url}")
    if token:
        print("  (token mode: open the URL as …/#token=<your token>, or paste the token when the page asks)")
    print()
    print(f"{'✅' if st['engine']['ok'] else '❌'} Godot {cfg.engine.tag}: "
          + (f"{st['engine']['version']} at {st['engine']['binary']}" if st['engine']['ok'] else
             f"{st['engine']['error']} → python3 -m godotai install-godot"))
    print(f"{'✅' if st['model']['ready'] else '❌'} model key ({st['model']['key_env']}): "
          + ("set" if st['model']['ready'] else "not set — the page explains what to export before sending a message"))
    if not is_loopback_host(args.host):
        print(f"⚠ listening on {args.host} — token required on every API request (kept out of logs)")
    print("Ctrl+C to stop.")
    if args.open:
        import webbrowser
        webbrowser.open(url)
    if args.check:
        server.shutdown()
        return 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
        server.shutdown()
    return 0


def is_loopback_host(host: str) -> bool:
    from .chat import is_loopback
    return is_loopback(host)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="godotai", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to godot.toml (default: search upward / repo file)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check the environment against the engine pin").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("install-godot", help="download + verify + install the pinned editor and templates")
    s.add_argument("--system", action="store_true", help="install to /usr/local/bin (containers)")
    s.add_argument("--bin-dir")
    s.add_argument("--editor-only", action="store_true", help="skip export templates")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("setup-android", help="install Android SDK packages, debug keystore, editor settings")
    s.add_argument("--sdk-root")
    s.add_argument("--no-packages", action="store_true", help="only write editor settings/keystore")
    s.set_defaults(fn=cmd_setup_android)

    s = sub.add_parser("new", help="create a project from a template")
    s.add_argument("--template", default="mobile-2d")
    s.add_argument("--dest")
    s.add_argument("--name", default="My Game")
    s.add_argument("--package", help="Android package id, e.g. com.studio.mygame")
    s.add_argument("--list", action="store_true")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("verify", help="run the engine-backed verification pipeline")
    s.add_argument("--project", required=True)
    s.add_argument("--frames", type=int)
    s.add_argument("--no-lint", action="store_true")
    s.add_argument("--json", help="also write the report as JSON to this path")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("export", help="export with a preset (default: Android → APK)")
    s.add_argument("--project", required=True)
    s.add_argument("--preset")
    s.add_argument("--out", default="build/android/game.apk")
    s.add_argument("--release", action="store_true")
    s.set_defaults(fn=cmd_export)

    for name, plan_only in (("plan", True), ("run", False)):
        s = sub.add_parser(name, help="plan only" if plan_only else "plan → approve → build → verify")
        s.add_argument("task")
        s.add_argument("--workspace", required=True, help="game project directory (created if missing)")
        s.add_argument("--yes", action="store_true", help="auto-approve the plan")
        s.add_argument("--no-github", action="store_true", help="do not expose GitHub tools")
        s.set_defaults(fn=lambda a, po=plan_only: cmd_run(a, plan_only=po))

    s = sub.add_parser("chat", help="browser chat UI: talk to the AI, approve plans, watch the engine verify")
    s.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1; anything else needs --token)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--games-dir", default="./games", help="each project is a sub-folder here (default ./games)")
    s.add_argument("--token", help="access token for every API request (env GODOTAI_CHAT_TOKEN); required off-loopback")
    s.add_argument("--yes", action="store_true", help="tick 'auto-approve the plan' by default in the page")
    s.add_argument("--no-github", action="store_true", help="do not expose GitHub tools to the model")
    s.add_argument("--open", action="store_true", help="open the page in the default browser")
    s.add_argument("--verbose", action="store_true", help="log every HTTP request")
    s.add_argument("--check", action="store_true", help="start, print the URL + readiness, stop (CI smoke test)")
    s.set_defaults(fn=cmd_chat)

    s = sub.add_parser("apiref", help="engine-generated API index: build / lookup / search / lint")
    asub = s.add_subparsers(dest="action", required=True)
    b = asub.add_parser("build", help="run --doctool on the pinned binary and cache the index")
    b.add_argument("--docs-dir", help="checkout of godot/doc/classes for the same tag → adds descriptions")
    b.add_argument("--force", action="store_true")
    lk = asub.add_parser("lookup", help="class or class + member")
    lk.add_argument("class_name")
    lk.add_argument("member", nargs="?")
    se = asub.add_parser("search", help="search names")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)
    li = asub.add_parser("lint", help="lint a project's scripts against the index")
    li.add_argument("--project", required=True)
    s.set_defaults(fn=cmd_apiref)

    s = sub.add_parser("eval", help="engine-verified task bank: list / run / score")
    esub = s.add_subparsers(dest="action", required=True)
    esub.add_parser("list")
    r = esub.add_parser("run", help="run the agent on a task, then score the result")
    r.add_argument("task")
    r.add_argument("--workspace", required=True)
    r.add_argument("--yes", action="store_true")
    r.add_argument("--results-dir")
    sc = esub.add_parser("score", help="score an existing project against a task (no LLM)")
    sc.add_argument("--task", required=True)
    sc.add_argument("--project", required=True)
    s.set_defaults(fn=cmd_eval)

    s = sub.add_parser("dataset", help="extract training examples from engine-verified run logs")
    dsub = s.add_subparsers(dest="action", required=True)
    ex = dsub.add_parser("extract")
    ex.add_argument("--runs", required=True, help="directory with .godotai/runs/*.json logs (searched recursively)")
    ex.add_argument("--out", required=True, help="output .jsonl")
    ex.add_argument("--include-failed", action="store_true", help="also keep runs whose verification never passed")
    ex.add_argument("--min-iterations", type=int, default=2)
    s.set_defaults(fn=cmd_dataset)

    args = p.parse_args(argv)
    if args.cmd == "new" and not args.list and not args.dest:
        p.error("--dest is required")
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
