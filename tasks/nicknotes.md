### Prompts

Can you create a game for me where you explore a world, and when you "fight" opponents you do it with a card game? I'd like for the game to be played for ante, so if you win the game you get some of their cards, if you lose, you lose some of your cards. Also, I'd like to be able to buy and sell cards at shops.


###

Commands:
```
source venv/bin/activate && python run.py
cd frontend && npm run dev
comfy-start
docker start kokoro
llama-server \
  --models-dir /home/nick/Documents/models/LLM \
  --host 127.0.0.1 --port 8080 \
  -ngl 99 \
  -c 32768 \
  --jinja \
  --reasoning-budget 0
```