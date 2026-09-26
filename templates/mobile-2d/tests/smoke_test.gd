extends SceneTree
## Headless smoke test, run by the verification pipeline:
##   godot --headless --path <project> --script res://tests/smoke_test.gd
## Loads the main scene, lets it run for a couple of seconds of engine time and
## checks gameplay invariants. Prints SMOKE_TEST_OK on success, exits 1 on failure.

const RUN_SECONDS: float = 2.2
const MAX_FRAMES: int = 100000

var _time: float = 0.0
var _frames: int = 0
var _main: Node


func _initialize() -> void:
	var packed: PackedScene = load("res://scenes/main.tscn")
	if packed == null:
		push_error("SMOKE_TEST_FAIL: res://scenes/main.tscn could not be loaded")
		quit(1)
		return
	_main = packed.instantiate()
	root.add_child(_main)


func _process(delta: float) -> bool:
	_time += delta
	_frames += 1
	if _time < RUN_SECONDS and _frames < MAX_FRAMES:
		return false
	var failures: int = 0
	if _main == null or not _main.has_method("current_fall_speed"):
		push_error("SMOKE_TEST_FAIL: Main.current_fall_speed() missing")
		failures += 1
	elif float(_main.call("current_fall_speed")) <= 0.0:
		push_error("SMOKE_TEST_FAIL: fall speed must be positive")
		failures += 1
	var obstacles: Node = _main.get_node_or_null("Obstacles") if _main != null else null
	if obstacles == null:
		push_error("SMOKE_TEST_FAIL: Obstacles container missing")
		failures += 1
	elif obstacles.get_child_count() == 0:
		push_error("SMOKE_TEST_FAIL: no obstacles spawned after %.1f s" % _time)
		failures += 1
	if _main != null and bool(_main.get("game_over")):
		push_error("SMOKE_TEST_FAIL: game over without any input")
		failures += 1
	if failures == 0:
		print("SMOKE_TEST_OK frames=%d time=%.2f obstacles=%d" % [_frames, _time, obstacles.get_child_count()])
	quit(1 if failures > 0 else 0)
	return true
