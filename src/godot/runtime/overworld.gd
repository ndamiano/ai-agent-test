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

# Below this, a fitted tile reads as illegible confetti on a big map — switch to a fixed
# readable tile size and pan a camera instead of squeezing the whole grid into the frame.
const _PAN_FIT_FLOOR := 32
const _PAN_TILE := 40
const _FRAME_W := 1280
const _FRAME_H := 720

# Fixed scan order for autotile transition lookups (tile_<self>__<neighbor>_<dir>.png).
const _TRANS_DIRS := [["n", 0, -1], ["e", 1, 0], ["s", 0, 1], ["w", -1, 0]]

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
var _tex_cache := {}    # theme -> texture-or-null, so a map of many same-theme cells loads art once
var _trans_cache := {}  # "<selfSlug>__<neighborSlug>_<dir>" -> transition texture-or-null
var _labels := []       # {x, y, node} per interactable label — visibility follows the avatar


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

	# Geometry: largest square tile that fits the grid in the 1280x720 view, centred. Once a big
	# map (zones are moving to one-map-per-island, up to 64x48) would squeeze below a readable
	# size, hold tile size fixed and pan a camera over the grid instead (see _pan_target).
	var fit_tile := int(min(_TILE_MAX, min((1280 - 80) / gw, (720 - 120) / gh)))
	var scrolling := fit_tile < _PAN_FIT_FLOOR
	var tile := _PAN_TILE if scrolling else fit_tile

	# Cells under a feature whose sprite is on disk render as plain open ground — the sprite
	# IS the feature; the themed mosaic + wall faces beneath it just clash. (Movement blocking
	# is untouched: `blocked` reads roles from the legend above.)
	var covered := {}
	var fps = place.get("footprints", {})
	if typeof(fps) == TYPE_DICTIONARY:
		for fid in fps:
			var fp = fps[fid]
			if g._texture_file("feature_%s.png" % _slug(String(fp.get("label", "")))) == null:
				continue
			for dy in int(fp.get("h", 0)):
				for dx in int(fp.get("w", 0)):
					covered[_key(int(fp["x"]) + dx, int(fp["y"]) + dy)] = true

	var built := _build_layer(place, rows, legend, gw, gh, tile, inter, covered)
	var layer: Control = built["layer"]
	var world: Control = built["world"]
	_draw_features(world, fps, tile)

	var sx := 0
	var sy := 0
	if spawn != null and spawn.has("cell"):
		sx = int(spawn["cell"]["x"])
		sy = int(spawn["cell"]["y"])
	var start_cell := _nearest_open(sx, sy, blocked, gw, gh)
	var ax := start_cell.x
	var ay := start_cell.y
	var home_x := ax   # wild-defeat respawn point: where the player entered this zone
	var home_y := ay
	var table = place.get("encounter_table")
	var avatar := _make_avatar(tile)
	world.add_child(avatar)
	_place_avatar(avatar, ax, ay, tile)
	_refresh_labels(ax, ay)
	g.avatar_cell = {"x": ax, "y": ay}
	g.set_hud(_HINT)

	# Camera-follow: all world content lives under `world`; panning it is the ONLY screen
	# transform — every position above is world-local (cx*tile, cy*tile), so nothing else needs
	# to know about scrolling. Snap when the map fits the frame (unchanged, static centring);
	# smooth-follow only kicks in once panning is live, and it's the sole interpolation on
	# `world.position` (the avatar itself never tweens in 2D) — no double-smoothing to jitter.
	var pan_target := _pan_target(ax, ay, tile, gw, gh)
	world.position = pan_target

	while true:
		await g.get_tree().process_frame

		if scrolling:
			var dt: float = g.get_process_delta_time()
			world.position = world.position.lerp(pan_target, clampf(dt * 10.0, 0.0, 1.0))

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
				_place_avatar(avatar, ax, ay, tile)
				_refresh_labels(ax, ay)
				g.avatar_cell = {"x": ax, "y": ay}
				pan_target = _pan_target(ax, ay, tile, gw, gh)
				var it = inter.get(_key(ax, ay))
				if it != null and it["action"]["type"] in ["move", "start_combat"]:
					var r = await _fire(layer, it)
					if r != null:
						return r
				elif it == null and typeof(table) == TYPE_DICTIONARY \
						and randf() < float(table.get("rate", 0)):
					var lost = await _wild_fight(layer, table)
					if lost:
						ax = home_x
						ay = home_y
						_place_avatar(avatar, ax, ay, tile)
						_refresh_labels(ax, ay)
						g.avatar_cell = {"x": ax, "y": ay}
						pan_target = _pan_target(ax, ay, tile, gw, gh)

		if Input.is_action_just_pressed("interact"):
			var it = inter.get(_key(ax, ay))
			if it != null and not (it["action"]["type"] in ["move", "start_combat"]):
				var r = await _fire(layer, it)
				if r != null:
					return r

		if Input.is_action_just_pressed("ui_pause"):
			await g.pause_menu()

		if Input.is_action_just_pressed("ui_journal"):
			await g.journal_panel()


