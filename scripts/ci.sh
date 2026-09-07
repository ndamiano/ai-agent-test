#!/usr/bin/env bash
# The whole of .github/workflows/ci.yml, run locally. The pre-commit hook is a subset
# scoped to staged files; this is what a push is graded against.
set -uo pipefail
root=$(git rev-parse --show-toplevel)
for v in $(compgen -e GIT_); do unset "$v"; done

py="$root/venv/bin/python"
[ -x "$py" ] || py=python
fail=0

step() {
    local name=$1; shift
    echo "== $name"
    if "$@"; then return 0; fi
    echo "FAIL: $name"
    fail=1
}

step "pytest" bash -c "cd '$root/src' && '$py' -m pytest ../tests/ -q -n auto -p no:cacheprovider"
step "ruff F401/F811/F841" bash -c "cd '$root' && '$py' -m ruff check src scripts run.py --select F401,F811,F841"
step "eslint" bash -c "cd '$root/frontend' && npx eslint ."
step "tsc" bash -c "cd '$root/frontend' && npx tsc -b --noEmit"
step "vitest" bash -c "cd '$root/frontend' && npm test -- --run"
step "vite build" bash -c "cd '$root/frontend' && npm run build"

[ "$fail" -eq 0 ] && echo "== all green" || echo "== red"
exit "$fail"
