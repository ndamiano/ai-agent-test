"""The build path: the local model writes plain browser HTML/CSS/JS, one file at a time.

A run holds an ASK — the user's words, verbatim — and a PROMPT (`run.propose_prompt` /
`run.set_prompt`): the systems DESIGN of the ask (`design`), which the human approves and edits,
sent verbatim as the build's one user message. The build is a CHAIN of `llm` jobs: `build_chain` seeds
the game folder and enqueues one llm turn; the control-plane's `/worker/complete` reloads the
durable cursor (`build_state.json`), applies the turn, and enqueues the next — no resident loop.
`build_steps` is the turn machine, and `index.html` existing is the whole of "built".
"""
