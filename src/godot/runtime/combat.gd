# Combat interpreter — a turn_based encounter loop. THIS is why Godot exists: web/renpy stub
# combat out; here a fight actually plays. New code with no engine.js equivalent. Reads
# encounters/combatants/abilities/stats/statuses from the IR and resolves combat_effects, then
# flows out via the encounter's on_victory/on_defeat (a node_end, returned to the caller).
extends RefCounted

const IRCore = preload("res://ir.gd")

var g  # Game driver


func _init(game) -> void:
	g = game


# Returns the resolution node_end (on_victory / on_defeat), or null if absent.
func run(enc_id):
	var enc = g.encounter_by_id[enc_id]
	g.set_scene(enc.get("background"))
	var units := []
	for c in enc["combatants"]:
		units.append(_make_unit(c))

	await g.show_line(null, "The fight begins.")
	g.hide_dialogue()

	while true:
		var res := _check_end(enc, units)
		# A fully dead field must resolve even when victory/defeat are `when`-conditions that
		# never match — otherwise every unit `continue`s and the loop spins forever with no awaits.
		if res == "" and not _any_alive(units):
			res = "defeat"
		if res != "":
			return _finish(enc, res)
		for u in units:
			if not u.alive:
				continue
			_tick_statuses(u)
			res = _check_end(enc, units)
			if res != "":
				return _finish(enc, res)
			if not u.alive:
				continue
			_update_hud(units)
			if not _blocked(u):
				if u.faction == "player":
					await _player_turn(u, units)
				else:
					await _enemy_turn(u, units)
			res = _check_end(enc, units)
			if res != "":
				return _finish(enc, res)


func _finish(enc, res: String):
	g.set_hud("")
	if res == "victory":
		return enc.get("on_victory")
	return enc.get("on_defeat", {"type": "end", "ending": "game_over"})


func _any_alive(units) -> bool:
	for u in units:
		if u["alive"]:
			return true
	return false


func _make_unit(c: Dictionary) -> Dictionary:
	var cb = g.combatant_by_id[c["ref"]]
	var stats := {}
	for s in g.stat_by_id.values():
		stats[s["id"]] = s["default"]
	for sv in cb.get("stats", []):
		stats[sv["stat"]] = sv["value"]
	var nm = c["ref"]
	if cb.has("character") and g.chars.has(cb["character"]):
		nm = g.chars[cb["character"]]["name"]
	return {"ref": c["ref"], "faction": c["faction"], "stats": stats,
		"abilities": cb.get("abilities", []), "statuses": [], "alive": true, "name": nm}


# ── turns ───────────────────────────────────────────────────────────────────────────────────
func _player_turn(u, units) -> void:
	var ids := []
	var labels := []
	for aid in u["abilities"]:
		var ab = g.ability_by_id.get(aid)
		if ab == null:
			continue
		if ab.has("requires") and not IRCore.eval_cond(g.state, ab["requires"]):
			continue
		if not _can_afford(u, ab):
			continue
		ids.append(aid)
		labels.append(ab.get("name", aid))
	if ids.is_empty():
		await g.show_line(u["name"], "%s can do nothing." % u["name"])
		g.hide_dialogue()
		return
	var ab = g.ability_by_id[ids[await g.show_menu(labels)]]
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
	var names := []
	for u in pool:
		names.append(u["name"])
	return [pool[await g.show_menu(names)]]


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
			_apply_combat_effect(user, tg, ce)


func _apply_combat_effect(user, tg, ce) -> void:
	if ce.has("stat"):
		var sid = ce["stat"]
		var mag := _formula(user, ce.get("formula"))
		var cur := float(tg["stats"].get(sid, 0))
		match ce["op"]:
			"damage": cur -= mag
			"heal": cur += mag
			"set": cur = mag
			"add": cur += mag
		tg["stats"][sid] = _clamp(sid, cur)
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
			_apply_combat_effect(u, u, ce)
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


func _update_hud(units) -> void:
	var parts := []
	for u in units:
		parts.append("%s%s" % [u["name"], _depletable_str(u)])
	g.set_hud("    ".join(parts))


func _depletable_str(u) -> String:
	if not u["alive"]:
		return " (down)"
	for sid in u["stats"]:
		var sdef = g.stat_by_id.get(sid, {})
		if sdef.get("role") == "resource_depletable":
			return " (%d/%d)" % [int(u["stats"][sid]), int(sdef.get("max", u["stats"][sid]))]
	return ""
