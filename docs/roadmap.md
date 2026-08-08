# Maestro Roadmap

`vision.md` holds the destination and does not move. `CLAUDE.md` holds the architecture and changes
as fast as the measurements do. This file is the line between them: where we stand against each
vision stage, and what would tell us to move on.

It stays at that altitude on purpose. What we are doing this week is not in the repo — a task list
that lives here rots into a citation layer nobody updates, and the work itself is thinking, not
documentation. If something here is specific enough to be wrong within a fortnight, it belongs in
`CLAUDE.md` (if it is architecture) or `docs/experiments.md` (if it is a measurement) or nowhere.

---

## Two tracks

The **product track** is vision stages 1–3: whether the games are worth having. The **delivery
track** is whether a stranger can pay for one and get it safely.

They gate each other in one direction only: delivery may run ahead, but no delivery stage that takes
money opens before the product stage it sells has cleared.

Delivery has run ahead, and that is the standing risk. Platform work has clear done-conditions and
"good game" does not, so the pull is always toward more platform. The gap between the tracks is the
number to watch.

---

## Product track

### Stage 1 — Any game asked for comes back running — **IN PROGRESS**

**Bar:** a battery of varied requests — 2D and 3D, arcade and turn-based and narrative — where every
build produces a game that loads, takes input, and does not crash. Failures are investigated, not
averaged away.

The build loop that clears this is described in `CLAUDE.md` and is not restated here. What stands
between us and the bar:

- **No battery runs on a schedule.** Every grid so far has been a one-off experiment. Until a fixed
  set of requests runs against a fixed configuration, "does it come back running" has no answer that
  survives the next change to the loop. This is the single biggest gap on the track.
- **Recurring defects with no fix yet earning more than a prompt line.** 3D scenes lit near-black,
  silent games, arrow-keys-only input. The bar for promoting any of these to code is in `CLAUDE.md`:
  a prompt line first, and a primitive only after a line has failed twice across two models.
- **Art is on trial and unsettled.** `generate_media` entered unmeasured. What settles it is
  coverage — how much of what the player sees got art — not whether the tool is called. An unused
  schema costs every turn of every build, so a tool that fails that comes back out.
- **Scene composition is new and unproven at scale.** `compose_scene` and the asset store landed
  against a validated recipe and one real build. One build is an existence proof, not a measurement.

### Stage 2 — Any game asked for comes back good — **NOT STARTED**

The hard one, and the one everything downstream waits on.

**Bar:** the owner plays a battery of builds and is happy with them. Deliberately a human judgement,
and it stays one. There is no metric here — there is a point at which the owner calls it.

What is settled, and stays settled unless something answers the failure that retired it:

- No local LLM-judge gates.
- No "the game must do X" checks. A gate may only detect broken.
- No automated round that fixes what a previous round already got right.

The open question is not how to build a better judge. It is what a play-critic would have to answer
before one is worth building again.

### Stage 3 — Cheap enough that making games is casual — **PARTLY LANDED**

**Bar:** a measured cost per game that supports a price a person spends without thinking, with the
margin coming from utilization rather than from charging more.

The mechanism exists: exec-second metering debited to the owning game, a compute budget that admits
or refuses jobs, and a queue-driven autoscaler. Builds run concurrently rather than serializing.

What is missing is the number. Earlier cost figures were measured on the owner's box, under turn
caps that have since changed, and their working notes no longer exist. Treat them as gone. The
measurement to make is cost per finished game **off rented pods, at the current caps**, with the
failure rate alongside it — cold start and a different serving stack are not in the old seconds.

Cheap to do, and it blocks every paid stage.

---

## Delivery track

### Pre-alpha — validate it FUNCTIONS — **DONE**

A non-owner logged in, requested a game, it built without hand-holding, and they played it.

### Private alpha — validate DEMAND and measure the economics — **IN PROGRESS**

Deployed off the owner's box. Containerized deploy, public HTTPS, prod hardening, origin isolation
for generated code, self-hosted analytics, and a restorable backup story are all live. Accounts are
gated by admin-minted invite codes, and there is a public front door with demo games behind it.

**Bar:** people want it, and real COGS/failure data exists.

**Open:** the economics measurement (stage 3), and enough product-track progress that showing it to
people is worth their time. The second half is the real blocker.

### Public beta, paid — first dollar — **BLOCKED on product stage 2**

**Bar:** paying users, positive unit economics confirmed, no safety or legal gaps.

Payments are live and a real purchase and refund have been verified end to end. Safety runs at two
seams — a prompt-input screen and a post-generation verdict on every render, both fail-closed, with
a text gate that holds a flagged build and a durable violations record.

**Remaining:** the legal checklist with counsel review, and the game-execution sandbox. Neither is
what holds this stage shut — stage 2 is.

### Public release — open and scaling — **NOT STARTED**

**Bar:** open to the public, scales without falling over.

Self-serve signup exists; "open" means dropping the invite gate, which is a policy call rather than
code. The rest is whatever the beta's load teaches about the autoscaler's ceilings.

---

## Stages 4–6 — after the business stands

Not scheduled. The direction is decided, and one has a prerequisite that must be built before its
stage starts.

**Stage 4 — a maker can hand their game to anyone.** Origin isolation for generated game code is a
hard prerequisite for *any* sharing feature, not for beta. Today a game runs only in its owner's
browser, so ownership-gating is what makes the posture safe; sharing removes exactly that
protection.

**Stage 5 — games are worth paying for.** The buying mechanism stays unspecified until games are
good enough that someone would want one. What already exists: run ownership and the credit ledger.
The IP-ownership question has to be answered before anyone sells anything.

**Stage 6 — we own the stack.** Rented GPUs today, owned hardware later, our own models after that.
Nothing is scheduled; the point of writing it down is that every architecture decision should keep
the exit open — which is why the control plane speaks a canonical request and the worker owns the
wire format, and why nothing proprietary is in the loop.

---

## Horizons — not commitments

Things we would love to make once the above stands: richer output (music, SFX, animation, real-time
games); other artifact types (long-form fiction, comics, world-building); VR world generation,
blocked mainly on text-to-3D asset quality.
