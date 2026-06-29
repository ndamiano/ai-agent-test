# Pure IR core — state, conditions, effects. A direct port of web/runtime/engine.js's pure core.
# No engine/UI dependency, so it is unit-testable headless. Every game ships THIS unchanged and the
# presenters (vn/pnc/combat) walk the IR through it. Referenced via preload (not class_name) so a
# freshly-written project runs headless without an editor pass to build the global class cache.
extends RefCounted


static func make_state(ir: Dictionary) -> Dictionary:
	var flags := {}
	for f in ir.get("flags", []):
		flags[f] = false
	var vars := {}
	for v in ir.get("variables", []):
		vars[v["id"]] = v["default"]
	return {"flags": flags, "vars": vars, "inv": []}


static func operand(state: Dictionary, val):
	if typeof(val) == TYPE_DICTIONARY and val.has("var"):
		return state["vars"][val["var"]]
	return val


static func eval_cond(state: Dictionary, cond) -> bool:
	if cond == null or (typeof(cond) == TYPE_DICTIONARY and cond.is_empty()):
		return true
	if cond.has("item"):
		return cond["item"] in state["inv"]
	if cond.has("flag"):
		return bool(state["flags"].get(cond["flag"], false))
	if cond.has("var"):
		return _cmp(cond["op"], state["vars"][cond["var"]], operand(state, cond["value"]))
	if cond.has("not"):
		return not eval_cond(state, cond["not"])
	if cond.has("all"):
		for c in cond["all"]:
			if not eval_cond(state, c):
				return false
		return true
	if cond.has("any"):
		for c in cond["any"]:
			if eval_cond(state, c):
				return true
		return false
	return true


static func _cmp(op: String, a, b) -> bool:
	match op:
		"==": return a == b
		"!=": return a != b
		"<": return a < b
		"<=": return a <= b
		">": return a > b
		">=": return a >= b
	return false


static func apply_effect(state: Dictionary, eff: Dictionary) -> void:
	if eff.has("set_flag"):
		state["flags"][eff["set_flag"]] = true
	elif eff.has("clear_flag"):
		state["flags"][eff["clear_flag"]] = false
	elif eff.has("add_item"):
		if not (eff["add_item"] in state["inv"]):
			state["inv"].append(eff["add_item"])
	elif eff.has("remove_item"):
		state["inv"].erase(eff["remove_item"])
	elif eff.has("set_var"):
		state["vars"][eff["set_var"]["var"]] = eff["set_var"]["value"]
	elif eff.has("add_var"):
		var k = eff["add_var"]["var"]
		state["vars"][k] = float(state["vars"].get(k, 0)) + eff["add_var"]["delta"]


static func apply_effects(state: Dictionary, effects) -> void:
	if effects == null:
		return
	for e in effects:
		apply_effect(state, e)
