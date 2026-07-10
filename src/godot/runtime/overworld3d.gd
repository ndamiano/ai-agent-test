# HD-2D (Octopath-style) overworld presenter — same place-loop/verb contract as overworld.gd,
# rendering the IDENTICAL game.json (tiles/legend/interactables) as a real 3D scene instead of
# flat 2D rects. Proves the IR is presentation-neutral: zero IR changes, only a different
# renderer wired in by Game._run_world when meta.presentation == "hd2d". Reuses overworld.gd's
# pure tile/texture/avatar helpers via composition (an Overworld instance built only for its
# helper methods — _init has no side effects beyond storing `g`) instead of duplicating them.
extends RefCounted

const Overworld = preload("res://overworld.gd")

const _CELL := 1.0
const _WALL_H := 0.6
const _SLIDE_SECS := 0.12
const _LABEL_RANGE := 2
const _HINT := "WASD / Arrows: move    E: interact"

var g  # Game driver
var _helper  # Overworld instance, used only for its pure tile/texture helper methods
var _labels := []  # {x, y, node} per interactable Label3D — visibility follows the avatar
var _root: Node3D
var _cam: Camera3D
var _avatar: Sprite3D


func _init(game) -> void:
	g = game
	_helper = Overworld.new(game)


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
			if _helper._spec_of(row[x], legend).get("role", "open") == "blocked":
				blocked[_helper._key(x, y)] = true

	var inter := {}  # "x,y" -> interactable, for cells that carry one
	for it in place.get("interactables", []):
		var cpos = it.get("position", {}).get("cell")
		if cpos != null:
			inter[_helper._key(cpos["x"], cpos["y"])] = it

	_root = _build_scene(rows, legend, gw, gh, inter, _covered_cells(place.get("footprints", {})))
	_draw_features(place.get("footprints", {}))

	var sx := 0
	var sy := 0
	if spawn != null and spawn.has("cell"):
		sx = int(spawn["cell"]["x"])
		sy = int(spawn["cell"]["y"])
	var start_cell: Vector2i = _helper._nearest_open(sx, sy, blocked, gw, gh)
	var ax := start_cell.x
	var ay := start_cell.y

	_avatar = _make_avatar()
	_root.add_child(_avatar)
	_avatar.position = Vector3(ax * _CELL, _CELL * 0.45, ay * _CELL)
	_cam.position = _avatar.position + Vector3(0, 5.0, 4.0)
	_cam.look_at(_avatar.position, Vector3.UP)
	_refresh_labels(ax, ay)
	g.set_hud(_HINT)

	var moving := false
	var slide_from := Vector3.ZERO
	var slide_to := Vector3.ZERO
	var slide_t := 0.0

	while true:
		await g.get_tree().process_frame
		var delta: float = g.get_process_delta_time()

		if Input.is_action_just_pressed("ui_pause"):
			await g.pause_menu()
			continue

		if Input.is_action_just_pressed("ui_journal"):
			await g.journal_panel()
			continue

		if moving:
			slide_t = min(1.0, slide_t + delta / _SLIDE_SECS)
			var p: Vector3 = slide_from.lerp(slide_to, slide_t)
			_avatar.position = Vector3(p.x, _avatar.position.y, p.z)
			if slide_t >= 1.0:
				moving = false
		else:
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
				if nx >= 0 and nx < gw and ny >= 0 and ny < gh and not blocked.has(_helper._key(nx, ny)):
					slide_from = Vector3(ax * _CELL, 0, ay * _CELL)
					ax = nx
					ay = ny
					slide_to = Vector3(ax * _CELL, 0, ay * _CELL)
					slide_t = 0.0
					moving = true
					_refresh_labels(ax, ay)
					var it = inter.get(_helper._key(ax, ay))
					if it != null and it["action"]["type"] in ["move", "start_combat"]:
						var r = await _fire(it)
						if r != null:
							return r

			if Input.is_action_just_pressed("interact"):
				var it = inter.get(_helper._key(ax, ay))
				if it != null and not (it["action"]["type"] in ["move", "start_combat"]):
					var r = await _fire(it)
					if r != null:
						return r

		_update_camera(delta)


# Run a verb through Game, hiding the 3D world while dialogue/combat owns the screen (and
# swapping back Game's flat scene backdrop so there's still something behind the dialogue box —
# mirrors overworld.gd's layer.visible toggle). Returns WIN/END/{move} to bubble up, null to stay.
func _fire(it) -> Variant:
	_root.visible = false
	g.set_scene(null)
	g._scene.visible = true
	var r = await g.run_action(it["action"])
	if typeof(r) == TYPE_DICTIONARY and r.has("move"):
		_teardown()
		return r
	elif r == g.WIN or r == g.END:
		_teardown()
		return r
	g._scene.visible = false
	_root.visible = true
	g.set_hud(_HINT)
	return null


