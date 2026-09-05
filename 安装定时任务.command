#!/bin/bash
# 双击安装 launchd 定时任务（每天 18:30 自动跑数据管道，开机自启）
cd "$(dirname "$0")"
SRC="deploy/macos/com.dshquant.daily-pipeline.plist"
DST="$HOME/Library/LaunchAgents/com.dshquant.daily-pipeline.plist"
if [ -f "$SRC" ]; then
  mkdir -p "$(dirname "$DST")"
  cp "$SRC" "$DST"
  echo "已复制 plist 到 $DST"
fi
launchctl unload "$DST" 2>/dev/null
launchctl load -w "$DST"
echo "== 已加载，当前状态： =="
launchctl list | grep dshquant || echo "(未找到 dshquant 任务，请检查上方报错)"
