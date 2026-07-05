# Game — the runtime driver. Loads game.json, indexes the IR, owns shared UI presenters, and
# dispatches to the vn/pnc/combat interpreters. Mirrors the boot + presenter layer of
# web/runtime/engine.js; the navigation/genre loops live in vn.gd / pnc.gd / overworld.gd /
# combat.gd, selected per place by the PRESENTERS registry below.
extends Control

const IRCore = preload("res://ir.gd")
const Vn = preload("res://vn.gd")
const Pnc = preload("res://pnc.gd")
const Overworld = preload("res://overworld.gd")
const Combat = preload("res://combat.gd")

# Presenter registry keyed by place.kind. Adding a navigation modality = a new presenter + one
# entry here; the world router below stays untouched. room => point-and-click, the RPG kinds =>
# the WASD overworld. The default keeps an unknown kind playable as PnC.
const PRESENTERS := {
	"room": Pnc,
	"world_map": Overworld,
	"town": Overworld,
	"interior": Overworld,
}

# Sentinels returned up the presenter -> world-router chain (a place id can never collide).
const WIN := "__win__"
const END := "__end__"

const SAVE_PATH := "user://save.json"

var ir: Dictionary = {}
var state: Dictionary = {}

var chars := {}
var node_by_id := {}
var place_by_id := {}
var bg_files := {}
var item_by_id := {}
var stat_by_id := {}
var ability_by_id := {}
var status_by_id := {}
var combatant_by_id := {}
var encounter_by_id := {}

# Save plumbing: _run_world tracks the live place; the overworld presenter mirrors the avatar's
# cell here on every step (null in a PnC room), so save_game never reaches into a presenter.
var avatar_cell = null
var _place_id = null
var _title_open := false
var _inv_last := []

signal advanced
signal menu_picked
var _await_advance := false
var _menu_pick := -1

@onready var _scene := ColorRect.new()
@onready var _sprites := Control.new()
@onready var _hotspots := Control.new()
@onready var _dialogue := PanelContainer.new()
@onready var _speaker := Label.new()
@onready var _text := Label.new()
@onready var _menu := VBoxContainer.new()
@onready var _inv := HBoxContainer.new()
@onready var _hud := Label.new()


func _ready() -> void:
	_setup_input()
	_build_ui()
	ir = _load_game()
	state = IRCore.make_state(ir)
	_index()
	await _boot()


# Bind movement/interact to physical keys (layout-independent) in code, so project.godot stays free
# of the brittle InputEvent serialization. WASD + arrows move; E / Space interact.
func _setup_input() -> void:
	var binds := {
		"move_up": [KEY_W, KEY_UP],
		"move_down": [KEY_S, KEY_DOWN],
		"move_left": [KEY_A, KEY_LEFT],
		"move_right": [KEY_D, KEY_RIGHT],
		"interact": [KEY_E, KEY_SPACE],
		"ui_pause": [KEY_ESCAPE],
	}
	for action in binds:
		# InputMap survives reload_current_scene (Play Again) — skip an already-bound action
		# instead of stacking duplicate events on it.
		if InputMap.has_action(action):
			continue
		InputMap.add_action(action)
		for kc in binds[action]:
			var ev := InputEventKey.new()
			ev.physical_keycode = kc
			InputMap.action_add_event(action, ev)


func _load_game() -> Dictionary:
	var f := FileAccess.open("res://game.json", FileAccess.READ)
	if f == null:
		return {}
	return JSON.parse_string(f.get_as_text())


func _index() -> void:
	for c in ir.get("characters", []):
		chars[c["id"]] = c
	for n in ir.get("nodes", []):
		node_by_id[n["id"]] = n
	for p in ir.get("places", []):
		place_by_id[p["id"]] = p
	for b in ir.get("backgrounds", []):
		bg_files[b["id"]] = b["image_file"]
	for i in ir.get("items", []):
		item_by_id[i["id"]] = i
	for s in ir.get("stats", []):
		stat_by_id[s["id"]] = s
	for a in ir.get("abilities", []):
		ability_by_id[a["id"]] = a
	for s in ir.get("statuses", []):
		status_by_id[s["id"]] = s
	for c in ir.get("combatants", []):
		combatant_by_id[c["id"]] = c
	for e in ir.get("encounters", []):
		encounter_by_id[e["id"]] = e


