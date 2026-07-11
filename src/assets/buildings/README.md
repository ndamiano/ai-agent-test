# Building shell registry

Hand-made building models keyed by footprint GEOMETRY, not semantics: a shell is "a 2x3
building with a front door", never "a tavern". `registry.json` declares them:

```json
{
  "shells": [
    {"file": "timber_2x3.glb", "w": 2, "h": 3, "style": "timber"},
    {"file": "timber_3x3.glb", "w": 3, "h": 3, "style": "timber"}
  ]
}
```

At compile, `godot/building_registry.py` resolves each doored footprint — exact w x h first,
then the transposed footprint — copies the chosen .glb into the project, and writes the pick
onto the footprint (`fp.shell_file`); the runtime loads what it's told. Tier order per
footprint: registry shell -> the run's generated TRELLIS mesh -> parametric blockout (box +
pitched roof + door slab, textured from ../tiles) -> sprite billboard -> blocked mosaic.

Authoring convention: front (entrance face) looks down +Z, door visually centered on the
front, base at y=0; the presenter scales the longest side to the footprint span and yaws the
front toward the doorstep cell. Style variants are separate entries sharing (w, h) with a
different `style` tag — resolution prefers the requested style, falls back to the first
match. Start with one neutral style; texture-projection per game comes later.
