# -*- coding: utf-8 -*-
"""Windows 全环境发布包生成器（代码 + HARNESS 运行时 + 便携 Python + 便携 Node，解压即用）。

用法：
    python scripts/build_full.py --py <便携venv> --node <便携node目录> [--version 1.0.8]

历史教训：
  - build/ dist/ 等目录名**只在各自来源树的顶层排除**——node_modules 内各包的
    dist/（js-yaml/dist 等）、build/（typebox/build 等）必须保留，否则 DSH 报 ERR_MODULE_NOT_FOUND
"""
import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_filter import should_skip, default_out_dir   # ★2026-09-06 R4：安全过滤（数据边界/密钥/用户数据）

ROOT = Path(__file__).resolve().parent.parent
# 顶层目录排除（★2026-09-06 增补 .git/.pytest_cache；node_modules 必须保留=HARNESS 运行时）
TOP_SKIP = {"build", "backups", "updates", "__pycache__", ".venv", "dist", ".git", ".pytest_cache"}


def copy_tree(src: Path, dst: Path, root_filter: bool = True):
    for root, dirs, files in os.walk(src):
        if root == str(src):
            dirs[:] = [d for d in dirs if d not in TOP_SKIP]
        else:
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
        rel = os.path.relpath(root, src)
        tgt = dst if rel == "." else dst / rel
        tgt.mkdir(parents=True, exist_ok=True)
        for f in files:
            relf = os.path.relpath(os.path.join(root, f), src).replace("\\", "/")
            if root_filter and should_skip(relf):
                continue
            shutil.copy2(os.path.join(root, f), tgt / f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--py", required=True, help="便携 Python venv 目录")
    ap.add_argument("--node", required=True, help="便携 Node 目录（node.exe 所在）")
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    ver = args.version or (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    out_dir = Path(args.out) if args.out else default_out_dir(ROOT)
    # ★2026-09-06 修复：暂存目录从硬编码 D:\quant-release 改为 --out 派生（macOS 可跑）
    stage = out_dir / "_full_stage" / "DSHQuant"
    if stage.exists():
        shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    copy_tree(ROOT, stage)                       # 仓库内容（安全过滤）
    copy_tree(Path(args.py), stage / "runtime" / "python", root_filter=False)
    copy_tree(Path(args.node), stage / "runtime" / "node", root_filter=False)
    out = out_dir / f"DSHQuant-v{ver}-Windows-Full.zip"
    if out.exists():
        out.unlink()
    n = 0
    with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(stage):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for f in files:
                p = os.path.join(root, f)
                rel = os.path.relpath(p, stage).replace("\\", "/")
                z.write(p, "DSHQuant/" + rel)
                n += 1
    shutil.rmtree(stage, ignore_errors=True)
    print(f"full: {out}  ({n} files / {out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
