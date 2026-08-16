#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN="${PYTHON3:-python3}"

exec "$PYTHON_BIN" "$SCRIPT_DIR/setup.py" "$@"
