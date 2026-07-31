# Maestro — Vision and Non-Goals

## The goal

**Someone describes the game in their head, and a while later they are playing it — and can hand it
to a friend with a link.**

That is the whole product. A person who cannot code, or cannot spare the years, says what they want
and gets a real game back. It opens in a browser: no install, no launcher, no store approval, no
build step between the maker and the player. Sharing a game is sharing a URL.

AI quality is the product. Everything else — UI, install experience, speed — is scaffolding. The
scaffolding matters, but it comes second. A beautiful UI over mediocre output is a failure. Ugly UI
over excellent output is a success with a clear next step.

---

## What success looks like

Two things, and the second depends on the first.

**Range.** Any game a person can imagine, made well:

- A branching visual novel whose characters feel like real people
- A roguelike you want one more run of
- A point-and-click mystery whose puzzles actually click
- A cozy life-sim you lose an evening to
- A horror game that genuinely gets under your skin

Different genres, one bar: a game someone chooses to play, and finishes.

**A market.** People pay to make games here, and people pay for games other people made. The first
half exists — credits, exec-second metering, a charge per build. The second half is the destination
and is deliberately unspecified: what the buying looks like (sale, tip, patronage, a cut) is a
decision for when games are good enough that someone would want one. Naming a mechanism now would be
inventing a business for a product that hasn't earned it.

Breadth past games — a tabletop campaign, a novel chapter, a song — is welcome fallout, not a
target. Games are the proving ground because they are the hardest: they have to run, and they have
to be fun.

---

## How we get there

Roughly in order. Each stage is only worth starting once the one before it holds, and we are early
in the list — breadth before quality is a deliberate choice, not an accident.

**1. Any game asked for comes back running.** Whatever the genre, whatever the request, a build ends
with something that opens and responds. Not good yet — running. Breadth first, because a maker who
has to pick from a menu of what we support is not describing the game in their head.

**2. Any game asked for comes back good.** This is the hard one and the whole product. Good is
judged by a person playing it, and it stays that way until something better than a person exists to
judge it. Progress here is measured on a battery of real requests, not on the game that motivated
the change.

**3. Cheap enough that making games is casual.** A person should be able to try an idea, hate it,
and try again without doing arithmetic first. That means open-weight models on modest cards and
enough utilization to make the margin work. Cheap is not a compromise on quality — it is what lets
someone iterate their way to quality.

**4. A maker can hand their game to anyone.** A link that works, on any device, for someone who has
never heard of Maestro. Playing a game made here should require knowing nothing about how it was
made.

**5. Games are worth paying for.** When people want a game someone else made badly enough to buy it,
the market is real and we build the buying. Not before — a storefront over games nobody finishes is
the wrong order.

**6. We own the stack.** Rented GPUs today, owned hardware later, our own models after that. Every
step off someone else's platform is a step we intend to take.

The architecture that serves these — one model given the whole problem, the transcript as memory,
gates that detect broken and never "bad" — is in `CLAUDE.md`, where it can change as fast as the
measurements do. The stages above should not.

---

## Non-goals

**Not built on anyone else's model.**
Maestro runs on MIT- and Apache-2.0-licensed weights and tooling, end to end, and no proprietary
model touches any part of it. This is three constraints agreeing: the licenses have to permit what we
do commercially, open weights on rented cheap GPUs are the margin, and being untethered is the point.
A frontier API might well be cheaper per game today; taking it would mean the product exists at
someone else's discretion. Renting GPUs from RunPod is the concession we currently have to make and
the one we intend to unwind — owned hardware, and eventually models we train ourselves.

**Not a general-purpose AI assistant.**
Maestro makes things. Questions, summaries, and emails are what Claude and ChatGPT are for. The value
is in the build architecture and the domain quality it buys, which is only useful for creative
production.

**Not an agent.**
Maestro doesn't know you, doesn't act on its own, doesn't run unprompted. Request in, game out. An
agent *could* call it to make games for someone — building that agent is a different project.

**Not a UI/UX product.**
The frontend drives builds and shows output. It will get better, but UI quality is never why we ship
or don't. Pixel-perfect before excellent output is the wrong order.

**Not optimized for speed.**
A 45-minute build that makes a great game beats a 5-minute one that makes a mediocre game. Speed is a
tiebreaker. Compute spent is a fact about a build, never an argument against it.

**Not trying to replace human creativity.**
Maestro takes the slow, tedious, technically complex parts — structuring a plot, keeping a cast
consistent, writing 200 lines of game code. Direction, judgement, and taste stay with the person.

---

## The filter

When evaluating a feature or change, ask: does this make the output better, or does it make
something else better?

If the output gets better: ship it.
If the output doesn't get better: don't ship it.
