"""The phone path (docs/TERMUX.md): host detection, installer guard, helper script, workflow inputs, release links.

Nothing here talks to a network, a model or GitHub. The one group that needs the real pinned engine
(``WorkflowVerifyStepEngineTests``) runs the *exact* shell block of the workflow's "Verify with the engine" step
against the template — positive and negative — and is skipped when the binary is not available (CI runs it in the
godot-verify-template job).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from _helpers import REPO, repo_config

from godotai import hostenv
from godotai.godot import Godot, GodotNotFound, GodotVersionMismatch, find_godot_binary
from godotai.install import InstallError, install_godot
from godotai.tools.github_tools import WORKFLOW_FILE, apk_releases, as_bool, dispatch_inputs, sync_workflow

CFG = repo_config()
WORKFLOW = REPO / ".github" / "workflows" / WORKFLOW_FILE
HELPER = REPO / "scripts" / "termux_setup.sh"
GUIDE = REPO / "docs" / "TERMUX.md"
DOCKERFILE = REPO / "deploy" / "paas" / "Dockerfile"

TERMUX_ENV = {"TERMUX_VERSION": "0.118.3", "PREFIX": hostenv.TERMUX_PREFIX, "HOME": "/data/data/com.termux/files/home"}
PROOT_ENV = {"PD_CONTAINER": "debian", "container": "proot-distro"}
CLEAN_ENV = {k: "" for k in ("TERMUX_VERSION", "PREFIX", "PD_CONTAINER", "container", hostenv.ENV_INSTALL_ANYWAY)}


# ----------------------------------------------------------------------------------------------------------------------
# godotai/hostenv.py — detection + wording
# ----------------------------------------------------------------------------------------------------------------------
class HostEnvTests(unittest.TestCase):
    def test_cpu_to_official_asset(self):
        self.assertEqual(hostenv.host_engine_platform("x86_64"), "linux.x86_64")
        self.assertEqual(hostenv.host_engine_platform("aarch64"), "linux.arm64")     # every 64-bit Android phone
        self.assertEqual(hostenv.host_engine_platform("arm64"), "linux.arm64")
        self.assertEqual(hostenv.host_engine_platform("armv8l"), "linux.arm32")      # 32-bit userland on a 64-bit kernel
        self.assertEqual(hostenv.host_engine_platform("armv7l"), "linux.arm32")
        self.assertEqual(hostenv.host_engine_platform("i686"), "linux.x86_32")
        self.assertIsNone(hostenv.host_engine_platform("riscv64"))
        self.assertIsNone(hostenv.host_engine_platform(""))

    def test_every_mapped_asset_is_pinned_in_the_checksums(self):
        sums = CFG.checksums_file().read_text(encoding="utf-8")
        for plat in set(hostenv._MACHINE_TO_PLATFORM.values()):
            self.assertIn(f"Godot_v{CFG.engine.tag}_{plat}.zip", sums, plat)

    def test_termux_native_detection(self):
        self.assertTrue(hostenv.is_termux_native(TERMUX_ENV, prefix="/data/data/com.termux/files/usr", release="5.10.0"))
        self.assertTrue(hostenv.is_termux_native({"TERMUX_VERSION": "0.118.3"}, prefix="/usr", release="5.10.0"))
        self.assertTrue(hostenv.is_termux_native({"PREFIX": hostenv.TERMUX_PREFIX}, prefix="/usr", release="5.10.0"))
        self.assertTrue(hostenv.is_termux_native({}, prefix=hostenv.TERMUX_PREFIX + "/", release="5.10.0"))
        self.assertFalse(hostenv.is_termux_native({}, prefix="/usr", release="6.1.0-generic"))

    def test_proot_guest_is_not_termux_native(self):
        for env, release in ((PROOT_ENV, "5.10.0"), ({"PD_CONTAINER": "debian"}, "5.10.0"),
                             ({"container": "proot-distro"}, "5.10.0"), ({}, "6.17.0-PRoot-Distro")):
            self.assertTrue(hostenv.is_proot_guest(env, release), (env, release))
            # even with Termux variables leaking in, a proot guest is a glibc userland: the editor can run there
            self.assertFalse(hostenv.is_termux_native({**TERMUX_ENV, **env}, prefix="/usr", release=release))
        self.assertFalse(hostenv.is_proot_guest({}, "6.1.0-generic"))

    def test_describe_host_and_label(self):
        info = hostenv.describe_host(TERMUX_ENV)
        self.assertTrue(info.termux_native)
        self.assertIn("Termux", info.label)
        self.assertEqual(set(info.to_dict()), {"machine", "engine_platform", "termux_native", "proot_guest", "python"})
        plain = hostenv.describe_host(CLEAN_ENV)
        self.assertFalse(plain.termux_native)
        self.assertFalse(plain.proot_guest)

    def test_platform_mismatch_wording(self):
        eng = CFG.engine                                   # linux.x86_64 in godot.toml
        self.assertIsNone(hostenv.platform_mismatch(eng, "linux.x86_64"))
        self.assertIsNone(hostenv.platform_mismatch(eng, None), "unknown CPU is the installer's business")
        msg = hostenv.platform_mismatch(eng, "linux.arm64")
        self.assertIn("GODOTAI_ENGINE_PLATFORM=linux.arm64", msg)
        self.assertIn(eng.editor_zip_name, msg)
        self.assertIn(hostenv.ENV_INSTALL_ANYWAY, msg)

    def test_termux_hint_in_both_languages_names_the_two_paths(self):
        for lang in ("ar", "en"):
            hint = hostenv.termux_native_hint(lang)
            self.assertIn("proot-distro install debian:bookworm", hint)
            self.assertIn("--shared-home", hint)
            self.assertIn("build-android.yml", hint)
            self.assertIn("docs/TERMUX.md", hint)
        self.assertIn("glibc", hostenv.termux_native_hint("en"))
        self.assertIn("Termux", hostenv.termux_native_hint("ar"))

    def test_install_blocker(self):
        eng = CFG.engine
        with mock.patch.object(hostenv, "host_engine_platform", return_value=eng.platform):
            self.assertIsNone(hostenv.install_blocker(eng, CLEAN_ENV))
            self.assertIn("Termux", hostenv.install_blocker(eng, TERMUX_ENV) or "")
            self.assertIsNone(hostenv.install_blocker(eng, {**TERMUX_ENV, hostenv.ENV_INSTALL_ANYWAY: "1"}))
        with mock.patch.object(hostenv, "host_engine_platform", return_value="linux.arm64"):
            self.assertIn("linux.arm64", hostenv.install_blocker(eng, CLEAN_ENV) or "")
            self.assertIsNone(hostenv.install_blocker(replace(eng, platform="linux.arm64"), CLEAN_ENV))

    def test_exec_failure_hint(self):
        eng = CFG.engine
        self.assertIn("Termux", hostenv.exec_failure_hint(eng, TERMUX_ENV))
        with mock.patch.object(hostenv, "host_engine_platform", return_value="linux.arm64"):
            self.assertIn("GODOTAI_ENGINE_PLATFORM=linux.arm64", hostenv.exec_failure_hint(eng, CLEAN_ENV))
        with mock.patch.object(hostenv, "host_engine_platform", return_value=eng.platform):
            self.assertIn("deploy/paas/Dockerfile", hostenv.exec_failure_hint(eng, CLEAN_ENV))


# ----------------------------------------------------------------------------------------------------------------------
# installer guard + engine wrapper
# ----------------------------------------------------------------------------------------------------------------------
class InstallerGuardTests(unittest.TestCase):
    def test_refuses_on_termux_before_downloading(self):
        calls: list = []
        with mock.patch.dict(os.environ, TERMUX_ENV), \
             mock.patch("godotai.install._download", side_effect=lambda *a, **k: calls.append(a)):
            with self.assertRaises(InstallError) as cm:
                install_godot(CFG, bin_dir=Path(tempfile.mkdtemp(prefix="godotai-termux-")), templates=False, log=lambda s: None)
        self.assertIn("Termux", str(cm.exception))
        self.assertIn("docs/TERMUX.md", str(cm.exception))
        self.assertEqual(calls, [], "nothing may be downloaded on a host that cannot run the binary")

    def test_refuses_on_cpu_mismatch_unless_overridden(self):
        with mock.patch.dict(os.environ, CLEAN_ENV), \
             mock.patch.object(hostenv, "host_engine_platform", return_value="linux.arm64"), \
             mock.patch("godotai.install._download", side_effect=AssertionError("must not download")):
            with self.assertRaises(InstallError) as cm:
                install_godot(CFG, bin_dir=Path(tempfile.mkdtemp(prefix="godotai-arm-")), templates=False, log=lambda s: None)
            self.assertIn("GODOTAI_ENGINE_PLATFORM=linux.arm64", str(cm.exception))
        # the override reaches the download step (which we make fail loudly to stop there)
        with mock.patch.dict(os.environ, {**CLEAN_ENV, hostenv.ENV_INSTALL_ANYWAY: "1"}), \
             mock.patch.object(hostenv, "host_engine_platform", return_value="linux.arm64"), \
             mock.patch("godotai.install._download", side_effect=OSError("stop here")):
            with self.assertRaises(Exception) as cm2:
                install_godot(CFG, bin_dir=Path(tempfile.mkdtemp(prefix="godotai-arm-")), templates=False, log=lambda s: None)
            self.assertNotIsInstance(cm2.exception, InstallError)


class GodotExecFailureTests(unittest.TestCase):
    def test_non_executable_or_foreign_binary_is_a_readable_error(self):
        tmp = Path(tempfile.mkdtemp(prefix="godotai-exec-"))
        not_exec = tmp / "godot-noexec"
        not_exec.write_text("#!/bin/sh\necho 4.7.2.stable.official\n", encoding="utf-8")   # no x bit → EACCES
        garbage = tmp / "godot-garbage"
        garbage.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56)                      # x bit, not loadable → ENOEXEC
        garbage.chmod(0o755)
        for binary in (not_exec, garbage):
            with mock.patch.dict(os.environ, CLEAN_ENV):
                with self.assertRaises(GodotNotFound) as cm:
                    Godot(CFG.engine, binary=binary)
            self.assertIn("cannot execute Godot binary", str(cm.exception))
            self.assertNotIn("Traceback", str(cm.exception))
        with mock.patch.dict(os.environ, TERMUX_ENV):
            with self.assertRaises(GodotNotFound) as cm:
                Godot(CFG.engine, binary=garbage)
        self.assertIn("Termux", str(cm.exception))


# ----------------------------------------------------------------------------------------------------------------------
# doctor + /api/status carry the host facts
# ----------------------------------------------------------------------------------------------------------------------
class HostInStatusAndDoctorTests(unittest.TestCase):
    def test_status_has_host_block_and_termux_hints(self):
        from godotai.chat.status import environment_status
        from _helpers import fake_probe
        with mock.patch.dict(os.environ, {**CLEAN_ENV, "GODOT_BIN": "", "PATH": "/nonexistent"}):
            st = environment_status(CFG, probe=fake_probe())
        host = st["host"]
        for key in ("machine", "engine_platform", "termux_native", "proot_guest", "python", "platform_mismatch",
                    "engine_platform_configured"):
            self.assertIn(key, host)
        self.assertEqual(host["engine_platform_configured"], CFG.engine.platform)
        with mock.patch.dict(os.environ, {**TERMUX_ENV, "GODOT_BIN": "", "PATH": "/nonexistent"}), \
             mock.patch("godotai.chat.status.find_godot_binary", return_value=None):
            st = environment_status(CFG, probe=fake_probe())
        self.assertTrue(st["host"]["termux_native"])
        self.assertFalse(st["engine"]["ok"])
        self.assertIn("proot-distro", st["engine"]["hint_ar"])
        self.assertIn("GitHub Actions", st["engine"]["hint_ar"])
        self.assertIn("proot-distro", st["engine"]["hint_en"])

    def test_doctor_prints_host_row_and_termux_guidance(self):
        env = {**os.environ, **TERMUX_ENV, "GODOT_BIN": "", "PATH": "/nonexistent:/usr/bin:/bin",
               "GODOTAI_SKIP_MODEL_PROBE": "1"}
        proc = subprocess.run([sys.executable, "-m", "godotai", "doctor"], cwd=str(REPO), env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertIn("host", proc.stdout)
        self.assertIn("Termux", proc.stdout)
        self.assertIn("docs/TERMUX.md", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


# ----------------------------------------------------------------------------------------------------------------------
# scripts/termux_setup.sh
# ----------------------------------------------------------------------------------------------------------------------
class TermuxHelperScriptTests(unittest.TestCase):
    def run_helper(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        full_env = {**os.environ, **CLEAN_ENV, **(env or {})}
        return subprocess.run(["sh", str(HELPER), *args], capture_output=True, text=True, timeout=60, env=full_env)

    def test_posix_and_bash_syntax(self):
        for shell in ("sh", "bash"):
            proc = subprocess.run([shell, "-n", str(HELPER)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_print_mode_changes_nothing_and_prints_the_plan(self):
        proc = self.run_helper("--print")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        for needle in ("pkg install -y python git proot-distro", "termux-setup-storage",
                       "git clone https://github.com/joknok72-ctrl/Ai-game-godot.git", "python3 -m godotai doctor",
                       "export GODOTAI_PRESET=openrouter_free", "export OPENROUTER_API_KEY='<", "python3 -m godotai presets",
                       "termux-wake-lock", "python3 -m godotai chat", "http://127.0.0.1:8765/", "docs/TERMUX.md"):
            self.assertIn(needle, out, needle)
        self.assertIn("20 req/min", out)
        self.assertIn("50 req/day", out)
        for mode, needles in (("--proot", ("proot-distro install debian:bookworm", "proot-distro login debian --shared-home",
                                           "--guest --install-godot")),
                              ("--guest", ("apt-get install -y --no-install-recommends", "install-godot --editor-only",
                                           "GODOTAI_ENGINE_PLATFORM="))):
            proc = self.run_helper(mode, "--print")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            for needle in needles:
                self.assertIn(needle, proc.stdout, (mode, needle))

    def test_guest_libs_match_the_app_image(self):
        libs = re.search(r'^GUEST_LIBS="([^"]+)"', HELPER.read_text(encoding="utf-8"), re.M).group(1).split()
        docker = DOCKERFILE.read_text(encoding="utf-8")
        for lib in libs:
            self.assertIn(lib, docker, f"{lib} is not in deploy/paas/Dockerfile")
        apt = re.search(r"apt-get install -y --no-install-recommends(.*?)&&", docker, re.S).group(1)
        docker_libs = [tok for tok in apt.replace("\\", " ").split() if tok.startswith("lib")]
        self.assertEqual(sorted(set(docker_libs)), sorted(set(libs)), "the two lists must be identical")

    def test_detects_termux_and_proot(self):
        proc = self.run_helper("--print", env=TERMUX_ENV)
        self.assertIn("where: Termux itself", proc.stdout)
        proc = self.run_helper("--print", env=PROOT_ENV)
        self.assertIn("where: PRoot guest", proc.stdout)
        proc = self.run_helper("--print")
        self.assertIn("regular Linux host", proc.stdout)

    def test_cpu_mapping(self):
        proc = self.run_helper("--guest", "--print")
        machine = os.uname().machine
        expected = {"x86_64": "linux.x86_64", "aarch64": "linux.arm64"}.get(machine)
        if expected:
            self.assertIn(f"export GODOTAI_ENGINE_PLATFORM={expected}", proc.stdout)

    def test_never_handles_a_secret(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"read\s+-r?\s*[A-Z_]*KEY", "must not prompt for a key")
        self.assertNotRegex(text, r"(sk-or-v1-|ghp_|github_pat_|gsk_)[A-Za-z0-9]", "no key material, not even an example")
        for var in ("OPENROUTER_API_KEY", "GITHUB_TOKEN"):
            for m in re.finditer(rf"{var}=([^\s\"]+)", text):
                self.assertTrue(m.group(1).startswith("'<"), f"{var} may only be shown with a placeholder: {m.group(0)}")
        self.assertNotRegex(text, r"\bcurl\b.*(api\.|openrouter|github\.com/[^ ]*api)", "no network calls with credentials")

    def test_unknown_option_fails(self):
        proc = self.run_helper("--bogus")
        self.assertEqual(proc.returncode, 2)
        proc = self.run_helper("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--proot", proc.stdout)


# ----------------------------------------------------------------------------------------------------------------------
# build-android.yml — phone-path inputs and the release job
# ----------------------------------------------------------------------------------------------------------------------
def _input_block(text: str, name: str) -> str:
    m = re.search(rf"(?ms)^      {name}:\n((?:        .*\n)+)", text)
    assert m, name
    return m.group(1)


def _step_run_block(text: str, step_name_prefix: str) -> str:
    """The de-indented ``run: |`` script of the step whose name starts with ``step_name_prefix``."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith("- name: " + step_name_prefix))
    run_at = next(i for i in range(start, len(lines)) if re.match(r"\s+run:\s*\|\s*$", lines[i]))
    indent = len(lines[run_at]) - len(lines[run_at].lstrip()) + 2
    body: list[str] = []
    for l in lines[run_at + 1:]:
        if l.strip() and (len(l) - len(l.lstrip())) < indent:
            break
        body.append(l[indent:] if len(l) >= indent else "")
    return "\n".join(body) + "\n"


class WorkflowPhonePathTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.code = "\n".join(l for l in self.text.splitlines() if not l.strip().startswith("#"))

    def test_inputs(self):
        pub = _input_block(self.code, "publish_release")
        self.assertIn("type: boolean", pub)
        self.assertIn("default: false", pub, "a plain build must not create releases")
        ver = _input_block(self.code, "verify")
        self.assertIn("type: boolean", ver)
        self.assertIn("default: true", ver, "the engine verdict is on by default")

    def test_least_privilege(self):
        self.assertRegex(self.code, r"(?m)^permissions:\n  contents: read")
        release = self.code.split("\n  release:\n", 1)[1]
        self.assertIn("if: ${{ inputs.publish_release }}", release)
        self.assertIn("needs: apk", release)
        self.assertIn("permissions:\n      contents: write", release)
        apk_job = self.code.split("\n  apk:\n", 1)[1].split("\n  release:\n", 1)[0]
        self.assertNotIn("contents: write", apk_job, "the build job must not be able to write to the repository")
        self.assertEqual(self.code.count("contents: write"), 1)

    def test_release_is_created_by_gh_with_the_apk_and_marked_latest(self):
        block = _step_run_block(self.text, "Create the GitHub Release")
        self.assertIn("gh release create", block)
        self.assertIn("apk/*.apk", block)
        self.assertIn("--latest", block, "GitHub orders 'latest' by commit date otherwise")
        self.assertIn("--target \"$GITHUB_SHA\"", block)
        self.assertIn("releases/latest/download/game-${BUILD_TYPE}.apk", block)
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.code)
        self.assertNotIn("secrets.GITHUB_TOKEN", self.code)

    def test_debug_keystore_secret_is_optional_and_env_only(self):
        self.assertIn("DEBUG_KEYSTORE_BASE64: ${{ secrets.ANDROID_DEBUG_KEYSTORE_BASE64 }}", self.code)
        block = _step_run_block(self.text, "Configure editor settings")
        self.assertIn('if [ -n "${DEBUG_KEYSTORE_BASE64}" ]', block)
        self.assertIn("keytool -keyalg RSA -genkeypair -alias androiddebugkey", block, "fallback: fresh key per run")
        self.assertNotRegex(block, r"echo .*\$\{?DEBUG_KEYSTORE_BASE64", "never echo the secret's value")
        self.assertNotRegex(block, r"set -x", "no command tracing around the secret")
        self.assertIn("::notice::", block)

    def test_verify_step_is_gated_and_self_contained(self):
        step = self.text.split("- name: Verify with the engine before export", 1)[1].split("- name: ", 1)[0]
        self.assertIn("if: ${{ inputs.verify }}", step)
        self.assertIn("shell: bash", step)
        block = _step_run_block(self.text, "Verify with the engine before export")
        for needle in ("--import", "--check-only", "SMOKE_TEST_OK", "--quit-after", "GITHUB_STEP_SUMMARY", "exit \"$fail\""):
            self.assertIn(needle, block, needle)
        self.assertNotIn("godotai", block, "a game repo has no agent package: plain shell only")
        self.assertNotIn("${{", block)

    def test_inputs_never_reach_a_shell_unquoted(self):
        in_run = False
        for line in self.text.splitlines():
            if re.match(r"\s+run:\s*\|", line):
                in_run = True
                continue
            if in_run and re.match(r"\s+- (name|uses):", line):
                in_run = False
            if in_run:
                self.assertNotIn("${{", line, line)

    def test_sync_into_a_game_repo_keeps_the_new_inputs(self):
        dest = Path(tempfile.mkdtemp(prefix="godotai-sync-"))
        t = sync_workflow(REPO, CFG.engine.version, CFG.engine.release, dest).read_text(encoding="utf-8")
        self.assertIn("publish_release:", t)
        self.assertIn("verify:", t)
        self.assertIn("gh release create", t)
        shutil.rmtree(dest, ignore_errors=True)


