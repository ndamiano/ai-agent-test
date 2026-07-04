# Overworld presenter — a walkable RPG tile grid. Renders place.tiles (rows of chars, each resolved
# via tiles.legend to an open/blocked role + a cosmetic theme) as coloured tiles, drops the avatar at
# the arrival spawn, and steps it one cell per WASD/arrow press (blocked by wall tiles and the grid
# edge). The map IS the scene — no background image behind it. Grid is any size (it's just the shape
# of rows). Triggering is HYBRID: walking ONTO a move/start_combat cell auto-fires it; for
# talk/examine/take/use/win you stand on the cell and press E. Every verb resolves through the shared
# Game.run_action — this file owns only movement + layout. One place at a time, mirroring
# pnc.run_place's signature/return contract.
extends RefCounted

const _TILE_MAX := 88
const _HINT := "WASD / Arrows: move    E: interact"

# The built-in tile vocabulary (mirrors world.DEFAULT_LEGEND) — chars a map can use without a legend
# entry. A place's own tiles.legend overrides/extends this per char.
const _DEFAULTS := {
	".": {"role": "open", "theme": "ground"},
	",": {"role": "open", "theme": "path"},
	"#": {"role": "blocked", "theme": "wall"},
	"T": {"role": "blocked", "theme": "tree"},
	"~": {"role": "blocked", "theme": "water"},
	"%": {"role": "blocked", "theme": "rock"},
}

# Keyword -> base colour, scanned against a tile's theme so common terrain reads right (water blue,
# tree green, snow white). An unknown theme falls back to a hash of the string, so distinct themes
# still get distinct, stable colours.
const _PALETTE := {
	"water": Color(0.20, 0.38, 0.62), "river": Color(0.20, 0.38, 0.62),
	"stream": Color(0.22, 0.40, 0.60), "sea": Color(0.18, 0.36, 0.60),
	"tree": Color(0.20, 0.42, 0.24), "forest": Color(0.18, 0.40, 0.22),
	"pine": Color(0.20, 0.40, 0.26), "grass": Color(0.30, 0.52, 0.30),
	"snow": Color(0.80, 0.84, 0.90), "ice": Color(0.66, 0.78, 0.85),
	"path": Color(0.52, 0.42, 0.28), "road": Color(0.50, 0.40, 0.28),
	"trail": Color(0.50, 0.42, 0.30), "dirt": Color(0.46, 0.36, 0.26),
	"ground": Color(0.42, 0.44, 0.34), "sand": Color(0.72, 0.64, 0.42),
	"wall": Color(0.34, 0.32, 0.36), "stone": Color(0.40, 0.40, 0.44),
	"rock": Color(0.36, 0.36, 0.40), "cliff": Color(0.34, 0.33, 0.36),
	"lava": Color(0.66, 0.24, 0.16), "ash": Color(0.34, 0.31, 0.31),
	"floor": Color(0.40, 0.38, 0.42), "mud": Color(0.40, 0.33, 0.24),
}

# Marker colour by the interactable's verb, so exits/fights/NPCs read at a glance.
const _MARKERS := {
	"move": Color(0.35, 0.80, 0.45), "start_combat": Color(0.86, 0.32, 0.30),
	"talk": Color(0.34, 0.70, 0.92), "win": Color(0.96, 0.82, 0.24),
	"take": Color(0.92, 0.60, 0.26), "examine": Color(0.78, 0.78, 0.82),
	"use": Color(0.66, 0.56, 0.90),
}

var g  # Game driver
var _tex_cache := {}  # theme -> texture-or-null, so a map of many same-theme cells loads art once
var _labels := []     # {x, y, node} per interactable label — visibility follows the avatar


func _init(game) -> void:
	g = game