func _boot() -> void:
	if await _title_screen() == "continue":
		var data = _load_save()
		# user:// is shared across games (one project name), so a stale save may point at a
		# place this game doesn't have — fall through to a fresh start instead of crashing.
		if data != null and place_by_id.has(data.get("place")):
			state = data["state"]
			await _run_world(data["place"], data.get("spawn"))
			return
	var start = ir.get("start", {})
	if start.has("place"):
		await _run_world(start["place"], start.get("spawn"))
	elif start.has("node"):
		await Vn.new(self).play_node(start["node"])


# Boot chrome: title art (or the IR title on dark ground) + New Game / Continue / Quit.
# Returns "new" or "continue"; Quit exits the app from here.
func _title_screen() -> String:
	_title_open = true
	var layer := Control.new()
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(layer)
	var card = _texture_file("title_card.png")
	if card != null:
		var tr := TextureRect.new()
		tr.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
		tr.texture = card
		tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
		layer.add_child(tr)
	else:
		var ground := ColorRect.new()
		ground.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		ground.color = Color(0.06, 0.06, 0.10)
		ground.mouse_filter = Control.MOUSE_FILTER_IGNORE
		layer.add_child(ground)
		var tl := Label.new()
		tl.text = String(ir.get("meta", {}).get("title", "Untitled"))
		tl.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
		tl.position.y = 170
		tl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		tl.add_theme_font_size_override("font_size", 46)
		tl.mouse_filter = Control.MOUSE_FILTER_IGNORE
		layer.add_child(tl)
	var box := VBoxContainer.new()
	box.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	box.grow_horizontal = Control.GROW_DIRECTION_BOTH
	box.grow_vertical = Control.GROW_DIRECTION_BOTH
	box.custom_minimum_size = Vector2(280, 0)
	box.position.y += 140
	layer.add_child(box)
	var picked := {"v": ""}
	var nb := Button.new()
	nb.text = "New Game"
	nb.pressed.connect(func(): picked["v"] = "new")
	box.add_child(nb)
	if FileAccess.file_exists(SAVE_PATH):
		var cb := Button.new()
		cb.text = "Continue"
		cb.pressed.connect(func(): picked["v"] = "continue")
		box.add_child(cb)
	var qb := Button.new()
	qb.text = "Quit"
	qb.pressed.connect(func(): get_tree().quit())
	box.add_child(qb)
	while picked["v"] == "":
		await get_tree().process_frame
	layer.queue_free()
	_title_open = false
	return picked["v"]


# ── save / pause ─────────────────────────────────────────────────────────────────────────────
func save_game() -> void:
	var data := {"state": state, "place": _place_id}
	if avatar_cell != null:
		data["spawn"] = {"cell": avatar_cell}
	var f := FileAccess.open(SAVE_PATH, FileAccess.WRITE)
	if f != null:
		f.store_string(JSON.stringify(data))


func _load_save():
	if not FileAccess.file_exists(SAVE_PATH):
		return null
	var data = JSON.parse_string(FileAccess.get_file_as_string(SAVE_PATH))
	if typeof(data) != TYPE_DICTIONARY or not data.has("state") or not data.has("place"):
		return null
	return data


# Esc menu, awaited from a presenter's poll loop: Resume / Save / Quit. The dim layer STOPs the
# mouse so the hotspots underneath stay dead while the menu is up.
func pause_menu() -> void:
	var layer := Control.new()
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(layer)
	var dim := ColorRect.new()
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	dim.color = Color(0, 0, 0, 0.6)
	dim.mouse_filter = Control.MOUSE_FILTER_STOP
	layer.add_child(dim)
	var box := VBoxContainer.new()
	box.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	box.grow_horizontal = Control.GROW_DIRECTION_BOTH
	box.grow_vertical = Control.GROW_DIRECTION_BOTH
	box.custom_minimum_size = Vector2(280, 0)
	layer.add_child(box)
	var done := {"v": false}
	var rb := Button.new()
	rb.text = "Resume"
	rb.pressed.connect(func(): done["v"] = true)
	box.add_child(rb)
	var sb := Button.new()
	sb.text = "Save"
	sb.pressed.connect(func():
		save_game()
		sb.text = "Saved")
	box.add_child(sb)
	var qb := Button.new()
	qb.text = "Quit"
	qb.pressed.connect(func(): get_tree().quit())
	box.add_child(qb)
	while not done["v"]:
		await get_tree().process_frame
		if Input.is_action_just_pressed("ui_pause"):
			done["v"] = true
	layer.queue_free()