func _teardown() -> void:
	g._scene.visible = true
	if _root != null:
		_root.queue_free()
		_root = null


# ── rendering ────────────────────────────────────────────────────────────────────────────────
# Footprint cells whose object actually renders (a .glb or its sprite) get NO wall box: the box
# wraps the mesh's lower half (a spot object drowns in it entirely) and reads as a grey cube
# under every set piece. A footprint with no art keeps its boxes — the blocked mosaic is the
# degrade path, an invisible obstacle is not.
func _dominant_open_theme(rows, legend, gw: int, gh: int) -> String:
	var counts := {}
	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var spec = _helper._spec_of(ch, legend)
			if String(spec.get("role", "open")) == "open":
				var th := String(spec.get("theme", ""))
				counts[th] = int(counts.get(th, 0)) + 1
	var best := ""
	var best_n := -1
	for th in counts:
		if counts[th] > best_n:
			best_n = counts[th]
			best = th
	return best


func _covered_cells(footprints) -> Dictionary:
	var out := {}
	if typeof(footprints) != TYPE_DICTIONARY:
		return out
	for fid in footprints:
		var fp = footprints[fid]
		var slug: String = _helper._slug(String(fp.get("label", "")))
		if not (FileAccess.file_exists("res://images/feature_%s.glb" % slug)
				or g._texture_file("feature_%s.png" % slug) != null):
			continue
		for dy in int(fp.get("h", 1)):
			for dx in int(fp.get("w", 1)):
				out[_helper._key(int(fp.get("x", 0)) + dx, int(fp.get("y", 0)) + dy)] = true
	return out


func _build_scene(rows, legend, gw, gh, inter, covered = {}) -> Node3D:
	# Game's flat ColorRect scene backdrop is opaque and full-rect, drawn as a 2D CanvasItem ON
	# TOP of the Viewport's 3D pass — it must be hidden or it mattes out the whole 3D world.
	g.set_scene(null)
	g._scene.visible = false
	for c in g.sprites_node().get_children():
		c.queue_free()

	var root := Node3D.new()
	g.add_child(root)

	var env := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color(0.55, 0.68, 0.85)
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color(1, 1, 1)
	environment.ambient_light_energy = 1.0
	env.environment = environment
	root.add_child(env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55, -35, 0)
	sun.light_energy = 1.1
	sun.shadow_enabled = true
	root.add_child(sun)

	var open_theme := _dominant_open_theme(rows, legend, gw, gh)
	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var spec = _helper._spec_of(ch, legend)
			var role := String(spec.get("role", "open"))
			var theme := String(spec.get("theme", ""))
			if role == "blocked" and covered.has(_helper._key(cx, cy)):
				# the mesh IS the object: its floor is the surrounding ground, not the object's
				# own blocked theme grey-tinted under it (reads as a mismatched plinth)
				role = "open"
				theme = open_theme
			var tex = _helper._tile_texture(theme)

			var mat := StandardMaterial3D.new()
			if tex != null:
				mat.albedo_texture = tex
				if role == "blocked":
					mat.albedo_color = Color(0.62, 0.62, 0.70)
			else:
				mat.albedo_color = _helper._tile_color(role, theme)

			var floor_mesh := PlaneMesh.new()
			floor_mesh.size = Vector2(_CELL, _CELL)
			var floor_inst := MeshInstance3D.new()
			floor_inst.mesh = floor_mesh
			floor_inst.material_override = mat
			floor_inst.position = Vector3(cx * _CELL, 0, cy * _CELL)
			root.add_child(floor_inst)

			if role == "blocked":
				var wall_mesh := BoxMesh.new()
				wall_mesh.size = Vector3(_CELL * 0.92, _WALL_H, _CELL * 0.92)
				var wall_inst := MeshInstance3D.new()
				wall_inst.mesh = wall_mesh
				wall_inst.material_override = mat
				wall_inst.position = Vector3(cx * _CELL, _WALL_H / 2.0, cy * _CELL)
				root.add_child(wall_inst)

	_labels.clear()
	for k in inter:
		var it = inter[k]
		var cell = it["position"]["cell"]
		var cx := int(cell["x"])
		var cy := int(cell["y"])
		var atype := String(it["action"].get("type", ""))
		var m := _CELL * 0.85

		# An examine/use hotspot is a physical object standing in the world — its generated
		# prop MESH is the marker when one exists (a flat sprite next to real geometry reads
		# as a bug); anything flat falls through to the billboard icon.
		var drew := false
		if atype == "examine" or atype == "use":
			var pslug: String = _helper._slug(String(it.get("label", "")))
			var ppath := "images/prop_%s.glb" % pslug
			if pslug != "" and FileAccess.file_exists("res://" + ppath):
				var pnode := _load_glb(ppath, _CELL, g._texture_file("prop_%s.png" % pslug))
				if pnode != null:
					pnode.position = Vector3(cx * _CELL, 0.0, cy * _CELL)
					root.add_child(pnode)
					drew = true
		if not drew:
			var icon = _helper._interactable_icon(it)
			var spr := Sprite3D.new()
			spr.billboard = BaseMaterial3D.BILLBOARD_FIXED_Y
			spr.shaded = false
			if icon != null:
				spr.texture = icon
				spr.pixel_size = m / icon.get_height()
			else:
				# no art: a small full-billboard chip, not a ground-skewed square
				var col: Color = Overworld._MARKERS.get(atype, Color(0.85, 0.85, 0.85))
				spr.billboard = BaseMaterial3D.BILLBOARD_ENABLED
				spr.texture = _solid_texture(col)
				spr.pixel_size = (m * 0.35) / 8.0
			spr.position = Vector3(cx * _CELL, m / 2.0, cy * _CELL)
			root.add_child(spr)

		var lbl := Label3D.new()
		lbl.text = it.get("label", "")
		lbl.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		lbl.font_size = 28
		lbl.outline_size = 8
		lbl.pixel_size = 0.003
		lbl.position = Vector3(cx * _CELL, m + 0.35, cy * _CELL)
		lbl.visible = false  # shown only when the avatar is near (see _refresh_labels)
		root.add_child(lbl)
		_labels.append({"x": cx, "y": cy, "node": lbl})

	var cam := Camera3D.new()
	cam.fov = 30
	root.add_child(cam)
	cam.make_current()
	_cam = cam

	return root


