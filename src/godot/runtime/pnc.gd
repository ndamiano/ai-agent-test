# Point-and-click interpreter — rooms + hotspot verbs. Port of engine.js runPnc/runPlace/runAction.
# Enters dialogue (talk) via Vn and fights (start_combat) via Combat, both of which resolve back here.
extends RefCounted

const IRCore = preload("res://ir.gd")
const Vn = preload("res://vn.gd")
const Combat = preload("res://combat.gd")
const WIN := "__win__"

var g  # Game driver


func _init(game) -> void:
	g = game


func run() -> void:
	var place_id = g.ir["start"]["place"]
	while true:
		var nxt = await _run_place(place_id)
		if nxt == WIN:
			g.show_ending("escaped" if g.ir.has("goal") else null)
			return
		place_id = nxt


func _run_place(place_id):  # returns WIN or a destination place id (loops until one happens)
	var place = g.place_by_id[place_id]
	g.set_scene(place.get("background"))
	for c in g.sprites_node().get_children():
		c.queue_free()
	while true:
		var it = await _await_hotspot(place)
		var r = await _run_action(it["action"])
		if r == WIN:
			return WIN
		if typeof(r) == TYPE_DICTIONARY and r.has("move"):
			return r["move"]
		g.set_scene(place.get("background"))  # talk/examine may have changed the scene


func _await_hotspot(place) -> Dictionary:
	var box: Control = g.hotspots_node()
	for c in box.get_children():
		c.queue_free()
	var picked := {"v": null}
	var stack := 0
	for it in place.get("interactables", []):
		var rect = it.get("position", {}).get("rect", {})
		var b := Button.new()
		b.text = it.get("label", "")
		if rect.get("w", 0) > 0 and rect.get("h", 0) > 0:
			b.position = Vector2(rect["x"], rect["y"])
			b.size = Vector2(rect["w"], rect["h"])
			b.custom_minimum_size = b.size
		else:
			# No pixel rect (RPG cell / unpositioned): stack a labelled button so it stays clickable.
			b.position = Vector2(40, 120 + stack * 64)
			b.custom_minimum_size = Vector2(280, 52)
			stack += 1
		b.pressed.connect(func(): picked["v"] = it; g.menu_picked.emit())
		box.add_child(b)
	await g.menu_picked
	for c in box.get_children():
		c.queue_free()
	return picked["v"]


func _run_action(act) -> Variant:
	match act["type"]:
		"examine":
			await g.show_line(null, act["text"])
			g.hide_dialogue()
			return null
		"take":
			if not (act["item"] in g.state["inv"]):
				g.state["inv"].append(act["item"])
			if act.has("text"):
				await g.show_line(null, act["text"])
				g.hide_dialogue()
			return null
		"talk":
			await Vn.new(g).play_node(act["node"])
			return null
		"move":
			if act.has("requires") and not IRCore.eval_cond(g.state, act["requires"]):
				await g.show_line(null, "You can't go that way yet.")
				g.hide_dialogue()
				return null
			return {"move": act["target"]}
		"use":
			var outcome = act.get("fallback")
			for clause in act.get("clauses", []):
				if IRCore.eval_cond(g.state, clause["requires"]):
					outcome = clause["outcome"]
					break
			if outcome != null:
				IRCore.apply_effects(g.state, outcome.get("effects"))
				if outcome.has("text"):
					await g.show_line(null, outcome["text"])
					g.hide_dialogue()
			return null
		"win":
			var gate = act.get("requires", g.ir.get("goal"))
			if gate != null and not IRCore.eval_cond(g.state, gate):
				await g.show_line(null, "Not yet.")
				g.hide_dialogue()
				return null
			return WIN
		"start_combat":
			if act.has("requires") and not IRCore.eval_cond(g.state, act["requires"]):
				await g.show_line(null, "Not now.")
				g.hide_dialogue()
				return null
			var resolution = await Combat.new(g).run(act["encounter"])
			if resolution != null:
				await _flow(resolution)
			return null
		"play_match":
			await g.show_line(null, "[Card matches play in the web build.]")
			g.hide_dialogue()
			return null
		_:
			return null


# A node_end flowing out of combat: jump/end play through Vn; return falls back to the place loop.
func _flow(end) -> void:
	match end.get("type"):
		"jump":
			await Vn.new(g).play_node(end["target"])
		"end":
			g.show_ending(end.get("ending"))
		_:
			pass
