class_name Player
extends Area2D
## The player square. Touch/drag anywhere to set a target X; arrow keys / A-D on desktop.

signal hit

@export var speed: float = 1100.0
@export var half_width: float = 48.0

var _target_x: float = -1.0


func _ready() -> void:
	area_entered.connect(_on_area_entered)
	_target_x = position.x


func _unhandled_input(event: InputEvent) -> void:
	var touch := event as InputEventScreenTouch
	if touch != null and touch.pressed:
		_target_x = touch.position.x
		return
	var drag := event as InputEventScreenDrag
	if drag != null:
		_target_x = drag.position.x


func _physics_process(delta: float) -> void:
	var direction: float = 0.0
	if Input.is_key_pressed(KEY_LEFT) or Input.is_key_pressed(KEY_A):
		direction -= 1.0
	if Input.is_key_pressed(KEY_RIGHT) or Input.is_key_pressed(KEY_D):
		direction += 1.0
	if direction != 0.0:
		_target_x = position.x + direction * speed * delta
	var limit: float = get_viewport_rect().size.x - half_width
	_target_x = clampf(_target_x, half_width, limit)
	position.x = move_toward(position.x, _target_x, speed * delta)


func _on_area_entered(area: Area2D) -> void:
	if area is Obstacle:
		hit.emit()
