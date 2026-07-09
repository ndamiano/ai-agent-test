# Tasks

Work plans for Maestro. One file per **workstream** (a coherent body of work), not per atomic
task. `docs/ROADMAP.md` is the high-altitude forward plan; these files are the drainable
detail under it.

## Active

Grouped by theme. Generation-quality work makes the output good; platform work makes it
shippable to many users.

**Generation quality**
| File | Workstream | Status |
|------|-----------|--------|
| [quality_backlog.md](quality_backlog.md) | Judge-in-loop, prompt iteration, continuity, mechanic depth | §1 (judge) is the lever; ship first |
| [world_first.md](world_first.md) | World-first content model: bible, objectives/quests, residents, ambient dialogue | Designed 2026-07-09 — depends on aspects A1/A2; W7 gold game can start now |
| [aspects_and_scale.md](aspects_and_scale.md) | Nouns-primary aspect layer (A) + declared scale/coverage (B) | Not started — A before B |
| [module_catalog.md](module_catalog.md) | Build out the mechanic library (card game, shop, affinity, quests…) | Depends on aspect layer (A) |
| [game_style.md](game_style.md) | LLM-authored style module + projection theming (the OUTPUT game's look/UX) | Not started — schema+module first |
| [realtime_substrate.md](realtime_substrate.md) | Build the `real_time_sim` substrate (continuous time, Godot-only) | Designed not built — reconcile design first |
| [game_media.md](game_media.md) | Music + sound effects + animation (three generative pipelines) | Not started — M1 music first |
| [asset_quality.md](asset_quality.md) | Art quality via better prompting + better models per asset type | Not started — T1 prompting first |
| [hitl_backlog.md](hitl_backlog.md) | Dirty-core HITL rebuild + component browser UI (epics A–G) | Not started — backend spine first |

**Business / strategy** — start here for the path to launch
| File | Workstream | Status |
|------|-----------|--------|
| [pre_alpha_kickoff.md](pre_alpha_kickoff.md) | **Agent playbook** for building pre-alpha (scope + copy-paste prompts) | ← start building here |
| [launch_plan.md](launch_plan.md) | **Meta-doc**: current→business, staged (pre-alpha → alpha → paid beta → public) | The sequencing map — read first |
| [unit_economics.md](unit_economics.md) | Cost/game ($0.99 on 5090) + credit pricing ($5 base / 2 for A100 speed-up) | Measure during alpha; not MVP-blocking |
| [legal_ops.md](legal_ops.md) | ToS/privacy/refunds/IP-ownership/age-gate/entity | Alpha: minimal; full set before paid |

**Platform / launch**
| File | Workstream | Status |
|------|-----------|--------|
| [auth_and_billing.md](auth_and_billing.md) | Login gate, run ownership, credit ledger (NO self-serve signup) | Not started — auth+ownership first |
| [scaleout.md](scaleout.md) | Concurrent builds, parallel asset gen, runpod on-demand inference | Not started — depends on auth; S1 is launch blocker |
| [build_deploy.md](build_deploy.md) | CI, containerize, persistence, deploy pipeline | Not started — CI (T1) first, cheap |
| [safety_filter.md](safety_filter.md) | Illegal-content filter over text + (uncensored) image gen | Research first; launch gate |

**Research / governance**
| File | Workstream | Status |
|------|-----------|--------|
| [create_ux_research.md](create_ux_research.md) | Research: what a *good* create-game UX is (deep-dive, not build) | Research not started |
| [test_health.md](test_health.md) | Test audit + anti-bloat governance (582 tests, high quality, structural risk) | Recon done; G1 fixtures first |
| [doc_accuracy.md](doc_accuracy.md) | Doc audit + anti-drift governance (CLAUDE/README/ROADMAP/VISION/docs) | Recon-ready; run T1 audit |

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
