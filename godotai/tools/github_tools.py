"""GitHub integration: create a repository, push the game, build the APK in Actions.

Authentication: a personal access token (classic ``repo`` + ``workflow`` scopes,
or a fine-grained token with Contents/Actions/Administration write) in the
``GITHUB_TOKEN`` environment variable. The token is never written to disk, never
placed in a URL and never echoed back to the model.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .base import ToolContext, ToolRegistry, ToolResult

API = "https://api.github.com"
WORKFLOW_FILE = "build-android.yml"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

DEFAULT_GITIGNORE = """# Godot 4
.godot/
*.import
build/
export/
*.apk
*.aab
*.pck
# secrets — never commit keystores or credentials
*.keystore
*.jks
.env
# agent state that is not source
.godotai/runs/
"""


class GitHubError(RuntimeError):
    pass


def _token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok:
        raise GitHubError("GITHUB_TOKEN is not set; connect your GitHub account by exporting a token first.")
    return tok


def api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "godotai-agent",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise GitHubError(f"GitHub API {method} {path} → {exc.code}: {detail}") from None


def _git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False,
                          env=env or os.environ.copy())


def _askpass_env() -> tuple[dict[str, str], Path]:
    """Return env with GIT_ASKPASS pointing at a helper that supplies the token."""
    tmp = Path(tempfile.mkdtemp(prefix="godotai-git-"))
    helper = tmp / "askpass.sh"
    helper.write_text("#!/bin/sh\ncase \"$1\" in\n  *sername*) echo x-access-token ;;\n  *) printf '%s' \"$GODOTAI_GIT_TOKEN\" ;;\nesac\n")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    env = os.environ.copy()
    env["GIT_ASKPASS"] = str(helper)
    env["GODOTAI_GIT_TOKEN"] = _token()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env, tmp


def sync_workflow(cfg_root: Path, engine_version: str, engine_release: str, dest_project: Path,
                  project_dir_default: str = ".") -> Path:
    """Copy the repo's build-android workflow into a game project, pinning the engine version."""
    src = cfg_root / ".github" / "workflows" / WORKFLOW_FILE
    text = src.read_text(encoding="utf-8")
    text = re.sub(r"GODOT_VERSION:\s*\"?[\d.]+\"?", f'GODOT_VERSION: "{engine_version}"', text, count=1)
    text = re.sub(r"GODOT_RELEASE:\s*\"?[a-z0-9]+\"?", f'GODOT_RELEASE: "{engine_release}"', text, count=1)
    text = re.sub(r"(project_dir:\s*\n(?:\s+.*\n)*?\s+default:\s*)\S+", rf"\g<1>{project_dir_default}", text, count=1)
    out = dest_project / ".github" / "workflows" / WORKFLOW_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def register(reg: ToolRegistry) -> None:
    @reg.add(
        "github_create_repo",
        "Create a new GitHub repository for the game under the authenticated user. Returns full_name and URL.",
        {"type": "object", "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "private": {"type": "boolean", "default": True},
        }, "required": ["name"]},
        mutating=True,
    )
    def github_create_repo(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        name = str(args["name"]).strip()
        if not _NAME_RE.match(name):
            return ToolResult.error("invalid repository name")
        try:
            data = api("POST", "/user/repos", {
                "name": name, "description": str(args.get("description") or "Godot game built by godotai"),
                "private": bool(args.get("private", True)), "auto_init": False,
            })
        except GitHubError as exc:
            return ToolResult.error(str(exc))
        ctx.extra["repo_full_name"] = data["full_name"]
        return ToolResult.success(json.dumps({"full_name": data["full_name"], "html_url": data["html_url"],
                                              "default_branch": data.get("default_branch", "main")}))

    @reg.add(
        "github_push_project",
        "Commit the whole project and push it to a GitHub repository (created with github_create_repo or existing). "
        "Adds a .gitignore (excludes .godot/, builds, keystores) and the APK build workflow if missing.",
        {"type": "object", "properties": {
            "repo_full_name": {"type": "string", "description": "owner/name"},
            "branch": {"type": "string", "default": "main"},
            "message": {"type": "string"},
        }, "required": ["repo_full_name", "message"]},
        mutating=True,
    )
    def github_push_project(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        full = str(args["repo_full_name"]).strip()
        if not _REPO_RE.match(full):
            return ToolResult.error("repo_full_name must be owner/name")
        if not shutil.which("git"):
            return ToolResult.error("git is not installed")
        ws = ctx.workspace
        branch = str(args.get("branch") or "main")
        gi = ws / ".gitignore"
        if not gi.exists():
            gi.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
        wf = ws / ".github" / "workflows" / WORKFLOW_FILE
        if not wf.exists():
            sync_workflow(ctx.cfg.root, ctx.cfg.engine.version, ctx.cfg.engine.release, ws)
        if not (ws / ".git").is_dir():
            r = _git(["init", "-b", branch], ws)
            if r.returncode != 0:  # older git without -b
                _git(["init"], ws)
                _git(["checkout", "-b", branch], ws)
        _git(["config", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "godotai@users.noreply.github.com")], ws)
        _git(["config", "user.name", os.environ.get("GIT_AUTHOR_NAME", "godotai")], ws)
        _git(["add", "-A"], ws)
        commit = _git(["commit", "-m", str(args["message"])], ws)
        if commit.returncode != 0 and "nothing to commit" not in commit.stdout + commit.stderr:
            return ToolResult.error(f"git commit failed: {commit.stderr[-800:]}")
        _git(["remote", "remove", "origin"], ws)
        _git(["remote", "add", "origin", f"https://github.com/{full}.git"], ws)
        env, tmp = _askpass_env()
        try:
            push = _git(["push", "-u", "origin", f"HEAD:{branch}"], ws, env=env)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if push.returncode != 0:
            return ToolResult.error("git push failed: " + (push.stderr or push.stdout)[-800:].replace(_token(), "***"))
        head = _git(["rev-parse", "HEAD"], ws).stdout.strip()
        ctx.extra["repo_full_name"] = full
        return ToolResult.success(json.dumps({"repo": full, "branch": branch, "commit": head,
                                              "url": f"https://github.com/{full}/tree/{branch}"}))

    @reg.add(
        "github_build_apk",
        "Trigger the 'Build Android APK' GitHub Actions workflow in the game repository (workflow_dispatch). "
        "The workflow downloads the pinned Godot + export templates, sets up JDK 17 and the Android SDK, exports "
        "the APK and uploads it as an artifact. Use github_build_status to follow it.",
        {"type": "object", "properties": {
            "repo_full_name": {"type": "string"},
            "ref": {"type": "string", "default": "main"},
            "project_dir": {"type": "string", "default": "."},
            "build_type": {"type": "string", "enum": ["debug", "release"], "default": "debug"},
        }, "required": ["repo_full_name"]},
        mutating=True,
    )
    def github_build_apk(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        full = str(args["repo_full_name"]).strip()
        if not _REPO_RE.match(full):
            return ToolResult.error("repo_full_name must be owner/name")
        try:
            api("POST", f"/repos/{full}/actions/workflows/{WORKFLOW_FILE}/dispatches", {
                "ref": str(args.get("ref") or "main"),
                "inputs": {"project_dir": str(args.get("project_dir") or "."),
                           "build_type": str(args.get("build_type") or "debug")},
            })
        except GitHubError as exc:
            return ToolResult.error(str(exc))
        return ToolResult.success(f"workflow dispatched; check https://github.com/{full}/actions and call github_build_status")

    @reg.add(
        "github_build_status",
        "Show the latest runs of the APK workflow with status, conclusion, URL and artifact names/sizes.",
        {"type": "object", "properties": {"repo_full_name": {"type": "string"}}, "required": ["repo_full_name"]},
    )
    def github_build_status(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        full = str(args["repo_full_name"]).strip()
        if not _REPO_RE.match(full):
            return ToolResult.error("repo_full_name must be owner/name")
        try:
            runs = api("GET", f"/repos/{full}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=3")
            out = []
            for run in runs.get("workflow_runs", []):
                item = {"id": run["id"], "status": run["status"], "conclusion": run["conclusion"],
                        "url": run["html_url"], "created_at": run["created_at"]}
                if run["status"] == "completed":
                    arts = api("GET", f"/repos/{full}/actions/runs/{run['id']}/artifacts")
                    item["artifacts"] = [{"name": a["name"], "size_bytes": a["size_in_bytes"],
                                          "download_api": a["archive_download_url"]} for a in arts.get("artifacts", [])]
                out.append(item)
        except GitHubError as exc:
            return ToolResult.error(str(exc))
        return ToolResult.success(json.dumps(out, indent=2) if out else "no runs yet")
