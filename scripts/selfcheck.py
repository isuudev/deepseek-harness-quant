#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/selfcheck.py — 静态回归自检（★2026-09-06 新增 · AGENTS.md「改动后必须回归验证」的落点）

改动代码后运行本脚本做无网络、无数据库的静态检查：
  1. 语法：compile 全部业务 .py（内存编译，不产生 .pyc；排除 .venv / node_modules / 打包产物）
  2. 关键文件存在性：入口 / 配置模板 / V2 页面 / 技能（harness+assets 双份）
  3. 产出 JSON：output/ 关键状态文件可解析且含必要字段

用法：
  python scripts/selfcheck.py        # 全检，退出码 0=通过 / 1=存在失败项
  python scripts/selfcheck.py --json # 输出 JSON 汇总（供管道消费）
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# 跳过的目录部件（任一级命中即跳过）
SKIP_DIRS = {".venv", "node_modules", "__pycache__", "updates", "backups", "dist", "build"}

REQUIRED_FILES = [
    "launcher.py", "VERSION", "README.md", ".gitignore",
    "config/params.yaml.example", "config/strategies.yaml.example", "config/etf_pool.yaml.example",
    "data/cache.py", "data/_platform.py",
    "deck/deck_server.py", "deck/system_live.py",
    "factors/factor_engine.py", "factors/opportunities/scan.py",
    "strategy/portfolio.py", "risk/risk_agent.py", "backtest/bt_runner.py",
    "ui_v2/pages/portal.html", "ui_v2/pages/pitch.html", "ui_v2/pages/holdings.html",
    "ui_v2/pages/factors.html", "ui_v2/pages/help.html", "ui_v2/pages/control.html",
    "harness/home/skills/backtest-acceptance/SKILL.md",
    "assets/skills/backtest-acceptance/SKILL.md",
    "scripts/macos/com.dshquant.deck.plist.example",
    "scripts/macos/com.dshquant.daily-pipeline.plist.example",
]

# (文件, 必要字段列表)；文件缺失只告警不失败（数据未就绪属正常态）
JSON_CHECKS = [
    ("output/harness_state.json", ["harness", "agent", "goal"]),
    ("output/timing_system.json", ["level", "score", "dims"]),
    ("output/traffic_light.json", ["state", "asof"]),
]


def check_python() -> tuple:
    ok = fail = 0
    fails = []
    for p in sorted(BASE.rglob("*.py")):
        rel = p.relative_to(BASE)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
            compile(src, str(p), "exec")
            ok += 1
        except SyntaxError as e:
            fail += 1
            fails.append(f"{rel}:{e.lineno} {e.msg}")
        except Exception as e:
            fail += 1
            fails.append(f"{rel}: {e}")
    return ok, fail, fails


def check_required() -> tuple:
    ok = fail = 0
    fails = []
    for rel in REQUIRED_FILES:
        if (BASE / rel).exists():
            ok += 1
        else:
            fail += 1
            fails.append(rel)
    return ok, fail, fails


def check_json() -> tuple:
    ok = fail = warn = 0
    fails, warns = [], []
    for rel, keys in JSON_CHECKS:
        p = BASE / rel
        if not p.exists():
            warn += 1
            warns.append(f"{rel} 不存在（数据未生成，属正常态）")
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            missing = [k for k in keys if k not in d]
            if missing:
                fail += 1
                fails.append(f"{rel} 缺字段 {missing}")
            else:
                ok += 1
        except Exception as e:
            fail += 1
            fails.append(f"{rel} 解析失败: {e}")
    return ok, fail, warn, fails, warns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    args = ap.parse_args()

    py_ok, py_fail, py_fails = check_python()
    req_ok, req_fail, req_fails = check_required()
    js_ok, js_fail, js_warn, js_fails, js_warns = check_json()

    total_fail = py_fail + req_fail + js_fail
    summary = {
        "python": {"ok": py_ok, "fail": py_fail, "details": py_fails[:20]},
        "required_files": {"ok": req_ok, "fail": req_fail, "details": req_fails},
        "json_outputs": {"ok": js_ok, "fail": js_fail, "warn": js_warn,
                         "details": js_fails[:20], "warnings": js_warns},
        "result": "PASS" if total_fail == 0 else "FAIL",
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print(f"[selfcheck] Python 语法: {py_ok} OK / {py_fail} FAIL")
        for d in py_fails[:20]:
            print(f"  ✗ {d}")
        print(f"[selfcheck] 关键文件: {req_ok} OK / {req_fail} 缺失")
        for d in req_fails:
            print(f"  ✗ {d}")
        print(f"[selfcheck] 产出 JSON: {js_ok} OK / {js_fail} FAIL / {js_warn} 缺失(正常态)")
        for d in js_fails[:20]:
            print(f"  ✗ {d}")
        for w in js_warns:
            print(f"  ⚠ {w}")
        print(f"[selfcheck] 结果: {'PASS' if total_fail == 0 else 'FAIL'}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
