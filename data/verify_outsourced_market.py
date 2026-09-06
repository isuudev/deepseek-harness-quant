# -*- coding: utf-8 -*-
"""data/verify_outsourced_market.py — 外包市场数据三件套投递验收（2026-09 新增）

校验 data/factorpool/output/ 下的三个外包数据产物是否满足消费契约（docs/外包数据投递契约.md）：
  market_emotion_temp*.json   → temp（温度）
  market_snapshot_ext_*.json  → width5（宽度）
  crowding_*.json             → crowding_pctile_252（拥挤度）

验收口径与消费端逐字对齐（deck/live_api.py timing_dash / factors/policy/timing_system.py）：
  - glob 模式、字段名、round 精度完全一致 → 本脚本 PASS 即状态栏三项亮起
  - 文件存在但字段缺失/非法 → 逐项列出（消费端会静默跳过该文件，与"缺失"等效）
  - 快照 date 滞后 >3 日历日 → WARN（timing_system #135 同口径：风格判定跳过）

用法：
  python data/verify_outsourced_market.py            # 人读报告 + 模拟状态栏显示
  python data/verify_outsourced_market.py --json     # 机器可读输出

退出码：0 = 三件套齐备；1 = 有缺口（诚实反映"数据未接入"，不算系统故障）
"""
import glob
import json
import os
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SD = BASE / "data" / "factorpool" / "output"   # 与消费端 glob 目录一致（绝对化，避免 CWD 依赖）

PRODUCTS = {
    "temp": {
        "label": "温度（情绪温度计）",
        "glob": "market_emotion_temp*.json",
        "field": "temp",
        "round": 1,
        "consumers": "live_ticker 温度 · timing_system.sentiment_score",
    },
    "width": {
        "label": "宽度（市场快照扩展）",
        "glob": "market_snapshot_ext_*.json",
        "field": "width5",
        "round": 3,
        "consumers": "live_ticker 宽度 · timing_system.breadth_score",
    },
    "crowd": {
        "label": "拥挤度",
        "glob": "crowding_*.json",
        "field": "crowding_pctile_252",
        "round": 4,
        "consumers": "live_ticker 拥挤度 · 拥挤摘要卡",
    },
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_product(key: str) -> dict:
    """镜像消费端逻辑：glob 全文件 → 逐文件取字段（round 同精度）→ 得 hist 序列。"""
    spec = PRODUCTS[key]
    files = sorted(glob.glob(str(SD / spec["glob"])), key=os.path.getmtime)
    out = {"label": spec["label"], "files": [Path(f).name for f in files],
           "hist": [], "problems": [], "latest": None}
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as e:
            out["problems"].append(f"{Path(f).name}: JSON 解析失败（{str(e)[:60]}）")
            continue
        v = d.get(spec["field"])
        if v is None:
            out["problems"].append(f"{Path(f).name}: 缺字段 {spec['field']}（消费端将静默跳过）")
            continue
        n = _num(v)
        if n is None:
            out["problems"].append(f"{Path(f).name}: 字段 {spec['field']}={v!r} 非数值")
            continue
        out["hist"].append(round(n, spec["round"]))
    if files:
        try:
            d = json.loads(Path(files[-1]).read_text(encoding="utf-8"))
            out["latest"] = {"file": Path(files[-1]).name, "date": d.get("date")}
            # 快照新鲜度（#135 同口径：日历日 >3 告警）
            sdate = str(d.get("date") or "")
            if sdate and len(sdate) >= 10:
                try:
                    lag = (date.today() - date.fromisoformat(sdate[:10])).days
                    out["latest"]["lag_days"] = lag
                except Exception:
                    pass
        except Exception:
            pass
    return out


def main():
    as_json = "--json" in sys.argv
    results = {k: check_product(k) for k in PRODUCTS}
    all_ok = all(r["hist"] for r in results.values())

    if as_json:
        payload = {
            "ok": all_ok,
            "products": {k: {"label": r["label"], "files": r["files"],
                             "values": r["hist"], "problems": r["problems"],
                             "latest": r["latest"]} for k, r in results.items()},
            "ticker": {k: (r["hist"][-1] if r["hist"] else None) for k, r in results.items()},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0 if all_ok else 1

    print("== 外包市场数据三件套投递验收 ==")
    print(f"投递目录: {SD}\n")
    for k, r in results.items():
        tick = "✅" if r["hist"] else "❌"
        print(f"{tick} {r['label']}（{PRODUCTS[k]['consumers']}）")
        if r["files"]:
            print(f"   文件 {len(r['files'])} 个 → 序列 {r['hist']}")
        else:
            print(f"   无文件（glob: {r['glob']}）")
        for p in r["problems"]:
            print(f"   ⚠ {p}")
        if r["latest"]:
            lag = r["latest"].get("lag_days")
            lag_txt = f"（滞后 {lag} 天{'，>3 天风格判定将跳过' if lag is not None and lag > 3 else ''}）" if lag is not None else ""
            print(f"   最新: {r['latest']['file']} date={r['latest']['date']}{lag_txt}")
        print()

    print("== 模拟环境状态栏显示（取各序列末值，与 live_ticker.js 同逻辑）==")
    sim = {k: (r["hist"][-1] if r["hist"] else None) for k, r in results.items()}
    for k, label in (("temp", "温度"), ("width", "宽度"), ("crowd", "拥挤度")):
        print(f"  {label}: {sim[k] if sim[k] is not None else '—'}")

    print()
    if all_ok:
        print("结论: PASS —— 三件套齐备，状态栏三项将亮起（timing_dash 60s 轮询，无需重启）。")
    else:
        print("结论: 有缺口 —— 缺失项如实显示「—」。补齐后重跑本脚本，PASS 即接入完成。")
        print("详见 docs/外包数据投递契约.md；导入完整因子池可用 scripts/import_factorpool.py。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