# A wild fight from this zone's encounter_table: weighted draw, tier-scaled enemy, fought by
# the persistent progression player. Returns true on DEFEAT — the caller respawns the avatar at
# the zone entrance with half its depletables (grinding is safe-ish; authored fights keep their
# on_defeat stakes).
func _wild_fight(layer: Control, table) -> bool:
	var entries: Array = table.get("entries", [])
	if entries.is_empty() or g.ir.get("progression") == null:
		return false
	var total := 0
	for e in entries:
		total += int(e.get("weight", 1))
	var roll: int = randi() % maxi(total, 1)
	var pick = entries[0]
	for e in entries:
		roll -= int(e.get("weight", 1))
		if roll < 0:
			pick = e
			break
	layer.visible = false
	var cmb = load("res://combat.gd").new(g)
	var r = await cmb.run_wild(String(pick["combatant"]), float(pick.get("tier", 1.0)))
	g.set_scene(null)
	layer.visible = true
	g.set_hud(_HINT)
	if typeof(r) == TYPE_DICTIONARY and r.get("wild_defeat"):
		var ps = g.pstats()
		if ps != null:
			for sid in ps["max"]:
				ps["stats"][sid] = max(1, int(float(ps["max"][sid]) / 2))
		await g.show_line(null, "You come to at the edge of the zone, wounds half-bound.")
		g.hide_dialogue()
		return true
	return false


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
# Returns {"layer": the top-level screen Control (visibility toggle + queue_free owner, unchanged
# contract), "world": the container every tile/feature/label/avatar is parented into at WORLD-LOCAL
# coordinates (cx*tile, cy*tile) — panning is just `world.position`, so it's the one screen
# transform and every drawn node picks it up for free.
func _build_layer(place, rows, legend, gw, gh, tile, inter, covered := {}) -> Dictionary:
	g.set_scene(null)  # the tiles ARE the scene — no backdrop image
	for c in g.sprites_node().get_children():
		c.queue_free()
	var layer := Control.new()
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	g.add_child(layer)
	var world := Control.new()
	world.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(world)

	# Optional per-cell heightmap (place.layout.elevation, same dims as tiles.rows). Absent or
	# flat -> has_elev stays false and every cell renders exactly as without this field.
	var layout = place.get("layout", {})
	var elev = layout.get("elevation") if typeof(layout) == TYPE_DICTIONARY else null
	var elev_min := INF
	var elev_max := -INF
	var has_elev := false
	if typeof(elev) == TYPE_ARRAY:
		for erow in elev:
			if typeof(erow) != TYPE_ARRAY:
				continue
			for v in erow:
				if typeof(v) != TYPE_FLOAT and typeof(v) != TYPE_INT:
					continue
				elev_min = minf(elev_min, float(v))
				elev_max = maxf(elev_max, float(v))
				has_elev = true
	var elev_range := elev_max - elev_min
	if elev_range <= 0.0:
		has_elev = false

	# Role lookup for neighbour checks: 2.5D wall treatment needs to know what's below/above.
	# Sprite-covered cells count as open so no face bands sprout around a drawn feature.
	var roles := []
	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		var rrow := []
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var r := String(_spec_of(ch, legend).get("role", "open"))
			rrow.append("open" if covered.has(_key(cx, cy)) else r)
		roles.append(rrow)

	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var spec = _spec_of("." if covered.has(_key(cx, cy)) else ch, legend)
			var role := String(spec.get("role", "open"))
			var theme := String(spec.get("theme", ""))
			var pos := Vector2(cx * tile, cy * tile)
			var siz := Vector2(tile, tile)
			var below_open: bool = cy + 1 < gh and roles[cy + 1][cx] == "open"
			var above_open: bool = cy > 0 and roles[cy - 1][cx] == "open"
			var drawn: CanvasItem = null
			# Directional autotile: a ground cell bordering a different theme draws that edge's
			# authored transition art (self-contained, no 3x3 sampling) instead of the base mosaic.
			var trans_tex = _transition_texture(theme, cx, cy, rows, legend, gw, gh, covered) \
					if role == "open" else null
			if trans_tex != null:
				var ttr := TextureRect.new()
				ttr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
				ttr.stretch_mode = TextureRect.STRETCH_SCALE
				ttr.texture = trans_tex
				ttr.position = pos
				ttr.size = siz
				ttr.mouse_filter = Control.MOUSE_FILTER_IGNORE
				world.add_child(ttr)
				drawn = ttr
			else:
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
					world.add_child(tr)
					drawn = tr
				else:
					# colour fallback keeps a 1px grid inset so bare cells still read as tiles
					var cell := ColorRect.new()
					cell.position = pos + Vector2(1, 1)
					cell.size = siz - Vector2(2, 2)
					cell.mouse_filter = Control.MOUSE_FILTER_IGNORE
					cell.color = _tile_color(role, theme)
					world.add_child(cell)
					drawn = cell
			if has_elev and role == "open":
				# Subtle brightness-by-height + a darker line where the slope to a neighbour is
				# steep (a ledge/step read) — normalized per place so it stays terrain-legible.
				var f := _elevation_factor(elev, elev_min, elev_range, cx, cy)
				drawn.modulate = drawn.modulate * Color(f, f, f, 1.0)
			# 2.5D wall treatment — a flat full-tile texture reads as ground no matter how good
			# the masonry is. A blocked cell over open ground gets a FACE (darkened lower band =
			# the wall's front) and casts a shadow onto the ground cell below; a top edge above
			# open ground gets a thin highlight. Pure overlays, works over textures and fills.
			if role == "blocked":
				if below_open:
					var ftex = _tile_texture(theme)
					if ftex != null:
						# The face shows the wall MATERIAL, vertically squashed (top third of
						# this cell's texture region) and darkened — masonry on the face, not
						# a flat black card. The plain band stays as the no-texture fallback.
						var third_w = ftex.get_width() / 3.0
						var third_h = ftex.get_height() / 3.0
						var fat := AtlasTexture.new()
						fat.atlas = ftex
						fat.region = Rect2((cx % 3) * third_w, (cy % 3) * third_h,
							third_w, third_h / 3.0)
						var ftr := TextureRect.new()
						ftr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
						ftr.stretch_mode = TextureRect.STRETCH_SCALE
						ftr.texture = fat
						ftr.position = pos + Vector2(0, siz.y * 0.5)
						ftr.size = Vector2(siz.x, siz.y * 0.5)
						ftr.modulate = Color(0.42, 0.40, 0.45)
						ftr.mouse_filter = Control.MOUSE_FILTER_IGNORE
						world.add_child(ftr)
					else:
						var face := ColorRect.new()
						face.position = pos + Vector2(0, siz.y * 0.5)
						face.size = Vector2(siz.x, siz.y * 0.5)
						face.color = Color(0, 0, 0, 0.51)
						face.mouse_filter = Control.MOUSE_FILTER_IGNORE
						world.add_child(face)
					var lip := ColorRect.new()
					lip.position = pos + Vector2(0, siz.y * 0.5 - 2)
					lip.size = Vector2(siz.x, 2)
					lip.color = Color(1, 1, 1, 0.16)
					lip.mouse_filter = Control.MOUSE_FILTER_IGNORE
					world.add_child(lip)
					var shadow := ColorRect.new()
					shadow.position = pos + Vector2(0, siz.y)
					shadow.size = Vector2(siz.x, siz.y * 0.2)
					shadow.color = Color(0, 0, 0, 0.27)
					shadow.mouse_filter = Control.MOUSE_FILTER_IGNORE
					world.add_child(shadow)
				if above_open:
					var crest := ColorRect.new()
					crest.position = pos
					crest.size = Vector2(siz.x, 3)
					crest.color = Color(1, 1, 1, 0.22)
					crest.mouse_filter = Control.MOUSE_FILTER_IGNORE
					world.add_child(crest)

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
			tr.position = Vector2(cx * tile + (tile - m) / 2, cy * tile + (tile - m) / 2)
			tr.size = Vector2(m, m)
			tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
			world.add_child(tr)
		else:
			# No art: a small rotated diamond, colour-coded by verb; exits stay subtlest.
			var m = tile * (0.30 if atype == "move" else 0.42)
			var marker := ColorRect.new()
			marker.position = Vector2(cx * tile + tile / 2.0, cy * tile + (tile - m) / 2)
			marker.size = Vector2(m, m)
			marker.rotation = PI / 4
			var col: Color = _MARKERS.get(atype, Color(0.85, 0.85, 0.85))
			col.a = 0.55 if atype == "move" else 0.9
			marker.color = col
			marker.mouse_filter = Control.MOUSE_FILTER_IGNORE
			world.add_child(marker)
		var lbl := Label.new()
		lbl.text = it.get("label", "")
		lbl.position = Vector2(cx * tile - tile * 0.5, cy * tile + tile * 0.66)
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
		world.add_child(lbl)
		_labels.append({"x": cx, "y": cy, "node": lbl})
	return {"layer": layer, "world": world}


