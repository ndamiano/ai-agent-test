"""Codegen build path: the local model writes real JS game code against the primitive kit.

Replaces the IR path. The chat model drafts a design SPEC (stage 1), the human freezes it, then the
surviving `AgentLoop` drives ONE `CodegenModule` (stage 2) that authors/patches `game.js` against the
kit until the local gradient (headless smoke + probe invariants) passes. No engine backend, no IR.
"""
