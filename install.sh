#!/usr/bin/env bash
# Install the math-rigor suite into ZCode's user scope.
# Creates a virtualenv, installs dependencies, then registers the MCP server,
# the skill, and the slash commands.  Safe to re-run.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/venv"
VENV_PYTHON="$VENV/bin/python"

echo "math-rigor installer"
echo "repository root: $ROOT"
echo

# --- 1. locate a Python interpreter ---------------------------------------- #
if [ ! -x "$VENV_PYTHON" ]; then
  SYSTEM_PYTHON=""
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
        SYSTEM_PYTHON="$candidate"
        echo "found $candidate ($("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])'))"
        break
      fi
    fi
  done
  if [ -z "$SYSTEM_PYTHON" ]; then
    echo "ERROR no Python 3.10+ interpreter found on PATH." >&2
    exit 1
  fi

  echo
  echo "creating the virtualenv at $VENV"
  "$SYSTEM_PYTHON" -m venv "$VENV"

  echo
  echo "installing dependencies (this downloads sympy and z3, ~60 MB)"
  "$VENV_PYTHON" -m pip install --quiet --upgrade pip
  "$VENV_PYTHON" -m pip install --quiet -r "$ROOT/requirements.txt"
else
  echo "reusing the existing virtualenv at $VENV"
fi

# --- 2. prove the server actually starts before touching any config --------- #
echo
echo "checking that the server starts"
"$VENV_PYTHON" "$ROOT/tools/smoke_test.py"

# --- 3. install into ZCode -------------------------------------------------- #
echo
echo "installing into ZCode"
"$VENV_PYTHON" "$ROOT/tools/install.py"

echo
echo "done."
