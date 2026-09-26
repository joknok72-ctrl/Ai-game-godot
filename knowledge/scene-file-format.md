# Hand-writing .tscn / .tres files that survive `--import`

The agent writes scene files as text. These rules are checked by the real engine in the
verification pipeline; a malformed scene shows up as an error in the `--import` or smoke-test
step.

## Header

```
[gd_scene load_steps=3 format=3]
```
- `format=3` is the Godot 4 text format.
- `load_steps` = number of `[ext_resource]` + `[sub_resource]` entries **+ 1**. Wrong counts
  only produce a warning, but keep them right.
- `uid="uid://…"` is optional when writing by hand; the editor adds one on save.

## External resources

```
[ext_resource type="Script" path="res://scripts/player.gd" id="1_player"]
[ext_resource type="PackedScene" path="res://scenes/enemy.tscn" id="2_enemy"]
[ext_resource type="Texture2D" path="res://art/player.png" id="3_tex"]
```
Ids are arbitrary strings; reference them with `ExtResource("1_player")`.

## Sub-resources

```
[sub_resource type="RectangleShape2D" id="RectangleShape2D_1"]
size = Vector2(88, 88)
```
Reference with `SubResource("RectangleShape2D_1")`.

## Nodes

```
[node name="Player" type="Area2D"]                 ; root: no parent
script = ExtResource("1_player")

[node name="Sprite" type="Sprite2D" parent="."]    ; child of root
texture = ExtResource("3_tex")
position = Vector2(0, -8)

[node name="Enemy" parent="." instance=ExtResource("2_enemy")]   ; instanced scene (no type=)
position = Vector2(300, 200)

[node name="Label" type="Label" parent="HUD"]      ; nested path parent
```
- Property syntax mirrors GDScript literals: `Vector2(x, y)`, `Color(r, g, b, a)`,
  `Rect2(...)`, `PackedStringArray("a", "b")`, `true/false`, strings in double quotes.
- Theme overrides: `theme_override_font_sizes/font_size = 48`,
  `theme_override_colors/font_color = Color(1, 1, 1, 1)`.
- Controls: `offset_left/top/right/bottom`, `anchors_preset = 15` (full rect) plus
  `anchor_right = 1.0`, `anchor_bottom = 1.0`, `grow_horizontal = 2`, `grow_vertical = 2`.
- Label enums are ints: `horizontal_alignment = 1` (center), `autowrap_mode = 3` (word smart).
- Signals: `[connection signal="timeout" from="SpawnTimer" to="." method="_on_spawn_timer_timeout"]`
  after all nodes — or connect in `_ready()` (preferred by this agent: explicit, greppable).

## Resources (.tres)

```
[gd_resource type="Resource" script_class="EnemyStats" load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/enemy_stats.gd" id="1"]

[resource]
script = ExtResource("1")
speed = 120.0
hp = 3
```

## Import files

Textures/audio need `.import` sidecar files — **never write them by hand**; run
`godot --headless --import` (the `godot_verify` tool does) and the engine generates them.
Commit `.import` files or ignore them (`*.import` in .gitignore) consistently; this repo ignores
them and re-imports in CI.
