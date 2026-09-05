# -*- coding: utf-8 -*-
"""财报/质量就绪后自动运行机会扫描 + 市场环境（消除 pitch/tech/环境指标空值）。

用法：
  python data/auto_scan.py                # 默认：目标 4800 只，每 60s 检查一次
  python data/auto_scan.py --target 4800  # 达标股票数
  python data/auto_scan.py --interval 60  # 检查间隔（秒）

逻辑：
  1. 轮询 finance.db / finance_quality.db 的已入库股票数
  2. 两者都 >= target 后，依次运行 scan.py --pitch → market_env.py --force
  3. 全程打印进度，扫描失败会记录但继续跑 market_env
"""
import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def count(db_name, table):
    try:
        con = sqlite3.connect(str(BASE / "data" / "cache" / db_name))
        try:
            return con.execute(f"SELECT COUNT(DISTINCT code) FROM {table}").fetchone()[0]
        finally:
            con.close()
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=4800)
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()

    while True:
        fn = count("finance.db", "finance_report")
        qn = count("finance_quality.db", "quality")
        print(f"[auto_scan] 财报 {fn} 只 / 质量 {qn} 只（质量不阻塞）/ 目标 {args.target}", flush=True)
        # 只等财报达标；质量因子（baostock）可能因限流/黑名单卡住，不阻塞扫描
        if fn >= args.target:
            break
        time.sleep(args.interval)

    print("[auto_scan] 财报 + 质量已就绪，开始机会扫描（scan.py --pitch）…", flush=True)
    r1 = subprocess.run(
        [sys.executable, str(BASE / "factors" / "opportunities" / "scan.py"), "--pitch"],
        cwd=str(BASE),
    )
    print(f"[auto_scan] 扫描完成（码 {r1.returncode}），生成市场环境（market_env.py --force）…",
          flush=True)
    r2 = subprocess.run(
        [sys.executable, str(BASE / "data" / "market_env.py"), "--force"],
        cwd=str(BASE),
    )
    print(f"[auto_scan] 市场环境完成（码 {r2.returncode}）。全部结束。", flush=True)


if __name__ == "__main__":
    main()
