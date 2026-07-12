# HD-2D (Octopath-style) overworld presenter — same place-loop/verb contract as overworld.gd,
# rendering the IDENTICAL game.json (tiles/legend/interactables) as a real 3D scene instead of
# flat 2D rects. Proves the IR is presentation-neutral: zero IR changes, only a different
# renderer wired in by Game._run_world when meta.presentation == "hd2d". Reuses overworld.gd's
# pure tile/texture/avatar helpers via composition (an Overworld instance built only for its
# helper methods — _init has no side effects beyond storing `g`) instead of duplicating them.
#
# Terrain: place.elevation (rows of 0..1 floats, same dims as tiles.rows) + place.sea_level are an
# OPTIONAL data-presence contract — absent (old/2d places) renders the ground exactly as flat
# PlaneMesh-per-cell, unchanged. Present, it drives a single heightfield ArrayMesh instead: corner
# height = average of the up to 4 adjacent cells' elevation (a water cell's contribution clamped
# up to sea_level so the dry-land floor under the water plane never pits), scaled by
# _HEIGHT_SCALE. Every entity Y (avatar/tokens/markers/meshes/icons) reads off the SAME terrain
# sampler so nothing floats or sinks into the new relief.
extends RefCounted

const Overworld = preload("res://overworld.gd")

const _CELL := 1.0
const _WALL_H := 0.6
const _SLIDE_SECS := 0.12
const _LABEL_RANGE := 2
const _HINT := "WASD / Arrows: move    E: interact"
const _AVATAR_Y := _CELL * 0.45

# Heights are SEA-RELATIVE (see _effective_elev): land renders as (elevation - sea_level) *
# this scale, so relief is spent on the range the player actually sees — absolute scaling
# wasted most of it below the waterline and land barely rose above the sea plane.
const _HEIGHT_SCALE := 8.0
# Seafloor depth floor (pre-scale): water keeps a little visible depth under the translucent
# plane without opening scaled-up pits at the coast.
const _SEA_FLOOR := -0.06

# Fixed neighbour scan order for the border-fringe theme lookup (mirrors overworld._TRANS_DIRS).

const _WATER_COLOR := Color(0.28, 0.50, 0.78, 0.62)

var g  # Game driver
var _helper  # Overworld instance, used only for its pure tile/texture helper methods
var _labels := []  # {x, y, node} per interactable Label3D — visibility follows the avatar
var _root: Node3D
var _cam: Camera3D
# Camera pull-back scaled from the place's declared m_per_cell (2 = town scale, 20+ = world
# map): a fixed chase offset that reads right in a 2 m/cell town feels zoomed-in and HUGE on a
# 20 m/cell overworld.
var _cam_zoom := 1.0
# TRELLIS meshes face +Z when the source sprite's front faces the viewer; flip to PI if a
# batch lands back-to-front.
const _MESH_FRONT_YAW := 0.0
var _avatar: Sprite3D

# Per-place terrain state, set by _load_elevation each run_place. _has_elev false = every
# _terrain_y query returns 0.0, so the flat contract holds by construction, not by branching.
var _elev := []
var _has_elev := false
var _sea_level := 0.0
var _gw := 0
var _gh := 0
var _corner_cache := {}


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

	_load_elevation(place, gw, gh)
	var mpc := float(place.get("m_per_cell", 2.0))
	_cam_zoom = clampf(sqrt(mpc / 2.0), 1.25, 3.2)

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
	_avatar.position = Vector3(ax * _CELL, _terrain_y(ax * _CELL, ay * _CELL) + _AVATAR_Y, ay * _CELL)
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
			_avatar.position = slide_from.lerp(slide_to, slide_t)
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
					slide_from = Vector3(ax * _CELL, _terrain_y(ax * _CELL, ay * _CELL) + _AVATAR_Y, ay * _CELL)
					ax = nx
					ay = ny
					slide_to = Vector3(ax * _CELL, _terrain_y(ax * _CELL, ay * _CELL) + _AVATAR_Y, ay * _CELL)
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


