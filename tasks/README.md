# Tasks

Work plans for Maestro. One file per **workstream** (a coherent body of work), not per atomic
task. `docs/roadmap.md` is the high-altitude forward plan; these files are the drainable
detail under it.

## Active

Grouped by theme. Generation quality makes the output good; platform work makes it shippable to
many users. Re-baselined 2026-07-23 against the codegen architecture (IR-era workstreams retired
to `finished.md`), then re-audited per file 2026-07-25: drained sections cut, the four
paid-beta engineering files merged into one, and `build_loop.md` opened for the core work that
had no file at all (see `finished.md` § Drained sections).

**Generation quality**
| File | Workstream | Status |
|------|-----------|--------|
| [build_loop.md](build_loop.md) | **The core**: gates, fix shapes, kit surface, step economics, the audit | New 2026-07-25 — B2 step-economics baseline first |
| [quality_backlog.md](quality_backlog.md) | Judging output quality: grading skills, prompt hill-climbing, play-critic last | Judge is the lever; Q1 retargets the stale grading skills |
| [world_first.md](world_first.md) | World-first content on the live seams: residents-as-data, kit.quest, dialogue | Residents dataset first |
| [game_style.md](game_style.md) | Per-game visual identity: style brief composed into every asset prompt | Styled-prompt stage (S1) first |
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
| [platform_polish.md](platform_polish.md) | The paid-beta residue: payments, origin isolation, inference globals, fair scheduling, release hygiene | Merged 2026-07-25 from auth_and_billing + build_deploy + scaleout + production_hardening |
| [safety_filter.md](safety_filter.md) | Illegal-content filter over text + (uncensored) image gen | Input side landed; output side (hooks 3/5/6) open |

**Research / governance**
| File | Workstream | Status |
|------|-----------|--------|
| [create_ux_research.md](create_ux_research.md) | Research: what a *good* create-game UX is (deep-dive, not build) | Research not started |
| [test_health.md](test_health.md) | Test governance (812 tests / 57 files, green) | Nothing landed yet; T1 shared fixtures first |
| [doc_accuracy.md](doc_accuracy.md) | Doc audit + anti-drift governance (CLAUDE/README/ROADMAP/VISION/docs) | T1 drift LOCATED 2026-07-25: README+ROADMAP still describe the deleted probe/scroll gates |

## Reference

| File | What |
|------|------|
| [finished.md](finished.md) | Append-only ledger of shipped work (retired checklists land here) |
| [safety_phase1_notes.md](safety_phase1_notes.md) | Research backing for `safety_filter.md` Phase 1 (policy scope, hook map, tool survey) |
| [nicknotes.md](nicknotes.md) | Scratch: prompts to try + local-service startup commands |

---

## How to write a drainable workstream file

So an agent (or you, cold) can execute with zero prior conversation:

```
# <Workstream> — Work Plan
Verified: YYYY-MM-DD    — when Background was last checked against the code

## Why          — rationale + decisions already made ("do not re-litigate")
## Background   — POINTS AT code (file:line); never restates what the code says
## Guardrails   — reject-if rules (keep scope honest)
## Tasks        — ### T1 … with [ ] atomic subtasks, each with a "→ done when:" check
## Parked       — logged, not lost
```

Rules that make a file drainable:
- **Every `[ ]` carries a `→ done when:` check that a script or a person can evaluate to
  true/false with no judgement.** Best is a shell command with an expected result
  (`grep -n "X" path` is empty, a named test passes, a file exists). For a decision or a
  measurement, the check is that the recorded result exists in the file. This is what makes
  auditing this directory mechanical instead of seventeen LLM agents reading prose.
- **A check must be FALSE today.** If it already passes, the item is done — delete it.
- **`Verified:` stamp at the top.** Background rots silently; a date makes the rot visible
  without reading. Stamp it when you re-check, not when you edit.
- **Background points at code, it does not copy it.** Copied facts are the drift.
- **List verified real APIs up front** — kills hallucinated signatures.

No `## Ordering` section: sequencing across workstreams lives in `launch_plan.md`, and inside a
file the task numbers carry it.

## Lifecycle

- **An item lands → delete the line and add its `finished.md` entry in the same commit.** Do not
  leave `[x]` behind: a section that is half-shipped accumulates dead prose that reads as active
  work, which is exactly how 418 lines of it built up before the 2026-07-25 audit.
- Workstream fully done: remove it from the Active table, one summary line in `finished.md`.
- A file down to 3–4 items behind its own Why/Background/Guardrails is overhead — merge it into a
  sibling (that is what `platform_polish.md` is).
