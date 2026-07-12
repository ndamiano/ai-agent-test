export function createGame(kit) {
  return {
    config: { mode: "3d", width: 800, height: 600, background: "#101018", seed: 1 },
    state: { 
      world: [],
      player: { x: 0, y: 1, z: 0, vx: 0, vy: 0, vz: 0 },
      score: 0,
      gameOver: false
    },
    init(kit) {
      this.state.world.push({
        shape: "ground",
        size: 50,
        color: "#444444"
      });
      
      this.state.world.push({
        shape: "box",
        x: 0, y: 1, z: 0,
        w: 2, h: 2, d: 2,
        color: "#0066ff"
      });
      
      // Spawn initial collectibles
      for (let i = 0; i < 5; i++) {
        this.spawnCollectible(kit);
      }
      
      // Spawn initial enemies
      for (let i = 0; i < 3; i++) {
        this.spawnEnemy(kit);
      }
    },
    update(dt, input, kit) {
      if (this.state.gameOver) return;
      
      const player = this.state.player;
      
      // Player movement
      player.vx = 0;
      player.vz = 0;
      
      if (input.down("w")) player.vz = 8;
      if (input.down("s")) player.vz = -8;
      if (input.down("a")) player.vx = -8;
      if (input.down("d")) player.vx = 8;
      
      // Apply movement
      kit.integrate3(player, dt, 0);
      
      // Keep player on ground
      player.y = 1;
      
      // Update collectibles
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const entity = this.state.world[i];
        if (entity.shape === "sphere" && entity.type === "collectible") {
          // Simple floating animation
          entity.y = 2 + Math.sin(Date.now() * 0.001 + entity.x * 0.1 + entity.z * 0.1) * 0.5;
        }
      }
      
      // Update enemies
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const entity = this.state.world[i];
        if (entity.shape === "box" && entity.type === "enemy") {
          // Simple AI movement
          if (Math.random() < 0.02) {
            entity.vx = (Math.random() - 0.5) * 6;
            entity.vz = (Math.random() - 0.5) * 6;
          }
          
          kit.integrate3(entity, dt, 0);
          
          // Keep enemies on ground
          entity.y = 1;
          
          // Bounce off walls
          if (entity.x < -24 || entity.x > 24) entity.vx *= -1;
          if (entity.z < -24 || entity.z > 24) entity.vz *= -1;
        }
      }
      
      // Check collisions
      this.checkCollisions(kit);
    },
    checkCollisions(kit) {
      const player = this.state.player;
      
      // Collectible collisions
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const entity = this.state.world[i];
        if (entity.shape === "sphere" && entity.type === "collectible") {
          const dx = player.x - entity.x;
          const dy = player.y - entity.y;
          const dz = player.z - entity.z;
          const distance = Math.hypot(dx, dy, dz);
          
          if (distance < 1.5) {
            // Collect the item
            this.state.world.splice(i, 1);
            this.state.score += 10;
            this.spawnCollectible(kit);
          }
        }
      }
      
      // Enemy collisions
      for (let i = this.state.world.length - 1; i >= 0; i--) {
        const entity = this.state.world[i];
        if (entity.shape === "box" && entity.type === "enemy") {
          const dx = player.x - entity.x;
          const dy = player.y - entity.y;
          const dz = player.z - entity.z;
          const distance = Math.hypot(dx, dy, dz);
          
          if (distance < 2) {
            // Game over
            this.state.gameOver = true;
            kit.lose("You were caught by an enemy!");
            return;
          }
        }
      }
    },
    spawnCollectible(kit) {
      const collectible = {
        shape: "sphere",
        x: (Math.random() - 0.5) * 40,
        y: 2,
        z: (Math.random() - 0.5) * 40,
        r: 1,
        color: "#ffff00",
        type: "collectible"
      };
      this.state.world.push(collectible);
    },
    spawnEnemy(kit) {
      const enemy = {
        shape: "box",
        x: (Math.random() - 0.5) * 40,
        y: 1,
        z: (Math.random() - 0.5) * 40,
        w: 2, h: 2, d: 2,
        color: "#ff0000",
        vx: (Math.random() - 0.5) * 6,
        vz: (Math.random() - 0.5) * 6,
        type: "enemy"
      };
      this.state.world.push(enemy);
    },
    camera(cam, kit) {
      const player = this.state.player;
      cam.x = player.x - 5;
      cam.y = player.y + 3;
      cam.z = player.z - 5;
      cam.tx = player.x;
      cam.ty = player.y;
      cam.tz = player.z;
    }
  };
}