# The generated art for a hotspot, if any: a take/use that names an item shows that item's
# inventory icon (<item_id>.png — generated from the asset manifest every build).
func _interactable_icon(it):
	var a = it.get("action", {})
	var atype = a.get("type", "")
	if atype == "talk":
		# An NPC hotspot IS a person — draw their token instead of a blue diamond. The hotspot
		# label is the only link to the cast (a talk action targets a node, not a character).
		# Exact match wins; else a character name as a whole word inside the label ("Elara
		# Checkin" -> Elara) — labels routinely decorate the name with a verb.
		var label := String(it.get("label", "")).strip_edges().to_lower()
		for cid in g.chars:
			var ch = g.chars[cid]
			if label == String(cid).to_lower() or label == String(ch.get("name", "")).to_lower():
				return g._texture_file("%s_token.png" % cid)
		for cid in g.chars:
			var nm := String(g.chars[cid].get("name", "")).strip_edges().to_lower()
			if nm != "" and (" " + label + " ").contains(" " + nm + " "):
				return g._texture_file("%s_token.png" % cid)
		return null
	if atype == "move":
		return g._texture_file("marker_signpost.png")
	if atype == "win":
		return g._texture_file("marker_banner.png")
	if atype == "start_combat":
		return g._texture_file("marker_combat.png")
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
	if item != "":
		var it_tex = g._texture_file("%s.png" % item)
		if it_tex != null:
			return it_tex
	if atype == "use":
		# a use hotspot is a physical mechanism — its label prop is the marker
		var ulbl := String(it.get("label", ""))
		if ulbl != "":
			return g._texture_file("prop_%s.png" % _slug(ulbl))
	return null


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


