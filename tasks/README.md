# Tasks

Work plans for Maestro. One file per **workstream** (a coherent body of work), not per atomic
task. `docs/ROADMAP.md` is the high-altitude forward plan; these files are the drainable
detail under it.

## Active

Grouped by theme. Generation-quality work makes the output good; platform work makes it
shippable to many users. All files re-baselined 2026-07-23 against the codegen architecture
(the IR-era workstreams were retired to `finished.md`).

**Generation quality**
| File | Workstream | Status |
|------|-----------|--------|
| [quality_backlog.md](quality_backlog.md) | Gate/prompt hill-climbing toward "good, not just valid"; play-critic last | Judge is the lever; Q1 retargets the stale grading skills |
| [world_first.md](world_first.md) | World-first content on the live seams: residents-as-data, kit.quest, dialogue | Rewritten 2026-07-23 — residents dataset first |
| [game_style.md](game_style.md) | Per-game visual identity: style brief composed into every asset prompt | Rewritten — styled-prompt stage (S1) first |
| [game_media.md](game_media.md) | Audio backend for the kit + music/SFX/animation pipelines | kit.audio is a stub; wiring-first, generation later |
| [asset_quality.md](asset_quality.md) | Art quality: per-asset-type models + prompt lint + identity-stable regen | Typed job builders (A2) first |

**Business / strategy** — start here for the path to launch
| File | Workstream | Status |
|------|-----------|--------|
| [launch_plan.md](launch_plan.md) | **Meta-doc**: current→business, staged (alpha → paid beta → public) | The sequencing map — read first. Pre-alpha DONE |
| [unit_economics.md](unit_economics.md) | Cost/game + credit pricing | Measure during alpha; metering hooks now exist |
| [legal_ops.md](legal_ops.md) | ToS/privacy/refunds/IP-ownership/age-gate/entity | Alpha: minimal; full set before paid |

**Platform / launch**
| File | Workstream | Status |
|------|-----------|--------|
| [production_hardening.md](production_hardening.md) | Pre-beta security: game sandbox, chat rate limit, API surface, sessions | H1 (sandbox) is the big one |
| [auth_and_billing.md](auth_and_billing.md) | Login gate, ownership, credit ledger + compute budget (NO self-serve signup) | Core landed; T4 payments open |
| [scaleout.md](scaleout.md) | Queue/scaler follow-ups: connector singleton, fair scheduling | Spine landed (queue + autoscaler); polish open |
| [build_deploy.md](build_deploy.md) | CI, containerize, persistence, deploy pipeline | Landed through deploy; CI lint/docker-build + T5 versioning open |
| [safety_filter.md](safety_filter.md) | Illegal-content filter over text + (uncensored) image gen | Phase 1 + partial 2 landed; tests + classifier open |

**Research / governance**
| File | Workstream | Status |
|------|-----------|--------|
| [create_ux_research.md](create_ux_research.md) | Research: what a *good* create-game UX is (deep-dive, not build) | Research not started |
| [test_health.md](test_health.md) | Test governance (618 tests / 52 files, green) | Re-reconned 2026-07-23; T1 shared fixtures first |
| [doc_accuracy.md](doc_accuracy.md) | Doc audit + anti-drift governance (CLAUDE/README/ROADMAP/VISION/docs) | T1 superseded by rebuild; rerun via T2 method |

## Reference

| File | What |
|------|------|
| [finished.md](finished.md) | Append-only ledger of shipped work (retired checklists land here) |
| [nicknotes.md](nicknotes.md) | Scratch: prompts to try + local-service startup commands |

---

## How to write a drainable workstream file

So an agent (or you, cold) can execute with zero prior conversation:

```
# <Workstream> — Work Plan
## Why          — rationale + decisions already made ("do not re-litigate")
## Background    — self-contained; VERIFIED file paths + real API signatures
## Guardrails    — reject-if rules (keep scope honest)
## Tasks         — ### T1 … with [ ] atomic subtasks, files-touched, and the test that proves it
## Parked        — logged, not lost
```

Rules that make a file drainable:
- **Each subtask names the files it touches + how to verify** (which test). That's what lets an
  agent go cold-start → checked box without asking.
- **List verified real APIs up front** — kills hallucinated signatures.
- **State ordering + coordination** — what lands before what; worktree hints for parallel work.

## Lifecycle

- Working a task: check the box `[x]` as subtasks land.
- A whole **section** goes all-`[x]`: cut it, add one line to [finished.md](finished.md). Keeps
  active files free of dead checklists.
- Workstream fully done: remove it from the Active table, one summary line in `finished.md`.
