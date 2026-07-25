"""Codegen build path: the local model writes real TS game code against the primitive kit.

Replaces the IR path. The chat model drafts a design SPEC (stage 1), the human freezes it, then the
build (stage 2) drives ONE `CodegenModule`'s gates to green. The build is a CHAIN of `llm` jobs:
`build_chain` seeds the scaffolds, sweeps the gates, and enqueues one llm turn; the control-plane's
`/worker/complete` reloads the durable cursor (`build_state.json`), applies the turn, and enqueues the
next — no resident loop. `build_steps` holds the per-shape fix machines (plan/data/author/read→edit).
The gate gradient (typecheck + headless/render smoke) decides "done", never the model.
"""
