#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
VENV="$PROJECT_DIR/env/.venv/bin/python3"
[ -x "$VENV" ] && exec "$VENV" "$SCRIPT_DIR/pipeline.py" "$@"
echo "请先: cd $PROJECT_DIR/env && uv venv && source .venv/bin/activate && uv pip install numpy rich"
exit 1