func run_place(place_id, spawn):
	var place = g.place_by_id[place_id]
	var tiles = place.get("tiles", {})
	var rows: Array = tiles.get("rows", [])
	var legend: Dictionary = tiles.get("legend", {})

	var gh := rows.size()
	var gw := 0
	for r in rows:
		gw = max(gw, String(r).length())
	if gw == 0 or gh == 0:
		gw = 8
		gh = 6

	var blocked := {}
	for y in rows.size():
		var row := String(rows[y])
		for x in row.length():
			if _spec_of(row[x], legend).get("role", "open") == "blocked":
				blocked[_key(x, y)] = true

	var inter := {}  # "x,y" -> interactable, for cells that carry one
	for it in place.get("interactables", []):
		var cpos = it.get("position", {}).get("cell")
		if cpos != null:
			inter[_key(cpos["x"], cpos["y"])] = it

	# Geometry: largest square tile that fits the grid in the 1280x720 view, centred.
	var tile := int(min(_TILE_MAX, min((1280 - 80) / gw, (720 - 120) / gh)))
	var ox := (1280 - tile * gw) / 2
	var oy := (720 - tile * gh) / 2

	var layer := _build_layer(rows, legend, gw, gh, tile, ox, oy, inter)

	var ax := 0
	var ay := 0
	if spawn != null and spawn.has("cell"):
		ax = int(spawn["cell"]["x"])
		ay = int(spawn["cell"]["y"])
	var avatar := _make_avatar(tile)
	layer.add_child(avatar)
	_place_avatar(avatar, ax, ay, tile, ox, oy)
	_refresh_labels(ax, ay)
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
				_refresh_labels(ax, ay)
				var it = inter.get(_key(ax, ay))
				if it != null and it["action"]["type"] in ["move", "start_combat"]:
					var r = await _fire(layer, it)
					if r != null:
						return r

		if Input.is_action_just_pressed("interact"):
			var it = inter.get(_key(ax, ay))
			if it != null and not (it["action"]["type"] in ["move", "start_combat"]):
				var r = await _fire(layer, it)
				if r != null:
					return r


# Run a verb through Game, hiding the grid while dialogue/combat owns the screen. Returns a
# WIN/END/{move} result to bubble up, or null to keep exploring (and restores the map).
func _fire(layer: Control, it) -> Variant:
	layer.visible = false
	var r = await g.run_action(it["action"])
	# Check the {move} dict BEFORE the string compares — Godot 4 errors on Dictionary == String.
	if typeof(r) == TYPE_DICTIONARY and r.has("move"):
		layer.queue_free()
		return r
	elif r == g.WIN or r == g.END:
		layer.queue_free()
		return r
	g.set_scene(null)
	layer.visible = true
	g.set_hud(_HINT)
	return null


