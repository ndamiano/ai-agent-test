# Game — the runtime driver. Loads game.json, indexes the IR, owns shared UI presenters, and
# dispatches to the vn/pnc/combat interpreters. Mirrors the boot + presenter layer of
# web/runtime/engine.js; the genre loops live in vn.gd / pnc.gd / combat.gd.
extends Control

const IRCore = preload("res://ir.gd")
const Vn = preload("res://vn.gd")
const Pnc = preload("res://pnc.gd")

var ir: Dictionary = {}
var state: Dictionary = {}

var chars := {}
var node_by_id := {}
var place_by_id := {}
var bg_files := {}
var stat_by_id := {}
var ability_by_id := {}
var status_by_id := {}
var combatant_by_id := {}
var encounter_by_id := {}

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
@onready var _ending := Label.new()
@onready var _hud := Label.new()


func _ready() -> void:
	_build_ui()
	ir = _load_game()
	state = IRCore.make_state(ir)
	_index()
	await _boot()


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
	var start = ir.get("start", {})
	if ir.get("genre") == "point_and_click" or start.has("place"):
		await Pnc.new(self).run()
	elif start.has("node"):
		await Vn.new(self).play_node(start["node"])


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

	_ending.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_ending.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_ending.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_ending.add_theme_font_size_override("font_size", 48)
	_ending.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_ending.visible = false
	add_child(_ending)


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
	_ending.text = "The End" + ("\n" + str(label) if label else "")
	_ending.visible = true


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


func sprites_node() -> Control:
	return _sprites


func hotspots_node() -> Control:
	return _hotspots