# The place loop, presenter-agnostic. Picks the presenter for each place by kind (PRESENTERS),
# runs it, and follows the result: a {move} hops to the next place (carrying the RPG arrival
# spawn), WIN/END terminate. Moving between an RPG zone and a PnC room just swaps presenters here.
func _run_world(place_id, spawn) -> void:
	while true:
		_place_id = place_id
		avatar_cell = null
		var kind = place_by_id[place_id].get("kind", "room")
		var presenter = (PRESENTERS.get(kind, Pnc)).new(self)
		var r = await presenter.run_place(place_id, spawn)
		# Match the {move} dict BEFORE the string compares — Godot 4 errors on Dictionary == String.
		if typeof(r) == TYPE_DICTIONARY and r.has("move"):
			place_id = r["move"]
			spawn = r.get("spawn")
		elif r == WIN:
			show_ending("escaped" if ir.has("goal") else null)
			return
		else:
			return


# The closed verb vocabulary, shared by every navigation presenter (pnc/overworld). Returns
# null = stay in the place; a {move,spawn} dict = leave for another place; WIN/END = terminate.
# Combat and dialogue resolve THROUGH here so a presenter never re-implements a verb.
func run_action(act) -> Variant:
	match act["type"]:
		"examine":
			await show_line(null, act["text"])
			hide_dialogue()
			return null
		"take":
			if not (act["item"] in state["inv"]):
				state["inv"].append(act["item"])
			if act.has("text"):
				await show_line(null, act["text"])
				hide_dialogue()
			return null
		"talk":
			await Vn.new(self).play_node(act["node"])
			return null
		"move":
			if act.has("requires") and not IRCore.eval_cond(state, act["requires"]):
				await show_line(null, "You can't go that way yet.")
				hide_dialogue()
				return null
			return {"move": act["target"], "spawn": act.get("spawn")}
		"use":
			var outcome = act.get("fallback")
			for clause in act.get("clauses", []):
				if IRCore.eval_cond(state, clause["requires"]):
					outcome = clause["outcome"]
					break
			if outcome != null:
				IRCore.apply_effects(state, outcome.get("effects"))
				if outcome.has("text"):
					await show_line(null, outcome["text"])
					hide_dialogue()
			return null
		"win":
			var gate = act.get("requires", ir.get("goal"))
			if gate != null and not IRCore.eval_cond(state, gate):
				await show_line(null, "Not yet.")
				hide_dialogue()
				return null
			return WIN
		"start_combat":
			if act.has("requires") and not IRCore.eval_cond(state, act["requires"]):
				await show_line(null, "Not now.")
				hide_dialogue()
				return null
			var resolution = await Combat.new(self).run(act["encounter"])
			return await _flow(resolution)
		_:
			return null


# A node_end flowing out of combat: jump/menu/return play through Vn and fall back to the place
# (null); end terminates the whole run (END). Mirrors how dialogue ends elsewhere.
func _flow(end) -> Variant:
	if end == null:
		return null
	match end.get("type"):
		"jump":
			await Vn.new(self).play_node(end["target"])
			return null
		"menu":
			var vn = Vn.new(self)
			await vn.play_node(await vn.menu_pick(end))
			return null
		"end":
			show_ending(end.get("ending"))
			return END
		_:
			return null


