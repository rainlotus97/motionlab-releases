#!/bin/zsh
set -e
cd "$(dirname "$0")"
if ! python3 scripts/setup_studio.py; then
  print '启动未完成。请按上方提示处理，再双击打开。按回车关闭。'
  read
  exit 1
fi