# Feature objects: a generated .glb is REAL geometry standing on the footprint (the HD-2D
# payoff — a building you walk around, not a cardboard cutout); when only the sprite exists it
# falls back to a fixed-Y billboard (still 3D-placed, just flat). The glb is loaded from
# res://images/feature_<slug>.glb, centered on the footprint rect and scaled to span it, sitting
# on the ground plane.
func _draw_features(footprints) -> void:
	if typeof(footprints) != TYPE_DICTIONARY:
		return
	for fid in footprints:
		var fp = footprints[fid]
		var slug: String = _helper._slug(String(fp.get("label", "")))
		var fx := (float(fp.get("x", 0)) + float(fp.get("w", 1)) / 2.0 - 0.5) * _CELL
		var fz := (float(fp.get("y", 0)) + float(fp.get("h", 1)) / 2.0 - 0.5) * _CELL
		var span: float = max(float(fp.get("w", 1)), float(fp.get("h", 1))) * _CELL
		var glb_path := "images/feature_%s.glb" % slug
		if FileAccess.file_exists("res://" + glb_path):
			var node := _load_glb(glb_path, span, g._texture_file("feature_%s.png" % slug))
			if node != null:
				node.position = Vector3(fx, 0.0, fz)
				_root.add_child(node)
				continue
		var tex = g._texture_file("feature_%s.png" % slug)
		if tex != null:
			var spr := Sprite3D.new()
			spr.billboard = BaseMaterial3D.BILLBOARD_FIXED_Y
			spr.shaded = false
			spr.texture = tex
			spr.pixel_size = (span * 1.1) / tex.get_height()
			spr.position = Vector3(fx, span * 0.55, fz)
			_root.add_child(spr)


# Load a .glb, drop it on the ground, scale its longest horizontal side to `span`, and give the
# untextured shape a warm stone material so its form reads under the scene light.
func _load_glb(path: String, span: float, tex = null) -> Node3D:
	var doc := GLTFDocument.new()
	var st := GLTFState.new()
	if doc.append_from_buffer(FileAccess.get_file_as_bytes("res://" + path), "", st) != OK:
		return null
	var scene := doc.generate_scene(st)
	if scene == null:
		return null
	var aabb := _mesh_aabb(scene)
	if aabb.size == Vector3.ZERO:
		return null
	# A TRELLIS mesh already carries its own baked PBR texture — keep it, project nothing. Only
	# the untextured Hunyuan/shape-only path needs the sprite projected on for colour.
	if _has_baked_texture(scene):
		return _seat_glb(scene, aabb, span)
	# Shape-only meshes are untextured — project the SOURCE SPRITE onto the mesh as a triplanar
	# texture (local space, one repeat across the mesh bounds) so the generated art becomes the
	# colour. Crude (the front reads best, sides smear) but fully local, no paint model. No sprite
	# -> a warm stone fallback so the form still reads.
	var mat := StandardMaterial3D.new()
	mat.roughness = 0.85
	if tex != null:
		mat.albedo_texture = tex
		mat.uv1_triplanar = true
		mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR
		# the sprite's transparent regions carry near-white RGB; scissor on their alpha so the
		# mesh's auto-generated base slab (which samples them) punches through to the ground
		# instead of showing as a white platform
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
		mat.alpha_scissor_threshold = 0.5
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		var m: float = max(aabb.size.x, max(aabb.size.y, aabb.size.z))
		if m > 0.0:
			mat.uv1_scale = Vector3(1.0 / m, 1.0 / m, 1.0 / m)
		mat.uv1_offset = Vector3(0.5, 0.5, 0.5)
	else:
		mat.albedo_color = Color(0.80, 0.76, 0.68)
	_apply_material(scene, mat)
	return _seat_glb(scene, aabb, span)


