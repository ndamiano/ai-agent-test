# Overworld presenter — a walkable RPG tile grid. Renders place.grid as cells, drops the avatar at
# the arrival spawn, and steps it one cell per WASD/arrow press (blocked by place.impassable and the
# grid edge). Triggering is HYBRID: walking ONTO a move/start_combat cell auto-fires it (you walk
# through the doorway / into the monster); for talk/examine/take/use/win you stand on the cell and
# press E. Every verb resolves through the shared Game.run_action — this file owns only movement +
# layout. One place at a time, mirroring pnc.run_place's signature/return contract.
extends RefCounted

const _TILE_MAX := 96
const _HINT := "WASD / Arrows: move    E: interact"

var g  # Game driver


func _init(game) -> void:
	g = game


func run_place(place_id, spawn):
	var place = g.place_by_id[place_id]
	var grid = place.get("grid", {"w": 8, "h": 6})
	var gw := int(grid.get("w", 8))
	var gh := int(grid.get("h", 6))

	var blocked := {}
	for cell in place.get("impassable", []):
		blocked[_key(cell["x"], cell["y"])] = true

	var inter := {}  # "x,y" -> interactable, for cells that carry one
	for it in place.get("interactables", []):
		var cpos = it.get("position", {}).get("cell")
		if cpos != null:
			inter[_key(cpos["x"], cpos["y"])] = it

	# Geometry: largest square tile that fits the grid in the 1280x720 view, centred.
	var tile := int(min(_TILE_MAX, min((1280 - 80) / gw, (720 - 120) / gh)))
	var ox := (1280 - tile * gw) / 2
	var oy := (720 - tile * gh) / 2

	var layer := _build_layer(place, gw, gh, tile, ox, oy, blocked, inter)

	var ax := 0
	var ay := 0
	if spawn != null and spawn.has("cell"):
		ax = int(spawn["cell"]["x"])
		ay = int(spawn["cell"]["y"])
	var avatar := _make_avatar(tile)
	layer.add_child(avatar)
	_place_avatar(avatar, ax, ay, tile, ox, oy)
	g.set_hud(_HINT)

	while true:
		await g.get_tree().process_frame

		var dx := 0
		var dy := 0
		if Input.is_action_just_pressed("move_up"):
			dy = -1
		elif Input.is_action_just_pressed("move_down"):
			dy = 1
		elif Input.is_action_just_pressed("move_left"):
			dx = -1
		elif Input.is_action_just_pressed("move_right"):
			dx = 1

		if dx != 0 or dy != 0:
			var nx := ax + dx
			var ny := ay + dy
			if nx >= 0 and nx < gw and ny >= 0 and ny < gh and not blocked.has(_key(nx, ny)):
				ax = nx
				ay = ny
				_place_avatar(avatar, ax, ay, tile, ox, oy)
				var it = inter.get(_key(ax, ay))
				if it != null and it["action"]["type"] in ["move", "start_combat"]:
					var r = await _fire(layer, place, it)
					if r != null:
						return r

		if Input.is_action_just_pressed("interact"):
			var it = inter.get(_key(ax, ay))
			if it != null and not (it["action"]["type"] in ["move", "start_combat"]):
				var r = await _fire(layer, place, it)
				if r != null:
					return r


# Run a verb through Game, hiding the grid while dialogue/combat owns the screen. Returns a
# WIN/END/{move} result to bubble up, or null to keep exploring (and restores the map).
func _fire(layer: Control, place, it) -> Variant:
	layer.visible = false
	var r = await g.run_action(it["action"])
	# Check the {move} dict BEFORE the string compares — Godot 4 errors on Dictionary == String.
	if typeof(r) == TYPE_DICTIONARY and r.has("move"):
		layer.queue_free()
		return r
	elif r == g.WIN or r == g.END:
		layer.queue_free()
		return r
	g.set_scene(place.get("background"))
	layer.visible = true
	g.set_hud(_HINT)
	return null


# ── rendering ────────────────────────────────────────────────────────────────────────────────
func _build_layer(place, gw, gh, tile, ox, oy, blocked, inter) -> Control:
	g.set_scene(place.get("background"))
	for c in g.sprites_node().get_children():
		c.queue_free()
	var layer := Control.new()
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	g.add_child(layer)
	for cy in gh:
		for cx in gw:
			var cell := ColorRect.new()
			cell.position = Vector2(ox + cx * tile + 1, oy + cy * tile + 1)
			cell.size = Vector2(tile - 2, tile - 2)
			cell.mouse_filter = Control.MOUSE_FILTER_IGNORE
			var k := _key(cx, cy)
			if blocked.has(k):
				cell.color = Color(0.18, 0.18, 0.24)
			elif inter.has(k):
				cell.color = Color(0.28, 0.34, 0.5)
			else:
				cell.color = Color(0.16, 0.5, 0.32, 0.55)
			layer.add_child(cell)
			if inter.has(k):
				var lbl := Label.new()
				lbl.text = inter[k].get("label", "")
				lbl.position = cell.position
				lbl.size = cell.size
				lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
				lbl.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
				lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
				lbl.add_theme_font_size_override("font_size", 14)
				lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
				layer.add_child(lbl)
	return layer


func _make_avatar(tile) -> Control:
	var tex = _avatar_texture()
	if tex != null:
		var tr := TextureRect.new()
		tr.texture = tex
		tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		tr.size = Vector2(tile, tile)
		tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
		return tr
	var dot := ColorRect.new()
	dot.color = Color(0.95, 0.85, 0.2)
	dot.size = Vector2(tile * 0.6, tile * 0.6)
	dot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	return dot


# Avatar sprite = the player-faction combatant's character (falls back to characters[0], then a
# coloured dot). Combat identity is reused for the map so there is no extra IR field.
func _avatar_texture():
	var cid = null
	for enc in g.ir.get("encounters", []):
		for c in enc.get("combatants", []):
			if c.get("faction") == "player":
				var cb = g.combatant_by_id.get(c.get("ref"), {})
				cid = cb.get("character")
				break
		if cid != null:
			break
	if cid == null:
		var chars = g.ir.get("characters", [])
		if not chars.is_empty():
			cid = chars[0]["id"]
	if cid != null and g.chars.has(cid) and g.chars[cid].has("sprite"):
		return g._texture_file(g.chars[cid]["sprite"])
	return null


func _place_avatar(avatar: Control, cx, cy, tile, ox, oy) -> void:
	var s = avatar.size
	avatar.position = Vector2(ox + cx * tile + (tile - s.x) / 2, oy + cy * tile + (tile - s.y) / 2)


func _key(x, y) -> String:
	return "%d,%d" % [int(x), int(y)]