# ── terrain sampling ─────────────────────────────────────────────────────────────────────────
# Validates place.elevation is present, well-formed (same dims as the tile grid) and paired with
# a numeric sea_level; anything short of that leaves _has_elev false so every _terrain_y query
# below returns 0.0 — the flat contract is enforced by this one gate, not scattered branches.
func _load_elevation(place, gw: int, gh: int) -> void:
	_has_elev = false
	_elev = []
	_sea_level = 0.0
	_gw = gw
	_gh = gh
	_corner_cache = {}

	var elev = place.get("elevation")
	var sea = place.get("sea_level")
	var sea_ok := typeof(sea) == TYPE_FLOAT or typeof(sea) == TYPE_INT
	if not sea_ok or typeof(elev) != TYPE_ARRAY or elev.size() != gh:
		return
	for r in elev:
		if typeof(r) != TYPE_ARRAY or r.size() != gw:
			return

	_elev = elev
	_sea_level = float(sea)
	_has_elev = true


func _is_water(cx: int, cy: int) -> bool:
	if not _has_elev or cx < 0 or cx >= _gw or cy < 0 or cy >= _gh:
		return false
	return float(_elev[cy][cx]) < _sea_level


# Raw 0..1 elevation a cell contributes to its corners: a water cell is clamped UP to sea_level so
# the dry floor never dips into a pit under the translucent water plane.
func _effective_elev(cx: int, cy: int) -> float:
	return maxf(float(_elev[cy][cx]) - _sea_level, _SEA_FLOOR)


# Corner (lattice point) elevation at (ix, iy), ix in [0, gw], iy in [0, gh]: the average of the
# up to 4 adjacent cells that exist. Memoized — up to 4 cells share each corner.
func _corner_elev(ix: int, iy: int) -> float:
	var key := "%d,%d" % [ix, iy]
	if _corner_cache.has(key):
		return _corner_cache[key]
	var total := 0.0
	var n := 0
	for cy in [iy - 1, iy]:
		for cx in [ix - 1, ix]:
			if cx >= 0 and cx < _gw and cy >= 0 and cy < _gh:
				total += _effective_elev(cx, cy)
				n += 1
	var v := total / n if n > 0 else 0.0
	_corner_cache[key] = v
	return v


# Bilinear elevation sample at continuous lattice coords (fx, fy) — fx/fy integer hits an exact
# corner (used for mesh vertices), fractional interpolates (used for entity placement). Returns
# world-scale height (already * _HEIGHT_SCALE).
func _lattice_y(fx: float, fy: float) -> float:
	if not _has_elev:
		return 0.0
	var ix := clampi(int(floor(fx)), 0, _gw)
	var iy := clampi(int(floor(fy)), 0, _gh)
	var tx := clampf(fx - floor(fx), 0.0, 1.0)
	var ty := clampf(fy - floor(fy), 0.0, 1.0)
	var ix1 := clampi(ix + 1, 0, _gw)
	var iy1 := clampi(iy + 1, 0, _gh)
	var h00 := _corner_elev(ix, iy)
	var h10 := _corner_elev(ix1, iy)
	var h01 := _corner_elev(ix, iy1)
	var h11 := _corner_elev(ix1, iy1)
	var h := lerpf(lerpf(h00, h10, tx), lerpf(h01, h11, tx), ty)
	return h * _HEIGHT_SCALE


# World-space terrain Y at world (wx, wz) — the ONE call site every entity/mesh placement below
# uses, so a place with no elevation data places everything at y=0 exactly as before.
func _terrain_y(wx: float, wz: float) -> float:
	return _lattice_y(wx / _CELL + 0.5, wz / _CELL + 0.5)


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