# ── UI construction (built in code so Main.tscn stays trivial and correct) ──────────────────
func _build_ui() -> void:
	# Non-interactive layers must IGNORE the mouse, else a full-rect Control consumes the click as
	# GUI input and "click to advance" (which rides on _unhandled_input) never fires. Buttons
	# (hotspots/menu) keep their own STOP and stay clickable regardless of their container's filter.
	mouse_filter = Control.MOUSE_FILTER_IGNORE

	_scene.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_scene.color = Color(0.1, 0.1, 0.18)
	_scene.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_scene)

	_sprites.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_sprites.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_sprites)

	_hotspots.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_hotspots.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_hotspots)

	_hud.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE)
	_hud.add_theme_color_override("font_color", Color.WHITE)
	_hud.position.y = 8
	_hud.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hud.visible = false
	add_child(_hud)

	_dialogue.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	_dialogue.position.y = -200
	_dialogue.custom_minimum_size = Vector2(0, 180)
	_dialogue.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var box := VBoxContainer.new()
	_speaker.add_theme_font_size_override("font_size", 22)
	_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_text.add_theme_font_size_override("font_size", 26)
	box.add_child(_speaker)
	box.add_child(_text)
	_dialogue.add_child(box)
	_dialogue.visible = false
	add_child(_dialogue)

	_menu.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	_menu.custom_minimum_size = Vector2(600, 0)
	_menu.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_menu.visible = false
	add_child(_menu)

	# Inventory strip: bottom-right, above the dialogue/combat strip clearance so it never
	# collides with the HP panels (top corners) or the ability buttons (bottom strip).
	_inv.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT)
	_inv.offset_right = -12
	_inv.offset_bottom = -210
	_inv.grow_horizontal = Control.GROW_DIRECTION_BEGIN
	_inv.grow_vertical = Control.GROW_DIRECTION_BEGIN
	_inv.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_inv)


# The strip rebuilds only when the item set changes — take/use/combat world effects all mutate
# state["inv"] in place, so a per-frame value compare is the one hook that catches every path.
func _process(_delta: float) -> void:
	_inv.visible = not _title_open
	if _title_open:
		return
	var inv: Array = state.get("inv", [])
	if inv == _inv_last:
		return
	_inv_last = inv.duplicate()
	for c in _inv.get_children():
		c.queue_free()
	for iid in inv:
		# The asset pipeline writes <item_id>.png (see overworld._interactable_icon); probe the
		# item_-prefixed name first for hand-dropped art.
		var tex = _texture_file("item_%s.png" % iid)
		if tex == null:
			tex = _texture_file("%s.png" % iid)
		if tex != null:
			var tr := TextureRect.new()
			tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
			tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
			tr.custom_minimum_size = Vector2(40, 40)
			tr.texture = tex
			tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
			_inv.add_child(tr)
		else:
			var chip := PanelContainer.new()
			chip.mouse_filter = Control.MOUSE_FILTER_IGNORE
			var lbl := Label.new()
			lbl.text = String(item_by_id.get(iid, {}).get("name", str(iid)))
			lbl.add_theme_font_size_override("font_size", 13)
			lbl.mouse_filter = Control.MOUSE_FILTER_IGNORE
			chip.add_child(lbl)
			_inv.add_child(chip)


func _unhandled_input(event: InputEvent) -> void:
	if not _await_advance:
		return
	if (event is InputEventMouseButton and event.pressed) or event.is_action_pressed("ui_accept"):
		_await_advance = false
		advanced.emit()


# ── presenters (each awaits player input, mirroring engine.js promise presenters) ───────────
func show_line(speaker, text: String) -> void:
	_menu.visible = false
	_speaker.text = "" if speaker == null else char_name(speaker)
	_speaker.add_theme_color_override(
		"font_color", Color.WHITE if speaker == null else char_color(speaker))
	_text.text = text
	_dialogue.visible = true
	_await_advance = true
	await advanced


func show_menu(options: Array) -> int:
	for c in _menu.get_children():
		c.queue_free()
	_menu.visible = true
	for i in options.size():
		var b := Button.new()
		b.text = str(options[i])
		b.pressed.connect(_on_menu_pick.bind(i))
		_menu.add_child(b)
	await menu_picked
	_menu.visible = false
	return _menu_pick


func _on_menu_pick(i: int) -> void:
	_menu_pick = i
	menu_picked.emit()