# The theme a grid cell resolves to, honouring the same feature-covered-as-ground override the
# render loop uses — shared so a transition lookup sees exactly what a neighbour will draw as.
# Off-grid returns "" (no theme), which _transition_texture treats as "no edge here".
func _theme_at(cx: int, cy: int, rows: Array, legend: Dictionary, gw: int, gh: int,
		covered: Dictionary) -> String:
	if cx < 0 or cx >= gw or cy < 0 or cy >= gh:
		return ""
	var row := String(rows[cy]) if cy < rows.size() else ""
	var ch := row.substr(cx, 1) if cx < row.length() else "."
	var spec = _spec_of("." if covered.has(_key(cx, cy)) else ch, legend)
	return String(spec.get("theme", ""))


# Directional autotile art for a ground cell against a differently-themed neighbour: an asset
# pass generates tile_<selfSlug>__<neighborSlug>_<n|s|e|w>.png for exactly this "cell of theme A
# whose <dir> neighbor is theme B" case. Scans neighbours in fixed n,e,s,w priority and returns
# the first variant that exists on disk; null (caller falls back to the plain base mosaic) when
# every neighbour is same-themed or no variant was generated for the pairing.
func _transition_texture(theme: String, cx: int, cy: int, rows: Array, legend: Dictionary,
		gw: int, gh: int, covered: Dictionary):
	if theme == "":
		return null
	var self_slug := _slug(theme)
	for d in _TRANS_DIRS:
		var ntheme := _theme_at(cx + int(d[1]), cy + int(d[2]), rows, legend, gw, gh, covered)
		if ntheme == "" or ntheme == theme:
			continue
		var key := "%s__%s_%s" % [self_slug, _slug(ntheme), d[0]]
		if not _trans_cache.has(key):
			_trans_cache[key] = g._texture_file("tile_%s.png" % key)
		var tex = _trans_cache[key]
		if tex != null:
			return tex
	return null


