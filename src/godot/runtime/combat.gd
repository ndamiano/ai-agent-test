# Combat interpreter — a turn_based encounter loop. THIS is why Godot exists: web/renpy stub
# combat out; here a fight actually plays. New code with no engine.js equivalent. Reads
# encounters/combatants/abilities/stats/statuses from the IR and resolves combat_effects, then
# flows out via the encounter's on_victory/on_defeat (a node_end, returned to the caller).
extends RefCounted

const IRCore = preload("res://ir.gd")

signal _picked

var g  # Game driver
var _ui: Control = null          # bars + banner + popups layer
var _sprites := []               # staged combatant TextureRects, index-aligned with units
var _bars := []                  # {fill, value, name_lbl, status_lbl, max} per unit index
var _pick := -1


func _init(game) -> void:
	g = game


# Returns the resolution node_end (on_victory / on_defeat), or null if absent.
func run(enc_id):
	var enc = g.encounter_by_id[enc_id]
	return await _run_enc(enc, "The fight begins.")


# A wild fight rolled from a zone's encounter_table: the progression player vs one tier-scaled
# enemy. Returns null on victory (keep exploring) or {"wild_defeat": true} — the overworld
# respawns the avatar instead of ending the game.
func run_wild(cb_ref: String, tier: float):
	var prog = g.ir.get("progression")
	if prog == null:
		return null
	var enemy = _make_unit({"ref": cb_ref, "faction": "enemy"})
	if tier != 1.0:
		for sid in enemy["stats"]:
			enemy["stats"][sid] = int(round(float(enemy["stats"][sid]) * tier))
	var enc = {"combatants": [], "victory": {"all_defeated": "enemy"},
		"defeat": {"all_defeated": "player"}, "wild": true, "prebuilt":
		[_make_unit({"ref": prog["player"], "faction": "player"}), enemy]}
	var res = await _run_enc(enc, "A %s attacks!" % enemy["name"])
	if typeof(res) == TYPE_DICTIONARY and res.get("wild_defeat"):
		return res
	return null


func _run_enc(enc, opener: String):
	g.set_scene(enc.get("background"))
	var units := []
	if enc.has("prebuilt"):
		units = enc["prebuilt"]
	else:
		for c in enc["combatants"]:
			units.append(_make_unit(c))
	_units_ctx = units
	_stage(units)

	await g.show_line(null, opener)
	g.hide_dialogue()

	while true:
		var res := _check_end(enc, units)
		# A fully dead field must resolve even when victory/defeat are `when`-conditions that
		# never match — otherwise every unit `continue`s and the loop spins forever with no awaits.
		if res == "" and not _any_alive(units):
			res = "defeat"
		if res != "":
			return await _finish(enc, res, units)
		for u in units:
			if not u.alive:
				continue
			_tick_statuses(u)
			res = _check_end(enc, units)
			if res != "":
				return await _finish(enc, res, units)
			if not u.alive:
				continue
			_refresh_bars(units)
			if not _blocked(u):
				_banner_text("%s's turn" % u["name"])
				if u.faction == "player":
					await _player_turn(u, units)
				else:
					await _enemy_turn(u, units)
				_refresh_bars(units)
			res = _check_end(enc, units)
			if res != "":
				return await _finish(enc, res, units)


func _finish(enc, res: String, units):
	g.set_hud("")
	if res == "victory":
		await _award_xp(units)
	_teardown()
	if res == "victory":
		return enc.get("on_victory")
	if enc.get("wild"):
		return {"wild_defeat": true}
	return enc.get("on_defeat", {"type": "end", "ending": "game_over"})


# Victory pays the defeated enemies' xp_yield into the persistent player stats; crossing a
# threshold levels up (growth applied, depletables healed to their grown max).
func _award_xp(units) -> void:
	var prog = g.ir.get("progression")
	var ps = g.pstats()
	if prog == null or ps == null:
		return
	var gain := 0
	for u in units:
		if u["faction"] != "player" and not u["alive"]:
			gain += int(g.combatant_by_id.get(u["ref"], {}).get("xp_yield", 0))
	if gain <= 0:
		return
	ps["xp"] = int(ps["xp"]) + gain
	var msg := "Gained %d XP." % gain
	var need = int(prog["xp_per_level"])
	while int(ps["xp"]) >= int(ps["level"]) * need:
		ps["level"] = int(ps["level"]) + 1
		for gr in prog.get("growth", []):
			var sid = gr["stat"]
			if ps["max"].has(sid):
				ps["max"][sid] = float(ps["max"][sid]) + float(gr["per_level"])
			else:
				ps["stats"][sid] = float(ps["stats"].get(sid, 0)) + float(gr["per_level"])
		for sid in ps["max"]:
			ps["stats"][sid] = ps["max"][sid]
		msg += "  LEVEL %d!" % int(ps["level"])
	await g.show_line(null, msg)
	g.hide_dialogue()