# Nearest open, non-covered theme within a small ring — the local ground an object sits on.
func _nearby_open_theme(cx: int, cy: int, rows, legend, gw: int, gh: int, covered,
		fallback: String) -> String:
	for radius in [1, 2, 3]:
		for dy in range(-radius, radius + 1):
			for dx in range(-radius, radius + 1):
				if maxi(absi(dx), absi(dy)) != radius:
					continue
				var nx := cx + dx
				var ny := cy + dy
				if nx < 0 or ny < 0 or nx >= gw or ny >= gh:
					continue
				if covered.has(_helper._key(nx, ny)):
					continue
				var row := String(rows[ny]) if ny < rows.size() else ""
				if nx >= row.length():
					continue
				var spec = _helper._spec_of(row[nx], legend)
				if String(spec.get("role", "open")) == "open":
					return String(spec.get("theme", fallback))
	return fallback


func _covered_cells(footprints) -> Dictionary:
	var out := {}
	if typeof(footprints) != TYPE_DICTIONARY:
		return out
	for fid in footprints:
		var fp = footprints[fid]
		var slug: String = _helper._slug(String(fp.get("label", "")))
		if not (fp.get("door") != null
				or FileAccess.file_exists("res://images/feature_%s.glb" % slug)
				or g._texture_file("feature_%s.png" % slug) != null):
			continue
		for dy in int(fp.get("h", 1)):
			for dx in int(fp.get("w", 1)):
				out[_helper._key(int(fp.get("x", 0)) + dx, int(fp.get("y", 0)) + dy)] = true
	return out


# Splat terrain: ONE heightfield surface whose fragment shader blends the per-cell theme
# textures with noise-perturbed borders and draws the water line where INTERPOLATED elevation
# crosses sea level — sub-cell coastlines, no per-cell texture seams, no tile-shaped ground.
# The walk grid is untouched; only projection changed. Wobble is kept under half a cell so the
# visual border never strays far from the walkable one.
const _SPLAT_MAX_THEMES := 8
const _SPLAT_SHADER := """
shader_type spatial;
uniform sampler2D control : filter_nearest;
uniform sampler2D elev_tex : filter_linear;
uniform sampler2D tex0 : filter_linear, repeat_enable;
uniform sampler2D tex1 : filter_linear, repeat_enable;
uniform sampler2D tex2 : filter_linear, repeat_enable;
uniform sampler2D tex3 : filter_linear, repeat_enable;
uniform sampler2D tex4 : filter_linear, repeat_enable;
uniform sampler2D tex5 : filter_linear, repeat_enable;
uniform sampler2D tex6 : filter_linear, repeat_enable;
uniform sampler2D tex7 : filter_linear, repeat_enable;
uniform sampler2D water_tex : filter_linear, repeat_enable;
uniform float sea_level;
uniform vec2 grid_size;
uniform float cell_size;
uniform float wobble = 0.3;

float hash2(vec2 p) {
	return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}
float vnoise(vec2 p) {
	vec2 i = floor(p);
	vec2 f = fract(p);
	vec2 u = f * f * (3.0 - 2.0 * f);
	return mix(mix(hash2(i), hash2(i + vec2(1, 0)), u.x),
	           mix(hash2(i + vec2(0, 1)), hash2(i + vec2(1, 1)), u.x), u.y);
}

varying vec2 wpos;
void vertex() {
	wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xz / cell_size;
}

vec3 theme_color(float idx, vec2 uv) {
	if (idx < 0.5) { return texture(tex0, uv).rgb; }
	else if (idx < 1.5) { return texture(tex1, uv).rgb; }
	else if (idx < 2.5) { return texture(tex2, uv).rgb; }
	else if (idx < 3.5) { return texture(tex3, uv).rgb; }
	else if (idx < 4.5) { return texture(tex4, uv).rgb; }
	else if (idx < 5.5) { return texture(tex5, uv).rgb; }
	else if (idx < 6.5) { return texture(tex6, uv).rgb; }
	return texture(tex7, uv).rgb;
}

void fragment() {
	vec2 cell_uv = wpos + vec2(0.5);
	vec2 wob = vec2(vnoise(wpos * 2.3), vnoise(wpos * 2.3 + 17.0)) - 0.5;
	vec2 look = (cell_uv + wob * wobble * 2.0) / grid_size;
	float idx = texture(control, clamp(look, vec2(0.001), vec2(0.999))).r * 255.0;
	vec2 tuv = wpos * 0.33;
	vec3 col = theme_color(idx, tuv);

	float e = texture(elev_tex, clamp(cell_uv / grid_size, vec2(0.001), vec2(0.999))).r;
	float shore = e - sea_level;
	if (shore < 0.0) {
		float depth = clamp(-shore * 14.0, 0.0, 1.0);
		vec3 wcol = texture(water_tex, tuv).rgb;
		col = mix(col * 0.85, wcol * mix(1.0, 0.55, depth), clamp(-shore * 40.0, 0.25, 1.0));
	} else if (shore < 0.012) {
		col *= 0.88;
	}
	ALBEDO = col;
}
"""


