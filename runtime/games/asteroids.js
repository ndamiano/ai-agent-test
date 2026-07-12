export function createGame(kit) {
  const PLAYER_SIZE = 20;
  const BULLET_SIZE = 4;
  const ASTEROID_MIN_SIZE = 30;
  const ASTEROID_MAX_SIZE = 60;
  const PLAYER_SPEED = 200;
  const BULLET_SPEED = 400;
  const ASTEROID_MIN_SPEED = 20;
  const ASTEROID_MAX_SPEED = 80;
  const MAX_ASTEROIDS = 10;
  const INITIAL_ASTEROID_COUNT = 5;
  const PLAYER_LIVES = 3;
  const ROTATION_SPEED = 3;

  return {
    config: {
      width: 800,
      height: 600,
      title: "Classic Asteroids",
      background: "#000",
      gravity: 0,
      seed: 1
    },

    state: {
      world: [],
      player: null,
      lives: PLAYER_LIVES,
      score: 0,
      level: 1,
      gameOver: false,
      asteroidCount: 0
    },

    init(kit) {
      this.state.player = kit.spawn(this.state.world, {
        x: this.config.width / 2,
        y: this.config.height / 2,
        vx: 0,
        vy: 0,
        w: PLAYER_SIZE,
        h: PLAYER_SIZE,
        angle: -Math.PI / 2,
        thrusting: false,
        dead: false
      });

      // Spawn initial asteroids
      for (let i = 0; i < INITIAL_ASTEROID_COUNT; i++) {
        this.spawnAsteroid(kit);
      }
    },

    update(dt, input, kit) {
      if (this.state.gameOver) return;

      // Update player
      const player = this.state.player;
      if (player && !player.dead) {
        // Rotation
        if (input.down("ArrowLeft")) {
          player.angle -= ROTATION_SPEED * dt;
        }
        if (input.down("ArrowRight")) {
          player.angle += ROTATION_SPEED * dt;
        }

        // Thrust
        player.thrusting = false;
        if (input.down("ArrowUp")) {
          player.thrusting = true;
          player.vx += Math.cos(player.angle) * PLAYER_SPEED * dt;
          player.vy += Math.sin(player.angle) * PLAYER_SPEED * dt;
        }

        // Fire bullet
        if (input.pressed(" ")) {
          this.fireBullet(kit);
        }

        // Apply gravity (none)
        kit.integrate(player, dt, 0);

        // Screen wrap
        if (player.x < -player.w) player.x = this.config.width;
        if (player.x > this.config.width + player.w) player.x = 0;
        if (player.y < -player.h) player.y = this.config.height;
        if (player.y > this.config.height + player.h) player.y = 0;
      }

      // Update bullets
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const entity = this.state.world[i];
        if (entity.type === "bullet") {
          kit.integrate(entity, dt, 0);
          
          // Screen wrap bullets
          if (entity.x < -entity.w) entity.x = this.config.width;
          if (entity.x > this.config.width + entity.w) entity.x = 0;
          if (entity.y < -entity.h) entity.y = this.config.height;
          if (entity.y > this.config.height + entity.h) entity.y = 0;
        }
      }

      // Update asteroids
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const entity = this.state.world[i];
        if (entity.type === "asteroid") {
          kit.integrate(entity, dt, 0);
          
          // Screen wrap asteroids
          if (entity.x < -entity.w) entity.x = this.config.width;
          if (entity.x > this.config.width + entity.w) entity.x = 0;
          if (entity.y < -entity.h) entity.y = this.config.height;
          if (entity.y > this.config.height + entity.h) entity.y = 0;
        }
      }

      // Check collisions
      this.checkCollisions(kit);

      // Spawn new asteroids if needed
      if (this.state.asteroidCount < MAX_ASTEROIDS) {
        if (kit.rng.chance(0.005 * this.state.level)) {
          this.spawnAsteroid(kit);
        }
      }

      // Increase difficulty
      this.state.level += 0.0001;
    },

    draw(g, kit) {
      // Draw player
      const player = this.state.player;
      if (player && !player.dead) {
        const halfSize = player.w / 2;
        const points = [
          { x: player.x + Math.cos(player.angle) * halfSize, y: player.y + Math.sin(player.angle) * halfSize },
          { x: player.x + Math.cos(player.angle + 2.5) * halfSize, y: player.y + Math.sin(player.angle + 2.5) * halfSize },
          { x: player.x + Math.cos(player.angle - 2.5) * halfSize, y: player.y + Math.sin(player.angle - 2.5) * halfSize }
        ];
        
        g.line(points[0].x, points[0].y, points[1].x, points[1].y, "#fff", 2);
        g.line(points[1].x, points[1].y, points[2].x, points[2].y, "#fff", 2);
        g.line(points[2].x, points[2].y, points[0].x, points[0].y, "#fff", 2);
        
        // Draw thrust
        if (player.thrusting) {
          const thrustX = player.x - Math.cos(player.angle) * halfSize * 0.7;
          const thrustY = player.y - Math.sin(player.angle) * halfSize * 0.7;
          g.line(player.x, player.y, thrustX, thrustY, "#ff8800", 2);
        }
      }

      // Draw bullets
      for (const entity of this.state.world) {
        if (entity.type === "bullet") {
          g.circle(entity.x, entity.y, entity.w / 2, "#fff");
        }
      }

      // Draw asteroids
      for (const entity of this.state.world) {
        if (entity.type === "asteroid") {
          g.circle(entity.x, entity.y, entity.w / 2, "#888");
        }
      }

      // Draw UI
      g.text(`Score: ${this.state.score}`, 10, 20, "#fff");
      g.text(`Lives: ${this.state.lives}`, 10, 40, "#fff");
      g.text(`Level: ${Math.floor(this.state.level)}`, 10, 60, "#fff");
    },

    fireBullet(kit) {
      const player = this.state.player;
      if (!player || player.dead) return;

      const bullet = kit.spawn(this.state.world, {
        x: player.x,
        y: player.y,
        vx: Math.cos(player.angle) * BULLET_SPEED,
        vy: Math.sin(player.angle) * BULLET_SPEED,
        w: BULLET_SIZE,
        h: BULLET_SIZE,
        type: "bullet",
        dead: false
      });
    },

    spawnAsteroid(kit) {
      const size = kit.rng.range(ASTEROID_MIN_SIZE, ASTEROID_MAX_SIZE);
      const speed = kit.rng.range(ASTEROID_MIN_SPEED, ASTEROID_MAX_SPEED);
      
      // Spawn at screen edge
      let x, y, vx, vy;
      const edge = kit.rng.int(0, 3);
      
      switch (edge) {
        case 0: // top
          x = kit.rng.range(0, this.config.width);
          y = -size;
          vx = kit.rng.range(-speed, speed);
          vy = kit.rng.range(0, speed);
          break;
        case 1: // right
          x = this.config.width + size;
          y = kit.rng.range(0, this.config.height);
          vx = kit.rng.range(-speed, 0);
          vy = kit.rng.range(-speed, speed);
          break;
        case 2: // bottom
          x = kit.rng.range(0, this.config.width);
          y = this.config.height + size;
          vx = kit.rng.range(-speed, speed);
          vy = kit.rng.range(-speed, 0);
          break;
        case 3: // left
          x = -size;
          y = kit.rng.range(0, this.config.height);
          vx = kit.rng.range(0, speed);
          vy = kit.rng.range(-speed, speed);
          break;
      }
      
      kit.spawn(this.state.world, {
        x: x,
        y: y,
        vx: vx,
        vy: vy,
        w: size,
        h: size,
        type: "asteroid",
        dead: false
      });
      
      this.state.asteroidCount++;
    },

    checkCollisions(kit) {
      const player = this.state.player;
      
      // Bullet-asteroid collisions
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const bullet = this.state.world[i];
        if (bullet.type !== "bullet") continue;
        
        for (let j = this.state.world.length - 1; j >= 0; j--) {
          const asteroid = this.state.world[j];
          if (asteroid.type !== "asteroid") continue;
          
          if (kit.aabb(bullet, asteroid)) {
            // Remove bullet and asteroid
            bullet.dead = true;
            asteroid.dead = true;
            
            // Split asteroid
            if (asteroid.w > ASTEROID_MIN_SIZE * 1.5) {
              this.splitAsteroid(kit, asteroid);
            }
            
            // Update score
            const points = Math.floor(100 * (ASTEROID_MAX_SIZE - asteroid.w) / (ASTEROID_MAX_SIZE - ASTEROID_MIN_SIZE));
            this.state.score += points;
            
            this.state.asteroidCount--;
            break;
          }
        }
      }
      
      // Player-asteroid collisions
      if (player && !player.dead) {
        for (const asteroid of this.state.world) {
          if (asteroid.type === "asteroid" && kit.aabb(player, asteroid)) {
            // Player hit
            player.dead = true;
            this.state.lives--;
            
            if (this.state.lives <= 0) {
              kit.lose("Game Over! Final Score: " + this.state.score);
              this.state.gameOver = true;
            } else {
              // Reset player position
              player.x = this.config.width / 2;
              player.y = this.config.height / 2;
              player.vx = 0;
              player.vy = 0;
              player.dead = false;
            }
            break;
          }
        }
      }
      
      // Clean up dead entities
      kit.cull(this.state.world);
    },

    splitAsteroid(kit, asteroid) {
      const newSize = asteroid.w * 0.6;
      const speed = kit.rng.range(ASTEROID_MIN_SPEED, ASTEROID_MAX_SPEED * 1.5);
      
      for (let i = 0; i < 2; i++) {
        const angle = kit.rng.range(0, Math.PI * 2);
        kit.spawn(this.state.world, {
          x: asteroid.x,
          y: asteroid.y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed,
          w: newSize,
          h: newSize,
          type: "asteroid",
          dead: false
        });
      }
      
      this.state.asteroidCount += 2;
    }
  };
}