# ── combat UI: staged fighters, bar panels, banner, popups ──────────────────────────────────
# The old presentation was one white HUD string + the shared dialogue menu — fighters were
# invisible. Everything here uses assets that already exist (character sprites) plus plain
# Controls; no new generation.
const _PANEL_W := 360
const _PANEL_H := 68
const _BOTTOM := 190.0   # dialogue panel clearance


func _stage(units) -> void:
	_teardown()
	g.set_hud("")
	for c in g.sprites_node().get_children():
		c.queue_free()
	_ui = Control.new()
	_ui.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_ui.mouse_filter = Control.MOUSE_FILTER_IGNORE
	g.add_child(_ui)
	var vp: Vector2 = g.get_viewport_rect().size
	var left_n := 0
	var right_n := 0
	_sprites.clear()
	_bars.clear()
	for i in units.size():
		var u = units[i]
		var is_player: bool = u["faction"] == "player"
		# sprite, players from the left edge, enemies mirrored from the right
		var tr: TextureRect = null
		var cb = g.combatant_by_id.get(u["ref"], {})
		if cb.has("character") and g.chars.has(cb["character"]) \
				and g.chars[cb["character"]].has("sprite"):
			var tex = g._texture_file(g.chars[cb["character"]]["sprite"])
			if tex != null:
				tr = TextureRect.new()
				tr.texture = tex
				tr.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
				tr.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
				tr.size = Vector2(300, 430)
				tr.flip_h = not is_player
				var slot = left_n if is_player else right_n
				var x = 90 + slot * 200 if is_player else vp.x - 90 - 300 - slot * 200
				tr.position = Vector2(x, vp.y - _BOTTOM - 430)
				tr.mouse_filter = Control.MOUSE_FILTER_IGNORE
				g.sprites_node().add_child(tr)
		_sprites.append(tr)
		# bar panel, stacked per side
		var slot2 = left_n if is_player else right_n
		var px = 24.0 if is_player else vp.x - _PANEL_W - 24.0
		var py = 20.0 + slot2 * (_PANEL_H + 10)
		var panel := ColorRect.new()
		panel.color = Color(0.08, 0.08, 0.11, 0.82)
		panel.position = Vector2(px, py)
		panel.size = Vector2(_PANEL_W, _PANEL_H)
		panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_ui.add_child(panel)
		var nm := Label.new()
		nm.text = u["name"]
		nm.position = Vector2(px + 12, py + 6)
		nm.add_theme_font_size_override("font_size", 18)
		_ui.add_child(nm)
		var val := Label.new()
		val.position = Vector2(px + _PANEL_W - 86, py + 8)
		val.add_theme_font_size_override("font_size", 15)
		val.add_theme_color_override("font_color", Color(0.85, 0.85, 0.9))
		_ui.add_child(val)
		var track := ColorRect.new()
		track.color = Color(0.2, 0.2, 0.24)
		track.position = Vector2(px + 12, py + 36)
		track.size = Vector2(_PANEL_W - 24, 14)
		track.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_ui.add_child(track)
		var fill := ColorRect.new()
		fill.color = Color(0.28, 0.66, 0.36) if is_player else Color(0.78, 0.28, 0.24)
		fill.position = track.position
		fill.size = track.size
		fill.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_ui.add_child(fill)
		var st := Label.new()
		st.position = Vector2(px + 12, py + 50)
		st.add_theme_font_size_override("font_size", 12)
		st.add_theme_color_override("font_color", Color(0.95, 0.8, 0.4))
		_ui.add_child(st)
		_bars.append({"fill": fill, "track_w": track.size.x, "value": val, "status": st,
			"name_lbl": nm, "sprite": tr})
		if is_player:
			left_n += 1
		else:
			right_n += 1
	var banner := Label.new()
	banner.name = "turn_banner"
	banner.position = Vector2(vp.x / 2 - 140, 16)
	banner.size = Vector2(280, 34)
	banner.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	banner.add_theme_font_size_override("font_size", 20)
	banner.add_theme_color_override("font_color", Color(0.95, 0.87, 0.5))
	banner.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.9))
	_ui.add_child(banner)


