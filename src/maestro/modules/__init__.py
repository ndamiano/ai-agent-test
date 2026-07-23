"""maestro.modules — the module ABC.

The old IR mechanic-modules are gone. What remains is the behavior contract (`Module`/`Check`/
`Error`) the codegen path builds on; a module is instantiated directly, not resolved from a registry.
"""

from maestro.modules.module import (  # noqa: F401
    Check,
    Error,
    ErrorType,
    Module,
    idkey,
)
