#!/bin/bash
# run.sh — 一键运行四卡并行基线验证
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
VENV_PYTHON="$PROJECT_DIR/env/.venv/bin/python3"

if [ -x "$VENV_PYTHON" ]; then
    cd "$PROJECT_DIR"
    exec "$VENV_PYTHON" scripts/run_verify.py "$@"
else
    echo "请先初始化 uv 环境: cd $PROJECT_DIR/env && uv venv && source .venv/bin/activate && uv pip install numpy rich"
    exit 1
fi
