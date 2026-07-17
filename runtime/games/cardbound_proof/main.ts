// Hand-authored SEAM PROOF: 3D overworld + contact-triggered CARD COMBAT sub-mode + card shop.
// Proves: state.mode switch, menu-driven card battle over the 3D scene, deck persistence, shop
// buys cards, asymmetric enemy decks. The distilled version is the kit_api_3d worked example.

interface Card { name: string; attack: number; defense: number; cost?: number }
interface Combatant { hp: number; maxHp: number; deck: Card[]; hand: Card[]; discard: Card[] }

const CARD_POOL: Record<string, Card> = {
  strike: { name: "Strike", attack: 4, defense: 0, cost: 0 },
  guard: { name: "Guard", attack: 0, defense: 4, cost: 0 },
  cleave: { name: "Cleave", attack: 7, defense: 0, cost: 12 },
  bulwark: { name: "Bulwark", attack: 0, defense: 9, cost: 10 },
  fang: { name: "Fang", attack: 3, defense: 0 },
  howl: { name: "Howl", attack: 0, defense: 3 },
  crush: { name: "Crush", attack: 6, defense: 0 },
};

function mk(card: Card): Card { return { ...card }; }

export function createGame(kit: Kit) {
  return {
    config: { mode: "3d", controls: "orbital", width: 1280, height: 720, background: "#101018", seed: 3, fog: 90 },
    state: {
      world: [] as Kit.Entity[],
      mode: "world" as "world" | "combat",
      player: null as any,
      npcs: [] as any[],
      enemies: [] as any[],
      gold: 15,
      deck: [mk(CARD_POOL.strike), mk(CARD_POOL.strike), mk(CARD_POOL.strike), mk(CARD_POOL.guard), mk(CARD_POOL.guard), mk(CARD_POOL.strike)],
      hp: 30, maxHp: 30,
      combat: null as null | { enemy: any; me: Combatant; foe: Combatant; selected: number; log: string; over: boolean },
      slain: 0,
      t: 0,
      talk: null as any,
    },

    init(kit: Kit) {
      const w = this.state.world;
      kit.spawn(w, { shape: "ground", size: 120, color: "#2c4a2e" });
      // a tiny hamlet: two huts + the card vendor's stall
      for (const [x, z, c] of [[-8, -6, "#7a5a3a"], [6, -9, "#8a6a4a"]] as any) {
        kit.spawn(w, { shape: "box", x, y: 1.5, z, w: 4, h: 3, d: 4, color: c });
        kit.spawn(w, { shape: "box", x, y: 3.4, z, w: 5, h: 0.8, d: 5, color: "#5a3a26" });
      }
      kit.spawn(w, { shape: "box", x: 0, y: 1, z: -12, w: 3, h: 2, d: 2, color: "#a8842e" }); // stall
      for (let i = 0; i < 14; i++) { // tree ring
        const a = (i / 14) * Math.PI * 2, r = 34 + kit.rng.range(0, 14);
        const tx = Math.cos(a) * r, tz = Math.sin(a) * r;
        kit.spawn(w, { shape: "box", x: tx, y: 1.5, z: tz, w: 0.7, h: 3, d: 0.7, color: "#4a3220" });
        kit.spawn(w, { shape: "sphere", x: tx, y: 3.6, z: tz, r: 1.6, color: "#2f6a33" });
      }
      this.state.player = kit.spawn(w, { shape: "box", x: 0, y: 0.9, z: 0, w: 0.8, h: 1.7, d: 0.8, color: "#2a4a7a" });
      // the vendor (an npc, not an enemy)
      this.state.npcs.push(Object.assign(kit.spawn(w, { shape: "box", x: 0, y: 0.85, z: -10.5, w: 0.7, h: 1.6, d: 0.7, color: "#c8b04a" }), {
        name: "Card Vendor",
        lines: ["Fresh cards, forged sharp.", "Cleave 12g. Bulwark 10g."],
        options: ["Buy Cleave (12g)", "Buy Bulwark (10g)", "Leave"],
        role: "vendor",
      }));
      // ASYMMETRIC enemy decks: wolves bleed you fast, the brute hits like a wall
      const wolfDeck = () => [mk(CARD_POOL.fang), mk(CARD_POOL.fang), mk(CARD_POOL.fang), mk(CARD_POOL.howl), mk(CARD_POOL.fang)];
      const bruteDeck = () => [mk(CARD_POOL.crush), mk(CARD_POOL.guard), mk(CARD_POOL.crush), mk(CARD_POOL.crush)];
      for (const [x, z] of [[18, 10], [-16, 14]] as any) {
        this.state.enemies.push(Object.assign(kit.spawn(w, { shape: "box", x, y: 0.7, z, w: 1.1, h: 1.1, d: 2.2, color: "#6a6a72" }), {
          kind: "wolf", hp: 12, deck: wolfDeck, homeX: x, homeZ: z,
        }));
      }
      this.state.enemies.push(Object.assign(kit.spawn(w, { shape: "box", x: 0, y: 1.4, z: 34, w: 2.2, h: 2.8, d: 2.2, color: "#7a3a3a" }), {
        kind: "brute", hp: 16, deck: bruteDeck, homeX: 0, homeZ: 34,
      }));
    },

    enterCombat(kit: Kit, enemy: any) {
      const deal = (c: Combatant) => { for (let i = 0; i < 3 && c.deck.length; i++) c.hand.push(c.deck.pop()!); };
      const me: Combatant = { hp: this.state.hp, maxHp: this.state.maxHp, deck: kit.rng.shuffle(this.state.deck.map(mk)), hand: [], discard: [] };
      const foe: Combatant = { hp: enemy.hp, maxHp: enemy.hp, deck: kit.rng.shuffle(enemy.deck()), hand: [], discard: [] };
      deal(me); deal(foe);
      this.state.combat = { enemy, me, foe, selected: 0, log: `A ${enemy.kind} attacks!`, over: false };
      this.state.mode = "combat";
    },

    draw3(kit: Kit, c: Combatant) {
      if (!c.hand.length) {
        if (!c.deck.length) { c.deck = kit.rng.shuffle(c.discard); c.discard = []; }
        for (let i = 0; i < 3 && c.deck.length; i++) c.hand.push(c.deck.pop()!);
      }
    },

    playCard(kit: Kit, cb: { attacker: Combatant; defender: Combatant; idx: number; who: string }) {
      const { attacker, defender, idx, who } = cb;
      const card = attacker.hand.splice(idx, 1)[0];
      attacker.discard.push(card);
      if (card.attack > 0) { defender.hp -= card.attack; return `${who} ${card.name}: ${card.attack} dmg`; }
      attacker.hp = Math.min(attacker.maxHp, attacker.hp + card.defense);
      return `${who} ${card.name}: +${card.defense} hp`;
    },

    update(dt: number, input: Input, kit: Kit) {
      const s = this.state;
      s.t += dt;

      if (s.mode === "combat") {
        const c = s.combat!;
        if (c.over) return;
        const n = c.me.hand.length;
        if (n > 0) {
          if (input.pressed("ArrowLeft")) c.selected = (c.selected - 1 + n) % n;
          if (input.pressed("ArrowRight")) c.selected = (c.selected + 1) % n;
          if (input.pressed(" ")) {
            let log = this.playCard(kit, { attacker: c.me, defender: c.foe, idx: Math.min(c.selected, n - 1), who: "You:" });
            if (c.foe.hp > 0 && c.foe.hand.length) {
              log += "  " + this.playCard(kit, { attacker: c.foe, defender: c.me, idx: kit.rng.int(0, c.foe.hand.length - 1), who: "Foe:" });
            }
            c.log = log;
            this.draw3(kit, c.me); this.draw3(kit, c.foe);
            c.selected = Math.min(c.selected, Math.max(0, c.me.hand.length - 1));
            if (c.foe.hp <= 0) {
              c.over = true; c.log = `The ${c.enemy.kind} falls! +10 gold`;
              s.gold += 10; s.slain++;
              c.enemy.dead = true; kit.cull(s.world);
              s.enemies = s.enemies.filter((e: any) => e !== c.enemy);
              kit.notify(`Slew the ${c.enemy.kind} (+10g)`);
            } else if (c.me.hp <= 0) {
              c.over = true;
              kit.lose("You were slain.");
            }
          }
        }
        if (c.over && c.foe.hp <= 0) {
          s.hp = c.me.hp;                       // wounds persist into the world
          s.mode = "world"; s.combat = null;
          if (s.slain === 3) kit.win("The wilds are safe — all three beasts slain!");
        }
        return;                                  // no movement while in combat
      }

      // ---- world mode ----
      const pick = kit.talkStep(s, input);
      if (pick && pick.npc.role === "vendor") {
        const buy = (card: Card) => {
          if (s.gold >= (card.cost || 0)) { s.gold -= card.cost || 0; s.deck.push(mk(card)); kit.notify(`Bought ${card.name}`); }
          else kit.notify("Not enough gold");
        };
        if (pick.pick === 0) buy(CARD_POOL.cleave);
        if (pick.pick === 1) buy(CARD_POOL.bulwark);
      }
      if (s.talk) return;                        // stand still while trading

      kit.drive(s.player, input, dt, 8);
      s.player.x = kit.V.clamp(s.player.x, -55, 55);
      s.player.z = kit.V.clamp(s.player.z, -55, 55);
      kit.avoidRects(s.player, [{ x: -8, z: -6, w: 4, d: 4 }, { x: 6, z: -9, w: 4, d: 4 }, { x: 0, z: -12, w: 3, d: 2 }]);

      const near = s.npcs.find((v: any) => Math.hypot(v.x - s.player.x, v.z - s.player.z) < 3);
      if (near && input.pressed("e")) kit.talkOpen(s, near);

      for (const e of s.enemies) {
        if (Math.hypot(e.x - s.player.x, e.z - s.player.z) < 12) kit.seek3(e, s.player, 3, dt);
        else if (Math.hypot(e.x - e.homeX, e.z - e.homeZ) > 8) kit.seek3(e, { x: e.homeX, z: e.homeZ }, 2, dt);
        else kit.wander3(e, 1.2, dt, kit.rng);
        if (Math.hypot(e.x - s.player.x, e.z - s.player.z) < 1.6) { this.enterCombat(kit, e); return; }
      }
    },

    hud(kit: Kit): Kit.HudItem[] {
      const s = this.state;
      if (s.mode === "combat" && s.combat) {
        const c = s.combat;
        return [
          { kind: "bar", value: c.me.hp, max: c.me.maxHp, at: "bottom-left", color: "#4a8", label: "You" },
          { kind: "bar", value: c.foe.hp, max: c.foe.maxHp, at: "top-right", color: "#e44", label: c.enemy.kind },
          { kind: "panel", text: c.log, title: "Battle", at: "top" },
          { kind: "menu", title: "Your hand  (←/→ pick, SPACE play)", at: "bottom",
            options: c.me.hand.map((k) => `${k.name}  ${k.attack ? `⚔${k.attack}` : `♥+${k.defense}`}`),
            selected: c.selected },
        ];
      }
      const nearest = s.enemies[0];
      return [
        { kind: "text", text: `Gold: ${s.gold}   Deck: ${s.deck.length} cards   HP: ${s.hp}/${s.maxHp}`, at: "top-left" },
        { kind: "text", text: "WASD move · walk INTO a beast to fight · E to trade at the stall", at: "bottom" },
        ...(nearest ? [{ kind: "marker", x: nearest.x, z: nearest.z, text: nearest.kind, color: "#e66" } as Kit.HudItem] : []),
        ...kit.talkHud(s),
      ];
    },
  };
}
