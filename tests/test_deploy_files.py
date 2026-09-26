"""Hygiene checks for deploy/cloudflare/ — structural (no YAML parser, no Docker): the files must keep the
security properties the README promises, and must never carry a credential or an account-specific identifier."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO

from godotai import secrets

DEPLOY = REPO / "deploy" / "cloudflare"
COMPOSE = (DEPLOY / "compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (DEPLOY / "env.example").read_text(encoding="utf-8")
DEPLOY_README = (DEPLOY / "README.md").read_text(encoding="utf-8")
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
SYNTHETIC_IDS = {"0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"}   # the obviously fake test ids


def service_block(name: str) -> str:
    """Text of one top-level compose service (2-space indented key) up to the next service/top-level key."""
    m = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|^[a-z]+:\n|\Z)", COMPOSE)
    assert m, f"service {name} not found"
    return m.group(1)


class ComposeTests(unittest.TestCase):
    def test_nothing_publishes_a_port(self):
        self.assertNotRegex(COMPOSE, r"(?m)^\s*ports:", "no service may publish a host port — cloudflared is the only way in")
        self.assertIn('- "8765"', service_block("chat"))
        self.assertIn("expose:", service_block("chat"))

    def test_chat_is_access_protected_and_fails_fast_without_the_values(self):
        chat = service_block("chat")
        for flag in ("--host 0.0.0.0", "--public-host ${GODOTAI_PUBLIC_HOST:?", "--access-team-domain ${GODOTAI_ACCESS_TEAM_DOMAIN:?",
                     "--access-aud ${GODOTAI_ACCESS_AUD:?"):
            self.assertIn(flag, chat, flag)
        self.assertNotIn("--token", chat, "Access JWTs, not a shared token, authenticate visitors")
        self.assertIn("GODOTAI_PROVIDER: openai_compat", chat)
        self.assertIn("OPENAI_BASE_URL: ${OPENAI_BASE_URL:-http://model:8000/v1}", chat)
        self.assertIn("GODOTAI_ROUTE: ${GODOTAI_ROUTE:-direct}", chat, "your own model server is the default route")
        self.assertIn("GODOTAI_SERVING: ${GODOTAI_SERVING:-self_hosted}", chat)
        for vendor in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotIn(vendor, COMPOSE, f"{vendor}: the website path uses your own model server only")

    def test_workers_ai_path_gets_only_a_narrow_runtime_token(self):
        chat = service_block("chat")
        self.assertIn("CF_WORKERS_AI_TOKEN: ${CF_WORKERS_AI_TOKEN:-}", chat)
        self.assertIn("CF_ACCOUNT_ID: ${CF_ACCOUNT_ID:-}", chat)
        self.assertNotIn("CLOUDFLARE_API_TOKEN", COMPOSE, "the Tunnel/Access/DNS setup token must never reach a container")

    def test_run_quota_is_on_by_default_for_the_public_site(self):
        chat = service_block("chat")
        self.assertIn("GODOTAI_MAX_CONCURRENT_RUNS: ${GODOTAI_MAX_CONCURRENT_RUNS:-1}", chat)
        self.assertIn("GODOTAI_MAX_RUNS_PER_DAY: ${GODOTAI_MAX_RUNS_PER_DAY:-40}", chat)
        main_src = (REPO / "godotai" / "__main__.py").read_text(encoding="utf-8")
        for name in ("GODOTAI_MAX_CONCURRENT_RUNS", "GODOTAI_MAX_RUNS_PER_DAY", "--max-concurrent-runs", "--max-runs-per-day"):
            self.assertIn(name, main_src, f"{name} must be read by the chat command")

    def test_healthcheck_uses_the_unauthenticated_health_route_only(self):
        chat = service_block("chat")
        self.assertIn("healthcheck:", chat)
        self.assertIn("http://127.0.0.1:8765/healthz", chat)
        self.assertNotIn("/api/status", chat.split("healthcheck:", 1)[1], "/api/status needs an Access JWT; the probe must not")

    def test_cloudflared_gets_the_tunnel_token_from_the_environment_only(self):
        cfd = service_block("cloudflared")
        self.assertIn("TUNNEL_TOKEN: ${TUNNEL_TOKEN:?", cfd)
        self.assertIn("tunnel --no-autoupdate run", cfd)
        self.assertNotIn("--token", cfd, "the token must not be on the command line (visible in `ps`/logs)")
        self.assertNotIn("credentials-file", COMPOSE)

    def test_exactly_the_documented_model_profiles(self):
        profiles = re.findall(r'profiles:\s*\["([a-z-]+)"\]', COMPOSE)
        self.assertEqual(profiles, ["gpu-base", "gpu-lora", "cpu"])
        for svc in ("model", "model-lora"):
            block = service_block(svc)
            self.assertIn("--served-model-name godotai", block) if svc == "model" else self.assertIn("--lora-modules godotai=", block)
            self.assertIn("--enable-auto-tool-choice --tool-call-parser hermes", block, "Qwen2.5 tool calls need the hermes parser")
            self.assertIn("Qwen/Qwen2.5-Coder-7B-Instruct", block)
            self.assertIn("driver: nvidia", block)
        self.assertIn("aliases: [model]", service_block("model-lora"), "the chat keeps talking to http://model:8000/v1")
        self.assertIn('- "11434"', service_block("ollama"))
        code_lines = "\n".join(l for l in COMPOSE.splitlines() if not l.strip().startswith("#"))
        self.assertNotIn("anthropic", code_lines.lower(), "no vendor model anywhere in the website path (comments aside)")

    def test_no_credential_or_account_identifier_in_deploy_files(self):
        for path in sorted(DEPLOY.iterdir()):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(secrets.find_in_text(text), [], f"{path.name}: token-shaped string")
            self.assertEqual(set(HEX32.findall(text)) - SYNTHETIC_IDS, set(), f"{path.name}: looks like a real account/zone id")
        self.assertEqual(secrets.scan_tree(DEPLOY), [])


class EnvExampleTests(unittest.TestCase):
    def assignments(self) -> dict[str, str]:
        out = {}
        for line in ENV_EXAMPLE.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            out[key.strip()] = value.split("#", 1)[0].strip()
        return out

    def test_every_secret_or_account_specific_value_is_an_angle_bracket_placeholder(self):
        values = self.assignments()
        for key in ("GODOTAI_PUBLIC_HOST", "GODOTAI_ACCESS_TEAM_DOMAIN", "GODOTAI_ACCESS_AUD", "CF_ACCOUNT_ID", "TUNNEL_ID",
                    "TUNNEL_TOKEN"):
            self.assertIn(key, values, key)
            self.assertRegex(values[key], r"^<.*>$", f"{key} must be a <placeholder>")
        # what is not a placeholder is a plain, non-secret model setting
        self.assertEqual(values["GODOTAI_MODEL"], "godotai")
        self.assertEqual(values["OPENAI_BASE_URL"], "http://model:8000/v1")
        self.assertEqual(values["GODOTAI_BASE_MODEL"], "Qwen/Qwen2.5-Coder-7B-Instruct")
        self.assertTrue(values["VLLM_MAX_MODEL_LEN"].isdigit())
        self.assertEqual((values["GODOTAI_MAX_CONCURRENT_RUNS"], values["GODOTAI_MAX_RUNS_PER_DAY"]), ("1", "40"),
                         "the documented defaults match compose.yml")
        for key in ("CLOUDFLARE_API_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CF_WORKERS_AI_TOKEN"):
            self.assertNotIn(key, values, f"{key} does not belong in the compose .env as an active line")
        self.assertIn("# CF_WORKERS_AI_TOKEN=<", ENV_EXAMPLE, "path B is documented as a commented placeholder")
        self.assertIn("# GODOTAI_ROUTE=workers_ai", ENV_EXAMPLE)
        self.assertIn("# GODOTAI_SERVING=managed", ENV_EXAMPLE, "Workers AI is labelled managed, never self-hosted")
        self.assertEqual(secrets.find_in_text(ENV_EXAMPLE), [])

    def test_lora_and_cpu_variants_are_documented_with_honest_identity(self):
        self.assertIn("GODOTAI_MODEL_KIND=fine_tune", ENV_EXAMPLE)
        self.assertIn("GODOTAI_ADAPTER=/adapters/godotai", ENV_EXAMPLE)
        self.assertIn("http://ollama:11434/v1", ENV_EXAMPLE)


PAAS = REPO / "deploy" / "paas"
PAAS_DOCKERFILE = (PAAS / "Dockerfile").read_text(encoding="utf-8")
PAAS_ENTRYPOINT = (PAAS / "entrypoint.sh").read_text(encoding="utf-8")
PAAS_README = (PAAS / "README.md").read_text(encoding="utf-8")


class PaasImageTests(unittest.TestCase):
    """deploy/paas/ — the app-only image for 1 GB hosts: no model, no Android toolchain, no credential, auth mandatory."""

    def test_copied_paths_exist_and_the_image_is_app_only(self):
        for m in re.finditer(r"^COPY\s+(.+?)\s+\S+$", PAAS_DOCKERFILE, re.M):
            for src in m.group(1).split():
                self.assertTrue((REPO / src).exists(), f"Dockerfile COPY {src} does not exist in the repo")
        self.assertNotIn("Godot_v", PAAS_DOCKERFILE, "engine assets come from godot.toml via the installer")
        self.assertIn("install-godot --system --editor-only", PAAS_DOCKERFILE, "editor only: no 1.4 GB export templates")
        self.assertNotIn("setup-android", PAAS_DOCKERFILE, "APK export belongs to GitHub Actions, not the 1 GB host")
        self.assertNotIn("openjdk", PAAS_DOCKERFILE)
        self.assertNotIn("vllm", PAAS_DOCKERFILE.lower())
        self.assertNotIn("ollama", PAAS_DOCKERFILE.lower())
        self.assertIn("GODOTAI_CONFIG=/opt/godotai/godot.toml", PAAS_DOCKERFILE)
        self.assertIn("linux.arm64", PAAS_DOCKERFILE, "Oracle Ampere A1 is the free ARM host")
        self.assertIn("TARGETARCH", PAAS_DOCKERFILE)

    def test_quota_defaults_and_health_probe(self):
        self.assertIn("GODOTAI_MAX_CONCURRENT_RUNS=1", PAAS_DOCKERFILE, "one verification ≈ 640 MB: never two on a 1 GB host")
        self.assertIn("GODOTAI_MAX_RUNS_PER_DAY=40", PAAS_DOCKERFILE)
        self.assertIn("HEALTHCHECK", PAAS_DOCKERFILE)
        self.assertIn("/healthz", PAAS_DOCKERFILE)
        self.assertNotIn("/api/status", PAAS_DOCKERFILE, "/api/status needs the token; the probe must not carry it")
        self.assertIn('ENTRYPOINT ["godotai-entrypoint"]', PAAS_DOCKERFILE)

    def test_no_credential_or_account_identifier_in_paas_files(self):
        for path in sorted(PAAS.iterdir()):
            text = path.read_text(encoding="utf-8")
            self.assertEqual(secrets.find_in_text(text), [], f"{path.name}: token-shaped string")
            self.assertEqual(set(HEX32.findall(text)) - SYNTHETIC_IDS, set(), f"{path.name}: looks like a real account id")
        self.assertEqual(secrets.scan_tree(PAAS), [])
        docker_code = "\n".join(l for l in PAAS_DOCKERFILE.splitlines() if not l.lstrip().startswith("#"))
        entry_code = "\n".join(l for l in PAAS_ENTRYPOINT.splitlines() if not l.lstrip().startswith("#"))
        for name in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "DASHSCOPE_API_KEY", "CF_WORKERS_AI_TOKEN", "CLOUDFLARE_API_TOKEN",
                     "GODOTAI_CHAT_TOKEN", "CF_ACCOUNT_ID"):
            self.assertNotRegex(docker_code, rf"{name}\s*=", f"{name} must never be baked into the image")
            self.assertNotRegex(entry_code, rf"(?m)^\s*(export\s+)?{name}=", f"{name} must never be assigned in the entrypoint")
        self.assertNotIn("GODOTAI_CHAT_TOKEN=<", PAAS_DOCKERFILE, "even the example passes the token through with -e NAME, not on the command line")

    def test_readme_is_honest_about_deployment_state_and_the_1gb_verdict(self):
        self.assertIn("لم يُنشر أي شيء", PAAS_README)
        self.assertIn("Nothing has been deployed by this repository", PAAS_README)
        self.assertIn("RAILWAY_DOCKERFILE_PATH=deploy/paas/Dockerfile", PAAS_README)
        self.assertIn("app only", PAAS_README)
        self.assertIn("cannot host any Qwen inference model", PAAS_README)
        self.assertIn("512 MB", PAAS_README)
        self.assertIn("2 OCPUs and 12 GB", PAAS_README, "Oracle Always Free figures as read on 2026-09-26")
        self.assertIn("openssl rand -hex 32", PAAS_README)
        self.assertNotIn("forever free", PAAS_README.lower())
        self.assertNotIn("outperform", PAAS_README.lower())
        for name in ("GODOTAI_CHAT_TOKEN", "GODOTAI_PRESET", "GODOTAI_MAX_RUNS_PER_DAY", "PORT", "RAILWAY_PUBLIC_DOMAIN",
                     "RENDER_EXTERNAL_HOSTNAME"):
            self.assertIn(name, PAAS_README)
            self.assertIn(name, (REPO / "godotai" / "__main__.py").read_text(encoding="utf-8") + PAAS_ENTRYPOINT,
                          f"{name} is documented but nothing reads it")

    # -- the entrypoint really runs (sh + a stub python3), so the refusals are behaviour, not prose -------------
    def _run_entrypoint(self, env: dict[str, str], *args: str) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as d:
            stub = Path(d) / "python3"
            stub.write_text("#!/bin/sh\nprintf 'ARGV:%s\\n' \"$@\"\nprintf 'PLATFORM:%s\\n' \"${GODOTAI_ENGINE_PLATFORM:-unset}\"\n",
                            encoding="utf-8")
            stub.chmod(0o755)
            full_env = {"PATH": f"{d}:{os.environ.get('PATH', '/usr/bin:/bin')}", **env}
            proc = subprocess.run(["sh", str(PAAS / "entrypoint.sh"), *args], env=full_env, capture_output=True, text=True,
                                  timeout=30)
        return proc.returncode, proc.stdout, proc.stderr

    def test_entrypoint_refuses_a_public_server_without_authentication(self):
        rc, out, err = self._run_entrypoint({"GODOTAI_PRESET": "openrouter_free", "OPENROUTER_API_KEY": "fake"}, "chat")
        self.assertEqual(rc, 64)
        self.assertIn("refusing to start a public chat server without authentication", err)
        self.assertIn("GODOTAI_CHAT_TOKEN", err)
        self.assertIn("GODOTAI_ACCESS_AUD", err)
        self.assertNotIn("ARGV:", out, "python must never have been launched")

    def test_entrypoint_refuses_without_a_model_endpoint(self):
        rc, out, err = self._run_entrypoint({"GODOTAI_CHAT_TOKEN": "t"}, "chat")
        self.assertEqual(rc, 64)
        self.assertIn("no model endpoint", err)
        self.assertIn("GODOTAI_PRESET", err)
        self.assertNotIn("ARGV:", out)

    def test_entrypoint_launches_the_public_chat_server_with_the_quota_env_intact(self):
        env = {"GODOTAI_CHAT_TOKEN": "t", "GODOTAI_PRESET": "groq", "GROQ_API_KEY": "fake", "GODOTAI_GAMES_DIR": "/games"}
        rc, out, err = self._run_entrypoint(env, "chat", "--verbose")
        self.assertEqual(rc, 0, err)
        self.assertEqual([l for l in out.splitlines() if l.startswith("ARGV:")],
                         ["ARGV:-m", "ARGV:godotai", "ARGV:chat", "ARGV:--host", "ARGV:0.0.0.0", "ARGV:--games-dir", "ARGV:/games",
                          "ARGV:--verbose"])
        self.assertNotIn("--token", out, "the token travels in the environment, never on the command line")
        self.assertNotIn("--port", out, "the port comes from PORT/GODOTAI_PORT inside the chat command")
        # no first argument at all == chat; Cloudflare Access instead of a token is accepted as authentication
        rc, out, _ = self._run_entrypoint({"GODOTAI_ACCESS_AUD": "aud", "GODOTAI_BASE_URL": "http://model.internal:8000/v1"})
        self.assertEqual(rc, 0)
        self.assertIn("ARGV:chat", out)
        # any other sub-command passes straight through (doctor, presets …) with no auth requirement
        rc, out, _ = self._run_entrypoint({}, "presets", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual([l for l in out.splitlines() if l.startswith("ARGV:")], ["ARGV:-m", "ARGV:godotai", "ARGV:presets", "ARGV:--json"])

    def test_entrypoint_platform_handling_matches_the_installed_asset(self):
        self.assertIn('uname -m', PAAS_ENTRYPOINT)
        self.assertIn("aarch64", PAAS_ENTRYPOINT)
        self.assertIn("GODOTAI_ENGINE_PLATFORM=linux.arm64", PAAS_ENTRYPOINT)
        _rc, out, _ = self._run_entrypoint({"GODOTAI_ENGINE_PLATFORM": "linux.x86_64"}, "doctor")
        self.assertIn("PLATFORM:linux.x86_64", out, "an explicit platform is never overridden")


class GitignoreAndDocsTests(unittest.TestCase):
    def test_env_files_are_ignored(self):
        ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignore)
        self.assertIn(".env.*", ignore)

    def test_readme_keeps_the_rotation_warning_and_the_no_deploy_statement(self):
        self.assertIn("Roll", DEPLOY_README)
        self.assertIn("API Tokens", DEPLOY_README)
        self.assertIn("لم يُنشر أي شيء", DEPLOY_README)
        self.assertIn("Nothing has been deployed by this repository", DEPLOY_README)
        self.assertIn("--dry-run", DEPLOY_README)
        self.assertIn("0600", DEPLOY_README)
        for perm in ("Access: Apps and Policies: Edit", "Cloudflare Tunnel: Edit", "DNS: Edit"):
            self.assertIn(perm, DEPLOY_README, perm)
        self.assertNotIn("outperform", DEPLOY_README.lower())

    def test_readme_matches_the_code_it_describes(self):
        # names the README tells the user to look for must exist in the code
        main_src = (REPO / "godotai" / "__main__.py").read_text(encoding="utf-8")
        access_src = (REPO / "godotai" / "chat" / "access.py").read_text(encoding="utf-8")
        app_js = (REPO / "godotai" / "chat" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Cloudflare Access required on every request", main_src)
        self.assertIn("no Cloudflare Access token", access_src)
        self.assertIn("محمي بـ Cloudflare Access", app_js)
        for flag in ("--public-host", "--access-team-domain", "--access-aud"):
            self.assertIn(flag, main_src)
            self.assertIn(flag, DEPLOY_README)


if __name__ == "__main__":
    unittest.main()
