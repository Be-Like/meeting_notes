#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="${VIRTUAL_ENV:-$repo_root/.venv}/bin"
if [ ! -x "$bin/ruff" ] || [ ! -x "$bin/pytest" ]; then
  echo "ruff and pytest not found in $bin; run pip install -r requirements-dev.txt" >&2
  exit 2
fi
cd "$repo_root"
"$bin/ruff" format --check .
"$bin/ruff" check .
# pytest exits 5 when no tests are collected. That is not a gate failure, so it is treated as a pass.
"$bin/pytest" || { status=$?; [ "$status" -eq 5 ] || exit "$status"; }
