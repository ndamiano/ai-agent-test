"""economy — flags / variables / items + the effect/condition vocabulary.

A VOCABULARY module: it owns no component of its own. Its data rides inside the other modules'
beats — flags/variables/items are declared on `nodes`/`places` and mutated by `effects` on a
line / choice / use-outcome; `conditions` gate choices and moves. assemble_ir lifts the declared
state to the IR top level (presence-driven), and ir_crossref already validates every effect/
condition reference. The module exists so a composition can *name* the economy dependency
explicitly (e.g. card_play's ante spends a variable → depends_on economy) and so the roster
documents that this capability is present, even though it contributes no component or baseline.
"""

from maestro.modules import Module

MODULE = Module(id="economy")
