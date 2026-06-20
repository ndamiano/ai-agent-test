/* Maestro web runtime — interprets a Game IR (game.json) in the browser.
 *
 * One static engine for every game: the project ships the engine-neutral IR as data and THIS
 * walks it. Covers visual_novel (dialogue graph) and point_and_click (rooms + hotspot verbs).
 * The condition/effect core is pure and exported for node tests; the DOM runtime boots only in
 * the browser.
 */
(function (global) {
  "use strict";

  // ── pure core (state · conditions · effects) ──────────────────────────────
  function makeState(ir) {
    const flags = {};
    (ir.flags || []).forEach(function (f) { flags[f] = false; });
    const vars = {};
    (ir.variables || []).forEach(function (v) { vars[v.id] = v.default; });
    return { flags: flags, vars: vars, inv: [] };
  }

  function operand(state, val) {
    return (val && typeof val === "object" && "var" in val) ? state.vars[val.var] : val;
  }

  var OPS = {
    "==": function (a, b) { return a === b; },
    "!=": function (a, b) { return a !== b; },
    "<": function (a, b) { return a < b; },
    "<=": function (a, b) { return a <= b; },
    ">": function (a, b) { return a > b; },
    ">=": function (a, b) { return a >= b; },
  };

  function evalCond(state, cond) {
    if (!cond) return true;
    if ("item" in cond) return state.inv.indexOf(cond.item) !== -1;
    if ("flag" in cond) return !!state.flags[cond.flag];
    if ("var" in cond) return OPS[cond.op](state.vars[cond.var], operand(state, cond.value));
    if ("not" in cond) return !evalCond(state, cond.not);
    if ("all" in cond) return cond.all.every(function (c) { return evalCond(state, c); });
    if ("any" in cond) return cond.any.some(function (c) { return evalCond(state, c); });
    return true;
  }

  function applyEffect(state, eff) {
    if ("set_flag" in eff) state.flags[eff.set_flag] = true;
    else if ("clear_flag" in eff) state.flags[eff.clear_flag] = false;
    else if ("add_item" in eff) { if (state.inv.indexOf(eff.add_item) === -1) state.inv.push(eff.add_item); }
    else if ("remove_item" in eff) { var i = state.inv.indexOf(eff.remove_item); if (i !== -1) state.inv.splice(i, 1); }
    else if ("set_var" in eff) state.vars[eff.set_var.var] = eff.set_var.value;
    else if ("add_var" in eff) state.vars[eff.add_var.var] = (state.vars[eff.add_var.var] || 0) + eff.add_var.delta;
  }

  function applyEffects(state, effects) { (effects || []).forEach(function (e) { applyEffect(state, e); }); }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { makeState: makeState, evalCond: evalCond, applyEffect: applyEffect, applyEffects: applyEffects };
    return; // node/jsdom unit context — do not boot the DOM runtime
  }

  // ── browser runtime ───────────────────────────────────────────────────────
  var ir, state, chars = {}, nodeById = {}, placeById = {}, bgFiles = {};
  var $ = function (id) { return document.getElementById(id); };
  var WIN = { win: true };

  function hue(s) { var h = 0; for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function charName(id) { return (chars[id] && chars[id].name) || id; }
  function charColor(id) { return "hsl(" + hue(id) + ",70%,72%)"; }

  function bgUrl(id) { return "images/" + (bgFiles[id] || (id + ".png")); }
  function setScene(id) {
    var el = $("scene");
    if (!id) { el.style.backgroundImage = "none"; el.style.background = "#1a1a2e"; return; }
    var img = new Image();
    img.onload = function () { el.style.backgroundImage = "url('" + bgUrl(id) + "')"; };
    img.onerror = function () { el.style.backgroundImage = "none"; el.style.background = "hsl(" + hue(id) + ",30%,22%)"; };
    img.src = bgUrl(id);
  }

  // ── presenters (each returns a promise resolved by player input) ───────────
  function showLine(speaker, text) {
    var box = $("dialogue");
    $("speaker").textContent = speaker == null ? "" : charName(speaker);
    $("speaker").style.color = speaker == null ? "#fff" : charColor(speaker);
    $("text").textContent = text;
    box.classList.remove("hidden");
    return new Promise(function (resolve) {
      box.onclick = function () { box.onclick = null; resolve(); };
    });
  }

  function showMenu(choices) {
    var menu = $("menu");
    menu.innerHTML = "";
    menu.classList.remove("hidden");
    return new Promise(function (resolve) {
      choices.forEach(function (ch, i) {
        var b = document.createElement("div");
        b.className = "choice";
        b.textContent = ch.text;
        b.onclick = function () { menu.classList.add("hidden"); menu.innerHTML = ""; resolve(i); };
        menu.appendChild(b);
      });
    });
  }

  function showEnding(label) {
    var e = $("ending");
    e.innerHTML = "<div>The End</div>" + (label ? "<div class='sub'>" + label + "</div>" : "");
    e.classList.remove("hidden");
    $("dialogue").classList.add("hidden");
  }

  function hideDialogue() { $("dialogue").classList.add("hidden"); }

  // ── visual novel ──────────────────────────────────────────────────────────
  function vnRoster(node) {
    var seen = [], out = [];
    (node.lines || []).forEach(function (l) {
      if (l.speaker && chars[l.speaker] && chars[l.speaker].sprite && seen.indexOf(l.speaker) === -1) {
        seen.push(l.speaker); out.push(l.speaker);
      }
    });
    return out;
  }

  function stageSprites(node) {
    var box = $("sprites");
    box.innerHTML = "";
    var roster = vnRoster(node);
    roster.forEach(function (cid, i) {
      var img = document.createElement("img");
      img.className = "sprite";
      img.dataset.cid = cid;
      img.style.left = ((i + 1) / (roster.length + 1) * 100) + "%";
      img.onerror = function () { img.style.visibility = "hidden"; };
      img.src = "images/" + chars[cid].sprite;
      box.appendChild(img);
    });
  }

  function highlightSpeaker(speaker) {
    var sprites = $("sprites").children;
    if (sprites.length < 2) return;
    for (var i = 0; i < sprites.length; i++) {
      sprites[i].style.opacity = (sprites[i].dataset.cid === speaker) ? "1" : "0.45";
    }
  }

  async function playNode(startId) {
    var id = startId;
    while (id) {
      var node = nodeById[id];
      if (node.location) setScene(node.location);
      stageSprites(node);
      var lines = node.lines || [];
      for (var i = 0; i < lines.length; i++) {
        highlightSpeaker(lines[i].speaker);
        await showLine(lines[i].speaker, lines[i].text);
        applyEffects(state, lines[i].effects);
      }
      var end = node.end || {};
      if (end.type === "jump") { id = end.target; }
      else if (end.type === "menu") {
        var open = (end.choices || []).filter(function (c) { return evalCond(state, c.requires); });
        var pick = open[await showMenu(open.map(function (c) { return { text: c.text }; }))];
        applyEffects(state, pick.effects);
        id = pick.target;
      }
      else if (end.type === "return") { hideDialogue(); return; }
      else { showEnding(end.ending); return; }
    }
  }

  // ── point and click ───────────────────────────────────────────────────────
  function renderInventory() {
    var bar = $("inventory");
    bar.innerHTML = "";
    state.inv.forEach(function (iid) {
      var item = (ir.items || []).find(function (it) { return it.id === iid; }) || { id: iid, name: iid };
      var cell = document.createElement("div");
      cell.className = "inv-item";
      var img = document.createElement("img");
      img.onerror = function () { img.style.display = "none"; };
      img.src = "images/" + iid + ".png";
      cell.appendChild(img);
      cell.appendChild(document.createTextNode(item.name || iid));
      bar.appendChild(cell);
    });
  }

  function awaitHotspot(place) {
    var box = $("hotspots");
    box.innerHTML = "";
    return new Promise(function (resolve) {
      (place.interactables || []).forEach(function (it) {
        var r = (it.position && it.position.rect) || { x: 0, y: 0, w: 0, h: 0 };
        var hs = document.createElement("div");
        hs.className = "hotspot";
        hs.style.left = r.x + "px"; hs.style.top = r.y + "px";
        hs.style.width = r.w + "px"; hs.style.height = r.h + "px";
        var lbl = document.createElement("div");
        lbl.className = "label"; lbl.textContent = it.label || "";
        hs.appendChild(lbl);
        hs.onclick = function () { box.innerHTML = ""; resolve(it); };
        box.appendChild(hs);
      });
    });
  }

  async function runAction(act) {
    switch (act.type) {
      case "examine":
        await showLine(null, act.text); hideDialogue(); return null;
      case "take":
        if (state.inv.indexOf(act.item) === -1) state.inv.push(act.item);
        if (act.text) { await showLine(null, act.text); hideDialogue(); }
        return null;
      case "talk":
        await playNode(act.node); return null;
      case "move":
        if (act.requires && !evalCond(state, act.requires)) {
          await showLine(null, "You can't go that way yet."); hideDialogue(); return null;
        }
        return { move: act.target };
      case "use": {
        var outcome = act.fallback;
        for (var i = 0; i < (act.clauses || []).length; i++) {
          if (evalCond(state, act.clauses[i].requires)) { outcome = act.clauses[i].outcome; break; }
        }
        if (outcome) { applyEffects(state, outcome.effects); if (outcome.text) { await showLine(null, outcome.text); hideDialogue(); } }
        return null;
      }
      case "win": {
        var gate = act.requires || ir.goal;
        if (gate && !evalCond(state, gate)) { await showLine(null, "Not yet."); hideDialogue(); return null; }
        return WIN;
      }
      case "start_combat":
        await showLine(null, "[Combat is not available in this build yet.]"); hideDialogue(); return null;
      default:
        return null;
    }
  }

  async function runPlace(placeId) {
    var place = placeById[placeId];
    setScene(place.background);
    $("sprites").innerHTML = "";
    while (true) {
      renderInventory();
      var it = await awaitHotspot(place);
      var r = await runAction(it.action);
      if (r === WIN) return WIN;
      if (r && r.move) return r.move;
      setScene(place.background); // talk/examine may have changed the scene
    }
  }

  async function runPnc() {
    var placeId = ir.start.place;
    while (true) {
      var next = await runPlace(placeId);
      if (next === WIN) { showEnding(ir.goal ? "escaped" : null); return; }
      placeId = next;
    }
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  function index() {
    (ir.characters || []).forEach(function (c) { chars[c.id] = c; });
    (ir.nodes || []).forEach(function (n) { nodeById[n.id] = n; });
    (ir.places || []).forEach(function (p) { placeById[p.id] = p; });
    (ir.backgrounds || []).forEach(function (b) { bgFiles[b.id] = b.image_file; });
  }

  function fit() {
    var s = Math.min(global.innerWidth / 1280, global.innerHeight / 720);
    $("stage").style.transform = "scale(" + s + ")";
  }

  async function boot() {
    ir = await fetch("game.json").then(function (r) { return r.json(); });
    state = makeState(ir);
    index();
    fit();
    global.addEventListener("resize", fit);
    if (ir.genre === "point_and_click") await runPnc();
    else if (ir.start && ir.start.node) await playNode(ir.start.node);
  }

  global.addEventListener("DOMContentLoaded", boot);
})(typeof window !== "undefined" ? window : this);
