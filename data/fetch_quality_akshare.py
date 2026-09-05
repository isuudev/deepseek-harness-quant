# -*- coding: utf-8 -*-
"""akshare 质量因子补拉（同花顺财务摘要 stock_financial_abstract_ths）

替代 baostock 版 fetch_quality.py（baostock 黑名单不可用时的免费备源）。
写入 finance_quality.db 的 quality 表，字段口径与 baostock/tushare 版一致。

字段映射（同花顺摘要 → quality 表）：
  roe_avg           ← 净资产收益率（"54.27%" → 0.5427）
  gp_margin         ← 销售毛利率
  np_margin         ← 销售净利率
  current_ratio     ← 流动比率（倍率，不除）
  liability_to_asset ← 资产负债率（"68.44%" → 0.6844）
  cfo_to_np         ← 每股经营现金流 / 基本每股收益（eps>0 时）
  cfo_to_or         ← None（同花顺无每股营收，置空）
  pub_date          ← 报告期

用法：
  python data/fetch_quality_akshare.py --limit 10    # 小样本验证
  python data/fetch_quality_akshare.py               # 全量（断点续传）
  python data/fetch_quality_akshare.py --status      # 查看进度
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
    os.environ.pop(k, None)

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

QD_DB = BASE / "data" / "cache" / "finance_quality.db"
LOG_FILE = BASE / "logs" / "quality_akshare.log"
START = "2024-01-01"
END = "2026-06-30"


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _conn():
    con = sqlite3.connect(str(QD_DB))
    con.execute("""CREATE TABLE IF NOT EXISTS quality (
        code TEXT, period TEXT, roe_avg REAL, gp_margin REAL, np_margin REAL,
        current_ratio REAL, liability_to_asset REAL, cfo_to_np REAL, cfo_to_or REAL,
        pub_date TEXT, PRIMARY KEY(code, period))""")
    con.commit()
    return con


def load_codes():
    con = sqlite3.connect(str(BASE / "data" / "cache" / "stock_basic.db"))
    codes = [r[0] for r in con.execute(
        "SELECT code FROM stock_basic WHERE code LIKE '%.SH' OR code LIKE '%.SZ'").fetchall()]
    con.close()
    return codes


def _done_codes():
    con = _conn()
    done = {r[0] for r in con.execute("SELECT DISTINCT code FROM quality").fetchall()}
    con.close()
    return done


def _pct(v):
    """百分数 → 小数："54.27%" → 0.5427；"False"/"--" → None"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("False", "nan", "None", "", "--"):
        return None
    if s.endswith("%"):
        s = s[:-1]
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    return f / 100.0 if abs(f) > 1 else f


def _num(v):
    """数值（倍率/每股）："False"/"--" → None"""
    if v is None:
        return None
    s = str(v).strip()
    if s in ("False", "nan", "None", "", "--"):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _fetch_one(code6):
    import akshare as ak
    pure = code6.split(".")[0]
    df = ak.stock_financial_abstract_ths(symbol=pure, indicator="按报告期")
    if df is None or df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        period = str(r.get("报告期"))[:10]
        if not (START <= period <= END):
            continue
        eps = _num(r.get("基本每股收益"))
        ocf = _num(r.get("每股经营现金流"))
        cfo_np = (ocf / eps) if (eps and eps > 0 and ocf is not None) else None
        rows.append((
            code6, period,
            _pct(r.get("净资产收益率")),
            _pct(r.get("销售毛利率")),
            _pct(r.get("销售净利率")),
            _num(r.get("流动比率")),
            _pct(r.get("资产负债率")),
            cfo_np, None, period,
        ))
    return rows


def run(workers=4, limit=None):
    codes = load_codes()
    done = _done_codes()
    todo = [c for c in codes if c not in done]
    if limit:
        todo = todo[:limit]
    log(f"质量补拉(akshare): 总 {len(codes)} 只，已完成 {len(done)}，待拉 {len(todo)}")

    if not todo:
        log("无需补拉")
        return

    con = _conn()
    t0 = time.time()
    ok = fail = 0

    import concurrent.futures
    import threading
    _rate_lock = threading.Lock()
    _last = [0.0]
    INTERVAL = 0.35  # 全局请求间隔（秒），防同花顺限流

    def _throttle():
        with _rate_lock:
            now = time.time()
            wait = INTERVAL - (now - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()

    def work(code6):
        for attempt in range(2):
            try:
                _throttle()
                return code6, _fetch_one(code6), None
            except Exception as e:
                if attempt == 1:
                    return code6, None, str(e)[:80]
                time.sleep(2.0)
        return code6, None, "unknown"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, c): c for c in todo}
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            c, rows, err = fut.result()
            if rows:
                con.executemany(
                    """INSERT OR REPLACE INTO quality
                       (code, period, roe_avg, gp_margin, np_margin, current_ratio,
                        liability_to_asset, cfo_to_np, cfo_to_or, pub_date)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
                con.commit()
                ok += 1
            else:
                fail += 1
                if fail <= 20 and err:
                    log(f"  [失败] {c}: {err}")
            if i % 200 == 0:
                el = time.time() - t0
                log(f"  进度 {i}/{len(todo)} ({el:.0f}s, 成功 {ok}, 速度 {i/el:.1f}只/s)")

    con.close()
    el = time.time() - t0
    log(f"完成: 成功 {ok}/{len(todo)} 只, 失败 {fail}, 耗时 {el/60:.1f} 分钟")


def status():
    con = _conn()
    n = con.execute("SELECT COUNT(DISTINCT code) FROM quality").fetchone()[0]
    rows = con.execute("SELECT COUNT(*) FROM quality").fetchone()[0]
    print(f"quality 表: {n} 只股票 / {rows} 行")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        status()
    else:
        run(workers=args.workers, limit=args.limit)