# A single elevation value at (cx, cy), or null when the heightmap is absent/malformed/out of
# bounds there — every caller treats null as "no data, don't tint."
func _elevation_at(elev, cx: int, cy: int):
	if typeof(elev) != TYPE_ARRAY or cy < 0 or cy >= elev.size():
		return null
	var erow = elev[cy]
	if typeof(erow) != TYPE_ARRAY or cx < 0 or cx >= erow.size():
		return null
	var v = erow[cx]
	if typeof(v) != TYPE_FLOAT and typeof(v) != TYPE_INT:
		return null
	return float(v)


# Brightness multiplier for a ground cell: 0.88..1.12 across the place's elevation range (subtle,
# terrain stays recognizable), knocked down another ~20% when the step to any 4-neighbour exceeds
# 0.18 of that range (reads as a slope/ledge line). 1.0 wherever this cell has no elevation data.
func _elevation_factor(elev, elev_min: float, elev_range: float, cx: int, cy: int) -> float:
	var v = _elevation_at(elev, cx, cy)
	if v == null:
		return 1.0
	var factor := lerpf(0.88, 1.12, clampf((v - elev_min) / elev_range, 0.0, 1.0))
	for d in [Vector2i(0, -1), Vector2i(1, 0), Vector2i(0, 1), Vector2i(-1, 0)]:
		var nv = _elevation_at(elev, cx + d.x, cy + d.y)
		if nv != null and absf(v - nv) > 0.18 * elev_range:
			factor *= 0.8
			break
	return factor


# The `world` container's target position: centred when the map fits the 1280x720 frame (exactly
# today's static ox/oy — a fitted axis never moves), panned to keep the avatar centred on that axis
# and clamped to the map's own pixel bounds otherwise (no empty space past an edge).
func _pan_target(ax: int, ay: int, tile: int, gw: int, gh: int) -> Vector2:
	var map_w := tile * gw
	var map_h := tile * gh
	var ox: float
	if map_w <= _FRAME_W:
		ox = float((_FRAME_W - map_w) / 2)
	else:
		ox = clampf(_FRAME_W / 2.0 - (ax + 0.5) * tile, float(_FRAME_W - map_w), 0.0)
	var oy: float
	if map_h <= _FRAME_H:
		oy = float((_FRAME_H - map_h) / 2)
	else:
		oy = clampf(_FRAME_H / 2.0 - (ay + 0.5) * tile, float(_FRAME_H - map_h), 0.0)
	return Vector2(ox, oy)


# Generated object sprites over stamped footprints — a building reads as a building, not a
# mosaic of blocked tiles. The mosaic stays underneath (sprite is matted transparent), so a
# missing asset degrades to the old look. The sprite overhangs upward half a tile for a hint
# of elevation.
func _draw_features(world: Control, footprints, tile: int) -> void:
	if typeof(footprints) != TYPE_DICTIONARY:
		return
	for fid in footprints:
		var fp = footprints[fid]
		var tex = g._texture_file("feature_%s.png" % _slug(String(fp.get("label", ""))))
		if tex == null:
			continue
		var tr := TextureRect.new()
		tr.texture = tex
		tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		var over := int(tile / 2.0)
		tr.position = Vector2(int(fp["x"]) * tile, int(fp["y"]) * tile - over)
		tr.size = Vector2(int(fp["w"]) * tile, int(fp["h"]) * tile + over)
		tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
		world.add_child(tr)


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


func _place_avatar(avatar: Control, cx, cy, tile) -> void:
	var s = avatar.size
	avatar.position = Vector2(cx * tile + (tile - s.x) / 2, cy * tile + (tile - s.y) / 2)


func _key(x, y) -> String:
	return "%d,%d" % [int(x), int(y)]


# Snap an arrival cell onto the walkable floor. A place entered with no spawn (New Game hits the
# start place, whose IR has no arrival cell) defaults to (0,0) — on a bordered map that's a wall
# corner boxed in by more wall, so the avatar can never take a step. BFS out to the nearest OPEN
# cell so every entry lands the player somewhere they can actually move from.
func _nearest_open(sx: int, sy: int, blocked: Dictionary, gw: int, gh: int) -> Vector2i:
	var start := Vector2i(clampi(sx, 0, gw - 1), clampi(sy, 0, gh - 1))
	var seen := {start: true}
	var q: Array[Vector2i] = [start]
	while not q.is_empty():
		var c: Vector2i = q.pop_front()
		if not blocked.has(_key(c.x, c.y)):
			return c
		for d in [Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)]:
			var n: Vector2i = c + d
			if n.x >= 0 and n.x < gw and n.y >= 0 and n.y < gh and not seen.has(n):
				seen[n] = true
				q.append(n)
	return start
