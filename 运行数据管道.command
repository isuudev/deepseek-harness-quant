#!/bin/bash
# 双击运行每日数据管道（macOS，收盘后全链）
cd "$(dirname "$0")"
echo "== 运行每日数据管道（18:30 全链：增量入库→机会扫描→Pitch→因子池→择时）=="
echo "== 周末会自动跳过；运行日志实时显示在下方 =="
exec .venv/bin/python -X utf8 data/daily_pipeline.py
