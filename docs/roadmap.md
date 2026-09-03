# Maestro Roadmap

Where we stand against each stage of `vision.md`, and what would tell us to move on. Anything
specific enough to be wrong within a fortnight belongs in `CLAUDE.md` (architecture) or
`docs/experiments.md` (measurement), not here.

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

- **The battery is run by hand, after every change to the loop, and that is the choice for now.**
  A fixed set of requests against a fixed configuration is what makes "does it come back running"
  survive the next change, and it is what happens — one grid per change, the owner playing every
  cell. Putting it on a schedule buys nothing while every run costs GPU dollars nobody is earning
  back; it becomes worth a few dollars a day once there is revenue to spend them from.
- **The helper library shipped and the games it produced played worse.** `runtime/vendor/lib/`
  closed the standing defect ledger (near-black 3D, silent games, arrow-keys-only input) 6/6
  against a control arm, and on the same six requests the games that actually PLAYED went from
  4/6 to 1/6 (`docs/experiments.md`, 2026-08-22). Two of the four deaths were one lib defect,
  since fixed; the open item is the play rate on a rebuilt battery.
- **Art is on trial and unsettled.** What settles `generate_media` is coverage — how much of what
  the player sees got art — not whether the tool is called (`CLAUDE.md` "Adding a capability").
- **A generated 3D world is something a build can now ask for, and unproven at scale.**
  `compose_world` puts a whole worldgen pipeline behind one tool call — ground, regions and the
  scenery standing on them — and a game written on its loader is a game nobody has built at
  volume yet. Same bar as the tool below it: coverage across a battery, not one world.
- **Scene composition is new and unproven at scale.** `compose_scene` and the asset store landed
  against a validated recipe and one real build. One build is an existence proof, not a measurement.

### Stage 2 — Any game asked for comes back good — **NOT STARTED**

The hard one, and the one everything downstream waits on.

**Bar:** the owner plays a battery of builds and is happy with them. Deliberately a human judgement,
and it stays one. There is no metric here — there is a point at which the owner calls it.

Settled, with the measurements behind each in `CLAUDE.md`: no LLM-judge gates, no "the game must
do X" checks, no automated round that fixes what a previous round already got right.

The open question is not how to build a better judge. It is what a play-critic would have to answer
before one is worth building again.

### Stage 3 — Cheap enough that making games is casual — **PARTLY LANDED**

**Bar:** a measured cost per game that supports a price a person spends without thinking, with the
margin coming from utilization rather than from charging more.

The mechanism exists: exec-second metering debited to the owning game, a compute budget that admits
or refuses jobs, and a queue-driven autoscaler. Builds run concurrently rather than serializing.

What is missing is the number: cost per finished game **off rented pods, at the current caps**,
with the failure rate alongside it. Nothing measured on the owner's box stands in for it — cold
start and the serving stack are in the pod's seconds and not in a workstation's.

Cheap to do, and it blocks every paid stage.

---

## Delivery track

### Pre-alpha — validate it FUNCTIONS — **DONE**

A non-owner logged in, requested a game, it built without hand-holding, and they played it.

### Private alpha — validate DEMAND and measure the economics — **IN PROGRESS**

Deployed off the owner's box. Containerized deploy, public HTTPS, prod hardening, origin isolation
for generated code, self-hosted analytics, and a restorable backup story are all live. Signup is
open, and there is a public front door with demo games behind it.

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

Self-serve signup is already open. The rest is whatever the beta's load teaches about the
autoscaler's ceilings.

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
