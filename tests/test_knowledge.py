"""Knowledge base: files exist, index is complete, search/read tools behave."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from _helpers import REPO, repo_config

from godotai.tools import ToolContext, ToolRegistry
from godotai.tools import knowledge
from godotai.tools.knowledge import search, split_sections

KDIR = REPO / "knowledge"


class KnowledgeFilesTests(unittest.TestCase):
    def test_index_lists_every_file(self):
        index = (KDIR / "INDEX.md").read_text(encoding="utf-8")
        for md in KDIR.glob("*.md"):
            if md.name != "INDEX.md":
                self.assertIn(md.name, index, f"{md.name} missing from INDEX.md")
        self.assertTrue(re.search(r"^- .*godot-4\.7-essentials\.md", index, re.M),
                        "agent.py injects '- ' bullet lines into the prompt")

    def test_notes_are_pinned_to_the_engine_version(self):
        cfg = repo_config()
        for md in KDIR.glob("*.md"):
            for i, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
                # Godot 3 identifiers may only appear as migration hints ("old → new").
                for legacy in ("KinematicBody2D", "Spatial", "yield", "instance()", "onready var", "export var"):
                    if legacy in line.replace("@onready var", "").replace("@export var", ""):
                        self.assertIn("→", line, f"{md.name}:{i} mentions Godot 3 API {legacy!r} outside a migration hint")
        essentials = (KDIR / "godot-4.7-essentials.md").read_text(encoding="utf-8")
        self.assertIn(cfg.engine.tag, essentials)
        android = (KDIR / "android-export.md").read_text(encoding="utf-8")
        for req in ("17", "35.0.1", "android-35", "28.1.13356709"):
            self.assertIn(req, android)


class KnowledgeToolTests(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        knowledge.register(self.reg)
        self.ctx = ToolContext(cfg=repo_config(), workspace=Path(tempfile.mkdtemp()), log=lambda s: None)

    def test_split_sections(self):
        secs = split_sections("intro\n# A\nbody a\n## B\nbody b\n", "f.md")
        self.assertEqual([s["title"] for s in secs], ["f.md", "A", "B"])
        self.assertEqual(secs[2]["body"], "body b")

    def test_search_finds_android_export_and_touch_input(self):
        hits = search(KDIR, "android export keystore apk")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "android-export.md", [h["source"] for h in hits])
        hits = search(KDIR, "InputEventScreenTouch touch drag")
        self.assertTrue(any("touch" in (h["title"] + h["body"]).lower() for h in hits))
        self.assertEqual(search(KDIR, "zz"), [])  # terms shorter than 3 chars are ignored

    def test_tools(self):
        res = self.reg.execute("knowledge_search", {"query": "smoke test SceneTree headless"}, self.ctx)
        self.assertTrue(res.ok)
        self.assertIn("###", res.content)
        res = self.reg.execute("knowledge_read", {"file": "INDEX.md"}, self.ctx)
        self.assertTrue(res.ok)
        res = self.reg.execute("knowledge_read", {"file": "../godot.toml"}, self.ctx)
        self.assertTrue(res.is_error, "path traversal must be refused")
        res = self.reg.execute("knowledge_read", {"file": "missing.md"}, self.ctx)
        self.assertIn("available", res.content)


if __name__ == "__main__":
    unittest.main()
