# -*- coding: utf-8 -*-
"""数据源健康检查（不输出密钥）。

检查项：
  1. config/params.yaml 是否存在
  2. Tushare token 是否配置
  3. Tushare trade_cal 是否可访问
  4. bars.db 最新数据日
  5. 分钟数据目录是否存在

用法：
  python data/check_data_sources.py
  python data/check_data_sources.py --json
  python data/check_data_sources.py --offline
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from data.cache import CACHE_DIR
from data.config import load_params

OUT_DIR = BASE / "output"


def _classify_error(msg: str) -> str:
    low = (msg or "").lower()
    if "token已过期" in msg or "token 已过期" in msg or "expired" in low:
        return "token_expired"
    if "token" in low and any(x in low for x in ("invalid", "无效", "错误")):
        return "token_invalid"
    if any(x in low for x in ("权限", "积分", "permission", "not purchased")):
        return "permission_or_points"
    if any(x in low for x in ("frequency", "频率", "每分钟", "每分钟最多")):
        return "rate_limited"
    if any(x in low for x in ("name resolution", "nodename", "connection", "timeout", "timed out", "ssl")):
        return "network_error"
    return "api_error"


def _latest_date() -> str | None:
    db = CACHE_DIR / "bars.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro&immutable=1", uri=True, timeout=5)
        row = con.execute("SELECT MAX(date) FROM daily_bar WHERE adjust='qfq'").fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def _minute_available() -> bool:
    roots = [
        BASE / "data" / "minute" / "incr_parquet",
        BASE / "data" / "minute" / "download" / "1m_price_zip",
    ]
    for p in roots:
        try:
            if p.exists() and any(p.iterdir()):
                return True
        except Exception:
            pass
    return False


def check(offline: bool = False) -> dict:
    try:
        from data.fetcher_tushare import _load_cfg
        cfg = _load_cfg()
    except Exception:
        cfg = (load_params().get("data") or {})
    token = cfg.get("tushare_token")
    url = cfg.get("tushare_api_url") or "https://api.tushare.pro"
    result = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params_exists": (BASE / "config" / "params.yaml").exists(),
        "token_present": bool(token),
        "token_len": len(token or ""),
        "api_url": url,
        "tushare": {"status": "not_checked", "message": ""},
        "bars_latest": _latest_date(),
        "minute_data_available": _minute_available(),
        "needs_user_config": [],
    }
    if not token:
        result["tushare"] = {
            "status": "missing_token",
            "message": "缺少 data.tushare_token；请在 config/params.yaml 配置。",
        }
        result["needs_user_config"].append("data.tushare_token")
        return result
    if offline:
        result["tushare"] = {"status": "not_checked_offline", "message": "离线模式未请求 API。"}
        return result
    try:
        from data.fetcher_tushare import _call, _pro
        pro = _pro()
        df = _call(pro.trade_cal, exchange="SSE", start_date="20260101",
                   end_date=datetime.now().strftime("%Y%m%d"), is_open="1", max_retry=1)
        ok = df is not None and not df.empty
        latest_open = str(df["cal_date"].max()) if ok else ""
        result["tushare"] = {
            "status": "ok" if ok else "empty",
            "message": "交易日历接口可访问。",
            "latest_open": latest_open,
        }
    except Exception as e:
        msg = str(e)
        if token:
            msg = msg.replace(str(token), "<redacted>")
        status = _classify_error(msg)
        result["tushare"] = {
            "status": status,
            "message": msg[:500],
        }
        if status in ("token_expired", "token_invalid", "missing_token"):
            result["needs_user_config"].append("data.tushare_token")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="数据源健康检查（脱敏）")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    ap.add_argument("--offline", action="store_true", help="跳过网络请求")
    args = ap.parse_args()
    r = check(offline=args.offline)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in (f"data_source_health_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                 "data_source_health.json"):
        try:
            (OUT_DIR / name).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        t = r.get("tushare") or {}
        print(f"Tushare: {t.get('status')} | {t.get('message')}")
        print(f"bars.db latest: {r.get('bars_latest') or '—'} | minute: {'yes' if r.get('minute_data_available') else 'no'}")
        if r.get("needs_user_config"):
            print("需要用户配置: " + ", ".join(r["needs_user_config"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
