# -*- coding: utf-8 -*-
"""质量因子拉取完成后自动重跑扫描（生成 pitch_v2，让 quality_gap 类机会生效）。

用法：
  python data/auto_rescan.py               # 默认：目标 4800 只，每 60s 检查
  python data/auto_rescan.py --target 4800
"""
import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def quality_count():
    try:
        con = sqlite3.connect(str(BASE / "data" / "cache" / "finance_quality.db"))
        try:
            return con.execute("SELECT COUNT(DISTINCT code) FROM quality").fetchone()[0]
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
        qn = quality_count()
        print(f"[auto_rescan] 质量因子 {qn} 只 / 目标 {args.target}", flush=True)
        if qn >= args.target:
            break
        time.sleep(args.interval)

    print("[auto_rescan] 质量因子达标，重跑扫描（scan.py --pitch）…", flush=True)
    r1 = subprocess.run(
        [sys.executable, str(BASE / "factors" / "opportunities" / "scan.py"), "--pitch"],
        cwd=str(BASE),
    )
    print(f"[auto_rescan] 扫描完成（码 {r1.returncode}），生成 pitch_v2…", flush=True)
    r2 = subprocess.run(
        [sys.executable, str(BASE / "factors" / "opportunities" / "pitch_v2.py")],
        cwd=str(BASE),
    )
    print(f"[auto_rescan] pitch_v2 完成（码 {r2.returncode}）。全部结束。", flush=True)


if __name__ == "__main__":
    main()
