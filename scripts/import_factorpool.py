# -*- coding: utf-8 -*-
"""scripts/import_factorpool.py — 外包因子池代码库同步（外部 → data/factorpool/）

背景：Deck 状态栏 温度/宽度/拥挤度 显示「—」= 外包因子池产物缺失。主系统内因子池
落位 data/factorpool/（对应 Windows 主机 C:\\Users\\12985\\Desktop\\量化代码库思路库\\因子池，
路径映射见 scripts/build_update.py）。本脚本把外部因子池目录幂等同步进来：

  python scripts/import_factorpool.py --from <因子池目录> [--dry-run] [--no-output]

规则（幂等、可重复）：
  1. 逐文件比较：目标不存在 / 大小不同 / 源更新 → 拷贝（shutil.copy2 保留 mtime）
  2. output/ 产物区默认一并同步（含温度/宽度/拥挤度三件套 JSON）；--no-output 跳过
  3. 同步后校验 core/scheduler.py 存在，并提示后续步骤（scheduler daily → 验收）

退出码：0 成功；1 源目录无效 / 缺 core/scheduler.py
"""
import argparse
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TARGET = BASE / "data" / "factorpool"

# 大文件/缓存目录不逐文件报告，但默认仍同步（三件套产物也在 output/ 下，必须带）
_SKIP_REPORT_DIRS = {".git", "__pycache__", ".venv"}


def _files(src: Path):
    for p in sorted(src.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_REPORT_DIRS for part in p.parts):
            continue
        yield p


def main():
    ap = argparse.ArgumentParser(description="外包因子池代码库同步 → data/factorpool/")
    ap.add_argument("--from", dest="src", required=True, help="外部因子池根目录")
    ap.add_argument("--dry-run", action="store_true", help="只比对不拷贝")
    ap.add_argument("--no-output", action="store_true", help="跳过 output/ 产物区（仅同步代码）")
    args = ap.parse_args()

    src = Path(args.src)
    if not (src / "core" / "scheduler.py").exists():
        print(f"✗ 源目录无效或缺少 core/scheduler.py: {src}")
        print("  请确认 --from 指向因子池根目录（量化代码库思路库/因子池）。")
        return 1

    target = TARGET
    target.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for p in _files(src):
        rel = p.relative_to(src)
        if args.no_output and rel.parts and rel.parts[0] == "output":
            continue
        dst = target / rel
        if dst.exists() and dst.stat().st_size == p.stat().st_size \
                and dst.stat().st_mtime >= p.stat().st_mtime:
            skipped += 1
            continue
        if not args.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
        copied += 1

    mode = "（dry-run 未落盘）" if args.dry_run else ""
    print(f"同步完成{mode}: 拷贝 {copied} · 跳过(相同) {skipped}")
    print(f"落位: {target}")
    if not args.dry_run:
        ok = (target / "core" / "scheduler.py").exists()
        print(f"core/scheduler.py: {'✅ 存在' if ok else '❌ 仍缺失'}")
        print()
        print("后续步骤:")
        print("  1) 生成三件套（scheduler 幂等）:")
        print(f"     python core/scheduler.py daily        # CWD = {target}")
        print("     或跑主链路补跑: python data/daily_pipeline.py（步骤 2.7 已挂接）")
        print("  2) 验收（PASS = 状态栏温度/宽度/拥挤度亮起）:")
        print("     python data/verify_outsourced_market.py")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
