# Point-and-click presenter — rooms + hotspot verbs. One place at a time: render the scene, await a
# hotspot click, run its action through the shared Game.run_action, repeat. Movement between places
# (and combat/dialogue) is owned by Game; this file owns only click presentation.
extends RefCounted

var g  # Game driver


func _init(game) -> void:
	g = game


# Returns Game.WIN / Game.END, or a {move,spawn} dict to leave for another place. spawn is unused
# here (rooms have no avatar); it exists so the presenter signature matches the overworld.
func run_place(place_id, _spawn):
	var place = g.place_by_id[place_id]
	g.set_scene(place.get("background"))
	for c in g.sprites_node().get_children():
		c.queue_free()
	while true:
		var it = await _await_hotspot(place)
		var r = await g.run_action(it["action"])
		# Check the {move} dict BEFORE the string compares — Godot 4 errors on Dictionary == String.
		if typeof(r) == TYPE_DICTIONARY and r.has("move"):
			return r
		elif r == g.WIN or r == g.END:
			return r
		g.set_scene(place.get("background"))  # talk/examine may have changed the scene


func _await_hotspot(place) -> Dictionary:
	var box: Control = g.hotspots_node()
	for c in box.get_children():
		c.queue_free()
	var picked := {"v": null}
	var stack := 0
	var hover := Label.new()
	hover.visible = false
	hover.add_theme_font_size_override("font_size", 16)
	hover.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.9))
	hover.add_theme_constant_override("shadow_offset_x", 1)
	hover.add_theme_constant_override("shadow_offset_y", 1)
	hover.mouse_filter = Control.MOUSE_FILTER_IGNORE
	for it in place.get("interactables", []):
		var rect = it.get("position", {}).get("rect", {})
		var b := Button.new()
		b.text = it.get("label", "")
		if rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
			b.position = Vector2(rect["x"], rect["y"])
			b.size = Vector2(rect["w"], rect["h"])
			b.custom_minimum_size = b.size
		else:
			# No pixel rect (unpositioned): stack a labelled button so it stays clickable.
			b.position = Vector2(40, 120 + stack * 64)
			b.custom_minimum_size = Vector2(280, 52)
			stack += 1
		b.pressed.connect(func(): picked["v"] = it)
		b.mouse_entered.connect(func():
			b.modulate = Color(1.3, 1.3, 1.3)
			hover.text = it.get("label", "")
			hover.position = b.position + Vector2(0, -24)
			hover.visible = true)
		b.mouse_exited.connect(func():
			b.modulate = Color(1, 1, 1)
			hover.visible = false)
		box.add_child(b)
	box.add_child(hover)  # last child, so the label draws above the buttons
	while picked["v"] == null:
		await g.get_tree().process_frame
		if Input.is_action_just_pressed("ui_pause"):
			await g.pause_menu()
	for c in box.get_children():
		c.queue_free()
	return picked["v"]
