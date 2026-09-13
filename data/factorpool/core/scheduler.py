# -*- coding: utf-8 -*-
"""Local factor-pool scheduler (drop-in replacement for the external scheduler).

The upstream factor-pool repository is not distributed with this open repository.
This module keeps the local lifecycle pipeline runnable and produces the files
consumed by scan.py, live_api.py and the factor page:

  - factor_manifest_YYYYMMDD.json
  - health/health_YYYYMMDD.csv
  - daily_scores/daily_YYYYMMDD.csv
  - ui_data/factor_lifecycle_YYYYMMDD.json
  - ui_data/factor_data_freshness_YYYYMMDD.json

Modes:
  daily   bounded evaluation (config factor_pool.daily_eval_limit) + refresh outputs
  full    evaluate all unlocked candidates/active factors + refresh outputs
  report  write factor-pool report only
  status  print registry summary

Rank columns in daily_scores are local reproducible proxies for factors whose
upstream external definitions are unavailable. They are explicitly marked in
the generated metadata; they must not be represented as external-provider values.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE))

import numpy as np
import pandas as pd

from data.cache import CACHE_DIR
from data.config import load_params
from factors.pool.registry import FactorRegistry
from factors.pool.lifecycle import evaluate_pool, fetch_policy_data, write_report

OUT = BASE / "data" / "factorpool" / "output"
HEALTH = OUT / "health"
DAILY = OUT / "daily_scores"
UI = OUT / "ui_data"
BARS = CACHE_DIR / "bars.db"

FAMILY_TO_CATEGORY = {
    "价值": "value", "成长": "growth", "质量": "fundamental_lowfreq",
    "量价": "kline", "情绪": "short_term", "反转动量": "a_share_alpha",
    "资金": "institution", "政策": "policy", "行业": "industry",
    "筹码": "chip", "流动性": "liquidity", "动量": "momentum", "其他": "other",
}


def _latest(pattern: str, sub: Path) -> Path | None:
    fs = sorted(sub.glob(pattern), key=lambda p: p.stat().st_mtime)
    return fs[-1] if fs else None


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _data_date() -> str:
    if not BARS.exists():
        return datetime.now().strftime("%Y-%m-%d")
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{BARS}?mode=ro&immutable=1", uri=True, timeout=5)
        row = con.execute("SELECT MAX(date) FROM daily_bar WHERE adjust='qfq'").fetchone()
        con.close()
        return row[0] if row and row[0] else datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _pid_alive(pid: int) -> bool:
    try:
        import os
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _acquire_lock(allow_pipeline_lock: bool = False) -> bool:
    if not allow_pipeline_lock:
        dp_lock = BASE / "data" / "logs" / "daily_pipeline.lock"
        try:
            if dp_lock.exists() and (__import__("time").time() - dp_lock.stat().st_mtime) < 7200:
                return False
        except Exception:
            pass
    lock = BASE / "data" / "logs" / "factor_scheduler.lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists():
            txt = lock.read_text(encoding="utf-8").strip()
            if txt.isdigit() and _pid_alive(int(txt)):
                return False
        import os
        lock.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return True


def _release_lock() -> None:
    lock = BASE / "data" / "logs" / "factor_scheduler.lock"
    try:
        import os
        if lock.exists() and lock.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock.unlink()
    except Exception:
        pass


def _status_cn(status: str) -> str:
    return {
        "active": "active",
        "candidate": "candidate（本机未评估）",
        "retired": "retired",
        "monitoring": "monitoring",
        "evaluating": "evaluating",
    }.get(status or "", status or "")


def _read_health() -> tuple[dict[str, dict], list[str]]:
    f = _latest("health_*.csv", HEALTH)
    if not f:
        return {}, []
    try:
        rows = list(csv.DictReader(f.read_text(encoding="utf-8-sig").splitlines()))
    except Exception:
        return {}, []
    return {r.get("factor", ""): r for r in rows if r.get("factor")}, (list(rows[0].keys()) if rows else [])


def _mark_unsupported(reg: FactorRegistry) -> int:
    try:
        from factors.factor_engine import FACTOR_FUNCS
        supported = set(FACTOR_FUNCS)
        n = 0
        for f in reg.list_factors(status="candidate"):
            if f.get("kind") != "cross_sectional" or f["name"] in supported:
                continue
            detail = f.get("last_eval_detail") or {}
            if detail.get("evaluation_status") == "not_evaluable_local":
                continue
            reg.update_score(f["name"], None, status=None, detail={
                "evaluation_status": "not_evaluable_local",
                "reason": "本地 factor_evaluator 未登记该因子；保留 candidate，不自动淘汰。",
            })
            n += 1
        return n
    except Exception:
        return 0


def _write_manifest(reg: FactorRegistry, date_tag: str) -> Path:
    old = _json(_latest("factor_manifest_*.json", OUT) or Path("/__missing__"))
    old_map = {}
    for x in old.get("factors", []):
        if isinstance(x, dict):
            old_map[x.get("code") or x.get("name")] = x
    factors = []
    for f in reg.list_factors():
        prev = old_map.get(f["name"], {})
        detail = f.get("last_eval_detail") or {}
        factors.append({
            "code": f["name"],
            "name": f["name"],
            "category": prev.get("category") or FAMILY_TO_CATEGORY.get(f.get("family"), "other"),
            "family": f.get("family") or prev.get("family") or "",
            "icir_60": prev.get("icir_60"),
            "direction": f.get("direction", 1),
            "status": _status_cn(f.get("status")),
            "usage": prev.get("usage") or "signal",
            "name_cn": prev.get("name_cn") or f.get("note") or f["name"],
            "score": f.get("score"),
            "kind": f.get("kind"),
            "locked": bool(f.get("locked")),
            "last_eval_at": f.get("last_eval_at") or "",
            "detail": detail if detail else None,
        })
    payload = {
        "date": _data_date(),
        "health_date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "local factors/pool scheduler",
        "n_factors": len(factors),
        "factors": factors,
    }
    p = OUT / f"factor_manifest_{date_tag}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _write_health(reg: FactorRegistry, date_tag: str) -> Path:
    HEALTH.mkdir(parents=True, exist_ok=True)
    old, cols = _read_health()
    base_cols = cols or ["factor", "icir120", "status", "t120", "ic_win_rate", "crowding"]
    for extra in ("local_score", "last_eval_at"):
        if extra not in base_cols:
            base_cols.append(extra)
    rows = []
    for f in reg.list_factors():
        row = dict(old.get(f["name"], {}))
        row["factor"] = f["name"]
        row["status"] = _status_cn(f.get("status"))
        row["local_score"] = "" if f.get("score") is None else f.get("score")
        row["last_eval_at"] = f.get("last_eval_at") or ""
        rows.append(row)
    p = HEALTH / f"health_{date_tag}.csv"
    with p.open("w", encoding="utf-8", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=base_cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def _rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, ascending=True)


def _write_daily_scores(date_tag: str) -> Path | None:
    """Build local reproducible rank columns from bars.db.

    These are local proxy definitions; external factor-pool ranks, when present,
    remain the preferred production source.
    """
    import sqlite3
    if not BARS.exists():
        return None
    con = sqlite3.connect(f"file:{BARS}?mode=ro&immutable=1", uri=True, timeout=15)
    try:
        latest = con.execute("SELECT MAX(date) FROM daily_bar WHERE adjust='qfq'").fetchone()[0]
        if not latest:
            return None
        start = con.execute(
            "SELECT MIN(date) FROM (SELECT DISTINCT date FROM daily_bar WHERE adjust='qfq' AND date<=? ORDER BY date DESC LIMIT 320)",
            (latest,)).fetchone()[0]
        rows = con.execute(
            "SELECT date,code,close,turn,pct_chg FROM daily_bar "
            "WHERE adjust='qfq' AND date>=? AND date<=? "
            "AND code NOT LIKE 'sh.%' AND code NOT LIKE 'sz.%'",
            (start, latest)).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "code", "close", "turn", "pct_chg"])
    close = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    turn = df.pivot_table(index="date", columns="code", values="turn", aggfunc="last").sort_index()
    pct = df.pivot_table(index="date", columns="code", values="pct_chg", aggfunc="last").sort_index()
    if close.empty:
        return None
    close = close.ffill()
    rank_cols = {}
    raw_cols = {}
    turn20 = turn.rolling(20, min_periods=10).mean().iloc[-1]
    turn60 = turn.rolling(60, min_periods=20).median().iloc[-1]
    rev20 = -(close / close.shift(20) - 1).iloc[-1]
    vol20 = -close.pct_change().rolling(20, min_periods=10).std().iloc[-1]
    sent = (pct.tail(5).ge(9.5).sum() * 2.0 + turn.tail(5).mean().fillna(0) / 5.0)
    for name, s in (("turnover", turn20), ("turn_mid_prox", turn60),
                    ("reversal20", rev20), ("lowvol", vol20), ("sentiment", sent)):
        rank_cols[f"{name}_rank"] = _rank(pd.to_numeric(s, errors="coerce"))
        raw_cols[f"{name}_raw"] = pd.to_numeric(s, errors="coerce")
    out = pd.DataFrame({"code": close.columns.astype(str)})
    out["date"] = latest
    for k, s in {**rank_cols, **raw_cols}.items():
        out[k] = out["code"].map(s.astype(float))
    DAILY.mkdir(parents=True, exist_ok=True)
    p = DAILY / f"daily_{date_tag}.csv"
    out.to_csv(p, index=False)
    meta = {
        "date": latest,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "local_proxy",
        "note": "外部分因池不可用时的本地可复现代理 rank；不是外部供应商原始值。",
        "columns": list(out.columns),
    }
    (DAILY / f"daily_{date_tag}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def _write_ui(reg: FactorRegistry, date_tag: str) -> None:
    UI.mkdir(parents=True, exist_ok=True)
    lifecycle = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "factors": {}}
    for f in reg.list_factors():
        lifecycle["factors"][f["name"]] = {
            "latest": f.get("score"),
            "status": f.get("status"),
            "series": [],
        }
    (UI / f"factor_lifecycle_{date_tag}.json").write_text(
        json.dumps(lifecycle, ensure_ascii=False, indent=1), encoding="utf-8")
    freshness = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "manifest": {"coverage": f"{len(reg.list_factors())} 因子", "date": datetime.now().strftime("%Y-%m-%d")},
        "health_csv": {"coverage": f"{len(reg.list_factors())} 因子", "date": datetime.now().strftime("%Y%m%d")},
        "source": "local scheduler",
    }
    (UI / f"factor_data_freshness_{date_tag}.json").write_text(
        json.dumps(freshness, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_meta(date_tag: str, summary: dict) -> None:
    p = OUT / f"scheduler_run_{date_tag}.json"
    p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def run(mode: str, skip_policy: bool = False, skip_eval: bool = False,
        allow_pipeline_lock: bool = False) -> dict:
    if mode in ("daily", "full") and not _acquire_lock(allow_pipeline_lock=allow_pipeline_lock):
        return {"mode": mode, "skipped": True,
                "reason": "factor scheduler 已在运行或每日管道锁未释放", "errors": []}
    try:
        return _run(mode, skip_policy=skip_policy, skip_eval=skip_eval)
    finally:
        if mode in ("daily", "full"):
            _release_lock()


def _run(mode: str, skip_policy: bool = False, skip_eval: bool = False) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now().strftime("%Y%m%d")
    cfg = (load_params().get("factor_pool") or {})
    reg = FactorRegistry()
    summary = {
        "mode": mode,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "factors_before": reg.stats(),
        "policy_refreshed": 0,
        "evaluated": 0,
        "files": {},
        "errors": [],
    }
    if mode in ("daily", "full") and not skip_policy and cfg.get("fetch_on_sched", True):
        try:
            summary["policy_refreshed"] = fetch_policy_data()
        except Exception as e:
            summary["errors"].append(f"policy_fetch: {str(e)[:200]}")
    if mode in ("daily", "full") and not skip_eval and cfg.get("evaluate_on_sched", True):
        try:
            limit = int(cfg.get("daily_eval_limit", 1) or 1) if mode == "daily" else None
            sample_limit = cfg.get("daily_eval_sample_limit", 500)
            results = evaluate_pool(reg, limit=limit, sample_limit=sample_limit)
            summary["evaluated"] = len(results)
        except Exception as e:
            summary["errors"].append(f"evaluate: {str(e)[:200]}")
    summary["unsupported_marked"] = _mark_unsupported(reg)
    if mode in ("daily", "full"):
        try:
            p = _write_manifest(reg, date_tag)
            summary["files"]["manifest"] = str(p.relative_to(BASE))
        except Exception as e:
            summary["errors"].append(f"manifest: {str(e)[:200]}")
        try:
            p = _write_health(reg, date_tag)
            summary["files"]["health"] = str(p.relative_to(BASE))
        except Exception as e:
            summary["errors"].append(f"health: {str(e)[:200]}")
        try:
            p = _write_daily_scores(date_tag)
            if p:
                summary["files"]["daily_scores"] = str(p.relative_to(BASE))
        except Exception as e:
            summary["errors"].append(f"daily_scores: {str(e)[:200]}")
        try:
            _write_ui(reg, date_tag)
        except Exception as e:
            summary["errors"].append(f"ui: {str(e)[:200]}")
    try:
        write_report(reg)
    except Exception as e:
        summary["errors"].append(f"report: {str(e)[:200]}")
    summary["factors_after"] = reg.stats()
    summary["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_meta(date_tag, summary)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Local factor-pool scheduler")
    ap.add_argument("mode", nargs="?", default="daily", choices=["daily", "full", "report", "status"])
    ap.add_argument("--skip-policy", action="store_true")
    ap.add_argument("--skip-eval", action="store_true")
    ap.add_argument("--allow-pipeline-lock", action="store_true",
                    help="仅由每日管道内部调用时使用")
    args = ap.parse_args()
    if args.mode == "status":
        reg = FactorRegistry()
        print(json.dumps(reg.stats(), ensure_ascii=False))
        for f in reg.list_factors():
            print(f"[{f['status']:9}] {f['name']:<24} score={f['score']} locked={f['locked']}")
        return 0
    if args.mode == "report":
        write_report(FactorRegistry())
        return 0
    r = run(args.mode, skip_policy=args.skip_policy, skip_eval=args.skip_eval,
           allow_pipeline_lock=args.allow_pipeline_lock)
    print(json.dumps({k: r.get(k) for k in ("mode", "policy_refreshed", "evaluated", "files", "errors", "factors_after")},
                     ensure_ascii=False, indent=2))
    return 0 if not r.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
