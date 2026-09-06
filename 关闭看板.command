#!/bin/bash
# 双击关闭 DeepSeek HARNESS Quant 看板服务（macOS）
# 对应「启动看板.command」，关闭占用 8787 端口的 deck_server 进程
cd "$(dirname "$0")"

PORT=8787
PIDS=$(lsof -ti :$PORT 2>/dev/null | sort -u)

if [ -z "$PIDS" ]; then
  echo "== 未发现监听端口 $PORT 的看板服务，可能已关闭 =="
  read -p "按回车键关闭本窗口..." _
  exit 0
fi

echo "== 正在关闭看板服务（端口 $PORT）=="
echo "   进程 PID: $PIDS"
kill $PIDS 2>/dev/null

# 等待进程释放端口
for _ in $(seq 1 20); do
  if ! lsof -ti :$PORT >/dev/null 2>&1; then
    break
  fi
  sleep 0.3
done

# 兜底：仍占用则强制结束
if lsof -ti :$PORT >/dev/null 2>&1; then
  echo "== 进程未退出，强制结束 =="
  kill -9 $PIDS 2>/dev/null
fi

echo "== 看板服务已关闭 =="
read -p "按回车键关闭本窗口..." _
