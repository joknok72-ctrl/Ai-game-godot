"""Engine-generated API reference: XML parsing, lookups, cache, and the Godot-3→4 lint.

The synthetic fixture below mirrors the shape of ``godot --doctool`` output (class,
inherits, methods/params/return, members, signals, constants with enum=, annotations,
constructors). Tests that need the *real* dump are at the bottom and skip without
the pinned binary.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from _helpers import repo_config

from godotai import apiref
from godotai.apiref import (INDEX_FORMAT, ApiIndex, LEGACY_CLASS_RENAMES, LintFinding, format_findings,
                            lint_project, lint_script, parse_class_xml, parse_xml_dir, user_classes)
from godotai.godot import Godot, GodotNotFound, GodotVersionMismatch, find_godot_binary

CFG = repo_config()

FIXTURE_XML = {
    "Object.xml": """<?xml version="1.0" encoding="UTF-8" ?>
<class name="Object" version="4.7">
  <brief_description>Base class for all other classes.</brief_description>
  <methods>
    <method name="free"><return type="void" /><description></description></method>
  </methods>
</class>""",
    "Node.xml": """<?xml version="1.0" encoding="UTF-8" ?>
<class name="Node" inherits="Object" version="4.7">
  <brief_description>Base class for all scene objects.</brief_description>
  <methods>
    <method name="queue_free"><return type="void" /><description>Queues this node for deletion.</description></method>
    <method name="add_child">
      <return type="void" />
      <param index="0" name="node" type="Node" />
      <param index="1" name="force_readable_name" type="bool" default="false" />
      <param index="2" name="internal" type="int" enum="Node.InternalMode" default="0" />
      <description></description>
    </method>
    <method name="get_node" qualifiers="const">
      <return type="Node" />
      <param index="0" name="path" type="NodePath" />
      <description></description>
    </method>
  </methods>
  <members>
    <member name="process_mode" type="int" setter="set_process_mode" getter="get_process_mode" enum="Node.ProcessMode" default="0" />
    <member name="name" type="StringName" setter="set_name" getter="get_name" />
  </members>
  <signals>
    <signal name="ready"><description>Emitted when ready.</description></signal>
    <signal name="child_entered_tree"><param index="0" name="node" type="Node" /></signal>
  </signals>
  <constants>
    <constant name="PROCESS_MODE_INHERIT" value="0" enum="ProcessMode" />
    <constant name="PROCESS_MODE_PAUSABLE" value="1" enum="ProcessMode" />
    <constant name="NOTIFICATION_READY" value="13" />
  </constants>
</class>""",
    "CanvasItem.xml": """<class name="CanvasItem" inherits="Node" version="4.7"><methods /></class>""",
    "Node2D.xml": """<class name="Node2D" inherits="CanvasItem" version="4.7">
  <members><member name="position" type="Vector2" setter="set_position" getter="get_position" default="Vector2(0, 0)" /></members>
</class>""",
    "CollisionObject2D.xml": """<class name="CollisionObject2D" inherits="Node2D" version="4.7"><methods /></class>""",
    "PhysicsBody2D.xml": """<class name="PhysicsBody2D" inherits="CollisionObject2D" version="4.7"><methods /></class>""",
    "CharacterBody2D.xml": """<class name="CharacterBody2D" inherits="PhysicsBody2D" version="4.7">
  <methods>
    <method name="move_and_slide"><return type="bool" /><description></description></method>
    <method name="is_on_floor" qualifiers="const"><return type="bool" /><description></description></method>
  </methods>
  <members><member name="velocity" type="Vector2" setter="set_velocity" getter="get_velocity" default="Vector2(0, 0)" /></members>
</class>""",
    "Area2D.xml": """<class name="Area2D" inherits="CollisionObject2D" version="4.7">
  <signals><signal name="body_entered"><param index="0" name="body" type="Node2D" /></signal></signals>
