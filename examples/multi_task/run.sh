#!/bin/bash
# run.sh — 一键运行多卡任务流示例
# 自动使用 uv 虚拟环境
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
VENV_PYTHON="$PROJECT_DIR/env/.venv/bin/python3"

if [ -x "$VENV_PYTHON" ]; then
    cd "$PROJECT_DIR"
    exec "$VENV_PYTHON" examples/multi_task/task_flow.py "$@"
else
    echo "请先初始化 uv 环境: cd $PROJECT_DIR/env && uv venv && source .venv/bin/activate && uv pip install numpy rich"
    exit 1
fi