# ── rendering ────────────────────────────────────────────────────────────────────────────────
func _build_layer(rows, legend, gw, gh, tile, ox, oy, inter) -> Control:
	g.set_scene(null)  # the tiles ARE the scene — no backdrop image
	for c in g.sprites_node().get_children():
		c.queue_free()
	var layer := Control.new()
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	g.add_child(layer)

	# Role lookup for neighbour checks: 2.5D wall treatment needs to know what's below/above.
	var roles := []
	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		var rrow := []
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			rrow.append(String(_spec_of(ch, legend).get("role", "open")))
		roles.append(rrow)

	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var spec = _spec_of(ch, legend)
			var role := String(spec.get("role", "open"))
			var theme := String(spec.get("theme", ""))
			var pos := Vector2(ox + cx * tile, oy + cy * tile)
			var siz := Vector2(tile, tile)
			var below_open: bool = cy + 1 < gh and roles[cy + 1][cx] == "open"
			var above_open: bool = cy > 0 and roles[cy - 1][cx] == "open"
			var tex = _tile_texture(theme)
			if tex != null:
				# Each cell samples ITS region of the (seamless) texture — one texture spans a
				# 3x3 cell block and wraps exactly, so same-theme terrain flows continuously
				# across cells instead of stamping the whole squeezed image per cell.
				var third_w = tex.get_width() / 3.0
				var third_h = tex.get_height() / 3.0
				var at := AtlasTexture.new()
				at.atlas = tex
				at.region = Rect2((cx % 3) * third_w, (cy % 3) * third_h, third_w, third_h)
				var tr := TextureRect.new()
				tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
				tr.stretch_mode = TextureRect.STRETCH_SCALE
				tr.texture = at
				tr.position = pos
				tr.size = siz
				tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
				if role == "blocked":
					# Mild dim only — the 2.5D face below is what reads as "wall"; heavy
					# dimming made whole walls read as dark ground (observed).
					tr.modulate = Color(0.85, 0.85, 0.90)
				layer.add_child(tr)
			else:
				# colour fallback keeps a 1px grid inset so bare cells still read as tiles
				var cell := ColorRect.new()
				cell.position = pos + Vector2(1, 1)
				cell.size = siz - Vector2(2, 2)
				cell.mouse_filter = Control.MOUSE_FILTER_IGNORE
				cell.color = _tile_color(role, theme)
				layer.add_child(cell)
			# 2.5D wall treatment — a flat full-tile texture reads as ground no matter how good
			# the masonry is. A blocked cell over open ground gets a FACE (darkened lower band =
			# the wall's front) and casts a shadow onto the ground cell below; a top edge above
			# open ground gets a thin highlight. Pure overlays, works over textures and fills.
			if role == "blocked":
				if below_open:
					var face := ColorRect.new()
					face.position = pos + Vector2(0, siz.y * 0.5)
					face.size = Vector2(siz.x, siz.y * 0.5)
					face.color = Color(0, 0, 0, 0.51)
					face.mouse_filter = Control.MOUSE_FILTER_IGNORE
					layer.add_child(face)
					var lip := ColorRect.new()
					lip.position = pos + Vector2(0, siz.y * 0.5 - 2)
					lip.size = Vector2(siz.x, 2)
					lip.color = Color(1, 1, 1, 0.16)
					lip.mouse_filter = Control.MOUSE_FILTER_IGNORE
					layer.add_child(lip)
					var shadow := ColorRect.new()
					shadow.position = pos + Vector2(0, siz.y)
					shadow.size = Vector2(siz.x, siz.y * 0.2)
					shadow.color = Color(0, 0, 0, 0.27)
					shadow.mouse_filter = Control.MOUSE_FILTER_IGNORE
					layer.add_child(shadow)
				if above_open:
					var crest := ColorRect.new()
					crest.position = pos
					crest.size = Vector2(siz.x, 3)
					crest.color = Color(1, 1, 1, 0.22)
					crest.mouse_filter = Control.MOUSE_FILTER_IGNORE
					layer.add_child(crest)

	_labels.clear()
	for k in inter:
		var it = inter[k]
		var cell = it["position"]["cell"]
		var cx := int(cell["x"])
		var cy := int(cell["y"])
		var atype := String(it["action"].get("type", ""))
		var icon = _interactable_icon(it)
		if icon != null:
			# The generated inventory icon IS the marker — the thing sits on the map.
			var m = tile * 0.82
			var tr := TextureRect.new()
			# expand_mode BEFORE size: with the default EXPAND_KEEP_SIZE the texture's native
			# resolution becomes the minimum size and the size assignment clamps up to it.
			tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
			tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
			tr.texture = icon
			tr.position = Vector2(ox + cx * tile + (tile - m) / 2, oy + cy * tile + (tile - m) / 2)
			tr.size = Vector2(m, m)
			tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
			layer.add_child(tr)
		else:
			# No art: a small rotated diamond, colour-coded by verb; exits stay subtlest.
			var m = tile * (0.30 if atype == "move" else 0.42)
			var marker := ColorRect.new()
			marker.position = Vector2(ox + cx * tile + tile / 2.0, oy + cy * tile + (tile - m) / 2)
			marker.size = Vector2(m, m)
			marker.rotation = PI / 4
			var col: Color = _MARKERS.get(atype, Color(0.85, 0.85, 0.85))
			col.a = 0.55 if atype == "move" else 0.9
			marker.color = col
			marker.mouse_filter = Control.MOUSE_FILTER_IGNORE
			layer.add_child(marker)
		var lbl := Label.new()
		lbl.text = it.get("label", "")
		lbl.position = Vector2(ox + cx * tile - tile * 0.5, oy + cy * tile + tile * 0.66)
		lbl.size = Vector2(tile * 2.0, tile * 0.34)
		lbl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		lbl.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
		lbl.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		lbl.add_theme_font_size_override("font_size", 12)
		lbl.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.9))
		lbl.add_theme_constant_override("shadow_offset_x", 1)
		lbl.add_theme_constant_override("shadow_offset_y", 1)
		lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
		lbl.visible = false   # shown only when the avatar is on/adjacent (no label soup)
		layer.add_child(lbl)
		_labels.append({"x": cx, "y": cy, "node": lbl})
	return layer


