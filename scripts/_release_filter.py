# -*- coding: utf-8 -*-
"""scripts/_release_filter.py — 发布包安全过滤器（★2026-09-06 新增 · R4 审计修复）

背景（R4 审计）：build_release.py / build_full.py 原实现 os.walk 全目录打包、
不尊重 .gitignore —— 会把 data/cache 行情库、harness/home/.credentials.yaml（真实
API Key）、quantapi_*.json（用户对话历史）、logs/output/report 运行产物、
deck/archive_old 历史快照（含行情数据）全部打进公开发布包：
  ① 数据边界违规（行情数据禁再分发）
  ② 密钥泄露（真实 sk- key）
  ③ 用户数据泄露（牛散对话含模型思考链）

设计：显式白名单外排除（比照 .gitignore 的数据/运行产物/敏感文件段），
**不排除 harness/node_modules**（发布包必须含 HARNESS 运行时，README 以
「解压后 harness/node_modules 存在 = 完整包」为判据）。

用法：
  from scripts._release_filter import should_skip
  should_skip("data/cache/bars.db")   # → True（排除）
  should_skip("harness/node_modules/@deepseek-ai/dsh/lib/bin.js")  # → False（保留）
"""
from pathlib import Path

# 目录前缀（含子路径一律排除）
SKIP_DIR_PREFIX = (
    "data/cache/", "data/factorpool/", "data/trash/",
    "logs/", "output/", "report/",
    "deck/archive_old/",
    "backups/", "updates/", "dist/", "build/",
)

# 精确文件（敏感配置/用户数据/内部文档）
SKIP_EXACT = {
    "config/params.yaml",
    "harness/home/.credentials.yaml",
    "harness/home/.anonymous-user-id",
    "harness/home/quantapi_niu_chat.json",
    "harness/home/quantapi_archived.json",
    "docs/上传GitHub.md",
    "docs/发布清单_v1.0.9.md",
}

# (目录, basename 通配) —— deck 运行产物快照
SKIP_GLOB = {
    ("deck", "*.json"),
    ("deck", "portal_*.html"),
    ("deck", "system_overview_*.html"),
    ("deck", "dashboard_*_20*.html"),
    ("deck", "dashboard_*.log"),
}

# 后缀（数据文件/打包产物）
SKIP_SUFFIX = (".zip", ".exe", ".db", ".sqlite", ".parquet", ".xlsx", ".log", ".spec", ".DS_Store")


def should_skip(rel: str) -> bool:
    """发布包是否排除该相对路径（posix 风格，如 data/cache/bars.db）"""
    rel = rel.replace("\\", "/")
    if rel in SKIP_EXACT:
        return True
    for pre in SKIP_DIR_PREFIX:
        if rel.startswith(pre):
            return True
    from fnmatch import fnmatch
    parts = rel.split("/")
    if len(parts) == 2:
        for d, pat in SKIP_GLOB:
            if parts[0] == d and fnmatch(parts[1], pat):
                return True
    if rel.endswith(SKIP_SUFFIX):
        return True
    return False


def default_out_dir(root: Path) -> Path:
    """发布输出目录默认值（★2026-09-06 平台化：Windows 保留 D:\\quant-release，
    其他平台回退仓库上级 quant-release/，避免在 macOS 上创建名为 D:\\quant-release 的怪目录）"""
    import os
    if os.name == "nt":
        return Path(r"D:\quant-release")
    return Path(root).resolve().parent / "quant-release"
