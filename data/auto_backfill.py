# -*- coding: utf-8 -*-
"""bulk_loader 自动重启包装：熔断退出后自动续传，直到全市场数据回填完成。

用法：
  python data/auto_backfill.py                 # 默认：单进程 + 每轮间隔 300s
  python data/auto_backfill.py --workers 1     # 并发进程数（baostock 免费源限流，1 最稳）
  python data/auto_backfill.py --pause 300     # 每轮之间的等待秒数（限流恢复缓冲）
  python data/auto_backfill.py --target 5150   # 达到该股票数即视为完成

原理：
  bulk_loader 本身有断点续传 + 重试 + 熔断。但连续 3 轮熔断会「自我保护退出」，
  需要人工重跑。本脚本把它包成循环：退出后等 --pause 秒自动再拉起，直到达标。
"""
import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "cache" / "bars.db"
BULK = BASE / "data" / "bulk_loader.py"


def count_codes():
    try:
        con = sqlite3.connect(str(DB))
        try:
            return con.execute("SELECT COUNT(DISTINCT code) FROM daily_bar").fetchone()[0]
        finally:
            con.close()
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--pause", type=int, default=300, help="每轮之间等待秒数")
    ap.add_argument("--target", type=int, default=5150, help="达到该股票数即视为完成")
    ap.add_argument("--max-rounds", type=int, default=0, help="最大轮数，0=不限")
    args = ap.parse_args()

    round_no = 0
    while True:
        round_no += 1
        n = count_codes()
        print(f"\n[auto_backfill] 第 {round_no} 轮开始 | 已入库 {n} 只 / 目标 {args.target}",
              flush=True)
        if n >= args.target:
            print(f"[auto_backfill] 全量完成（{n} 只）", flush=True)
            return 0

        # 运行 bulk_loader（子进程 stdout 直接透传，便于观察进度）
        r = subprocess.run(
            [sys.executable, str(BULK), "--workers", str(args.workers)],
            cwd=str(BASE),
        )

        n = count_codes()
        if n >= args.target:
            print(f"[auto_backfill] 全量完成（{n} 只）", flush=True)
            return 0

        if r.returncode == 2:
            print("[auto_backfill] 检测到另一个 bulk_loader 实例正在运行，停止包装（避免冲突）",
                  flush=True)
            return 2

        if args.max_rounds and round_no >= args.max_rounds:
            print(f"[auto_backfill] 达到最大轮数 {args.max_rounds}，停止（当前 {n} 只）",
                  flush=True)
            return 1

        print(f"[auto_backfill] 本轮退出（码 {r.returncode}），{args.pause} 秒后自动重启…",
              flush=True)
        time.sleep(args.pause)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[auto_backfill] 已手动中断", flush=True)
        sys.exit(130)
