"""Hygiene checks for deploy/cloudflare/ — structural (no YAML parser, no Docker): the files must keep the
security properties the README promises, and must never carry a credential or an account-specific identifier."""
from __future__ import annotations

import re
import unittest

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
