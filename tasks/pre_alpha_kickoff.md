# Pre-Alpha Kickoff — Agent Playbook

**Read this first in a fresh session, then start building.** It defines the narrow pre-alpha scope
and gives copy-paste prompts for spawning build agents.

## Goal of pre-alpha
Prove the whole loop **functions** for someone who isn't the owner. Inference stays on the owner's PC
(5090) over **Tailscale**; a couple of trusted people. NOT demand validation, NOT scale, NOT payments
— just: a non-owner logs in → requests a game → it builds → they download a working game.

## Scope — what pre-alpha needs built (narrow)
The generate→download product already works. The NEW work is **multi-user access + a gate + not
thrashing the single GPU.** Four pieces:

1. **Manual auth + gate + run ownership** — `auth_and_billing.md` **T1 + T2**. Login, gate all routes
   (incl. WebSocket), attach `user_id` to runs, scope list/get/control to the owner. Manual account
   creation only — **no signup**.
2. **Build queue (serialize on the one GPU)** — `scaleout.md` **S1, queue subset only**. A couple of
   users must not run two builds at once on the single 5090. One build at a time; others queue with a
   visible position. (Full concurrency/isolation is later.)
3. **Per-user access + WS routing** — `scaleout.md` **S1 WS routing** (server-side filter by
   user/run so a person sees only their own build events) + a minimal reachable way for the couple of
   testers to hit the app (Tailscale or a simple deploy).
4. **Credits stubbed/manual** — `auth_and_billing.md` **T3 minimal** — a granted balance + deduct, or
   effectively unlimited — enough to exercise the gate. **No real payments in pre-alpha.**

**Explicitly OUT of pre-alpha:** payments (T4), self-serve signup, the full safety filter (trusted
users — but do a basic block and START `safety_filter.md` Phase 1 research), concurrency-at-scale,
runpod, module breadth, the quality overhaul. Keep inference pointed at the owner's Tailscale
endpoint in settings.

## How to prompt a build agent (template)
Point ONE agent at ONE task file + specific task IDs. Example for piece 1:

> Read `CLAUDE.md` (code standards) and `tasks/auth_and_billing.md`. Implement **T1 (auth) and T2
> (run ownership) ONLY** — leave T3/T4. Use the verified file paths in the Background section. Follow
> CLAUDE.md: minimal/surgical, no backwards-compat shims, write meaningful behaviour tests, run the
> suite (`cd src && python -m pytest ../tests/ --ignore=../tests/integration -q`) and fix failures,
> self-review the diff for scope creep + security. Keep docs in sync. **Do NOT commit — stop and
> report so I can validate.**

Swap the file + task IDs for each piece.

## Ordering (serial vs parallel — IMPORTANT)
Pieces 1 and 2/3 **both edit `src/api/routers/games.py` + the websocket layer** — they will conflict
if run in parallel in the same tree. Either run them **serially**, or give each agent its **own git
worktree** (`isolation: "worktree"`). Recommended serial order:

1. **Auth + ownership** (piece 1) — foundation; the queue's per-user routing needs identity first.
2. **Build queue + per-user WS routing** (pieces 2 + 3).
3. **Credits stub** (piece 4).
4. **Reachability + smoke test** — confirm a non-owner (second Tailscale user / account) can drive the
   full loop end-to-end.

For the design-heavier pieces, run a **Plan agent** first:

> Use the Plan agent: read `tasks/scaleout.md` S1 + its Background. Produce a step-by-step plan for a
> build queue that serializes builds on ONE GPU + server-side per-user WS routing, minimal for a
> couple of users (not 100). Identify the exact files/functions to change.

## Agent tips
- **One workstream per agent** — don't let it wander across files.
- **Worktrees** (`isolation: "worktree"`) if you want auth + queue built in parallel despite shared files.
- **Every agent:** tests + self-review + **STOP before commit** (owner validates first — the standing
  commit rule).
- **Verify by running the app**, not just tests — use the `run` / `verify` skills to drive the loop.
- Keep settings' inference endpoint on the owner's Tailscale address (no runpod yet).

## One prompt to start a fresh session
> Read `tasks/pre_alpha_kickoff.md` and `tasks/launch_plan.md`. We're building toward pre-alpha.
> Start with piece 1 (auth + ownership): spawn a build agent per the playbook, serial ordering, stop
> before any commit. Then we'll validate and move to the next piece.