# Scale the mesh to sit WITHIN its footprint (0.8 of its span wide) and seat its base on the
# ground, centered. Height is capped so nothing towers over the low following camera and fills the
# screen: at most ~1.15 footprint-spans tall AND a hard 3.0-unit ceiling (the avatar is ~0.9, so a
# building tops out around 3x human height — reads as a building, not a skyscraper next to you).
const _MAX_FEATURE_H := 3.0
func _seat_glb(scene: Node, aabb: AABB, span: float) -> Node3D:
	var wrap := Node3D.new()
	var footprint_side: float = max(aabb.size.x, aabb.size.z)
	var s: float = span * 0.8 / footprint_side if footprint_side > 0.0 else 1.0
	var max_h: float = min(span * 1.15, _MAX_FEATURE_H)
	if aabb.size.y * s > max_h and aabb.size.y > 0.0:
		s = max_h / aabb.size.y
	scene.scale = Vector3(s, s, s)
	scene.position = Vector3(-aabb.get_center().x * s, -aabb.position.y * s,
		-aabb.get_center().z * s)
	wrap.add_child(scene)
	return wrap


# Does the loaded glb carry its own albedo texture (a TRELLIS PBR mesh) vs bare geometry
# (Hunyuan shape-only)? Walk the mesh surfaces' materials for a base-colour texture.
func _has_baked_texture(n: Node) -> bool:
	if n is MeshInstance3D:
		var mi := n as MeshInstance3D
		var mesh := mi.mesh
		if mesh != null:
			for i in mesh.get_surface_count():
				var m = mi.get_active_material(i)
				if m is BaseMaterial3D and (m as BaseMaterial3D).albedo_texture != null:
					return true
				if m is StandardMaterial3D and (m as StandardMaterial3D).albedo_texture != null:
					return true
	for c in n.get_children():
		if _has_baked_texture(c):
			return true
	return false


func _mesh_aabb(n: Node) -> AABB:
	var a := AABB()
	var first := true
	for c in n.get_children():
		if c is MeshInstance3D:
			var m: AABB = (c as MeshInstance3D).get_aabb()
			if first:
				a = m
				first = false
			else:
				a = a.merge(m)
		var sub := _mesh_aabb(c)
		if sub.size != Vector3.ZERO:
			if first:
				a = sub
				first = false
			else:
				a = a.merge(sub)
	return a


func _apply_material(n: Node, mat: StandardMaterial3D) -> void:
	if n is MeshInstance3D:
		(n as MeshInstance3D).material_override = mat
	for c in n.get_children():
		_apply_material(c, mat)


func _make_avatar() -> Sprite3D:
	var spr := Sprite3D.new()
	spr.billboard = BaseMaterial3D.BILLBOARD_FIXED_Y
	spr.shaded = false
	var tex = _helper._avatar_texture()
	var h := _CELL * 0.9
	if tex != null:
		spr.texture = tex
		spr.pixel_size = h / tex.get_height()
	else:
		spr.texture = _solid_texture(Color(0.95, 0.85, 0.2))
		spr.pixel_size = h / 8.0
	return spr


func _solid_texture(color: Color) -> ImageTexture:
	var img := Image.create(8, 8, false, Image.FORMAT_RGBA8)
	img.fill(color)
	return ImageTexture.create_from_image(img)


func _update_camera(delta: float) -> void:
	var target: Vector3 = _avatar.position + Vector3(0, 5.0, 4.0)
	var w: float = clamp(delta * 6.0, 0.0, 1.0)
	_cam.position = _cam.position.lerp(target, w)
	_cam.look_at(_avatar.position, Vector3.UP)


# Labels only near the avatar (chebyshev <= _LABEL_RANGE) — a dense map stays readable.
func _refresh_labels(ax: int, ay: int) -> void:
	for e in _labels:
		e["node"].visible = max(abs(int(e["x"]) - ax), abs(int(e["y"]) - ay)) <= _LABEL_RANGE
