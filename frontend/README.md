# Maestro frontend

React + TypeScript + Vite + Tailwind. The operator UI: read a run's prompt, build it, watch the
feed, play the result.

```
npm install
npm run dev        # vite; proxies /api /auth /play to localhost:8000
npm test           # vitest
npm run lint
npm run build      # tsc -b && vite build
```

The backend must be running (`python run.py` from the repo root) — the dev server only proxies.

- `src/api/client.ts` — every call the UI makes. One `send()` owns the bearer header, the 429
  backoff, and the central 401 → back to the login gate.
- `src/hooks/useRunBuildStream.ts` — the build feed, folded from the durable `/events` replay plus
  the live websocket, so a tab switch never loses it.
- `src/components/cockpit/` — the pieces of a game's detail view.
