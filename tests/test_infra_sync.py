"""godot.toml is the single source of truth: CI workflow, Dockerfile and the
workflow copied into game repos must agree with it."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO, repo_config

from godotai.tools.github_tools import DEFAULT_GITIGNORE, WORKFLOW_FILE, sync_workflow

WORKFLOW = REPO / ".github" / "workflows" / WORKFLOW_FILE
CI = REPO / ".github" / "workflows" / "ci.yml"
DOCKERFILE = REPO / "docker" / "Dockerfile"


def _env(text: str, key: str) -> str:
    m = re.search(rf'^\s*{key}:\s*"?([^"\n]+)"?\s*$', text, re.M)
    assert m, key
    return m.group(1)


class WorkflowSyncTests(unittest.TestCase):
    def setUp(self):
        self.cfg = repo_config()
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_engine_pin_matches(self):
        self.assertEqual(_env(self.text, "GODOT_VERSION"), self.cfg.engine.version)
        self.assertEqual(_env(self.text, "GODOT_RELEASE"), self.cfg.engine.release)

    def test_android_requirements_match(self):
        pk = {p.split(";")[0]: p.split(";")[1] for p in self.cfg.android.sdk_packages if ";" in p}
        self.assertEqual(_env(self.text, "BUILD_TOOLS"), pk["build-tools"])
        self.assertEqual(_env(self.text, "PLATFORM"), pk["platforms"])
        self.assertIn(self.cfg.android.cmdline_tools_url, self.text)
        self.assertIn(f'java-version: "{self.cfg.android.jdk_major}"', self.text)

    def test_downloads_are_checksum_verified_and_official(self):
        self.assertIn("github.com/godotengine/godot-builds/releases/download", self.text)
        self.assertIn("SHA512-SUMS.txt", self.text)
        self.assertIn("sha512sum -c", self.text)

    def test_inputs_are_not_interpolated_into_shell(self):
        # ${{ inputs.* }} inside a run: block is a script-injection vector; inputs must go through env.
        in_run = False
        for line in self.text.splitlines():
            if re.match(r"\s+run:\s*\|", line):
                in_run = True
                continue
            if in_run and re.match(r"\s+- (name|uses):", line):
                in_run = False
            if in_run:
                self.assertNotIn("${{ inputs.", line, line)

    def test_release_secrets_only_via_env(self):
        self.assertIn("secrets.ANDROID_KEYSTORE_BASE64", self.text)
        self.assertIn("GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD: ${{ secrets.ANDROID_KEYSTORE_PASSWORD }}", self.text)
        self.assertNotRegex(self.text, r"echo .*secrets\.", "never echo secrets")

    def test_sync_into_game_repo(self):
        dest = Path(tempfile.mkdtemp(prefix="godotai-sync-"))
        out = sync_workflow(REPO, "4.9.1", "rc3", dest)
        self.assertEqual(out, dest / ".github" / "workflows" / WORKFLOW_FILE)
        t = out.read_text(encoding="utf-8")
        self.assertEqual(_env(t, "GODOT_VERSION"), "4.9.1")
        self.assertEqual(_env(t, "GODOT_RELEASE"), "rc3")
        m = re.search(r"project_dir:\s*\n(?:\s+.*\n)*?\s+default:\s*(\S+)", t)
        self.assertEqual(m.group(1), ".", "a game repo has project.godot at its root")
        # nothing else changed
        self.assertEqual(len(t.splitlines()), len(self.text.splitlines()))

    def test_default_gitignore_protects_secrets(self):
        for entry in (".godot/", "*.keystore", "*.jks", ".env", "*.apk"):
            self.assertIn(entry, DEFAULT_GITIGNORE)


class CIWorkflowTests(unittest.TestCase):
    def test_ci_runs_tests_and_engine_verification(self):
        t = CI.read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest discover -s tests", t)
        self.assertIn("python3 -m godotai install-godot", t)
        self.assertIn("python3 -m godotai verify", t)
        self.assertIn("pull_request", t)


def _run_block_lines(text: str):
    """Yield the lines inside every ``run: |`` block of a workflow file."""
    in_run = False
    for line in text.splitlines():
        if re.match(r"\s+run:\s*\|", line):
            in_run = True
            continue
        if in_run and re.match(r"\s+- (name|uses):", line):
            in_run = False
        if in_run:
            yield line


class CloudflareProvisionWorkflowTests(unittest.TestCase):
    """The manual provisioning workflow must stay manual, environment-gated and unable to leak a token."""

    PATH = REPO / ".github" / "workflows" / "cloudflare-provision.yml"

    def setUp(self):
        self.text = self.PATH.read_text(encoding="utf-8")
        self.code = "\n".join(l for l in self.text.splitlines() if not l.strip().startswith("#"))

    def test_manual_only_and_environment_gated(self):
        m = re.search(r"(?ms)^on:\n(.*?)^[a-z]", self.code)
        triggers = re.findall(r"(?m)^  ([a-z_]+):", m.group(1))
        self.assertEqual(triggers, ["workflow_dispatch"], "nothing may run on push / pull_request / schedule")
        self.assertIn("environment: cloudflare", self.code, "secrets live in a protected environment (reviewers, main only)")
        self.assertRegex(self.code, r"permissions:\n  contents: read")
        self.assertRegex(self.code, r"dry_run:\n(?:.*\n)*?\s+default: true", "a careless click only prints the plan")
        self.assertIn("if: ${{ !inputs.dry_run }}", self.code)

    def test_token_never_stored_echoed_or_uploaded(self):
        self.assertIn("--discard-tunnel-token", self.code, "the runner keeps no copy of the tunnel connector token")
        self.assertNotIn("--write-env", self.code)
        self.assertNotIn("upload-artifact", self.code, "a public repository's artifacts are public")
        self.assertNotRegex(self.code, r"echo .*secrets\.", "never echo secrets")
        self.assertNotRegex(self.code, r"echo .*CLOUDFLARE_API_TOKEN}", "never echo the token variable")
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}", self.code, "secret → env: only")
        # the token is scoped to the live step, not the whole job
        job_env = re.search(r"(?ms)^    env:\n(.*?)^    steps:", self.code).group(1)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", job_env)
        self.assertNotIn("TUNNEL_TOKEN", job_env)
        self.assertIn("::add-mask::", self.code, "allowed e-mails are masked before the script prints them")

    def test_inputs_are_not_interpolated_into_shell(self):
        for line in _run_block_lines(self.text):
            self.assertNotIn("${{", line, line)

    def test_uses_the_offline_tested_script_and_documents_the_rotation_rule(self):
        self.assertIn('python3 -m unittest discover -s tests -p "test_cloudflare_setup.py"', self.code)
        self.assertIn("python3 scripts/cloudflare_setup.py --dry-run", self.code)
        self.assertIn("--facts-json", self.code)
        for perm in ("Access: Apps and Policies: Edit", "Cloudflare Tunnel: Edit", "DNS: Edit"):
            self.assertIn(perm, self.text, perm)
        self.assertIn("rolled or deleted first", self.text)
        self.assertIn("Networking → Tunnels", self.text)
        self.assertIn("does not deploy", self.text)


class DockerfileTests(unittest.TestCase):
    def test_copied_paths_exist(self):
        t = DOCKERFILE.read_text(encoding="utf-8")
        for m in re.finditer(r"^COPY\s+(.+?)\s+\S+$", t, re.M):
            for src in m.group(1).split():
                self.assertTrue((REPO / src).exists(), f"Dockerfile COPY {src} does not exist in the repo")

    def test_versions_come_from_config_not_hardcoded(self):
        t = DOCKERFILE.read_text(encoding="utf-8")
        cfg = repo_config()
        self.assertNotIn("Godot_v", t, "engine assets must come from godot.toml via the installer")
        self.assertIn("install-godot", t)
        self.assertIn("setup-android", t)
        self.assertIn(f"openjdk-{cfg.android.jdk_major}", t)
        self.assertIn("GODOTAI_CONFIG=/opt/godotai/godot.toml", t)


class RepoHygieneTests(unittest.TestCase):
    def test_no_secrets_committed(self):
        # Legacy narrow patterns kept from PR #1, applied to the same text files the
        # project scanner considers (skips .git/, __pycache__/, binaries, .godot/).
        from godotai import secrets

        pat = re.compile(r"(sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})")
        for p in secrets.iter_text_files(REPO):
            text = p.read_text(encoding="utf-8", errors="ignore")
            self.assertIsNone(pat.search(text), p)
        # The broader scanner (Kaggle/Cloudflare/HF tokens, PEM blocks, credential assignments)
        # is exercised by tests/test_secrets.py::test_repository_is_clean and scripts/secret_scan.py in CI.

    def test_root_gitignore_and_pyproject(self):
        gi = (REPO / ".gitignore").read_text(encoding="utf-8")
        for entry in (".godot/", "*.keystore", "*.apk", "__pycache__/"):
            self.assertIn(entry, gi)
        py = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('name = "godotai"', py)
        self.assertIn("godotai.__main__:main", py)


if __name__ == "__main__":
    unittest.main()
