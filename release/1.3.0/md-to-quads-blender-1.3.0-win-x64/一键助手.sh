#!/usr/bin/env bash
# MD布料转四边面 · 一键助手（macOS / Linux）
# 双击或终端执行：./一键助手.sh
set -e
cd "$(dirname "$0")"

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then PY="$(command -v python || true)"; fi
if [ -z "$PY" ]; then
  echo "  [!] 没有找到 python3。请先安装 Python 3，或到 blender.org 下载 Blender 后重试。"
  exit 1
fi

exec "$PY" tools/md_launcher.py "$@"