# Analytic smooth normal at lattice corner (ix, iy) from the height field's central
# difference — generate_normals() would shade each triangle flat.
func _corner_normal(ix: int, iy: int) -> Vector3:
	var hl := _lattice_y(ix - 1, iy)
	var hr := _lattice_y(ix + 1, iy)
	var hu := _lattice_y(ix, iy - 1)
	var hd := _lattice_y(ix, iy + 1)
	return Vector3(-(hr - hl) / (2.0 * _CELL), 1.0, -(hd - hu) / (2.0 * _CELL)).normalized()


func _theme_texture_or_solid(theme: String) -> Texture2D:
	var tex = _helper._tile_texture(theme)
	if tex != null:
		return tex
	return _solid_texture(_helper._tile_color("open", theme))


func _build_splat_terrain(cell_theme: Array, gw: int, gh: int) -> MeshInstance3D:
	# index the themes by cell count; a place with more than _SPLAT_MAX_THEMES themes maps the
	# long tail onto the most common one (never happens with worldgen's palettes)
	var counts := {}
	for cy in gh:
		for cx in gw:
			var t := String(cell_theme[cy][cx])
			counts[t] = int(counts.get(t, 0)) + 1
	var order := counts.keys()
	order.sort_custom(func(a, b): return counts[a] > counts[b])
	var index := {}
	for i in order.size():
		index[order[i]] = mini(i, _SPLAT_MAX_THEMES - 1)

	var ctl := Image.create(gw, gh, false, Image.FORMAT_R8)
	var ele := Image.create(gw, gh, false, Image.FORMAT_RF)
	var water_counts := {}
	for cy in gh:
		for cx in gw:
			ctl.set_pixel(cx, cy, Color(float(index[String(cell_theme[cy][cx])]) / 255.0, 0, 0))
			ele.set_pixel(cx, cy, Color(float(_elev[cy][cx]), 0, 0))
			if _is_water(cx, cy):
				var t := String(cell_theme[cy][cx])
				water_counts[t] = int(water_counts.get(t, 0)) + 1
	var water_theme := ""
	var best_n := -1
	for t in water_counts:
		if water_counts[t] > best_n:
			best_n = water_counts[t]
			water_theme = t

	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for cy in gh:
		for cx in gw:
			var x0 := (cx - 0.5) * _CELL
			var x1 := (cx + 0.5) * _CELL
			var z0 := (cy - 0.5) * _CELL
			var z1 := (cy + 0.5) * _CELL
			var tl := Vector3(x0, _lattice_y(cx, cy), z0)
			var tr := Vector3(x1, _lattice_y(cx + 1, cy), z0)
			var bl := Vector3(x0, _lattice_y(cx, cy + 1), z1)
			var br := Vector3(x1, _lattice_y(cx + 1, cy + 1), z1)
			# Godot front faces wind CLOCKWISE from above; counterclockwise = culled.
			st.set_normal(_corner_normal(cx, cy)); st.add_vertex(tl)
			st.set_normal(_corner_normal(cx + 1, cy)); st.add_vertex(tr)
			st.set_normal(_corner_normal(cx, cy + 1)); st.add_vertex(bl)
			st.set_normal(_corner_normal(cx + 1, cy)); st.add_vertex(tr)
			st.set_normal(_corner_normal(cx + 1, cy + 1)); st.add_vertex(br)
			st.set_normal(_corner_normal(cx, cy + 1)); st.add_vertex(bl)
	st.index()
	var mesh := ArrayMesh.new()
	st.commit(mesh)

	var sh := Shader.new()
	sh.code = _SPLAT_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = sh
	mat.set_shader_parameter("control", ImageTexture.create_from_image(ctl))
	mat.set_shader_parameter("elev_tex", ImageTexture.create_from_image(ele))
	for i in _SPLAT_MAX_THEMES:
		var theme: String = order[i] if i < order.size() else (order[0] if order.size() > 0 else "")
		mat.set_shader_parameter("tex%d" % i, _theme_texture_or_solid(theme))
	if water_theme == "":
		mat.set_shader_parameter("water_tex", _solid_texture(_WATER_COLOR))
	else:
		mat.set_shader_parameter("water_tex", _theme_texture_or_solid(water_theme))
	mat.set_shader_parameter("sea_level", _sea_level)
	mat.set_shader_parameter("grid_size", Vector2(gw, gh))
	mat.set_shader_parameter("cell_size", _CELL)

	var inst := MeshInstance3D.new()
	inst.mesh = mesh
	inst.set_surface_override_material(0, mat)
	return inst


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
	var cell_theme := []
	for _cy in gh:
		var line := []
		line.resize(gw)
		cell_theme.append(line)

	for cy in gh:
		var row := String(rows[cy]) if cy < rows.size() else ""
		for cx in gw:
			var ch := row.substr(cx, 1) if cx < row.length() else "."
			var spec = _helper._spec_of(ch, legend)
			var role := String(spec.get("role", "open"))
			var theme := String(spec.get("theme", ""))
			if role == "blocked" and covered.has(_helper._key(cx, cy)):
				# the mesh IS the object: its floor is the surrounding ground, not the object's
				# own blocked theme grey-tinted under it (reads as a mismatched plinth). Use the
				# NEAREST adjacent open theme — a well on a plaza sits on plaza stone, not on
				# the map's dominant sand.
				role = "open"
				theme = _nearby_open_theme(cx, cy, rows, legend, gw, gh, covered, open_theme)
			cell_theme[cy][cx] = theme

			if _has_elev:
				pass  # the splat terrain below is the whole ground; no per-cell geometry
			else:
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
				# On a heightfield the TERRAIN communicates blocked: water dips under the sea
				# plane, land rises. Boxes are for flat maps (towns/interiors), where a wall
				# needs volume — on terrain they turn every coastline into a wall of cubes.
				if _has_elev:
					continue
				var wall_mat := StandardMaterial3D.new()
				var wtex = _helper._tile_texture(theme)
				if wtex != null:
					wall_mat.albedo_texture = wtex
					wall_mat.albedo_color = Color(0.62, 0.62, 0.70)
				else:
					wall_mat.albedo_color = _helper._tile_color(role, theme)
				var wall_mesh := BoxMesh.new()
				wall_mesh.size = Vector3(_CELL * 0.92, _WALL_H, _CELL * 0.92)
				var wall_inst := MeshInstance3D.new()
				wall_inst.mesh = wall_mesh
				wall_inst.material_override = wall_mat
				var base_y := _terrain_y(cx * _CELL, cy * _CELL)
				wall_inst.position = Vector3(cx * _CELL, base_y + _WALL_H / 2.0, cy * _CELL)
				root.add_child(wall_inst)

	if _has_elev:
		root.add_child(_build_splat_terrain(cell_theme, gw, gh))

	_labels.clear()
	for k in inter:
		var it = inter[k]
		var cell = it["position"]["cell"]
		var cx := int(cell["x"])
		var cy := int(cell["y"])
		var atype := String(it["action"].get("type", ""))
		var m := _CELL * 0.85
		var gy := _terrain_y(cx * _CELL, cy * _CELL)

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
					pnode.position = Vector3(cx * _CELL, gy, cy * _CELL)
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
			spr.position = Vector3(cx * _CELL, gy + m / 2.0, cy * _CELL)
			root.add_child(spr)

		var lbl := Label3D.new()
		lbl.text = it.get("label", "")
		lbl.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		lbl.font_size = 28
		lbl.outline_size = 8
		lbl.pixel_size = 0.003
		lbl.position = Vector3(cx * _CELL, gy + m + 0.35, cy * _CELL)
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
# on the ground plane (terrain-sampled — a building on a slope sits at its footprint's height).
func _draw_features(footprints) -> void:
	if typeof(footprints) != TYPE_DICTIONARY:
		return
	for fid in footprints:
		var fp = footprints[fid]
		var slug: String = _helper._slug(String(fp.get("label", "")))
		var fx := (float(fp.get("x", 0)) + float(fp.get("w", 1)) / 2.0 - 0.5) * _CELL
		var fz := (float(fp.get("y", 0)) + float(fp.get("h", 1)) / 2.0 - 0.5) * _CELL
		var span: float = max(float(fp.get("w", 1)), float(fp.get("h", 1))) * _CELL
		var gy := _terrain_y(fx, fz)
		# Tier 1: the registry-resolved hand-made shell — the compile annotated the footprint
		# with the chosen file (see godot/building_registry.py). Same door-facing yaw as any mesh.
		var shell_file = fp.get("shell_file")
		if shell_file != null and FileAccess.file_exists("res://images/" + String(shell_file)):
			var shell := _load_glb("images/" + String(shell_file), span, null)
			if shell != null:
				shell.position = Vector3(fx, gy, fz)
				_face_door(shell, fp, fx, fz)
				_root.add_child(shell)
				continue
		var glb_path := "images/feature_%s.glb" % slug
		if FileAccess.file_exists("res://" + glb_path):
			var node := _load_glb(glb_path, span, g._texture_file("feature_%s.png" % slug))
			if node != null:
				node.position = Vector3(fx, gy, fz)
				_face_door(node, fp, fx, fz)
				_root.add_child(node)
				continue
		# Tier 3: a doored footprint with no mesh gets a parametric BLOCKOUT building — box
		# walls + pitched roof + door slab on the doorstep face, textured from the shipped
		# library classes. Reads as "a 2x3 building with a door" by construction.
		if fp.get("door") != null:
			var blk := _blockout_building(fp)
			if blk != null:
				blk.position = Vector3(fx, gy, fz)
				_root.add_child(blk)
				continue
		var tex = g._texture_file("feature_%s.png" % slug)
		if tex != null:
			var spr := Sprite3D.new()
			spr.billboard = BaseMaterial3D.BILLBOARD_FIXED_Y
			spr.shaded = false
			spr.texture = tex
			spr.pixel_size = (span * 1.1) / tex.get_height()
			spr.position = Vector3(fx, gy + span * 0.55, fz)
			_root.add_child(spr)


