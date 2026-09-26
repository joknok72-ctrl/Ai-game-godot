class_name Main
extends Node2D
## Tap Dodge — a tiny, complete mobile game used as the verified starting template.
## Core loop: obstacles fall from the top, the player slides left/right (touch, drag
## or arrow keys) to dodge them, score = seconds survived, tap to restart.

const OBSTACLE_SCENE: PackedScene = preload("res://scenes/obstacle.tscn")
const SPAWN_MARGIN: float = 60.0

@export var start_fall_speed: float = 420.0
@export var speed_increase_per_second: float = 12.0
@export var min_spawn_interval: float = 0.35

var score: float = 0.0
var game_over: bool = false
var _elapsed: float = 0.0

@onready var _player: Player = $Player
@onready var _obstacles: Node2D = $Obstacles
@onready var _spawn_timer: Timer = $SpawnTimer
@onready var _score_label: Label = $HUD/ScoreLabel
@onready var _message_label: Label = $HUD/MessageLabel


func _ready() -> void:
	_spawn_timer.timeout.connect(_on_spawn_timer_timeout)
	_player.hit.connect(_on_player_hit)
	_message_label.text = ""


func _process(delta: float) -> void:
	if game_over:
		return
	_elapsed += delta
	score = _elapsed
	_score_label.text = str(int(score))


func _unhandled_input(event: InputEvent) -> void:
	if not game_over:
		return
	if (event is InputEventScreenTouch or event is InputEventKey) and event.is_pressed():
		restart()


## Current obstacle speed; grows linearly with survival time.
func current_fall_speed() -> float:
	return start_fall_speed + speed_increase_per_second * _elapsed


## Spawn interval shrinks as the game speeds up, never below min_spawn_interval.
func current_spawn_interval() -> float:
	return maxf(min_spawn_interval, 0.9 - _elapsed * 0.02)


func _on_spawn_timer_timeout() -> void:
	if game_over:
		return
	var obstacle: Obstacle = OBSTACLE_SCENE.instantiate()
	var width: float = get_viewport_rect().size.x
	obstacle.position = Vector2(randf_range(SPAWN_MARGIN, width - SPAWN_MARGIN), -SPAWN_MARGIN)
	obstacle.fall_speed = current_fall_speed()
	_obstacles.add_child(obstacle)
	_spawn_timer.wait_time = current_spawn_interval()


func _on_player_hit() -> void:
	if game_over:
		return
	game_over = true
	_spawn_timer.stop()
	_message_label.text = "Game over — %d\nTap to restart" % int(score)


func restart() -> void:
	get_tree().reload_current_scene()
