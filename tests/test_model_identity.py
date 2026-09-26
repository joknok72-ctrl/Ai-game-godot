"""What the model *is* must be stated plainly and enforced (godotai/model_identity.py)."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import PRIVATE_ENV, REPO, repo_config

from godotai import __main__ as cli
from godotai.model_identity import (GATEWAY_VENDOR_PREFIXES, ModelIdentity, ModelIdentityError, check_consistency,
                                    is_vendor_endpoint)


class IdentityRulesTests(unittest.TestCase):
    def test_default_is_an_honest_open_weight_deployment(self):
        m = ModelIdentity()
        self.assertEqual(m.kind, "open_weight_deployment")
        self.assertTrue(m.yours and m.open_weights)
        self.assertFalse(m.trained_by_you)
        d = m.describe()
        self.assertIsNone(d["quality_claim"])
        for lang in ("ar", "en"):
            text = m.disclosure(lang)
            self.assertIn("Qwen/Qwen2.5-Coder-7B-Instruct", text)
            self.assertIn("Apache-2.0", text)
        self.assertIn("not from scratch", m.disclosure("en"))
        self.assertIn("لم تُدرَّب من الصفر", m.disclosure("ar"))
        self.assertEqual(d["kind_label_ar"], "نشر خاص لنموذج مفتوح الوزن")
        self.assertEqual(json.loads(json.dumps(d)), d, "JSON-serialisable for /api/status")

    def test_fine_tune_needs_an_adapter_and_says_not_from_scratch(self):
        with self.assertRaises(ModelIdentityError):
            ModelIdentity(kind="fine_tune")
        m = ModelIdentity(kind="fine_tune", adapter="training/out/godotai-lora", name="godotai-ft")
        self.assertTrue(m.trained_by_you and m.yours)
        self.assertIn("Not trained from scratch", m.disclosure("en"))
        self.assertIn("training/out/godotai-lora", m.disclosure("en"))
        self.assertEqual(m.describe()["adapter"], "training/out/godotai-lora")

    def test_from_scratch_requires_evidence_and_no_base(self):
        with self.assertRaises(ModelIdentityError):
            ModelIdentity(kind="from_scratch", base_model="", training_report="")
        with self.assertRaises(ModelIdentityError):
            ModelIdentity(kind="from_scratch", training_report="docs/pretraining.md")     # base_model still set
        m = ModelIdentity(kind="from_scratch", base_model="", training_report="docs/pretraining.md")
        self.assertTrue(m.trained_by_you)
        self.assertIn("docs/pretraining.md", m.disclosure("ar"))

    def test_vendor_api_is_never_yours(self):
        with self.assertRaises(ModelIdentityError):
            ModelIdentity(kind="vendor_api", base_model="claude-fable-5-1")                 # serving must be vendor
        with self.assertRaises(ModelIdentityError):
            ModelIdentity(serving="vendor")                                                # only for vendor_api
        m = ModelIdentity(name="claude", kind="vendor_api", base_model="claude-fable-5-1", serving="vendor")
        self.assertFalse(m.yours or m.open_weights or m.trained_by_you)
        self.assertIn("not yours", m.disclosure("en"))
        self.assertIn("ليس نموذجك", m.disclosure("ar"))

    def test_invalid_values(self):
        for bad in ({"kind": "magic"}, {"serving": "cloud"}, {"name": " "}, {"base_model": ""}):
            with self.assertRaises(ModelIdentityError, msg=str(bad)):
                ModelIdentity(**bad)

    def test_vendor_endpoint_detection(self):
        self.assertTrue(is_vendor_endpoint("anthropic", None))
        self.assertTrue(is_vendor_endpoint("openai_compat", None))
        self.assertTrue(is_vendor_endpoint("openai_compat", "https://api.openai.com/v1"))
        self.assertFalse(is_vendor_endpoint("openai_compat", "http://127.0.0.1:8000/v1"))
        self.assertFalse(is_vendor_endpoint("openai_compat", None, "workers_ai"))
        self.assertTrue(is_vendor_endpoint("openai_compat", None, "cf_gateway", "openai/gpt-x"))
        self.assertTrue(is_vendor_endpoint("openai_compat", None, "cf_gateway", "Anthropic/claude-x"))
        self.assertFalse(is_vendor_endpoint("openai_compat", None, "cf_gateway", "workers-ai/@cf/qwen/qwen2.5-coder-32b-instruct"))
        self.assertIn("openai/", GATEWAY_VENDOR_PREFIXES)

    def test_check_consistency(self):
        mine = ModelIdentity()
        vendor = ModelIdentity(name="c", kind="vendor_api", base_model="claude-fable-5-1", serving="vendor")
        check_consistency(mine, "openai_compat", "godotai", "http://127.0.0.1:8000/v1")
        check_consistency(vendor, "anthropic", "claude-fable-5-1", None)
        check_consistency(vendor, "openai_compat", "gpt-x", "https://api.openai.com/v1")
        with self.assertRaises(ModelIdentityError) as cm:
            check_consistency(mine, "anthropic", "claude-fable-5-1", None)
        self.assertIn("Claude (Anthropic)", str(cm.exception))
        with self.assertRaises(ModelIdentityError) as cm:
            check_consistency(mine, "openai_compat", "gpt-x", "https://api.openai.com/v1")
        self.assertIn("api.openai.com", str(cm.exception))
        with self.assertRaises(ModelIdentityError) as cm:
            check_consistency(mine, "openai_compat", "openai/gpt-x", None, "cf_gateway")
        self.assertIn("AI Gateway", str(cm.exception))
        with self.assertRaises(ModelIdentityError):
            check_consistency(vendor, "openai_compat", "x", "http://localhost:11434/v1")


class RepoDocumentsTests(unittest.TestCase):
    """The shipped configuration and docs must match the code's honesty rules."""

    def test_godot_toml_model_section_matches_defaults(self):
        text = (REPO / "godot.toml").read_text(encoding="utf-8")
        self.assertRegex(text, r'(?m)^provider\s*=\s*"openai_compat"')
        self.assertRegex(text, r'(?m)^model\s*=\s*"godotai"')
        self.assertRegex(text, r'(?m)^\[model\]')
        self.assertRegex(text, r'(?m)^kind\s*=\s*"open_weight_deployment"')
        self.assertRegex(text, r'(?m)^base_model\s*=\s*"Qwen/Qwen2.5-Coder-7B-Instruct"')
        self.assertRegex(text, r'(?m)^base_license\s*=\s*"Apache-2.0"')
        self.assertNotRegex(text, r'(?m)^provider\s*=\s*"anthropic"', "Claude is documented as an opt-in, not the default")
        for cred in ("CLOUDFLARE_API_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            self.assertNotRegex(text, rf'(?m)^{cred}\s*=', f"{cred} must never be assigned in the repo")

    def test_docs_distinguish_the_three_kinds_and_make_no_unbacked_claim(self):
        model_doc = (REPO / "docs" / "MODEL.md").read_text(encoding="utf-8")
        for needle in ("open_weight_deployment", "fine_tune", "from_scratch", "eval compare", "Apache-2.0",
                       "Qwen2.5-Coder", "claude-fable-5-1"):
            self.assertIn(needle, model_doc, needle)
        low = model_doc.lower()
        self.assertNotIn("outperforms claude", low)
        self.assertNotIn("better than claude fable", low)
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("eval compare", readme)
        self.assertIn("Qwen", readme)

    def test_doctor_prints_identity_and_private_server_line(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**PRIVATE_ENV, "GODOTAI_SKIP_MODEL_PROBE": "1"}), redirect_stdout(out):
            cli.main(["doctor"])
        text = out.getvalue()
        self.assertIn("➖ your model server (http://127.0.0.1:8000/v1): not probed", text,
                      "a skipped probe must not be shown as a pass")
        self.assertIn("identity: godotai — private deployment of an open-weight model (base Qwen/Qwen2.5-Coder-7B-Instruct, Apache-2.0), on your own server — yours", text)
        self.assertIn("not from scratch", text)

    def test_vendor_config_file_loads_when_declared_honestly(self):
        from godotai.config import ConfigError, load_config
        base = (REPO / "godot.toml").read_text(encoding="utf-8")
        d = Path(tempfile.mkdtemp())
        p = d / "godot.toml"
        text = base.replace('provider = "openai_compat"', 'provider = "anthropic"', 1).replace('model = "godotai"', 'model = "claude-fable-5-1"', 1)
        p.write_text(text, encoding="utf-8")
        with mock.patch.dict(os.environ, PRIVATE_ENV):
            with self.assertRaises(ConfigError) as cm:
                load_config(p)
            self.assertIn("vendor_api", str(cm.exception))
            honest = text.replace('kind = "open_weight_deployment"', 'kind = "vendor_api"', 1)
            honest = honest.replace('serving = "self_hosted"', 'serving = "vendor"', 1)
            honest = honest.replace('base_model = "Qwen/Qwen2.5-Coder-7B-Instruct"', 'base_model = "claude-fable-5-1"', 1)
            p.write_text(honest, encoding="utf-8")
            cfg = load_config(p)
        self.assertEqual(cfg.agent.provider, "anthropic")
        self.assertFalse(cfg.model.yours)
        self.assertIn("not yours", cfg.model.disclosure("en"))


if __name__ == "__main__":
    unittest.main()
