# -*- coding: utf-8 -*-
"""Strategy/factor evolution monitor.

This closes the monitoring loop without silently mutating production weights:
  health snapshots -> IC/status change -> retrain/pause/observe proposal -> report

It deliberately does NOT write params.yaml.  A proposed change must be accepted
by a human or an explicit follow-up workflow because automatic live reweighting
without out-of-sample acceptance is unsafe.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from data.config import load_params
from validation.evolution import RetrainDecisionEngine

POOL = BASE / "data" / "factorpool" / "output"
HEALTH = POOL / "health"
OUT = BASE / "output"
REPORT = BASE / "report"


def _health_files() -> list[Path]:
    return sorted(HEALTH.glob("health_*.csv"), key=lambda p: p.stat().st_mtime)


def _read(path: Path) -> dict[str, dict]:
    try:
        rows = list(csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines()))
    except Exception:
        return {}
    return {r.get("factor", ""): r for r in rows if r.get("factor")}


def _num(v):
    try:
        if v in (None, ""):
            return None
        return float(v)
    except Exception:
        return None


def _is_bad(status: str) -> bool:
    s = (status or "").lower()
    return any(x in s for x in ("失效", "反向", "衰减", "retired", "invalid", "漂移"))


def generate() -> dict:
    fs = _health_files()
    cfg = load_params()
    weights = cfg.get("weights") or {}
    latest_path = fs[-1] if fs else None
    prev_path = fs[-2] if len(fs) >= 2 else None
    latest = _read(latest_path) if latest_path else {}
    prev = _read(prev_path) if prev_path else {}
    engine = RetrainDecisionEngine(psi_threshold=0.20, performance_drop_threshold=0.30)
    rows = []
    for name, cur in latest.items():
        old = prev.get(name) or {}
        ic = _num(cur.get("icir120"))
        old_ic = _num(old.get("icir120"))
        change = None
        if ic is not None and old_ic not in (None, 0):
            change = (ic - old_ic) / abs(old_ic)
        status = cur.get("status") or ""
        if not prev_path:
            action = "wait_for_history"
            reason = "只有一份 health 快照，无法判断 IC 变化；已保留监控。"
        elif _is_bad(status):
            action = "pause_review"
            reason = "状态标记为反向/失效/衰减，停止作为新增权重来源并转人工复核。"
        elif ic is not None and ic < 0.15:
            action = "review_or_retire"
            reason = "ICIR120 低于 0.15，进入观察/退役复核。"
        elif change is not None and change <= -0.30:
            decision = engine.decide(psi=0.0, ic_change_pct=change, data_quality="good")
            action = "investigate" if decision["action"] == "pause" else decision["action"]
            reason = decision["reason"] + "（当前无分布数据，PSI 暂按 0，不能据此自动重训）"
        else:
            action = "continue"
            reason = "IC 与状态未触发暂停/重训阈值。"
        rows.append({
            "factor": name,
            "status": status,
            "icir120": ic,
            "prev_icir120": old_ic,
            "ic_change_pct": round(change, 4) if change is not None else None,
            "psi": None,
            "psi_note": "当前 health 契约不携带因子分布，PSI 待评估器回写后可用。",
            "proposed_action": action,
            "reason": reason,
        })
    actionable = [r for r in rows if r["proposed_action"] != "continue"]
    payload = {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_health": latest_path.name if latest_path else "",
        "previous_health": prev_path.name if prev_path else "",
        "n_factors": len(rows),
        "n_actionable": len(actionable),
        "auto_apply": False,
        "weight_version": weights.get("weight_version"),
        "weight_date": weights.get("weight_date"),
        "policy": "只产出建议，不自动修改 params.yaml；需回测/样本外验收后人工接受。",
        "actions": actionable,
        "factors": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name in (f"strategy_health_{tag}.json", "strategy_health.json"):
        (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# 策略/因子演进巡检 · {payload['generated_at']}",
        "",
        f"- health: `{payload['latest_health']}`",
        f"- previous: `{payload['previous_health'] or '—'}`",
        f"- factors: {len(rows)} | actionable: {len(actionable)}",
        "- auto_apply: false（仅建议，不自动改权重）",
        "",
        "| 因子 | 状态 | ICIR | 变化 | 建议 | 原因 |",
        "|---|---|---:|---:|---|---|",
    ]
    for r in actionable[:200]:
        ch = "—" if r["ic_change_pct"] is None else f"{r['ic_change_pct']*100:.1f}%"
        lines.append(f"| {r['factor']} | {r['status']} | {r['icir120']} | {ch} | {r['proposed_action']} | {r['reason']} |")
    (REPORT / "strategy_health.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    r = generate()
    print(json.dumps({k: r[k] for k in ("latest_health", "previous_health", "n_factors", "n_actionable", "auto_apply")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