@unittest.skipUnless(find_godot_binary(CFG.engine) is not None, "pinned Godot binary not available")
class WorkflowVerifyStepEngineTests(unittest.TestCase):
    """Run the workflow's verification shell block for real, the way the runner would (bash, env, godot on PATH)."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.godot = Godot(CFG.engine)
        except (GodotNotFound, GodotVersionMismatch) as exc:  # pragma: no cover
            raise unittest.SkipTest(str(exc))
        cls.block = _step_run_block(WORKFLOW.read_text(encoding="utf-8"), "Verify with the engine before export")

    def setUp(self):
        from godotai.scaffold import create_project
        self.root = Path(tempfile.mkdtemp(prefix="godotai-wf-verify-"))
        self.project = self.root / "game"
        create_project(CFG, "mobile-2d", self.project, "WF Game", "com.example.wfgame")
        bindir = self.root / "bin"
        bindir.mkdir()
        os.symlink(self.godot.binary, bindir / "godot")
        self.summary = self.root / "summary.md"
        self.env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}", "PROJECT_DIR": str(self.project),
                    "VERIFY_FRAMES": "60", "GODOT_VERSION": CFG.engine.version, "GODOT_RELEASE": CFG.engine.release,
                    "GITHUB_STEP_SUMMARY": str(self.summary), "GODOT_SILENCE_ROOT_WARNING": "1"}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_block(self) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", self.block],
                              cwd=str(self.root), env=self.env, capture_output=True, text=True, timeout=900)

    def test_template_passes(self):
        proc = self.run_block()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn("| import (--import) | ✅", summary)
        self.assertRegex(summary, r"\| GDScript parse \(--check-only\) \| ✅ (\d+)/\1 scripts")
        self.assertIn("| smoke test (tests/smoke_test.gd) | ✅ SMOKE_TEST_OK", summary)

    def test_broken_script_fails_the_build_with_file_annotation(self):
        (self.project / "scripts" / "broken.gd").write_text(
            "extends Node\n\nvar speed: float = \"fast\"\n\nfunc _ready() -> void:\n\tundefined_call()\n", encoding="utf-8")
        proc = self.run_block()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("::error file=", proc.stdout)
        self.assertIn("broken.gd", proc.stdout)
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn("| GDScript parse (--check-only) | ❌", summary)

    def test_failing_smoke_test_fails_the_build(self):
        (self.project / "tests" / "smoke_test.gd").write_text(
            "extends SceneTree\n\nfunc _initialize() -> void:\n\tpush_error(\"SMOKE_TEST_FAIL: forced\")\n\tquit(1)\n",
            encoding="utf-8")
        proc = self.run_block()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("| smoke test (tests/smoke_test.gd) | ❌", self.summary.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------------------------------
# github tools: dispatch inputs, release parsing
# ----------------------------------------------------------------------------------------------------------------------
class GitHubToolHelpersTests(unittest.TestCase):
    def test_as_bool(self):
        for v in (True, 1, "true", "True", " yes ", "1", "on", "y"):
            self.assertTrue(as_bool(v), v)
        for v in (False, 0, None, "", "false", "no", "0", "off", "maybe", "False"):
            self.assertFalse(as_bool(v), v)

    def test_dispatch_inputs_are_strings_and_omit_defaults(self):
        base = dispatch_inputs()
        self.assertEqual(base, {"project_dir": ".", "build_type": "debug"}, "defaults omitted: older workflow copies")
        full = dispatch_inputs("games/x", "release", publish_release=True, verify=False)
        self.assertEqual(full, {"project_dir": "games/x", "build_type": "release", "publish_release": "true", "verify": "false"})
        for v in full.values():
            self.assertIsInstance(v, str)
        self.assertEqual(json.loads(json.dumps(full)), full)

    def test_apk_releases_keeps_only_workflow_releases_and_apk_assets(self):
        data = [
            {"tag_name": "apk-debug-7", "html_url": "https://github.com/o/r/releases/tag/apk-debug-7",
             "published_at": "2026-09-26T10:00:00Z",
             "assets": [{"name": "game-debug.apk", "size": 27000000,
                         "browser_download_url": "https://github.com/o/r/releases/download/apk-debug-7/game-debug.apk"},
                        {"name": "notes.txt", "size": 10, "browser_download_url": "https://x/notes.txt"}]},
            {"tag_name": "v1.0", "html_url": "https://github.com/o/r/releases/tag/v1.0", "assets": []},
        ]
        out = apk_releases(data)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tag"], "apk-debug-7")
        self.assertEqual([a["name"] for a in out[0]["apks"]], ["game-debug.apk"])
        self.assertTrue(out[0]["apks"][0]["download_url"].endswith("/game-debug.apk"))
        self.assertEqual(apk_releases({"message": "Not Found"}), [])
        self.assertEqual(apk_releases(None), [])

    def test_build_apk_tool_dispatches_booleans_as_strings(self):
        from godotai.tools import build_registry
        from godotai.tools.base import Phase, ToolContext
        reg = build_registry(include_github=True)
        tool = reg.get("github_build_apk")
        self.assertIn("publish_release", tool.schema["properties"])
        self.assertIn("verify", tool.schema["properties"])
        calls: list = []

        def fake_api(method, path, body=None):
            calls.append((method, path, body))
            return {}
        ws = Path(tempfile.mkdtemp(prefix="godotai-ws-"))
        ctx = ToolContext(cfg=CFG, workspace=ws, phase=Phase.ACT, log=lambda s: None)
        with mock.patch("godotai.tools.github_tools.api", side_effect=fake_api):
            # a smaller model sends the boolean as a string — it must still mean true
            res = tool.handler(ctx, {"repo_full_name": "o/r", "publish_release": "true", "step_id": "S1"})
            res2 = tool.handler(ctx, {"repo_full_name": "o/r", "verify": False, "step_id": "S1"})
            bad = tool.handler(ctx, {"repo_full_name": "not a repo", "step_id": "S1"})
        self.assertTrue(res.ok, res.content)
        self.assertEqual(calls[0][0], "POST")
        self.assertIn(f"/actions/workflows/{WORKFLOW_FILE}/dispatches", calls[0][1])
        self.assertEqual(calls[0][2]["inputs"], {"project_dir": ".", "build_type": "debug", "publish_release": "true"})
        self.assertIn("releases/latest/download/game-debug.apk", res.content)
        self.assertIn("verifies the project with the engine first", res.content)
        self.assertEqual(calls[1][2]["inputs"], {"project_dir": ".", "build_type": "debug", "verify": "false"})
        self.assertNotIn("verifies the project", res2.content)
        self.assertFalse(bad.ok)
        self.assertEqual(len(calls), 2, "an invalid repo name must not reach the API")
        shutil.rmtree(ws, ignore_errors=True)


# ----------------------------------------------------------------------------------------------------------------------
# docs/TERMUX.md — the guide exists, answers the user's exact request, links, tells the truth about limits
# ----------------------------------------------------------------------------------------------------------------------
USER_REQUEST = ("تمام خلاص الان هنشغله و لكن على ترميكس عندي فيه الهاتف بتاعي و لكن طبعا هنستخدم api مجاني عشان مش معنا GPU "
                "و كمان اديني الطريقة بالتفصيل كيف اشغله من ترميكس عندي على الهاتف و كمان هل لو قلتله يعمل اي لعبة مهما كانت "
                "اللي هو اي ذكاء اصطناعي هل لما يخلص هل اعرف اخليها apk لو انا ربطه بي مستودع على جيت هب ولا اي و كمان كيف "
                "اخليه apk و كيف اربطه و اخليه هو كمان اللي يعمل ال apk نفسه فاهمني")


class TermuxGuideTests(unittest.TestCase):
    def setUp(self):
        self.text = GUIDE.read_text(encoding="utf-8")

    def test_quotes_the_request_verbatim(self):
        self.assertIn(USER_REQUEST, self.text)

    def test_covers_the_commands_the_helper_prints(self):
        for needle in ("pkg install -y python git proot-distro", "termux-setup-storage", "python3 -m godotai doctor",
                       "export GODOTAI_PRESET=openrouter_free", "export OPENROUTER_API_KEY=", "termux-wake-lock",
                       "python3 -m godotai chat", "http://127.0.0.1:8765/", "proot-distro install debian:bookworm",
                       "proot-distro login debian --shared-home", "GODOTAI_ENGINE_PLATFORM=linux.arm64",
                       "install-godot --editor-only", "export GITHUB_TOKEN=", "publish_release", "github_build_status",
                       "releases/latest/download/game-debug.apk", "ANDROID_DEBUG_KEYSTORE_BASE64", "scripts/termux_setup.sh"):
            self.assertIn(needle, self.text, needle)

    def test_states_the_limits_honestly(self):
        for needle in ("20", "50", "1000", "glibc", "Bionic", "proot", "phantom", "SMOKE_TEST_OK"):
            self.assertIn(needle, self.text, needle)
        self.assertRegex(self.text, r"لم (يُجرَّب|يُجرّب|تُجرَّب|نجرّب|يجرب)", "the guide must say what was NOT tested")
        self.assertIn("مهما كانت", self.text)          # answers 'any game whatsoever' explicitly

    def test_no_secret_looking_values(self):
        self.assertNotRegex(self.text, r"(sk-or-v1-|ghp_|github_pat_|gsk_)[A-Za-z0-9]{8,}")
        for m in re.finditer(r"(OPENROUTER_API_KEY|GITHUB_TOKEN|GROQ_API_KEY|DASHSCOPE_API_KEY|CF_WORKERS_AI_TOKEN)=(\S+)", self.text):
            self.assertTrue(m.group(2).startswith(("'<", "\"<", "<", "…", "'…")), m.group(0))

    def test_linked_from_readme_and_usage(self):
        self.assertIn("docs/TERMUX.md", (REPO / "README.md").read_text(encoding="utf-8"))
        self.assertIn("TERMUX.md", (REPO / "docs" / "USAGE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