func _teardown() -> void:
	if _ui != null and is_instance_valid(_ui):
		_ui.queue_free()
	_ui = null
	for s in _sprites:
		if s != null and is_instance_valid(s):
			s.queue_free()
	_sprites.clear()
	_bars.clear()


func _banner_text(t: String) -> void:
	if _ui == null:
		return
	var b = _ui.get_node_or_null("turn_banner")
	if b != null:
		b.text = t


func _refresh_bars(units) -> void:
	if _bars.size() != units.size():
		return
	for i in units.size():
		var u = units[i]
		var bar = _bars[i]
		var cur := 0.0
		var mx := 1.0
		for sid in u["stats"]:
			var sdef = g.stat_by_id.get(sid, {})
			if sdef.get("role") == "resource_depletable":
				cur = float(u["stats"][sid])
				if u.has("max_override") and u["max_override"].has(sid):
					mx = max(float(u["max_override"][sid]), cur, 1.0)
				else:
					mx = max(float(sdef.get("max", 1.0)), cur, 1.0)
				break
		bar["fill"].size.x = bar["track_w"] * clamp(cur / mx, 0.0, 1.0)
		bar["value"].text = "%d/%d" % [int(cur), int(mx)] if u["alive"] else "down"
		var chips := []
		for stx in u["statuses"]:
			chips.append("%s(%d)" % [stx["id"], int(stx.get("left", 1))])
		bar["status"].text = "  ".join(chips)
		if not u["alive"]:
			bar["name_lbl"].add_theme_color_override("font_color", Color(0.6, 0.6, 0.6))
			if bar["sprite"] != null and is_instance_valid(bar["sprite"]):
				bar["sprite"].modulate = Color(0.4, 0.4, 0.4, 0.55)


func _popup(units, tg, text: String, col: Color) -> void:
	if _ui == null:
		return
	var i = units.find(tg)
	var vp: Vector2 = g.get_viewport_rect().size
	var x = vp.x / 2.0
	if i >= 0 and _sprites[i] != null and is_instance_valid(_sprites[i]):
		x = _sprites[i].position.x + 150
	var lbl := Label.new()
	lbl.text = text
	lbl.position = Vector2(x, vp.y - _BOTTOM - 470)
	lbl.add_theme_font_size_override("font_size", 34)
	lbl.add_theme_color_override("font_color", col)
	lbl.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.9))
	_ui.add_child(lbl)
	var tw = g.create_tween()
	tw.set_parallel(true)
	tw.tween_property(lbl, "position:y", lbl.position.y - 46, 0.7)
	tw.tween_property(lbl, "modulate:a", 0.0, 0.7)
	tw.chain().tween_callback(lbl.queue_free)


func _menu_buttons(entries: Array, note: String = "") -> int:
	# Combat's own picker: a bottom strip holding name + cost-sub buttons (wrapping 4 per row)
	# and the actor's resource line. The shared g.show_menu stays untouched for dialogue choices.
	var vp: Vector2 = g.get_viewport_rect().size
	var row := Control.new()
	row.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_ui.add_child(row)
	var strip := ColorRect.new()
	strip.color = Color(0.05, 0.05, 0.08, 0.8)
	strip.position = Vector2(0, vp.y - _BOTTOM)
	strip.size = Vector2(vp.x, _BOTTOM)
	strip.mouse_filter = Control.MOUSE_FILTER_IGNORE
	row.add_child(strip)
	for i in entries.size():
		var e = entries[i]
		var b := Button.new()
		b.text = e["label"] if e["sub"] == "" else "%s\n%s" % [e["label"], e["sub"]]
		b.position = Vector2(24 + (i % 4) * 250, vp.y - _BOTTOM + 16 + int(i / 4.0) * 76)
		b.size = Vector2(238, 66)
		b.pressed.connect(func():
			_pick = i
			_picked.emit())
		row.add_child(b)
	if note != "":
		var nl := Label.new()
		nl.text = note
		nl.position = Vector2(24, vp.y - 34)
		nl.add_theme_font_size_override("font_size", 15)
		nl.add_theme_color_override("font_color", Color(0.55, 0.7, 0.95))
		row.add_child(nl)
	await _picked
	row.queue_free()
	return _pick


