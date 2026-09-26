"""`godotai eval compare` — the only source of a "better than" statement, and it refuses to guess."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from _helpers import PRIVATE_ENV

from godotai import __main__ as cli
from godotai import evals


def result(task: str, model: str | None, passed: bool) -> dict:
    return {"task_id": task, "passed": passed, "checks": [], "verification": None,
            "agent": {"model": model, "status": "success"} if model else None, "seconds": 1.0, "engine": "4.7.2-stable"}


def write_results(d: Path, rows: list[dict]) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for i, r in enumerate(rows):
        (d / f"{r['task_id']}-{i:03d}.json").write_text(json.dumps(r), encoding="utf-8")
    (d / "junk.json").write_text("{not json", encoding="utf-8")
    (d / "notes.txt").write_text("ignored", encoding="utf-8")


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="godotai-evals-"))

    def test_no_results_means_no_evidence_not_a_claim(self):
        cmp = evals.compare_models(self.d, "godotai", "claude-fable-5-1")
        self.assertEqual(cmp.common_tasks, [])
        self.assertTrue(cmp.verdict().startswith("NO EVIDENCE"))
        self.assertIn("no results for candidate 'godotai'", cmp.verdict())
        self.assertIn("no results for reference 'claude-fable-5-1'", cmp.verdict())
        md = cmp.to_markdown()
        self.assertIn("no model judged another model", md)
        self.assertNotIn("better", cmp.verdict().split("No claim")[0].lower().replace("which model is better", ""))

    def test_score_only_results_do_not_count(self):
        write_results(self.d, [result("flappy", None, True), result("flappy", None, True)])
        cmp = evals.compare_models(self.d, "godotai", "claude-fable-5-1")
        self.assertEqual(cmp.models_seen, [])
        self.assertTrue(cmp.verdict().startswith("NO EVIDENCE"))

    def test_disjoint_tasks_give_no_verdict(self):
        write_results(self.d, [result("flappy", "godotai", True), result("runner", "claude-fable-5-1", True)])
        cmp = evals.compare_models(self.d, "godotai", "claude-fable-5-1")
        self.assertEqual(cmp.models_seen, ["claude-fable-5-1", "godotai"])
        self.assertIn("different tasks", cmp.verdict())
        self.assertTrue(cmp.verdict().startswith("NO EVIDENCE"))

    def test_common_tasks_are_tallied_honestly(self):
        rows = [result("flappy", "godotai", True), result("flappy", "godotai", False),          # 1/2
                result("flappy", "claude-fable-5-1", True),                                     # 1/1
                result("runner", "godotai", True), result("runner", "claude-fable-5-1", False),  # ahead
                result("match3", "godotai", True), result("match3", "claude-fable-5-1", True),   # tied
                result("only-ref", "claude-fable-5-1", True), result("x", "other-model", True)]
        write_results(self.d, rows)
        cmp = evals.compare_models(self.d, "godotai", "claude-fable-5-1")
        self.assertEqual(cmp.common_tasks, ["flappy", "match3", "runner"])
        self.assertEqual(cmp.candidate_scores["flappy"].attempts, 2)
        self.assertEqual(cmp.candidate_scores["flappy"].rate, 0.5)
        v = cmp.verdict()
        self.assertIn("on 3 common task(s)", v)
        self.assertIn("ahead on 1, behind on 1, tied on 1", v)
        self.assertNotIn("Too few", v)
        md = cmp.to_markdown()
        self.assertIn("| only-ref | — | 1/1 |", md)
        self.assertIn("| flappy | 1/2 | 1/1 |", md)
        self.assertIn("models seen: claude-fable-5-1, godotai, other-model", md)
        small = evals.compare_models(self.d, "godotai", "other-model")
        self.assertTrue(small.verdict().startswith("NO EVIDENCE"))
        write_results(self.d, [result("x", "godotai", False)])
        small = evals.compare_models(self.d, "godotai", "other-model")
        self.assertIn("Too few tasks for a general claim", small.verdict())

    def test_cli_defaults_to_config_models_and_exit_code_reflects_evidence(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, PRIVATE_ENV), redirect_stdout(out):
            rc = cli.main(["eval", "compare", "--results-dir", str(self.d)])
        self.assertEqual(rc, 1, "no evidence → non-zero, so a script cannot mistake it for a win")
        self.assertIn("candidate `godotai` vs reference `claude-fable-5-1`", out.getvalue())
        write_results(self.d, [result("flappy", "godotai", True), result("flappy", "claude-fable-5-1", True)])
        out = io.StringIO()
        with mock.patch.dict(os.environ, PRIVATE_ENV), redirect_stdout(out):
            rc = cli.main(["eval", "compare", "--results-dir", str(self.d), "--candidate", "godotai",
                           "--reference", "claude-fable-5-1"])
        self.assertEqual(rc, 0)
        self.assertIn("tied on 1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
