"""Sprite sheets rendered from a mesh the build asked for, instead of drawn by a video model.

MiniMax-H3 conditions a clip on the still as BOTH first and last frame, so a one-shot — an attack,
a death, a dodge — is asked to end where it began and answers by not moving: 14 of 16 clips in a
shipped sheet were stills. Here the character becomes a mesh, the mesh is rigged, a motion model
animates the skeleton and the sheet is RENDERED, so frame count, loop points, facings and timing
are the game's to choose.

The stages, each its own module:
  `verbs`     the motion library, as embeddings baked once so no language model runs in prod
  `rig`       a canonical humanoid measured onto a T-posed mesh, and skinned
  `retarget`  a motion clip moved onto that skeleton
  `sheet`     four camera angles x N frames, packed into the manifest `lib/sprites.js` reads
"""
