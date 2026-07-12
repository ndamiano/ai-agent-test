export function createGame(kit) {
  const config = {
    width: 608,
    height: 672,
    title: "Pacman",
    background: "#000",
    gravity: 0,
    seed: 1
  };

  const TILE_SIZE = 32;
  const PLAYER_SPEED = 120;
  const GHOST_SPEED = 90;

  return {
    config,
    state: {
      world: [],
      score: 0,
      pelletsRemaining: 0,
      playerDirection: null,
      queuedDirection: null,
      gameStarted: false
    },

    init(kit) {
      const mazeRows = [
        "###################",
        "#........#........#",
        "#.##.###.#.###.##.#",
        "#.................#",
        "#.##.#.#####.#.##.#",
        "#....#...#...#....#",
        "####.### # ###.####",
        "   #.#   G   #.#   ",
        "####.# ##### #.####",
        "#......#GGG#......#",
        "####.# ##### #.####",
        "   #.#       #.#   ",
        "####.# ##### #.####",
        "#........#........#",
        "#.##.###.#.###.##.#",
        "#..#.....P.....#..#",
        "##.#.#.#####.#.#.##",
        "#....#...#...#....#",
        "#.######.#.######.#",
        "#.................#",
        "###################"
      ];

      const tilemap = kit.makeTilemap(mazeRows, TILE_SIZE, "#");
      this.state.tilemap = tilemap;

      // Count pellets
      let pellets = 0;
      for (let y = 0; y < mazeRows.length; y++) {
        for (let x = 0; x < mazeRows[y].length; x++) {
          if (mazeRows[y][x] === '.') pellets++;
        }
      }
      this.state.pelletsRemaining = pellets;

      // Spawn player
      let playerX = 0, playerY = 0;
      for (let y = 0; y < mazeRows.length; y++) {
        for (let x = 0; x < mazeRows[y].length; x++) {
          if (mazeRows[y][x] === 'P') {
            playerX = x * TILE_SIZE + TILE_SIZE / 2;
            playerY = y * TILE_SIZE + TILE_SIZE / 2;
            break;
          }
        }
      }

      const player = kit.spawn(this.state.world, {
        x: playerX,
        y: playerY,
        vx: 0,
        vy: 0,
        w: TILE_SIZE * 0.7,
        h: TILE_SIZE * 0.7,
        type: 'player',
        radius: TILE_SIZE * 0.35
      });

      // Spawn ghosts
      const ghostColors = ['#FF0000', '#FFB8FF', '#00FFFF', '#FFB852'];
      for (let y = 0; y < mazeRows.length; y++) {
        for (let x = 0; x < mazeRows[y].length; x++) {
          if (mazeRows[y][x] === 'G') {
            const ghost = kit.spawn(this.state.world, {
              x: x * TILE_SIZE + TILE_SIZE / 2,
              y: y * TILE_SIZE + TILE_SIZE / 2,
              vx: 0,
              vy: 0,
              w: TILE_SIZE * 0.7,
              h: TILE_SIZE * 0.7,
              type: 'ghost',
              radius: TILE_SIZE * 0.35,
              color: ghostColors[Math.floor(Math.random() * ghostColors.length)],
              direction: null
            });
          }
        }
      }

      // Spawn pellets
      for (let y = 0; y < mazeRows.length; y++) {
        for (let x = 0; x < mazeRows[y].length; x++) {
          if (mazeRows[y][x] === '.') {
            kit.spawn(this.state.world, {
              x: x * TILE_SIZE + TILE_SIZE / 2,
              y: y * TILE_SIZE + TILE_SIZE / 2,
              w: 4,
              h: 4,
              type: 'pellet',
              radius: 2
            });
          }
        }
      }

      this.state.player = player;
      this.state.gameStarted = true;
    },

    update(dt, input, kit) {
      if (!this.state.gameStarted) return;

      if (kit.over) return;

      // Handle player input
      if (input.pressed('ArrowUp')) this.state.queuedDirection = 'up';
      if (input.pressed('ArrowDown')) this.state.queuedDirection = 'down';
      if (input.pressed('ArrowLeft')) this.state.queuedDirection = 'left';
      if (input.pressed('ArrowRight')) this.state.queuedDirection = 'right';

      // Update player direction if possible
      if (this.state.queuedDirection) {
        const dir = this.state.queuedDirection;
        let newX = this.state.player.x;
        let newY = this.state.player.y;
        
        switch (dir) {
          case 'up': newY -= TILE_SIZE; break;
          case 'down': newY += TILE_SIZE; break;
          case 'left': newX -= TILE_SIZE; break;
          case 'right': newX += TILE_SIZE; break;
        }
        
        // Check if the new position is solid
        const testGridX = Math.floor(newX / TILE_SIZE);
        const testGridY = Math.floor(newY / TILE_SIZE);
        
        if (!this.state.tilemap.solidAt(testGridX, testGridY)) {
          this.state.playerDirection = dir;
          this.state.queuedDirection = null;
        }
      }

      // Move player
      if (this.state.playerDirection) {
        switch (this.state.playerDirection) {
          case 'up': this.state.player.vy = -PLAYER_SPEED; break;
          case 'down': this.state.player.vy = PLAYER_SPEED; break;
          case 'left': this.state.player.vx = -PLAYER_SPEED; break;
          case 'right': this.state.player.vx = PLAYER_SPEED; break;
        }
      }

      // Update player position with collision checking
      const oldX = this.state.player.x;
      const oldY = this.state.player.y;
      
      kit.integrate(this.state.player, dt, 0);
      
      // Check if player would enter a wall and prevent it
      const playerGridX = Math.floor(this.state.player.x / TILE_SIZE);
      const playerGridY = Math.floor(this.state.player.y / TILE_SIZE);
      
      if (this.state.tilemap.solidAt(playerGridX, playerGridY)) {
        // Revert to old position
        this.state.player.x = oldX;
        this.state.player.y = oldY;
        this.state.player.vx = 0;
        this.state.player.vy = 0;
      }

      // Check for pellet collection
      const pelletsToRemove = [];
      for (let i = 0; i < this.state.world.length; i++) {
        const entity = this.state.world[i];
        if (entity.type === 'pellet') {
          const distance = Math.sqrt(
            Math.pow(this.state.player.x - entity.x, 2) + 
            Math.pow(this.state.player.y - entity.y, 2)
          );
          if (distance < this.state.player.radius + entity.radius) {
            pelletsToRemove.push(i);
            this.state.score += 1;
            this.state.pelletsRemaining--;
          }
        }
      }

      // Remove collected pellets
      for (let i = pelletsToRemove.length - 1; i >= 0; i--) {
        this.state.world.splice(pelletsToRemove[i], 1);
      }

      // Update ghosts
      for (let i = 0; i < this.state.world.length; i++) {
        const entity = this.state.world[i];
        if (entity.type === 'ghost') {
          // Simple ghost AI: random movement with direction changes at intersections
          if (Math.random() < 0.02) {
            const directions = ['up', 'down', 'left', 'right'];
            entity.direction = directions[Math.floor(Math.random() * directions.length)];
          }

          // Move ghost
          switch (entity.direction) {
            case 'up': entity.vy = -GHOST_SPEED; break;
            case 'down': entity.vy = GHOST_SPEED; break;
            case 'left': entity.vx = -GHOST_SPEED; break;
            case 'right': entity.vx = GHOST_SPEED; break;
            default: entity.vx = 0; entity.vy = 0;
          }

          // Update ghost position with collision checking
          const oldGhostX = entity.x;
          const oldGhostY = entity.y;
          
          kit.integrate(entity, dt, 0);
          
          // Check if ghost would enter a wall and prevent it
          const ghostGridX = Math.floor(entity.x / TILE_SIZE);
          const ghostGridY = Math.floor(entity.y / TILE_SIZE);
          
          if (this.state.tilemap.solidAt(ghostGridX, ghostGridY)) {
            // Revert to old position
            entity.x = oldGhostX;
            entity.y = oldGhostY;
            entity.vx = 0;
            entity.vy = 0;
          }
        }
      }

      // Check for collisions with ghosts
      for (let i = 0; i < this.state.world.length; i++) {
        const entity = this.state.world[i];
        if (entity.type === 'ghost') {
          const distance = Math.sqrt(
            Math.pow(this.state.player.x - entity.x, 2) + 
            Math.pow(this.state.player.y - entity.y, 2)
          );
          if (distance < this.state.player.radius + entity.radius) {
            kit.lose("Game Over! Ghost caught you!");
            return;
          }
        }
      }

      // Check win condition
      if (this.state.pelletsRemaining <= 0) {
        kit.win("You Win! All pellets collected!");
      }

      // Remove dead entities
      kit.cull(this.state.world);
    },

    draw(g, kit) {
      // Draw walls
      const tilemap = this.state.tilemap;
      for (let y = 0; y < tilemap.h; y++) {
        for (let x = 0; x < tilemap.w; x++) {
          if (tilemap.solidAt(x, y)) {
            g.rect(x * TILE_SIZE, y * TILE_SIZE, TILE_SIZE, TILE_SIZE, "#0000FF");
          }
        }
      }

      // Draw pellets
      for (let i = 0; i < this.state.world.length; i++) {
        const entity = this.state.world[i];
        if (entity.type === 'pellet') {
          g.circle(entity.x, entity.y, entity.radius, "#FFFFFF");
        }
      }

      // Draw player
      const player = this.state.player;
      if (player) {
        g.circle(player.x, player.y, player.radius, "#FFFF00");
      }

      // Draw ghosts
      for (let i = 0; i < this.state.world.length; i++) {
        const entity = this.state.world[i];
        if (entity.type === 'ghost') {
          g.circle(entity.x, entity.y, entity.radius, entity.color);
        }
      }

      // Draw score
      g.text(`Score: ${this.state.score}`, 10, 20, "#FFFFFF");
      g.text(`Pellets: ${this.state.pelletsRemaining}`, 10, 40, "#FFFFFF");
    }
  };
}