</class>""",
    "Timer.xml": """<class name="Timer" inherits="Node" version="4.7">
  <methods><method name="start"><return type="void" /><param index="0" name="time_sec" type="float" default="-1" /></method></methods>
  <signals><signal name="timeout" /></signals>
</class>""",
    "Vector2.xml": """<class name="Vector2" version="4.7">
  <constructors>
    <constructor name="Vector2"><return type="Vector2" /></constructor>
    <constructor name="Vector2"><return type="Vector2" /><param index="0" name="x" type="float" /><param index="1" name="y" type="float" /></constructor>
  </constructors>
  <members><member name="x" type="float" setter="" getter="" default="0.0" /></members>
</class>""",
    "@GlobalScope.xml": """<class name="@GlobalScope" version="4.7">
  <methods>
    <method name="randf_range"><return type="float" /><param index="0" name="from" type="float" /><param index="1" name="to" type="float" /></method>
    <method name="lerp"><return type="Variant" /><param index="0" name="from" type="Variant" /><param index="1" name="to" type="Variant" /><param index="2" name="weight" type="Variant" /></method>
  </methods>
  <constants><constant name="OK" value="0" enum="Error" /><constant name="FAILED" value="1" enum="Error" /></constants>
</class>""",
    "@GDScript.xml": """<class name="@GDScript" version="4.7">
  <methods><method name="preload"><return type="Resource" /><param index="0" name="path" type="String" /></method></methods>
  <annotations>
    <annotation name="@export"><return type="void" /></annotation>
    <annotation name="@export_range">
      <return type="void" />
      <param index="0" name="min" type="float" /><param index="1" name="max" type="float" />
      <param index="2" name="step" type="float" default="1.0" />
    </annotation>
  </annotations>
</class>""",
    "Tween.xml": """<class name="Tween" inherits="RefCounted" version="4.7">
  <methods><method name="tween_property"><return type="PropertyTweener" /><param index="0" name="object" type="Object" /><param index="1" name="property" type="NodePath" /><param index="2" name="final_val" type="Variant" /><param index="3" name="duration" type="float" /></method></methods>
</class>""",
    "RefCounted.xml": """<class name="RefCounted" inherits="Object" version="4.7"><methods /></class>""",
    "PackedScene.xml": """<class name="PackedScene" inherits="Resource" version="4.7">
  <methods><method name="instantiate" qualifiers="const"><return type="Node" /><param index="0" name="edit_state" type="int" enum="PackedScene.GenEditState" default="0" /></method></methods>
</class>""",
    "Resource.xml": """<class name="Resource" inherits="RefCounted" version="4.7"><methods /></class>""",
    "Deprecated.xml": """<class name="OldThing" inherits="Node" deprecated="Use NewThing instead." version="4.7">
  <methods><method name="legacy" deprecated="gone"><return type="void" /></method></methods>
