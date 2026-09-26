class_name Obstacle
extends Area2D
## A falling block. Removed once it leaves the bottom of the screen.

const DESPAWN_MARGIN: float = 120.0

var fall_speed: float = 400.0


func _physics_process(delta: float) -> void:
	position.y += fall_speed * delta
	if position.y > get_viewport_rect().size.y + DESPAWN_MARGIN:
		queue_free()
