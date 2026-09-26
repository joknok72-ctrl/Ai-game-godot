"""Offline coverage of the training helpers: dataset validation/dry-run, Kaggle metadata,
the secret scan CLI. Nothing here touches Kaggle, Cloudflare or a GPU."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import REPO
from test_evals_dataset import anthropic_run, openai_run

from godotai import dataset


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


train = load_module(REPO / "training" / "train_qlora.py", "train_qlora")
kpush = load_module(REPO / "scripts" / "kaggle_push.py", "kaggle_push")


class TrainQloraDryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="godotai-train-"))
        runs = self.tmp / "runs"
        runs.mkdir()
        (runs / "a.json").write_text(json.dumps(anthropic_run()), encoding="utf-8")
        (runs / "b.json").write_text(json.dumps(openai_run()), encoding="utf-8")
        self.data = self.tmp / "sft.jsonl"
        dataset.extract(runs, self.data)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extracted_dataset_validates_and_renders(self):
        examples, problems = train.load_examples(self.data)
        self.assertEqual(problems, [])
        self.assertEqual(len(examples), 2)
        msgs, tools = train.to_hf_messages(examples[0])
        self.assertEqual(msgs[0]["role"], "system")
        call = next(m for m in msgs if m["role"] == "assistant" and m.get("tool_calls"))
        self.assertEqual(call["tool_calls"][0]["type"], "function")
        self.assertEqual(call["tool_calls"][0]["function"]["name"], "write_file")
        self.assertIsInstance(call["tool_calls"][0]["function"]["arguments"], dict)
        tool_msg = next(m for m in msgs if m["role"] == "tool")
        self.assertIn("tool_call_id", tool_msg)
        err = [m for m in msgs if m["role"] == "tool" and m["content"].startswith("ERROR: ")]
        self.assertTrue(err, "tool errors are marked in the rendered transcript")
        self.assertEqual(tools[0]["type"], "function")
        self.assertIn("parameters", tools[0]["function"])
        text = train.render_plain(msgs, tools)
        self.assertIn("<tool_call>", text)
        self.assertIn("<tools>", text)
        self.assertNotIn("secret reasoning", text)

    def test_conversational_rows_for_assistant_only_loss(self):
        # TRL conversational format: `messages` (+ `tools`), assistant turns carry tool_calls,
        # tool results are role=tool — what SFTConfig(assistant_only_loss=True) expects.
        examples, _ = train.load_examples(self.data)
        row = train.to_conversational_row(examples[0])
        self.assertEqual(set(row), {"messages", "tools"})
        self.assertEqual({m["role"] for m in row["messages"]} - {"system", "user", "assistant", "tool"}, set())
        self.assertTrue(any(m["role"] == "assistant" and m.get("tool_calls") for m in row["messages"]))
        self.assertEqual(row["tools"][0]["type"], "function")
        no_tools = dict(examples[0], tools=[])
        self.assertEqual(set(train.to_conversational_row(no_tools)), {"messages"})
        args = train.build_parser().parse_args(["--data", "x.jsonl", "--assistant-only-loss"])
        self.assertTrue(args.assistant_only_loss)
        self.assertFalse(train.build_parser().parse_args(["--data", "x.jsonl"]).assistant_only_loss)

    def test_dry_run_reports_statistics(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = train.main(["--data", str(self.data), "--dry-run", "--max-seq-length", "64", "--min-iterations", "0"])
        out = buf.getvalue()
        self.assertEqual(rc, 0, out)
        self.assertIn("2 usable example(s), 0 rejected", out)
        self.assertIn("engine-verified: 2/2", out)
        self.assertIn("longer than --max-seq-length 64: 2", out)

    def test_validation_catches_bad_examples(self):
        bad = self.tmp / "bad.jsonl"
        lines = [
            json.dumps({"messages": []}),
            json.dumps({"messages": [{"role": "assistant", "content": "x"}]}),
            json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": ""}]}),
            json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "", "tool_calls": [{"name": "", "args": []}]},
                                     {"role": "tool", "tool_call_id": "zzz", "content": "x"}]}),
            json.dumps({"messages": [{"role": "user", "content": "hi"}, {"role": "wizard", "content": "x"}]}),
            "{not json",
        ]
        bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(train.DatasetError):
            train.load_examples(bad)
        bad.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        examples, problems = train.load_examples(bad)
        self.assertEqual(examples, [])
        joined = "\n".join(problems)
        for expect in ("no messages", "first message must be from the user", "empty assistant turn",
                       "tool call without name", "args must be an object", "unknown call zzz", "bad role 'wizard'"):
            self.assertIn(expect, joined)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = train.main(["--data", str(bad), "--dry-run"])
        self.assertEqual(rc, 1)
        self.assertIn("no usable examples", buf.getvalue())

    def test_training_path_fails_cleanly_without_ml_stack(self):
        if importlib.util.find_spec("trl") is not None:
            self.skipTest("ML stack installed — training path would actually start")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            rc = train.main(["--data", str(self.data), "--out", str(self.tmp / "out")])
        self.assertEqual(rc, 2)
        self.assertIn("training dependencies missing", err.getvalue())

    def test_kernel_script_compiles_and_has_no_secrets_or_hardcoded_auth(self):
        src = (REPO / "training" / "kaggle" / "godotai_sft_kaggle.py").read_text(encoding="utf-8")
        compile(src, "godotai_sft_kaggle.py", "exec")
        self.assertNotIn("KAGGLE_API_TOKEN", src, "the kernel must never need the Kaggle token")
        self.assertIn("UserSecretsClient", src)
        self.assertIn("/kaggle/working", src)


class KagglePushTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="godotai-home-"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_metadata_matches_documented_fields(self):
        meta = json.loads((REPO / "training" / "kaggle" / "kernel-metadata.json").read_text(encoding="utf-8"))
        for key in ("id", "title", "code_file", "language", "kernel_type", "is_private", "enable_gpu", "enable_internet",
                    "machine_shape", "dataset_sources", "competition_sources", "kernel_sources", "model_sources"):
            self.assertIn(key, meta, key)
        self.assertEqual(meta["kernel_type"], "script")
        self.assertEqual(meta["language"], "python")
        self.assertEqual(meta["is_private"], "true")
        self.assertEqual(meta["machine_shape"], "NvidiaTeslaT4", "GPU T4 x2 per kaggle-cli docs")
        self.assertTrue((REPO / "training" / "kaggle" / meta["code_file"]).is_file())
        self.assertTrue(meta["id"].startswith("KAGGLE_USERNAME/"))

    def test_render_substitutes_username_and_validates_accelerator(self):
        meta = kpush.render_metadata("someone")
        self.assertEqual(meta["id"], "someone/godotai-sft")
        self.assertEqual(meta["dataset_sources"], ["someone/godotai-sft-data"])
        meta = kpush.render_metadata("someone", "TpuV5E8")
        self.assertEqual(meta["machine_shape"], "TpuV5E8")
        with self.assertRaises(SystemExit):
            kpush.render_metadata("someone", "NvidiaGTX1080")
        staged = kpush.stage(meta)
        try:
            self.assertTrue((staged / "kernel-metadata.json").is_file())
            self.assertTrue((staged / "godotai_sft_kaggle.py").is_file())
            self.assertEqual(json.loads((staged / "kernel-metadata.json").read_text())["id"], "someone/godotai-sft")
        finally:
            shutil.rmtree(staged, ignore_errors=True)

    def test_token_detection_never_reads_values(self):
        self.assertIsNone(kpush.token_configured(env={}, home=self.home))
        self.assertEqual(kpush.token_configured(env={"KAGGLE_API_TOKEN": "x"}, home=self.home), "KAGGLE_API_TOKEN (environment)")
        (self.home / ".kaggle").mkdir()
        (self.home / ".kaggle" / "access_token").write_text("x", encoding="utf-8")
        self.assertEqual(kpush.token_configured(env={}, home=self.home), "~/.kaggle/access_token")
        self.assertNotIn("x", kpush.token_configured(env={}, home=self.home) or "")

    def test_check_mode_needs_no_credentials_and_calls_nothing(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("KAGGLE")}
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(kpush.subprocess, "run") as run:
            os.environ["HOME"] = home
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = kpush.main(["--check"])
            self.assertEqual(rc, 0, buf.getvalue())
            self.assertIn("check ok: kernel KAGGLE_USERNAME/godotai-sft", buf.getvalue())
            self.assertIn("auth source: none", buf.getvalue())
            run.assert_not_called()

    def test_cli_dry_run_makes_no_remote_call(self):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"KAGGLE_USERNAME": "someone", "KAGGLE_API_TOKEN": "fake-token-value"}), \
                mock.patch.object(kpush.subprocess, "run", side_effect=AssertionError("must not be called")), \
                redirect_stdout(buf):
            rc = kpush.main([])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("auth source: KAGGLE_API_TOKEN (environment)", out)
        self.assertIn("kernel: someone/godotai-sft", out)
        self.assertIn("dry run only", out)
        self.assertNotIn("fake-token-value", out)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"KAGGLE_USERNAME": ""}), mock.patch("sys.stderr", err):
            self.assertEqual(kpush.main([]), 2)
        self.assertIn("KAGGLE_USERNAME is not set", err.getvalue())

    def test_push_builds_the_documented_command(self):
        calls = []

        def fake_run(cmd, check=False):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.dict(os.environ, {"KAGGLE_USERNAME": "someone", "KAGGLE_API_TOKEN": "t"}), \
                mock.patch.object(kpush.shutil, "which", return_value="/usr/bin/kaggle"), \
                mock.patch.object(kpush.subprocess, "run", side_effect=fake_run), redirect_stdout(io.StringIO()):
            rc = kpush.main(["--push", "--status", "--no-run", "--timeout", "600"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][:4], ["kaggle", "kernels", "push", "-p"])
        self.assertIn("--accelerator", calls[0])
        self.assertEqual(calls[0][calls[0].index("--accelerator") + 1], "NvidiaTeslaT4")
        self.assertIn("--no-run", calls[0])
        self.assertEqual(calls[0][calls[0].index("--timeout") + 1], "600")
        self.assertEqual(calls[1], ["kaggle", "kernels", "status", "someone/godotai-sft"])


class SecretScanCliTests(unittest.TestCase):
    def test_cli_clean_and_dirty(self):
        script = REPO / "scripts" / "secret_scan.py"
        tmp = Path(tempfile.mkdtemp(prefix="godotai-scan-cli-"))
        try:
            (tmp / "ok.txt").write_text("export KAGGLE_API_TOKEN=<paste-your-token>\n", encoding="utf-8")
            r = subprocess.run([sys.executable, str(script), str(tmp)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("clean", r.stdout)
            (tmp / "leak.txt").write_text("".join(["export KAGGLE_API_TOKEN=", "KGAT_", "0123456789abcdef0123456789abcdef", "\n"]), encoding="utf-8")  # runtime join: not constant-folded
            r = subprocess.run([sys.executable, str(script), str(tmp)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("leak.txt:1: assigned_secret", r.stdout)
            self.assertIn("ROTATE", r.stderr)
            self.assertNotIn("0123456789abcdef", r.stdout + r.stderr)
            r = subprocess.run([sys.executable, str(script), str(tmp / "missing")], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
