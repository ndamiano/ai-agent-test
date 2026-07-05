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

	_root = _build_scene(rows, legend, gw, gh, inter)

	var ax := 0
	var ay := 0
	if spawn != null and spawn.has("cell"):
		ax = int(spawn["cell"]["x"])
		ay = int(spawn["cell"]["y"])

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
func _build_scene(rows, legend, gw, gh, inter) -> Node3D:
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

	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var spec = _helper._spec_of(ch, legend)
			var role := String(spec.get("role", "open"))
			var theme := String(spec.get("theme", ""))
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
		var icon = _helper._interactable_icon(it)
		var m := _CELL * 0.85

		var spr := Sprite3D.new()
		spr.billboard = BaseMaterial3D.BILLBOARD_FIXED_Y
		spr.shaded = false
		if icon != null:
			spr.texture = icon
			spr.pixel_size = m / icon.get_height()
		else:
			var col: Color = Overworld._MARKERS.get(atype, Color(0.85, 0.85, 0.85))
			spr.texture = _solid_texture(col)
			spr.pixel_size = m / 8.0
		spr.position = Vector3(cx * _CELL, m / 2.0, cy * _CELL)
		root.add_child(spr)

		var lbl := Label3D.new()
		lbl.text = it.get("label", "")
		lbl.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		lbl.font_size = 40
		lbl.outline_size = 10
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
