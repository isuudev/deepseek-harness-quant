#!/bin/bash
# 双击启动 DeepSeek HARNESS Quant 看板服务（macOS）
# 关闭方式：在本窗口按 Ctrl+C，或直接关闭窗口
cd "$(dirname "$0")"
echo "== 启动看板服务 http://127.0.0.1:8787 =="
echo "== 浏览器打开 http://127.0.0.1:8787 查看 =="
exec .venv/bin/python deck/deck_server.py --port 8787
