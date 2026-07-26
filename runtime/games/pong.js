// pong.js — hand-written demo. Proves the kit surface is real and composable.
// This is the SHAPE a generated game takes: config + state + init/update/hud. Nothing draws: the
// entities in state.world carry their own look and the engine renders them.

export function createGame(kit) {
  return {
    config: { width: 640, height: 480, title: "Pong", background: "#0a0a12", seed: 7 },
    state: { world: [], ball: null, player: null, cpu: null, score: [0, 0] },

    init(kit) {
      const s = this.state;
      kit.spawn(s.world, { x: 319, y: 0, w: 2, h: 480, color: "#222", layer: -1 });   // net
      s.player = kit.spawn(s.world, { x: 20, y: 200, w: 12, h: 80, color: "#e8e8f0", tag: "paddle" });
      s.cpu = kit.spawn(s.world, { x: 608, y: 200, w: 12, h: 80, color: "#e8e8f0", tag: "paddle" });
      s.ball = kit.spawn(s.world, { x: 316, y: 236, w: 8, h: 8, vx: 220, vy: 140, color: "#e8e8f0", tag: "ball" });
    },

    update(dt, input, kit) {
      const s = this.state, { player, cpu, ball } = s, cfg = this.config;
      // player paddle
      if (input.down("w") || input.down("ArrowUp")) player.y -= 320 * dt;
      if (input.down("s") || input.down("ArrowDown")) player.y += 320 * dt;
      player.y = kit.V.clamp(player.y, 0, cfg.height - player.h);
      // cpu tracks the ball with a speed cap (so it's beatable)
      const target = ball.y - cpu.h / 2 + ball.h / 2;
      cpu.y += kit.V.clamp(target - cpu.y, -260 * dt, 260 * dt);
      cpu.y = kit.V.clamp(cpu.y, 0, cfg.height - cpu.h);
      // ball
      kit.integrate(ball, dt);
      if (ball.y <= 0 || ball.y + ball.h >= cfg.height) { ball.vy *= -1; ball.y = kit.V.clamp(ball.y, 0, cfg.height - ball.h); }
      for (const p of [player, cpu]) {
        if (kit.aabb(ball, p)) {
          ball.vx = Math.abs(ball.vx) * (p === player ? 1 : -1) * 1.03;
          ball.vy += (ball.y - (p.y + p.h / 2)) * 3;
        }
      }
      // scoring
      if (ball.x < 0) { s.score[1]++; this._serve(kit, -1); }
      if (ball.x > cfg.width) { s.score[0]++; this._serve(kit, 1); }
      if (s.score[0] >= 5) kit.win("Player wins");
      if (s.score[1] >= 5) kit.lose("CPU wins");
    },

    _serve(kit, dir) {
      const b = this.state.ball;
      b.x = 316; b.y = 236; b.vx = 220 * dir; b.vy = kit.rng.range(-160, 160);
    },

    hud(kit) {
      const s = this.state;
      return [{ kind: "text", text: `${s.score[0]}`, at: "top", color: "#8ff", size: 40 },
              { kind: "text", text: `${s.score[1]}`, at: "top-right", color: "#f8f", size: 40 }];
    },
  };
}
