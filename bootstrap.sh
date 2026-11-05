#!/bin/bash
# 通过 PATH 或用户私有配置定位 Python；不记录开发机器安装位置。
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd -- "$script_dir"

python_command="${PYTHON_BINARY:-python3}"
if command -v "$python_command" >/dev/null 2>&1 &&
   "$python_command" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$python_command" -B main.py "$@"
fi

printf '%s\n' '{"items":[{"title":"需要 Python 3.10 或更新版本","subtitle":"安装 Python 3，或在 Workflow 配置的 PYTHON_BINARY 中填写你的解释器位置","valid":false}]}'
