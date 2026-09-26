"""Secret detection / redaction and the repository scan.

The fake tokens below are synthetic (generated shapes, not real credentials); the
repo-wide scan at the end is the same check ``scripts/secret_scan.py`` runs in CI.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO

from godotai import secrets

def _fake(*parts: str) -> str:
    """Join at runtime: unlike ``"a" + "b"``, ``str.join`` is not constant-folded by
    CPython, so neither this source file nor its ``.pyc`` ever contains a
    token-shaped literal (keeps every secret scanner, including GitHub's, quiet)."""
    return "".join(parts)


FAKE_ANTHROPIC = _fake("sk-ant-", "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
FAKE_OPENAI = _fake("sk-proj-", "Q" * 40)
FAKE_GHP = _fake("ghp_", "x1Y2z3" * 7)
FAKE_PAT = _fake("github_pat_", "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
FAKE_AWS = _fake("AKIA", "ABCDEFGHIJKLMNOP")
FAKE_GOOGLE = _fake("AIza", "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q")
FAKE_KAGGLE = _fake("KGAT_", "0123456789abcdef0123456789abcdef")
FAKE_CF = _fake("cfat_", "ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210")
PEM = _fake("-----BEGIN RSA PRIVATE ", "KEY-----\nMIIEow...\n-----END RSA PRIVATE ", "KEY-----")
FAKE_SLACK = _fake("xoxb-", "12345678901-abcdefghij")


class DetectionTests(unittest.TestCase):
    def kinds(self, text: str) -> list[str]:
        return [k for _, k in secrets.find_in_text(text)]

    def test_vendor_token_shapes(self):
        self.assertEqual(self.kinds(f"key = {FAKE_ANTHROPIC}"), ["anthropic_key"])
        self.assertEqual(self.kinds(f"OPENAI={FAKE_OPENAI}"), ["openai_key"])
        self.assertIn("github_token", self.kinds(f"token: {FAKE_GHP}"))
        self.assertIn("github_token", self.kinds(FAKE_PAT))
        self.assertIn("aws_access_key", self.kinds(f"aws {FAKE_AWS} id"))
        self.assertIn("google_api_key", self.kinds(FAKE_GOOGLE))
        self.assertIn("slack_token", self.kinds(FAKE_SLACK))
        self.assertEqual(self.kinds(PEM), ["private_key_block"])
        self.assertIn("bearer_token", self.kinds("Authorization: Bearer " + "t" * 30))

    def test_documented_variable_assignments(self):
        for line in (f"export KAGGLE_API_TOKEN={FAKE_KAGGLE}", f"CLOUDFLARE_API_TOKEN: '{FAKE_CF}'",
                     f'ANTHROPIC_API_KEY = "{FAKE_ANTHROPIC}"', f"cf_aig_token={FAKE_CF}",
                     "GODOT_ANDROID_KEYSTORE_RELEASE_PASSWORD=" "correct-horse-battery",
                     f"api_key = {'z' * 30}",
                     # the deploy/cloudflare path adds two more credential names: the tunnel token that cloudflared
                     # receives and the chat server's bearer token (both must only ever live in an untracked .env)
                     f"TUNNEL_TOKEN={'e' * 40}", f"export CLOUDFLARE_TUNNEL_TOKEN='{'e' * 40}'",
                     f"CF_TUNNEL_TOKEN: {'e' * 40}", f"GODOTAI_CHAT_TOKEN={'s' * 24}"):
            self.assertTrue(self.kinds(line), line)

    def test_placeholders_and_names_are_not_secrets(self):
        clean = [
            "export KAGGLE_API_TOKEN=<paste-your-token-here>",
            "KAGGLE_API_TOKEN=${{ secrets.KAGGLE_API_TOKEN }}",
            "CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN",
            "ANTHROPIC_API_KEY=your-key-here",
            "api_key = os.environ.get('ANTHROPIC_API_KEY', '')",
            "ANTHROPIC_API_KEY=xxxxxxxxxxxxxxxx",
            "token = REDACTED_PLACEHOLDER_VALUE",
            "The variable names are KAGGLE_API_TOKEN and CLOUDFLARE_API_TOKEN.",
            "Authorization: Bearer <gateway token>",
            "Authorization: Bearer ${CF_AIG_TOKEN}",
            "cache_control: {type: ephemeral}",
            "api_key: str | None = None",
            "KAGGLE_API_TOKEN={{ secrets.KAGGLE_API_TOKEN }}",
            "TUNNEL_TOKEN=${TUNNEL_TOKEN}",
            "TUNNEL_TOKEN=<paste the tunnel token written by cloudflare_setup.py>",
            "GODOTAI_CHAT_TOKEN=<choose-a-secret>",
            "- TUNNEL_TOKEN",
        ]
        for line in clean:
            self.assertEqual(self.kinds(line), [], line)

    def test_line_numbers_and_allow_marker(self):
        hits = secrets.find_in_text(f"line one\n\n{FAKE_ANTHROPIC}\nfour\n" + PEM.splitlines()[0])
        self.assertEqual(hits, [(3, "anthropic_key"), (5, "private_key_block")])
        self.assertEqual(secrets.find_in_text(f"{FAKE_ANTHROPIC}  # secret-scan:allow (format example)"), [])


class RedactionTests(unittest.TestCase):
    def test_redact_text_keeps_context_and_counts(self):
        text = f"export KAGGLE_API_TOKEN={FAKE_KAGGLE}\nx-api-key: {FAKE_ANTHROPIC}\nBearer {'q' * 30}\n{PEM}"
        out, n = secrets.redact(text)
        self.assertEqual(n, 4, out)
        self.assertNotIn(FAKE_KAGGLE, out)
        self.assertNotIn(FAKE_ANTHROPIC, out)
        self.assertNotIn("MIIEow", out)
        self.assertIn("export KAGGLE_API_TOKEN=[REDACTED]", out)
        self.assertIn("x-api-key: [REDACTED]", out)
        self.assertEqual(secrets.redact("nothing here")[1], 0)
        self.assertEqual(secrets.redact("export KAGGLE_API_TOKEN=<token>"), ("export KAGGLE_API_TOKEN=<token>", 0))

    def test_redact_obj_walks_structures(self):
        obj = {"messages": [{"role": "user", "content": [{"type": "text", "text": f"use {FAKE_GHP} please"}]}],
               "tools": ({"name": "x"},), "n": 3, "none": None, "env": {"GITHUB_TOKEN": FAKE_GHP}}
        counter = [0]
        red = secrets.redact_obj(obj, counter)
        self.assertEqual(counter[0], 2)
        self.assertEqual(red["messages"][0]["content"][0]["text"], "use [REDACTED] please")
        self.assertEqual(red["env"]["GITHUB_TOKEN"], "[REDACTED]")
        self.assertEqual(red["n"], 3)
        self.assertIsNone(red["none"])
        self.assertEqual(red["tools"], ({"name": "x"},), "non-list/dict containers pass through untouched")


class TreeScanTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="godotai-scan-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_scan_tree_reports_paths_and_skips_noise(self):
        (self.root / "src").mkdir()
        (self.root / "src" / "ok.py").write_text("KEY = os.environ['ANTHROPIC_API_KEY']\n", encoding="utf-8")
        (self.root / "src" / "leak.env").write_text(f"CLOUDFLARE_API_TOKEN={FAKE_CF}\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text(f"token = {FAKE_GHP}\n", encoding="utf-8")
        (self.root / "build").mkdir()
        (self.root / "build" / "out.txt").write_text(FAKE_ANTHROPIC, encoding="utf-8")
        (self.root / "icon.png").write_bytes(b"\x89PNG" + FAKE_ANTHROPIC.encode())
        (self.root / "binary.bin").write_bytes(b"\xff\xfe" + FAKE_ANTHROPIC.encode() + b"\xff")
        findings = secrets.scan_tree(self.root)
        self.assertEqual([(f.path, f.line, f.kind) for f in findings], [("src/leak.env", 1, "assigned_secret")])
        self.assertNotIn(FAKE_CF, str(findings[0]), "previews never contain the secret")

    def test_repository_is_clean(self):
        findings = secrets.scan_tree(REPO)
        self.assertEqual(findings, [], "\n".join(str(f) for f in findings))


if __name__ == "__main__":
    unittest.main()