# A TRELLIS mesh's front is the source sprite's view direction, and the sprite is authored
# with the entrance on its front (hand-made shells follow the same convention) — yaw the front
# toward the doorstep cell so the door visually sits where the enter-hotspot actually is.
func _face_door(node: Node3D, fp, fx: float, fz: float) -> void:
	var door = fp.get("door")
	if door == null or door.size() != 2:
		return
	var dx := float(door[0]) * _CELL - fx
	var dz := float(door[1]) * _CELL - fz
	if absf(dx) > 0.001 or absf(dz) > 0.001:
		node.rotation.y = atan2(dx, dz) + _MESH_FRONT_YAW


func _lib_material(cls: String, fallback: Color) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	var tex = g._texture_file("lib_%s.png" % cls)
	if tex != null:
		mat.albedo_texture = tex
		mat.uv1_triplanar = true
		mat.uv1_scale = Vector3(0.6, 0.6, 0.6)
	else:
		mat.albedo_color = fallback
	mat.roughness = 0.9
	return mat


# Parametric blockout: box walls sized to the footprint, a pitched PrismMesh roof with its
# ridge along the long axis, and a dark door slab centered on the face toward the doorstep
# cell. No per-label art needed — geometry IS the semantics ("a 2x3 building, door south").
func _blockout_building(fp) -> Node3D:
	var w := float(fp.get("w", 1)) * _CELL
	var d := float(fp.get("h", 1)) * _CELL
	var wall_h: float = clampf(minf(w, d) * 0.8, 0.8, 2.2)
	var roof_h: float = clampf(minf(w, d) * 0.45, 0.5, 1.4)

	var root := Node3D.new()

	var walls := MeshInstance3D.new()
	var wall_mesh := BoxMesh.new()
	wall_mesh.size = Vector3(w * 0.96, wall_h, d * 0.96)
	walls.mesh = wall_mesh
	walls.material_override = _lib_material("wall", Color(0.72, 0.68, 0.62))
	walls.position = Vector3(0, wall_h / 2.0, 0)
	root.add_child(walls)

	var roof := MeshInstance3D.new()
	var roof_mesh := PrismMesh.new()
	# PrismMesh ridge runs along X; size.z is the roof depth. Put the ridge on the long axis.
	var along_x := w >= d
	roof_mesh.size = Vector3(maxf(w, d) * 1.06, roof_h, minf(w, d) * 1.06)
	roof.mesh = roof_mesh
	roof.material_override = _lib_material("roof", Color(0.62, 0.36, 0.28))
	roof.position = Vector3(0, wall_h + roof_h / 2.0, 0)
	if not along_x:
		roof.rotation.y = PI / 2.0
	root.add_child(roof)

	var door = fp.get("door")
	if door != null and door.size() == 2:
		var fx := (float(fp.get("x", 0)) + float(fp.get("w", 1)) / 2.0 - 0.5) * _CELL
		var fz := (float(fp.get("y", 0)) + float(fp.get("h", 1)) / 2.0 - 0.5) * _CELL
		var dx := float(door[0]) * _CELL - fx
		var dz := float(door[1]) * _CELL - fz
		var slab := MeshInstance3D.new()
		var slab_mesh := BoxMesh.new()
		var door_w: float = clampf(_CELL * 0.6, 0.3, 0.9)
		var door_h: float = minf(wall_h * 0.8, 1.1)
		slab_mesh.size = Vector3(door_w, door_h, 0.06)
		slab.mesh = slab_mesh
		var dmat := StandardMaterial3D.new()
		dmat.albedo_color = Color(0.28, 0.20, 0.14)
		slab.material_override = dmat
		# stick the slab just proud of whichever wall faces the doorstep
		if absf(dx) > absf(dz):
			slab.position = Vector3(signf(dx) * (w * 0.48 + 0.04), door_h / 2.0, 0)
			slab.rotation.y = PI / 2.0
		else:
			slab.position = Vector3(0, door_h / 2.0, signf(dz) * (d * 0.48 + 0.04))
		root.add_child(slab)

	return root


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
	var target: Vector3 = _avatar.position + Vector3(0, 6.0, 4.8) * _cam_zoom
	var w: float = clamp(delta * 6.0, 0.0, 1.0)
	_cam.position = _cam.position.lerp(target, w)
	_cam.look_at(_avatar.position, Vector3.UP)


# Labels only near the avatar (chebyshev <= _LABEL_RANGE) — a dense map stays readable.
func _refresh_labels(ax: int, ay: int) -> void:
	for e in _labels:
		e["node"].visible = max(abs(int(e["x"]) - ax), abs(int(e["y"]) - ay)) <= _LABEL_RANGE