func _any_alive(units) -> bool:
	for u in units:
		if u["alive"]:
			return true
	return false


func _make_unit(c: Dictionary) -> Dictionary:
	var cb = g.combatant_by_id[c["ref"]]
	var nm = c["ref"]
	if cb.has("character") and g.chars.has(cb["character"]):
		nm = g.chars[cb["character"]]["name"]
	# The progression player fights on their PERSISTENT stat dict (damage, XP and growth carry
	# across fights); everyone else gets a fresh block from defaults + overrides.
	var prog = g.ir.get("progression")
	if prog != null and c["ref"] == prog.get("player") and c["faction"] == "player":
		var ps = g.pstats()
		for sid in ps["max"]:
			if float(ps["stats"].get(sid, 0)) <= 0:
				ps["stats"][sid] = 1   # never enter a fight already dead
		return {"ref": c["ref"], "faction": "player", "stats": ps["stats"],
			"max_override": ps["max"], "abilities": cb.get("abilities", []),
			"statuses": [], "alive": true, "name": "%s  Lv %d" % [nm, int(ps["level"])]}
	var stats := {}
	for s in g.stat_by_id.values():
		stats[s["id"]] = s["default"]
	for sv in cb.get("stats", []):
		stats[sv["stat"]] = sv["value"]
	return {"ref": c["ref"], "faction": c["faction"], "stats": stats,
		"abilities": cb.get("abilities", []), "statuses": [], "alive": true, "name": nm}


# ── turns ───────────────────────────────────────────────────────────────────────────────────
func _player_turn(u, units) -> void:
	var ids := []
	var entries := []
	for aid in u["abilities"]:
		var ab = g.ability_by_id.get(aid)
		if ab == null:
			continue
		if ab.has("requires") and not IRCore.eval_cond(g.state, ab["requires"]):
			continue
		if not _can_afford(u, ab):
			continue
		ids.append(aid)
		var costs := []
		for cost in ab.get("cost", []):
			costs.append("%d %s" % [int(cost["amount"]), cost["stat"]])
		entries.append({"label": ab.get("name", aid), "sub": ", ".join(costs)})
	if ids.is_empty():
		await g.show_line(u["name"], "%s can do nothing." % u["name"])
		g.hide_dialogue()
		return
	var seen := {}
	var notes := []
	for aid in u["abilities"]:
		var ab2 = g.ability_by_id.get(aid)
		if ab2 == null:
			continue
		for cost in ab2.get("cost", []):
			var sid = cost["stat"]
			if seen.has(sid):
				continue
			seen[sid] = true
			var sdef = g.stat_by_id.get(sid, {})
			notes.append("%s  %d/%d" % [sdef.get("name", sid),
				int(u["stats"].get(sid, 0)), int(sdef.get("max", 0))])
	var ab = g.ability_by_id[ids[await _menu_buttons(entries, "   ".join(notes))]]
	var targets = await _choose_targets(u, ab, units)
	await _resolve(u, ab, targets)


func _enemy_turn(u, units) -> void:
	for aid in u["abilities"]:
		var ab = g.ability_by_id.get(aid)
		if ab == null:
			continue
		if ab.has("requires") and not IRCore.eval_cond(g.state, ab["requires"]):
			continue
		if not _can_afford(u, ab):
			continue
		var pool = _valid_targets(u, ab, units)
		var targets = pool if ab["targeting"]["shape"] != "single" else (
			[pool[randi() % pool.size()]] if not pool.is_empty() else [])
		await _resolve(u, ab, targets)
		return
	await g.show_line(u["name"], "%s hesitates." % u["name"])
	g.hide_dialogue()


func _choose_targets(user, ab, units) -> Array:
	var pool = _valid_targets(user, ab, units)
	if ab["targeting"]["shape"] != "single":
		return pool
	if pool.size() <= 1:
		return pool
	var entries := []
	for u in pool:
		entries.append({"label": u["name"], "sub": ""})
	return [pool[await _menu_buttons(entries)]]


func _valid_targets(user, ab, units) -> Array:
	var t = ab["targeting"]
	if t["shape"] == "self":
		return [user]
	var pool := []
	for u in units:
		if u["alive"] and _faction_ok(user, u, t["faction"]):
			pool.append(u)
	return pool


