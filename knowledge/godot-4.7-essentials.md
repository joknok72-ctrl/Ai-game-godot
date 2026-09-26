# Godot 4.7.2-stable essentials

Sources: https://godotengine.org/releases/4.7/ ,
https://docs.godotengine.org/en/4.7/tutorials/migrating/upgrading_to_godot_4.7.html ,
https://docs.godotengine.org/en/stable/ (stable = 4.7 at the time of writing, 2026-09-26).

## Version facts

- 4.7 "Lights, Camera, Action!" is the current stable minor; 4.7.1 (14 Jul 2026) and 4.7.2
  (18 Aug 2026) are maintenance releases. `godot --version` prints `4.7.2.stable.official.<hash>`.
- Maintenance releases are compatible with previous 4.7.x projects.
- `project.godot` must have `config_version=5` and `config/features=PackedStringArray("4.7", …)`.
  The second feature tag names the renderer: `"GL Compatibility"`, `"Mobile"` or `"Forward Plus"`.
- Editor settings are stored per minor version: `~/.config/godot/editor_settings-4.7.tres`.
- Export templates live in `~/.local/share/godot/export_templates/4.7.2.stable/`.

## Upgrading 4.6 → 4.7 (from the official migration page)

- GDScript-compatible API tweaks: `Object.is_class(class)` now takes `StringName`;
  `ZIPPacker.start_file` gained optional `permissions`/`modified_time`;
  `OptimizedTranslation.generate` returns `bool`; particle `request_particles_process`
  gained an optional `process_time_residual`.
- `RichTextLabel.ImageUpdateMask.UPDATE_WIDTH_IN_PERCENT` was renamed to `UPDATE_WIDTH_UNIT`
  (not GDScript compatible); `add_image` width/height are now `float`.
- `Control.accessibility_live` now uses `AccessibilityServer.AccessibilityLiveMode`.
- Everything else in a normal 2D/3D game is unchanged from 4.6.

## 4.7 highlights relevant to game code (see release page for details)

- New `AreaLight3D` node (rectangular area lights).
- Offset transform for `Control` nodes (animate UI without breaking layout).
- One-way collision on `CollisionShape2D`; `GradientTexture2D` conic gradients; tile
  `AtlasTexture` in `TextureRect`; nearest-neighbour viewport scaling option.
- Tweens can wait for signals; built-in virtual joystick for mobile; gyro aiming support;
  ignore controller events when unfocused; distinct keyboard/mouse device ids.
- Android: create games entirely on the Android editor; implement Java interfaces from
  GDScript; customizable splash screens; Perfetto is the default tracing tool.
- "Only download the templates you need" — the editor can fetch per-platform templates.
  Our installer still installs the full `.tpz` for reproducibility.
- HDR output on desktop platforms; Asset Store replaces the old Asset Library.

## GDScript 2 rules (Godot 4.x)

- `extends Node2D` first (optionally after `class_name Foo`); doc comments use `##`.
- Static typing: `var hp: int = 10`, `func take(amount: int) -> void:`, typed arrays
  `var items: Array[Item] = []`, dictionaries `Dictionary[String, int]` (4.4+).
- Annotations: `@export var speed: float = 200.0`, `@export_range(0, 10) var lives: int`,
  `@onready var sprite: Sprite2D = $Sprite2D`, `@tool`, `@icon("res://…")`.
- Signals: `signal died(reason: String)`; emit with `died.emit("fell")`; connect with
  `died.connect(_on_died)` (Callable). `timer.timeout.connect(_on_timeout)`.
- Await: `await get_tree().create_timer(1.0).timeout`, `await some_signal`.
- Scenes: `const ENEMY := preload("res://scenes/enemy.tscn")`; `var e: Enemy = ENEMY.instantiate()`;
  `add_child(e)`; `queue_free()`.
- Physics bodies: `CharacterBody2D.velocity` + `move_and_slide()` (no arguments);
  `is_on_floor()`; gravity from `ProjectSettings.get_setting("physics/2d/default_gravity")`.
- Input: `Input.is_action_pressed("jump")`, `Input.get_vector("left","right","up","down")`,
  `Input.is_key_pressed(KEY_LEFT)`; events in `_unhandled_input(event: InputEvent)`.
  Downcast with `var touch := event as InputEventScreenTouch` then `if touch != null:`.
- Math helpers: `clampf`, `lerpf`, `move_toward`, `randf_range`, `randi_range`, `maxf/minf`.
- Strings: `"%d points" % score`, `str(x)`, `"a".to_upper()`, `String.num(x, 2)`.
- Scene switching: `get_tree().change_scene_to_file("res://scenes/menu.tscn")`,
  `get_tree().reload_current_scene()`, `get_tree().paused = true`.
- Node access: `$Path/To/Node`, `%UniqueName` (scene unique nodes), `get_node_or_null()`.
- `super()` calls the parent implementation; `_init()`, `_ready()`, `_process(delta)`,
  `_physics_process(delta)`, `_input`, `_unhandled_input`, `_exit_tree()` lifecycle.
- Indentation: **tabs**. Naming: snake_case for members/functions, PascalCase for classes,
  CONSTANT_CASE for constants, leading underscore for private members.

## Common pitfalls the engine will report

- Accessing a property that exists only on a subclass through a base-typed variable
  (`event.position` when `event: InputEvent`) → parse error. Cast first.
- `move_and_slide(velocity)` is Godot 3 → in 4.x call `move_and_slide()` with no args.
- `yield` no longer exists → `await`. `onready var` → `@onready var`. `export var` → `@export var`.
- `connect("timeout", self, "_on_timeout")` (Godot 3) → `timeout.connect(_on_timeout)`.
- `instance()` → `instantiate()`. `randi() % n` fine; `rand_range` → `randf_range`.
- `KinematicBody2D` → `CharacterBody2D`; `Spatial` → `Node3D`; `Area2D` overlap signals are
  `body_entered`/`area_entered`.
- Untyped `load()` result assigned to a typed variable is allowed (`var s: PackedScene = load(...)`),
  but a `null` result raises at runtime — check it.
- `class_name` globals are only known after the project has been scanned
  (`godot --headless --import`); the verification pipeline imports first for that reason.
- Android exports need `rendering/textures/vram_compression/import_etc2_astc=true` or the
  export fails with a texture-compression error.

## project.godot keys used by mobile games

```
[application]
config/name="Game"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("4.7", "GL Compatibility")
config/icon="res://icon.svg"

[display]
window/size/viewport_width=720
window/size/viewport_height=1280
window/stretch/mode="canvas_items"
window/stretch/aspect="expand"
window/handheld/orientation=1        ; 0 landscape, 1 portrait, 6 sensor

[input_devices]
pointing/emulate_touch_from_mouse=true

[rendering]
renderer/rendering_method="gl_compatibility"
renderer/rendering_method.mobile="gl_compatibility"
textures/vram_compression/import_etc2_astc=true
```
Input actions can be added in `[input]` (the editor writes `Object(InputEventKey, …)`
literals) or at runtime with `InputMap.add_action("jump")` + `InputMap.action_add_event(...)`.
