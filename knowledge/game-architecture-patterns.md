# Game architecture patterns for Godot 4.7 (GDScript)

## Scene composition

- One scene = one responsibility (`player.tscn`, `enemy.tscn`, `hud.tscn`, `level_01.tscn`).
- Root node type follows the role: `CharacterBody2D` for controllable bodies, `Area2D` for
  triggers/pickups/simple arcade colliders, `Node2D` for containers/levels, `CanvasLayer` for HUD,
  `Control` for menus.
- Communicate **up with signals, down with calls**: a child never reaches into its parent;
  the parent connects the child's signals in `_ready()`.
- Reusable scripts get `class_name` so other scripts can type against them (`if area is Obstacle`).
- Autoload (Project Settings → Autoload) only for truly global state: `GameState`, `AudioBus`,
  `SaveManager`. Keep them small and testable.

## Core loop & state

- Minimal finite state machine: an `enum State { MENU, PLAYING, PAUSED, GAME_OVER }` on the main
  scene plus a `set_state()` that toggles processing (`set_process(false)`,
  `get_tree().paused`) and HUD visibility.
- For entity behaviour (idle/run/jump/attack) use a `Node`-based state machine: one state node
  per state with `enter()`, `exit()`, `physics_update(delta)`, parent `StateMachine` script
  switches states — easy to extend and debug.
- Data-driven tuning: `@export` values on the scene, or `Resource` subclasses
  (`class_name EnemyStats extends Resource`) saved as `.tres` files.

## Spawning & performance

- Spawn from a `Timer`; adjust `wait_time` for difficulty curves; clamp with a minimum.
- Despawn off-screen with `VisibleOnScreenNotifier2D.screen_exited` or a bounds check;
  `queue_free()` never `free()` during physics callbacks.
- Pool bullets/particles when spawning > ~50/s: keep a `Array[Node]` of inactive instances.
- Movement in `_physics_process`, visuals/animation in `_process`. Avoid allocating arrays or
  dictionaries per frame; cache `get_viewport_rect().size` when the window size cannot change.
- Use `CPUParticles2D` on low-end mobile; `GPUParticles2D` is fine on gl_compatibility but test.

## Mobile input

- Touch events: `InputEventScreenTouch` (pressed/released, `index` for multi-touch) and
  `InputEventScreenDrag` (`position`, `relative`). Test on desktop with
  `input_devices/pointing/emulate_touch_from_mouse=true`.
- Virtual joystick: 4.7 ships a built-in virtual joystick (see release notes) — or implement a
  `Control` that maps drag offset to a direction vector.
- Big touch targets (≥ 96 px at 720-wide design resolution), no hover-only affordances,
  pause on `NOTIFICATION_APPLICATION_PAUSED`, save on `NOTIFICATION_APPLICATION_FOCUS_OUT`.
- Orientation: `window/handheld/orientation` (1 = portrait). Use `canvas_items` stretch with
  `expand` aspect so different phone ratios show more/less of the world, never distorted.

## Persistence

- Small saves: `var cfg := ConfigFile.new(); cfg.set_value("game", "best", best); cfg.save("user://save.cfg")`.
- Structured saves: `JSON.stringify(data)` to `FileAccess.open("user://save.json", FileAccess.WRITE)`.
- `user://` resolves to app-private storage on Android — no permissions required.

## UI

- `Control` anchors/presets instead of fixed offsets for anything that must survive aspect
  changes; `theme_override_font_sizes/font_size` for quick sizing; `Label.autowrap_mode`.
- Menus as separate scenes switched with `change_scene_to_file`; pause menu as a
  `CanvasLayer` with `process_mode = PROCESS_MODE_ALWAYS`.

## Testing hooks

- Keep rules in pure functions (`current_fall_speed()`, `score_for(hits)`) so the headless smoke
  test (`tests/smoke_test.gd`, a `SceneTree` script) can call them without input.
- Print a single machine-readable line (`SMOKE_TEST_OK …`) on success and `push_error` +
  `quit(1)` on failure — the verification pipeline keys off both.
- For larger projects add GUT or gdUnit4 (both run headless in CI).