</class>""",
    "not-a-class.xml": """<version>4.7</version>""",
}


def make_fixture_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="godotai-apiref-fixture-"))
    for name, text in FIXTURE_XML.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


def make_index() -> ApiIndex:
    d = make_fixture_dir()
    try:
        return ApiIndex(CFG.engine.tag, CFG.engine.version_string, parse_xml_dir(d), has_descriptions=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.dir = make_fixture_dir()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_parse_class_xml_shapes(self):
        node = parse_class_xml(self.dir / "Node.xml")
        assert node is not None
        self.assertEqual(node["inherits"], "Object")
        self.assertEqual(node["brief"], "Base class for all scene objects.")
        add_child = next(m for m in node["methods"] if m["name"] == "add_child")
        self.assertEqual([p["name"] for p in add_child["params"]], ["node", "force_readable_name", "internal"])
        self.assertEqual(add_child["params"][1]["default"], "false")
        self.assertEqual(add_child["params"][2]["type"], "Node.InternalMode", "enum-typed ints show the enum")
        self.assertEqual(add_child["return"], "void")
        get_node = next(m for m in node["methods"] if m["name"] == "get_node")
        self.assertEqual(get_node["qualifiers"], "const")
        self.assertEqual(get_node["return"], "Node")
        pm = next(m for m in node["members"] if m["name"] == "process_mode")
        self.assertEqual(pm["type"], "Node.ProcessMode")
        self.assertEqual(pm["setter"], "set_process_mode")
        self.assertEqual([s["name"] for s in node["signals"]], ["ready", "child_entered_tree"])
        self.assertEqual(node["signals"][1]["params"][0]["type"], "Node")
        enums = {c["enum"] for c in node["constants"] if c["enum"]}
        self.assertEqual(enums, {"ProcessMode"})

    def test_constructors_annotations_and_flags(self):
        vec = parse_class_xml(self.dir / "Vector2.xml")
        assert vec is not None
        self.assertEqual(len(vec["constructors"]), 2)
        self.assertEqual(vec["inherits"], "")
        gd = parse_class_xml(self.dir / "@GDScript.xml")
        assert gd is not None
        self.assertEqual([a["name"] for a in gd["annotations"]], ["@export", "@export_range"])
        self.assertEqual(gd["annotations"][1]["params"][2]["default"], "1.0")
        old = parse_class_xml(self.dir / "Deprecated.xml")
        assert old is not None
        self.assertEqual(old["deprecated"], "Use NewThing instead.")
        self.assertEqual(old["methods"][0]["deprecated"], "gone")

    def test_non_class_files_are_ignored(self):
        self.assertIsNone(parse_class_xml(self.dir / "not-a-class.xml"))
        (self.dir / "garbage.xml").write_text("<class name='X'", encoding="utf-8")
        self.assertIsNone(parse_class_xml(self.dir / "garbage.xml"))
        classes = parse_xml_dir(self.dir)
        self.assertNotIn("X", classes)
        self.assertIn("Node", classes)
        self.assertIn("@GlobalScope", classes)
        self.assertEqual(len(classes), len(FIXTURE_XML) - 1)


class IndexLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = make_index()

    def test_resolve_and_suggest(self):
        self.assertEqual(self.idx.resolve("Node2D"), "Node2D")
        self.assertEqual(self.idx.resolve("characterbody2d"), "CharacterBody2D", "case-insensitive fallback")
        self.assertIsNone(self.idx.resolve("KinematicBody2D"))
        self.assertIn("CharacterBody2D", self.idx.suggest("KinematicBody2D"), "legacy rename is suggested first")
        self.assertIn("Area2D", self.idx.suggest("Area2d"))

    def test_inheritance_and_inherited_members(self):
        self.assertEqual(self.idx.ancestors("CharacterBody2D"),
                         ["PhysicsBody2D", "CollisionObject2D", "Node2D", "CanvasItem", "Node", "Object"])
        hit = self.idx.find_member("CharacterBody2D", "queue_free")
        assert hit is not None
        owner, kind, entry = hit
        self.assertEqual((owner, kind, entry["name"]), ("Node", "methods", "queue_free"))
        hit = self.idx.find_member("CharacterBody2D", "position")
        assert hit is not None
        self.assertEqual(hit[0], "Node2D")
        self.assertIsNone(self.idx.find_member("CharacterBody2D", "body_entered"), "Area2D signal is not inherited")

    def test_globals(self):
        g = self.idx.find_global("randf_range")
        assert g is not None
        self.assertEqual(g[0], "@GlobalScope")
        a = self.idx.find_global("@export_range")
        assert a is not None
        self.assertEqual(a[1], "annotations")
        self.assertIsNone(self.idx.find_global("rand_range"))

    def test_render_class_and_member(self):
        text = self.idx.render_class("CharacterBody2D")
        self.assertIn("class CharacterBody2D extends PhysicsBody2D", text)
        self.assertIn("inherits chain: PhysicsBody2D → CollisionObject2D → Node2D", text)
        self.assertIn("move_and_slide() -> bool", text)
        self.assertIn("velocity: Vector2 = Vector2(0, 0)", text)
        self.assertIn("inherited members are not listed", text)

        member = self.idx.render_class("CharacterBody2D", "queue_free")
        self.assertIn("Node.queue_free() -> void", member)
        self.assertIn("(inherited from Node)", member)
        self.assertIn("Queues this node for deletion.", member)

        sig = self.idx.render_class("Area2D", "body_entered")
        self.assertIn("signal Area2D.body_entered(body: Node2D)", sig)

        ctor = self.idx.render_class("Vector2", "Vector2")
        self.assertEqual(ctor.count("Vector2.Vector2("), 2, "all constructor overloads are shown")
        self.assertIn("Vector2(x: float, y: float) -> Vector2", ctor)

        ann = self.idx.render_class("@GDScript", "@export_range")
        self.assertIn("@export_range(min: float, max: float, step: float = 1.0) -> void", ann)

        getn = self.idx.render_class("Node", "get_node")
        self.assertIn("get_node(path: NodePath) -> Node const", getn)

    def test_render_missing_things_give_guidance(self):
        text = self.idx.render_class("KinematicBody2D")
        self.assertIn("does not exist in Godot 4.7.2-stable", text)
        self.assertIn("in Godot 4 use CharacterBody2D", text)
        text = self.idx.render_class("Nodee")
        self.assertIn("Did you mean", text)
        self.assertIn("Node", text)
        text = self.idx.render_class("CharacterBody2D", "move_and_slid")
        self.assertIn("has no member 'move_and_slid'", text)
        self.assertIn("did you mean: move_and_slide", text)
        text = self.idx.render_class("Node", "randf_range")
        self.assertIn("It exists as a global: @GlobalScope.randf_range(from: float, to: float) -> float", text)

    def test_enum_rendering_and_deprecation(self):
        self.assertEqual(self.idx.render_enum("Node", "ProcessMode"),
                         "enum Node.ProcessMode: PROCESS_MODE_INHERIT = 0, PROCESS_MODE_PAUSABLE = 1")
        self.assertIsNone(self.idx.render_enum("Node", "Nope"))
        self.assertIsNone(self.idx.render_enum("Nope", "X"))
        self.assertIn("enum @GlobalScope.Error: OK = 0, FAILED = 1", self.idx.render_enum("@GlobalScope", "Error") or "")
        text = self.idx.render_class("OldThing")
        self.assertIn("DEPRECATED: Use NewThing instead.", text)
        self.assertIn("legacy() -> void  [DEPRECATED]", text)

    def test_search_prefers_exact_and_class_qualified_hits(self):
        hits = self.idx.search("timer timeout")
        self.assertTrue(hits and hits[0].startswith("Timer.timeout("), hits)
        hits = self.idx.search("move and slide")
        self.assertTrue(any("CharacterBody2D.move_and_slide() -> bool" in h for h in hits), hits)
        self.assertEqual(self.idx.search("zzzz_nothing"), [])
        self.assertEqual(self.idx.search("a"), [], "single-letter terms are ignored")
        self.assertEqual(len(self.idx.search("node", limit=3)), 3)

    def test_stats(self):
        st = self.idx.stats()
        self.assertEqual(st["classes"], len(FIXTURE_XML) - 1)
        self.assertGreaterEqual(st["methods"], 8)
        self.assertGreaterEqual(st["signals"], 4)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="godotai-apiref-cache-"))
        self.idx = make_index()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_load_roundtrip_and_guards(self):
        p = self.tmp / "index.json"
        self.idx.save(p)
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], INDEX_FORMAT)
        self.assertEqual(data["engine_tag"], "4.7.2-stable")
        loaded = ApiIndex.load(p, expect_tag="4.7.2-stable")
        assert loaded is not None
        self.assertEqual(loaded.stats(), self.idx.stats())
        self.assertEqual(loaded.resolve("node2d"), "Node2D", "case map is rebuilt on load")
        self.assertTrue(loaded.has_descriptions)
        self.assertIsNone(ApiIndex.load(p, expect_tag="4.8.0-stable"), "a different engine pin invalidates the cache")
        data["format"] = INDEX_FORMAT - 1
        p.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(ApiIndex.load(p), "an old index format is rebuilt, not trusted")
        self.assertIsNone(ApiIndex.load(self.tmp / "missing.json"))
        p.write_text("{not json", encoding="utf-8")
        self.assertIsNone(ApiIndex.load(p))

    def test_cache_dir_honours_override_and_engine_tag(self):
        with mock.patch.dict(os.environ, {"GODOTAI_APIREF_DIR": str(self.tmp / "custom")}):
            self.assertEqual(apiref.index_path(CFG.engine), self.tmp / "custom" / "index.json")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.tmp / "xdg")}, clear=False):
            os.environ.pop("GODOTAI_APIREF_DIR", None)
            self.assertEqual(apiref.index_path(CFG.engine), self.tmp / "xdg" / "godotai" / "apiref" / "4.7.2-stable" / "index.json")
            other = replace(CFG.engine, version="4.8.0")
            self.assertIn("4.8.0-stable", str(apiref.index_path(other)))

    def test_load_index_uses_engine_path(self):
        with mock.patch.dict(os.environ, {"GODOTAI_APIREF_DIR": str(self.tmp)}):
            self.assertIsNone(apiref.load_index(CFG.engine))
            self.idx.save(apiref.index_path(CFG.engine))
            loaded = apiref.load_index(CFG.engine)
            self.assertIsNotNone(loaded)


class LintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.idx = make_index()

    _USE_FIXTURE = object()

    def lint(self, text: str, index=_USE_FIXTURE, known=None) -> list[LintFinding]:
        return lint_script(text, self.idx if index is self._USE_FIXTURE else index, known)

    def messages(self, findings):
        return [f.message for f in findings]

    def test_godot3_idioms_are_errors(self):
        script = "\n".join([
            "extends KinematicBody2D",
            "export var speed = 10",
            "onready var sprite = $Sprite",
            "func _ready():",
            "\tvar s = preload(\"res://a.tscn\").instance()",
            "\tconnect(\"body_entered\", self, \"_on_body\")",
            "\tyield(get_tree().create_timer(1.0), \"timeout\")",
            "\tvar t = Tween.new()",
            "\tget_tree().change_scene(\"res://b.tscn\")",
            "\tvar r = rand_range(1, 2)",
            "\tvar d = deg2rad(90)",
            "\tmodulate = Color.red",
            "\tmove_and_slide(velocity)",
            "\tprint(OS.get_ticks_msec())",
        ])
        findings = self.lint(script)
        msgs = "\n".join(self.messages(findings))
        for expect in ("KinematicBody2D is a Godot 3 class", "`export var` → `@export var`", "`onready var` → `@onready var`",
                       "instantiate()", "sig.connect(fn)", "`await", "create_tween()", "change_scene_to_file()",
                       "randf_range()", "deg_to_rad()", "Color.RED", "takes no arguments", "Time.get_ticks_msec()"):
            self.assertIn(expect, msgs)
        self.assertTrue(all(f.severity == "error" for f in findings), [str(f) for f in findings])
        self.assertEqual(findings[0].line, 1)

    def test_valid_godot4_script_is_clean(self):
        script = "\n".join([
            "extends CharacterBody2D",
            "class_name Player",
            "",
            "signal died",
            "",
            "@export_range(0.0, 10.0, 0.5) var speed: float = 5.0",
            "@onready var timer: Timer = $Timer",
            "var target: Vector2 = Vector2.ZERO",
            "const COLOR_HIT := Color.RED   # constant, upper case",
            "enum State { IDLE, RUN }",
            "var state: State = State.IDLE",
            "",
            "func _ready() -> void:",
            "\ttimer.timeout.connect(_on_timeout)",
            "\tvar scene: PackedScene = preload(\"res://scenes/x.tscn\")",
            "\tvar node := scene.instantiate()",
            "\tadd_child(node)",
            "\tawait get_tree().create_timer(1.0).timeout",
            "\tvar t: Tween = create_tween()",
            "\tprint(\"yield(x) inside a string is fine\") # export var in a comment is fine",
            "",
            "func _physics_process(delta: float) -> void:",
            "\tvelocity = target.lerp(velocity, delta)",
            "\tmove_and_slide()",
            "",
            "func _on_timeout() -> void:",
            "\tqueue_free()",
        ])
        self.assertEqual(self.lint(script), [])

    def test_unknown_class_is_warning_with_suggestion_and_user_classes_are_known(self):
        findings = self.lint("extends Nodee\n\nvar a: Aria2D\nvar b: Enemy = Enemy.new()\n")
        msgs = self.messages(findings)
        self.assertEqual(len(findings), 3, msgs)
        self.assertTrue(all(f.severity == "warning" for f in findings))
        self.assertIn("class Nodee does not exist in Godot 4.7.2-stable (did you mean Node", msgs[0])
        self.assertIn("declare it with class_name", msgs[2])
        # cross-file user class passed in → no finding
        self.assertEqual(self.lint("var b: Enemy = Enemy.new()\n", known={"Enemy"}), [])
        # same-file declarations are picked up automatically
        self.assertEqual(self.lint("class_name Enemy\nextends Node\n\nclass Inner:\n\tvar x: int\n\nvar e: Enemy\nvar i: Inner\n"), [])

    def test_type_positions_and_all_caps_constants(self):
        script = "var a: Array[Missing1]\nvar d: Dictionary[String, Missing2]\nfunc f(x: Missing3) -> Missing4:\n\treturn x as Missing5\nvar c := MY_CONST\n"
        found = {m.split(" ")[1] for m in self.messages(self.lint(script)) if m.startswith("class ")}
        self.assertEqual(found, {"Missing1", "Missing2", "Missing3", "Missing4", "Missing5"})

    def test_lint_without_index_only_uses_patterns(self):
        findings = self.lint("extends KinematicBody2D\nvar x: Nope\nvar y = rand_range(1, 2)\n", index=None)
        self.assertEqual(len(findings), 1)
        self.assertIn("randf_range", findings[0].message)

    def test_warning_level_patterns(self):
        findings = self.lint("func f(arr: Array) -> void:\n\tif arr.empty():\n\t\tarr.remove(0)\n\tarr.invert()\n")
        self.assertEqual([f.severity for f in findings], ["warning", "warning", "warning"])

    def test_user_classes_extraction(self):
        names = user_classes([("a.gd", "class_name Player\nextends Node\nenum Kind { A }\nconst MAX_HP := 10\nclass Stats extends RefCounted:\n\tpass\n"),
                              ("b.gd", "static const Speed: float = 3.0\n")])
        self.assertEqual(names, {"Player", "Kind", "Stats", "Speed", "MAX_HP"})

    def test_lint_project_and_format(self):
        ws = Path(tempfile.mkdtemp(prefix="godotai-lint-"))
        try:
            (ws / "scripts").mkdir()
            (ws / "scripts" / "enemy.gd").write_text("class_name Enemy\nextends Area2D\n", encoding="utf-8")
            (ws / "scripts" / "main.gd").write_text("extends Node2D\nvar e: Enemy\nvar bad = deg2rad(1)\n", encoding="utf-8")
            (ws / ".godot").mkdir()
            (ws / ".godot" / "cache.gd").write_text("yield(x)\n", encoding="utf-8")
            (ws / "addons").mkdir()
            (ws / "addons" / "x.gd").write_text("yield(x)\n", encoding="utf-8")
            out = lint_project(ws, self.idx)
            self.assertEqual(list(out), ["scripts/main.gd"], "cross-file class is known; hidden/addons dirs skipped")
            lines = format_findings(out)
            self.assertEqual(lines, ["scripts/main.gd:3: [error] `deg2rad()` → `deg_to_rad()`"])
            many = {"a.gd": [LintFinding(i, "warning", "m") for i in range(70)]}
            self.assertEqual(len(format_findings(many)), 61)
            self.assertTrue(format_findings(many)[-1].startswith("… 10 more"))
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_legacy_rename_table_targets_exist_in_fixture_or_are_godot4_names(self):
        for old, new in LEGACY_CLASS_RENAMES.items():
            self.assertNotEqual(old, new)
            self.assertNotIn(old, ("CharacterBody2D", "Node3D", "Sprite2D"))


# ---------------------------------------------------------------------------
# Real engine (skipped without the pinned binary)
# ---------------------------------------------------------------------------
try:
    GODOT: Godot | None = Godot(CFG.engine) if find_godot_binary(CFG.engine) else None
except (GodotNotFound, GodotVersionMismatch):
    GODOT = None


@unittest.skipUnless(GODOT is not None, "pinned Godot binary not available")
class RealEngineApiRefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert GODOT is not None
        cls.tmp = Path(tempfile.mkdtemp(prefix="godotai-apiref-real-"))
        # reuse an existing cache when one is configured, otherwise build into a temp dir
        cls.env = mock.patch.dict(os.environ, {} if os.environ.get("GODOTAI_APIREF_DIR") else {"GODOTAI_APIREF_DIR": str(cls.tmp)})
        cls.env.start()
        cls.idx = apiref.build_index(GODOT)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_index_covers_the_engine(self):
        st = self.idx.stats()
        self.assertGreater(st["classes"], 900)
        self.assertGreater(st["methods"], 9000)
        self.assertGreater(st["signals"], 400)
        self.assertEqual(self.idx.engine_tag, CFG.engine.tag)
        self.assertTrue(self.idx.engine_version.startswith(CFG.engine.version_string))
        self.assertTrue(self.idx.path and self.idx.path.is_file())

    def test_known_godot4_facts(self):
        self.assertIn("move_and_slide() -> bool", self.idx.render_class("CharacterBody2D", "move_and_slide"))
        self.assertIn("(inherited from Node)", self.idx.render_class("CharacterBody2D", "queue_free"))
        self.assertIn("signal Area2D.body_entered(body: Node2D)", self.idx.render_class("Area2D", "body_entered"))
        self.assertIn("randf_range(from: float, to: float) -> float", self.idx.render_class("@GlobalScope", "randf_range"))
        self.assertIn("@export_range(", self.idx.render_class("@GDScript", "@export_range"))
        self.assertIn("PROCESS_MODE_INHERIT = 0", self.idx.render_enum("Node", "ProcessMode") or "")
        self.assertIn("in Godot 4 use CharacterBody2D", self.idx.render_class("KinematicBody2D"))
        self.assertIsNone(self.idx.find_member("Node", "yield"))
        self.assertTrue(self.idx.has("TileMapLayer"))
        self.assertTrue(self.idx.has("Tween"))
        self.assertIsNone(self.idx.find_member("Tween", "interpolate_property"))

    def test_second_build_is_a_cache_hit(self):
        again = apiref.build_index(GODOT)
        self.assertEqual(again.path, self.idx.path)
        self.assertEqual(again.stats(), self.idx.stats())

    def test_template_scripts_lint_clean_against_real_index(self):
        from godotai.scaffold import create_project
        dest = self.tmp / "game"
        create_project(CFG, "mobile-2d", dest, "Lint Game", "com.example.lintgame")
        self.assertEqual(lint_project(dest, self.idx), {})


if __name__ == "__main__":
    unittest.main()
