export function createGame(kit) {
  const WORLD_WIDTH = 2000;
  const WORLD_HEIGHT = 600;
  const PLAYER_SIZE = 20;
  const ENEMY_SIZE = 20;
  const COIN_SIZE = 10;
  const PIT_WIDTH = 20;
  const FLAG_WIDTH = 15;
  const FLAG_HEIGHT = 40;
  
  return {
    config: {
      width: 800,
      height: 600,
      title: "Platform Runner",
      background: "#111",
      gravity: 2000,
      seed: 1
    },
    
    state: {
      world: [],
      platforms: [],
      lives: 3,
      score: 0,
      gameOver: false,
      player: null,
      cam: null
    },
    
    init(kit) {
      this.state.cam = kit.makeCamera();
      
      // Create platforms
      this.state.platforms = [];
      const platformCount = 30;
      for (let i = 0; i < platformCount; i++) {
        const width = kit.rng.range(50, 200);
        const height = kit.rng.range(20, 80);
        const x = kit.rng.range(0, WORLD_WIDTH - width);
        const y = kit.rng.range(100, WORLD_HEIGHT - height - 50);
        
        this.state.platforms.push({
          x: x,
          y: y,
          w: width,
          h: height
        });
      }
      
      // Add some pits
      const pitCount = 15;
      for (let i = 0; i < pitCount; i++) {
        const x = kit.rng.range(0, WORLD_WIDTH - PIT_WIDTH);
        const y = kit.rng.range(100, WORLD_HEIGHT - 50);
        
        this.state.platforms.push({
          x: x,
          y: y,
          w: PIT_WIDTH,
          h: WORLD_HEIGHT - y
        });
      }
      
      // Create player
      this.state.player = kit.spawn(this.state.world, {
        x: 100,
        y: 300,
        w: PLAYER_SIZE,
        h: PLAYER_SIZE,
        vx: 0,
        vy: 0,
        grounded: false,
        lives: 3
      });
      
      // Create enemies
      const enemyCount = 8;
      for (let i = 0; i < enemyCount; i++) {
        const platform = kit.rng.pick(this.state.platforms);
        const x = platform.x + kit.rng.range(0, platform.w - ENEMY_SIZE);
        const y = platform.y - ENEMY_SIZE;
        
        kit.spawn(this.state.world, {
          x: x,
          y: y,
          w: ENEMY_SIZE,
          h: ENEMY_SIZE,
          vx: kit.rng.pick([-50, 50]),
          vy: 0,
          grounded: true,
          type: 'enemy'
        });
      }
      
      // Create coins
      const coinCount = 25;
      for (let i = 0; i < coinCount; i++) {
        const platform = kit.rng.pick(this.state.platforms);
        const x = platform.x + kit.rng.range(0, platform.w - COIN_SIZE);
        const y = platform.y - COIN_SIZE;
        
        kit.spawn(this.state.world, {
          x: x,
          y: y,
          w: COIN_SIZE,
          h: COIN_SIZE,
          collected: false,
          type: 'coin'
        });
      }
      
      // Create flag at end
      kit.spawn(this.state.world, {
        x: WORLD_WIDTH - FLAG_WIDTH - 20,
        y: WORLD_HEIGHT - FLAG_HEIGHT - 20,
        w: FLAG_WIDTH,
        h: FLAG_HEIGHT,
        type: 'flag'
      });
      
      // Add ground
      this.state.platforms.push({
        x: 0,
        y: WORLD_HEIGHT - 20,
        w: WORLD_WIDTH,
        h: 20
      });
    },
    
    update(dt, input, kit) {
      if (this.state.gameOver) return;
      
      const player = this.state.player;
      
      // Handle input
      if (input.down("ArrowLeft")) {
        kit.walk(player, -1, 150);
      } else if (input.down("ArrowRight")) {
        kit.walk(player, 1, 150);
      } else {
        kit.walk(player, 0, 150);
      }
      
      if (input.pressed(" ")) {
        kit.jump(player, 600);
      }
      
      // Update player physics
      kit.physics(player, dt, this.state.platforms, 2000);
      
      // Screen wrap player horizontally
      if (player.x < 0) player.x = 0;
      if (player.x > WORLD_WIDTH - player.w) player.x = WORLD_WIDTH - player.w;
      
      // Update enemies
      this.state.world.forEach(entity => {
        if (entity.type === 'enemy') {
          // Simple patrol - reverse direction at platform edges
          const platform = this.state.platforms.find(p => 
            p.y <= entity.y + entity.h && 
            p.y + p.h >= entity.y &&
            p.x <= entity.x + entity.w && 
            p.x + p.w >= entity.x
          );
          
          if (platform) {
            // Check if at edge of platform
            if (entity.x <= platform.x || entity.x + entity.w >= platform.x + platform.w) {
              entity.vx = -entity.vx;
            }
          }
          
          // Apply physics
          kit.physics(entity, dt, this.state.platforms, 2000);
        }
      });
      
      // Update coins
      this.state.world.forEach(entity => {
        if (entity.type === 'coin' && !entity.collected) {
          // Simple coin collection
          if (kit.aabb(player, entity)) {
            entity.collected = true;
            this.state.score += 10;
          }
        }
      });
      
      // Check collisions
      this.state.world.forEach(entity => {
        if (entity.type === 'enemy') {
          if (kit.aabb(player, entity)) {
            // Check if player is above enemy (stomp)
            if (player.vy > 0 && player.y < entity.y) {
              // Player stomps enemy
              entity.dead = true;
              player.vy = -300; // Bounce
              this.state.score += 100;
            } else {
              // Player hit by enemy
              player.lives--;
              if (player.lives <= 0) {
                kit.lose("Game Over! You ran out of lives.");
                this.state.gameOver = true;
              } else {
                // Reset player position
                player.x = 100;
                player.y = 300;
                player.vx = 0;
                player.vy = 0;
              }
            }
          }
        }
      });
      
      // Check if player fell into pit
      const playerBottom = player.y + player.h;
      const playerCenterX = player.x + player.w / 2;
      
      const inPit = this.state.platforms.some(platform => {
        if (platform.w === PIT_WIDTH && platform.x <= playerCenterX && platform.x + platform.w >= playerCenterX) {
          return platform.y > playerBottom;
        }
        return false;
      });
      
      if (inPit) {
        player.lives--;
        if (player.lives <= 0) {
          kit.lose("Game Over! You fell into a pit.");
          this.state.gameOver = true;
        } else {
          // Reset player position
          player.x = 100;
          player.y = 300;
          player.vx = 0;
          player.vy = 0;
        }
      }
      
      // Check if player reached flag
      const flag = this.state.world.find(e => e.type === 'flag');
      if (flag && kit.aabb(player, flag)) {
        kit.win("You Win! Congratulations!");
        this.state.gameOver = true;
      }
      
      // Update camera
      this.state.cam.follow(player, WORLD_WIDTH, WORLD_HEIGHT);
      
      // Remove dead entities
      kit.cull(this.state.world);
    },
    
    draw(g, kit) {
      // Draw background
      g.clear("#111");
      
      // Push camera
      g.push(this.state.cam);
      
      // Draw platforms
      this.state.platforms.forEach(platform => {
        g.rect(platform.x, platform.y, platform.w, platform.h, "#888");
      });
      
      // Draw entities
      this.state.world.forEach(entity => {
        if (entity.type === 'enemy') {
          g.rect(entity.x, entity.y, entity.w, entity.h, "#f00");
        } else if (entity.type === 'coin') {
          if (!entity.collected) {
            g.circle(entity.x + entity.w/2, entity.y + entity.h/2, entity.w/2, "#ff0");
          }
        } else if (entity.type === 'flag') {
          g.rect(entity.x, entity.y, entity.w, entity.h, "#0f0");
        }
      });
      
      // Draw player
      if (this.state.player) {
        g.rect(this.state.player.x, this.state.player.y, 
               this.state.player.w, this.state.player.h, "#00f");
      }
      
      // Pop camera
      g.pop();
      
      // Draw UI
      g.text(`Score: ${this.state.score}`, 10, 20, "#fff");
      g.text(`Lives: ${this.state.player ? this.state.player.lives : 3}`, 10, 40, "#fff");
    }
  };
}