func show_ending(label) -> void:
	_dialogue.visible = false
	_menu.visible = false
	_hud.visible = false
	var title := "The End"
	var text := str(label) if label else ""
	for e in ir.get("endings", []):
		if typeof(e) == TYPE_DICTIONARY and e.get("id") == label:
			title = String(e.get("title", e.get("name", title)))
			text = String(e.get("text", e.get("description", "")))
			break
	var layer := Control.new()
	layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(layer)
	var dim := ColorRect.new()
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	dim.color = Color(0, 0, 0, 0.78)
	dim.mouse_filter = Control.MOUSE_FILTER_STOP
	layer.add_child(dim)
	var box := VBoxContainer.new()
	box.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	box.grow_horizontal = Control.GROW_DIRECTION_BOTH
	box.grow_vertical = Control.GROW_DIRECTION_BOTH
	box.custom_minimum_size = Vector2(640, 0)
	box.add_theme_constant_override("separation", 18)
	layer.add_child(box)
	var tl := Label.new()
	tl.text = title
	tl.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	tl.add_theme_font_size_override("font_size", 48)
	box.add_child(tl)
	if text != "":
		var tx := Label.new()
		tx.text = text
		tx.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		tx.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
		tx.add_theme_font_size_override("font_size", 22)
		box.add_child(tx)
	var again := Button.new()
	again.text = "Play Again"
	again.pressed.connect(func(): get_tree().reload_current_scene())
	box.add_child(again)
	var qb := Button.new()
	qb.text = "Quit"
	qb.pressed.connect(func(): get_tree().quit())
	box.add_child(qb)


func hide_dialogue() -> void:
	_dialogue.visible = false


func set_hud(text: String) -> void:
	_hud.text = text
	_hud.visible = text != ""


func char_name(id) -> String:
	return chars[id]["name"] if chars.has(id) else str(id)


func char_color(id) -> Color:
	var h := 0
	for i in str(id).length():
		h = (h * 31 + str(id).unicode_at(i)) % 360
	return Color.from_hsv(h / 360.0, 0.5, 0.95)


func set_scene(bg_id) -> void:
	_clear_scene_texture()
	if bg_id == null:
		_scene.color = Color(0.1, 0.1, 0.18)
		return
	var tex = _texture(bg_id)
	if tex != null:
		var tr := TextureRect.new()
		tr.name = "bg"
		tr.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
		tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_COVERED
		tr.texture = tex
		tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_scene.add_child(tr)
	else:
		var h := 0
		for i in str(bg_id).length():
			h = (h * 31 + str(bg_id).unicode_at(i)) % 360
		_scene.color = Color.from_hsv(h / 360.0, 0.3, 0.22)


func _clear_scene_texture() -> void:
	for c in _scene.get_children():
		c.queue_free()


func _texture(bg_id):
	var file: String = bg_files.get(bg_id, str(bg_id) + ".png")
	return _texture_file(file)


func _texture_file(file: String):
	# Read via FileAccess so it works both in-editor (loose files) and in an exported PCK; the art
	# pipeline and the placeholders are all PNG. Returns null on miss — callers colour-fill instead.
	var path := "res://images/" + file
	if not FileAccess.file_exists(path):
		return null
	var img := Image.new()
	if img.load_png_from_buffer(FileAccess.get_file_as_bytes(path)) != OK:
		return null
	return ImageTexture.create_from_image(img)


# The persistent player combat block (stats/max/xp/level) for the progression loop — lives in
# `state` so saves carry it. Initialized lazily from the progression player's combatant.
func pstats():
	var prog = ir.get("progression")
	if prog == null:
		return null
	if not state.has("pstats"):
		var cb = combatant_by_id.get(prog["player"], {})
		var stats := {}
		for s in stat_by_id.values():
			stats[s["id"]] = s["default"]
		for sv in cb.get("stats", []):
			stats[sv["stat"]] = sv["value"]
		var mx := {}
		for s in stat_by_id.values():
			if s.get("role") == "resource_depletable":
				mx[s["id"]] = max(float(s.get("max", 1.0)), float(stats.get(s["id"], 1)), 1.0)
		state["pstats"] = {"stats": stats, "max": mx, "xp": 0, "level": 1}
	return state["pstats"]


func sprites_node() -> Control:
	return _sprites


func hotspots_node() -> Control:
	return _hotspots
