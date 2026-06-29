# Visual-novel interpreter — walks a dialogue node graph. Port of engine.js playNode.
# Constructed with the Game driver; uses its presenters. Reused by pnc (talk) and combat (resolve).
extends RefCounted

const IRCore = preload("res://ir.gd")

var g  # Game driver


func _init(game) -> void:
	g = game


func play_node(start_id) -> void:
	var id = start_id
	while id != null:
		var node = g.node_by_id[id]
		if node.has("location"):
			g.set_scene(node["location"])
		_stage_sprites(node)
		for line in node.get("lines", []):
			_highlight(line.get("speaker"))
			await g.show_line(line.get("speaker"), line["text"])
			IRCore.apply_effects(g.state, line.get("effects"))
		var end = node.get("end", {})
		match end.get("type"):
			"jump":
				id = end["target"]
			"menu":
				var open := []
				for c in end.get("choices", []):
					if IRCore.eval_cond(g.state, c.get("requires")):
						open.append(c)
				var labels := []
				for c in open:
					labels.append(c["text"])
				var pick = open[await g.show_menu(labels)]
				IRCore.apply_effects(g.state, pick.get("effects"))
				id = pick["target"]
			"return":
				g.hide_dialogue()
				return
			_:
				g.show_ending(end.get("ending"))
				return


func _stage_sprites(node) -> void:
	var box: Control = g.sprites_node()
	for c in box.get_children():
		c.queue_free()
	var roster := []
	for line in node.get("lines", []):
		var sp = line.get("speaker")
		if sp != null and g.chars.has(sp) and g.chars[sp].has("sprite") and not (sp in roster):
			roster.append(sp)
	for i in roster.size():
		var cid = roster[i]
		var tex = g._texture_file(g.chars[cid]["sprite"])
		if tex == null:
			continue
		var tr := TextureRect.new()
		tr.name = str(cid)
		tr.texture = tex
		tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT
		tr.custom_minimum_size = Vector2(360, 540)
		tr.size = Vector2(360, 540)
		tr.position = Vector2(float(i + 1) / (roster.size() + 1) * 1280 - 180, 180)
		box.add_child(tr)


func _highlight(speaker) -> void:
	var box: Control = g.sprites_node()
	if box.get_child_count() < 2:
		return
	for child in box.get_children():
		child.modulate.a = 1.0 if child.name == str(speaker) else 0.45