func _faction_ok(user, u, fac) -> bool:
	match fac:
		"self": return u == user
		"ally": return u["faction"] == user["faction"]
		"enemy": return u["faction"] != user["faction"]
		_: return true


# ── resolution ──────────────────────────────────────────────────────────────────────────────
func _resolve(user, ab, targets) -> void:
	_pay_cost(user, ab)
	var names := []
	for tg in targets:
		names.append(tg["name"])
	await g.show_line(user["name"], "%s uses %s on %s." % [
		user["name"], ab.get("name", "an ability"), ", ".join(names) if names else "no one"])
	g.hide_dialogue()
	for tg in targets:
		for ce in ab.get("effects", []):
			_apply_combat_effect(user, tg, ce, _units_ctx)
	if _units_ctx != null:
		_refresh_bars(_units_ctx)


var _units_ctx = null  # set by run(); popups locate the target's sprite through it


func _apply_combat_effect(user, tg, ce, units = null) -> void:
	if ce.has("stat"):
		var sid = ce["stat"]
		var mag := _formula(user, ce.get("formula"))
		var cur := float(tg["stats"].get(sid, 0))
		match ce["op"]:
			"damage": cur -= mag
			"heal": cur += mag
			"set": cur = mag
			"add": cur += mag
		var before := float(tg["stats"].get(sid, 0))
		tg["stats"][sid] = _clamp(sid, cur)
		var delta := float(tg["stats"][sid]) - before
		if units != null and delta != 0.0 \
				and g.stat_by_id.get(sid, {}).get("role") == "resource_depletable":
			var col := Color(1, 0.35, 0.27) if delta < 0 else Color(0.45, 1, 0.55)
			_popup(units, tg, "%+d" % int(delta), col)
		_recompute_alive(tg)
	elif ce.has("status"):
		if ce.get("remove", false):
			tg["statuses"] = tg["statuses"].filter(func(s): return s["id"] != ce["status"])
		else:
			tg["statuses"].append({"id": ce["status"], "left": ce.get("duration", 1)})
	elif ce.has("world"):
		IRCore.apply_effect(g.state, ce["world"])


func _formula(user, f) -> float:
	if f == null:
		return 0.0
	var base := float(f.get("base", 0))
	if f.has("scales_with"):
		base += float(user["stats"].get(f["scales_with"], 0)) * float(f.get("scale", 1))
	return base


func _clamp(sid, val: float) -> float:
	var sdef = g.stat_by_id.get(sid, {})
	if sdef.has("min"):
		val = max(val, float(sdef["min"]))
	if sdef.has("max"):
		val = min(val, float(sdef["max"]))
	return val


func _can_afford(u, ab) -> bool:
	for cost in ab.get("cost", []):
		if float(u["stats"].get(cost["stat"], 0)) < cost["amount"]:
			return false
	return true


func _pay_cost(u, ab) -> void:
	for cost in ab.get("cost", []):
		var sid = cost["stat"]
		u["stats"][sid] = _clamp(sid, float(u["stats"].get(sid, 0)) - cost["amount"])


func _recompute_alive(u) -> void:
	for sid in u["stats"]:
		var sdef = g.stat_by_id.get(sid)
		if sdef != null and sdef.get("role") == "resource_depletable":
			if float(u["stats"][sid]) <= float(sdef.get("min", 0)):
				u["alive"] = false
				return


# ── statuses, blocking, end ───────────────────────────────────────────────────────────────
func _tick_statuses(u) -> void:
	var keep := []
	for st in u["statuses"]:
		var sdef = g.status_by_id.get(st["id"], {})
		for ce in sdef.get("tick", []):
			_apply_combat_effect(u, u, ce, _units_ctx)
		st["left"] = int(st.get("left", 1)) - 1
		if st["left"] > 0:
			keep.append(st)
	u["statuses"] = keep


func _blocked(u) -> bool:
	for st in u["statuses"]:
		if g.status_by_id.get(st["id"], {}).get("blocks_action", false):
			return true
	return false


func _check_end(enc, units) -> String:
	if _cond_met(enc["victory"], units):
		return "victory"
	if _cond_met(enc.get("defeat", {"all_defeated": "player"}), units):
		return "defeat"
	return ""


func _cond_met(c, units) -> bool:
	if c.has("all_defeated"):
		for u in units:
			if u["faction"] == c["all_defeated"] and u["alive"]:
				return false
		return true
	if c.has("when"):
		return IRCore.eval_cond(g.state, c["when"])
	return false