# The generated art for a hotspot, if any: a take/use that names an item shows that item's
# inventory icon (<item_id>.png — generated from the asset manifest every build).
func _interactable_icon(it):
	var a = it.get("action", {})
	var atype = a.get("type", "")
	if atype == "talk":
		# An NPC hotspot IS a person — draw their token instead of a blue diamond. The hotspot
		# label is the only link to the cast (a talk action targets a node, not a character).
		var label := String(it.get("label", "")).strip_edges().to_lower()
		for cid in g.chars:
			var ch = g.chars[cid]
			if label == String(cid).to_lower() or label == String(ch.get("name", "")).to_lower():
				return g._texture_file("%s_token.png" % cid)
		return null
	if atype == "move":
		return g._texture_file("marker_signpost.png")
	if atype == "win":
		return g._texture_file("marker_banner.png")
	if atype == "examine":
		var lbl := String(it.get("label", ""))
		if lbl != "":
			return g._texture_file("prop_%s.png" % _slug(lbl))
		return null
	var item := ""
	if atype == "take":
		item = String(a.get("item", ""))
	elif atype == "use":
		for cl in a.get("clauses", []):
			var req = cl.get("requires", {})
			if req is Dictionary and req.has("item"):
				item = String(req["item"])
				break
	if item == "":
		return null
	return g._texture_file("%s.png" % item)


# Labels only near the avatar: on or adjacent (chebyshev <= 1) — otherwise a dense map is soup.
func _refresh_labels(ax: int, ay: int) -> void:
	for e in _labels:
		e["node"].visible = max(abs(int(e["x"]) - ax), abs(int(e["y"]) - ay)) <= 1


func _spec_of(ch, legend: Dictionary) -> Dictionary:
	if legend.has(ch):
		return legend[ch]
	return _DEFAULTS.get(ch, {"role": "open", "theme": ""})


# Generated terrain texture for a theme (tile_<slug>.png), or null when there's no art — the caller
# colour-fills instead. Cached per theme so a big same-theme map loads the file once.
func _tile_texture(theme: String):
	if _tex_cache.has(theme):
		return _tex_cache[theme]
	var tex = null
	if theme != "":
		tex = g._texture_file("tile_%s.png" % _slug(theme))
	_tex_cache[theme] = tex
	return tex


# Byte-for-byte the same rule as renpy/fns.tile_slug so the filename the generator wrote matches.
func _slug(s: String) -> String:
	var out := ""
	var prev_us := false
	for i in s.length():
		var c := s.substr(i, 1).to_lower()
		if (c >= "a" and c <= "z") or (c >= "0" and c <= "9"):
			out += c
			prev_us = false
		elif not prev_us:
			out += "_"
			prev_us = true
	return out.trim_prefix("_").trim_suffix("_")


func _tile_color(role: String, theme: String) -> Color:
	var t := theme.to_lower()
	var col := Color(0.40, 0.42, 0.38)
	var hit := false
	for kw in _PALETTE:
		if t.contains(kw):
			col = _PALETTE[kw]
			hit = true
			break
	if not hit and theme != "":
		var h := 0
		for i in theme.length():
			h = (h * 31 + theme.unicode_at(i)) % 360
		col = Color.from_hsv(h / 360.0, 0.32, 0.48)
	if role == "blocked":
		col = col.darkened(0.28)
	return col


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
	if cid == null:
		return null
	# A chibi token is drawn for tile scale; the VN portrait is the fallback, not the goal.
	var token = g._texture_file("%s_token.png" % cid)
	if token != null:
		return token
	if g.chars.has(cid) and g.chars[cid].has("sprite"):
		return g._texture_file(g.chars[cid]["sprite"])
	return null


func _place_avatar(avatar: Control, cx, cy, tile, ox, oy) -> void:
	var s = avatar.size
	avatar.position = Vector2(ox + cx * tile + (tile - s.x) / 2, oy + cy * tile + (tile - s.y) / 2)


func _key(x, y) -> String:
	return "%d,%d" % [int(x), int(y)]
