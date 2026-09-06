#!/bin/bash
# 双击安装 launchd 定时任务（18:30 每日全链 / 20:00 日报自动化），装一次即可
cd "$(dirname "$0")"
for SRC in deploy/macos/*.plist; do
  [ -f "$SRC" ] || continue
  NAME="$(basename "$SRC")"
  DST="$HOME/Library/LaunchAgents/$NAME"
  mkdir -p "$(dirname "$DST")"
  cp "$SRC" "$DST"
  echo "已复制 plist 到 $DST"
  launchctl unload "$DST" 2>/dev/null
  launchctl load -w "$DST"
done
echo "== 已加载，当前状态： =="
launchctl list | grep dshquant || echo "(未找到 dshquant 任务，请检查上方报错)"
