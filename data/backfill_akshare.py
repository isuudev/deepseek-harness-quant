# -*- coding: utf-8 -*-
"""全市场日线批量回填（baostock 主源 + akshare 新浪备源 → data/cache/bars.db）

用法：
  python data/backfill_akshare.py --limit 50      # 小样本测试
  python data/backfill_akshare.py                 # 全量（断点续传：已覆盖自动跳过）
  python data/backfill_akshare.py --status        # 查看覆盖进度

数据源策略：主源 baostock（免 token、TCP 不封 IP）；单股失败/空时用 akshare
新浪源兜底。单线程（akshare 备源含 mini_racer JS 解密，多线程 V8 崩溃）。
"""
import argparse
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import os

# 剥离系统代理（国内数据源直连，规避代理导致的 Connection reset）
for k in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
    os.environ.pop(k, None)

from data.fetcher_baostock import ensure_daily as bs_ensure_daily
from data.fetcher_akshare import ensure_daily as ak_ensure_daily
from data.cache import DailyCache


def fetch_codes(limit=0):
    """股票列表：优先读缓存 data/cache/stock_list.txt（baostock 生成），
    不存在则用 baostock query_stock_basic 获取并缓存。"""
    lst = BASE / "data" / "cache" / "stock_list.txt"
    if lst.exists():
        codes = [c.strip() for c in lst.read_text(encoding="utf-8").splitlines() if c.strip()]
    else:
        import baostock as bs
        bs.login()
        try:
            rs = bs.query_stock_basic()
            codes = []
            while rs.error_code == "0" and rs.next():
                r = rs.get_row_data()  # code, code_name, ipoDate, outDate, type, status
                if r[4] != "1":        # type=1 股票（排除指数/基金/债券）
                    continue
                raw = r[0]             # sh.600519 / sz.000001
                code = raw.replace("sh.", "").replace("sz.", "").upper()[:6]
                if raw.startswith("sh."):
                    codes.append(code + ".SH")
                elif raw.startswith("sz."):
                    codes.append(code + ".SZ")
        finally:
            bs.logout()
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text("\n".join(codes), encoding="utf-8")
    return codes[:limit] if limit else codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    end = args.end or time.strftime("%Y-%m-%d")
    cache = DailyCache()

    codes = fetch_codes(args.limit)
    total = len(codes)
    print(f"[backfill] {total} 只 | {args.start} ~ {end} | db={cache.db_path}",
          flush=True)

    if args.status:
        covered = sum(1 for c in codes if cache.covers(c, args.start, end))
        print(f"[status] 已覆盖 {covered}/{total}")
        return

    ok = empty = failed = skipped = 0
    t0 = time.time()

    for code in codes:
        try:
            if cache.covers(code, args.start, end):
                skipped += 1
                continue
            df = None
            # 主源 baostock
            try:
                df = bs_ensure_daily(code, start_date=args.start, end_date=end,
                                     adjust="qfq", cache=cache)
            except Exception:
                df = None
            # 备源 akshare 兜底（baostock 失败或空）
            if df is None or df.empty:
                try:
                    df = ak_ensure_daily(code, start=args.start, end=end,
                                         adjust="qfq", cache=cache)
                except Exception:
                    df = None
            if df is None or df.empty:
                empty += 1
            else:
                ok += 1
        except Exception as e:
            failed += 1
            if failed <= 30:
                print(f"  [err] {code}: {str(e)[:100]}", flush=True)
        n = ok + empty + failed + skipped
        if n % 100 == 0:
            el = time.time() - t0
            rate = (ok + empty) / el if el > 0 else 0
            print(f"  ... {n}/{total} | ok={ok} empty={empty} skip={skipped} "
                  f"fail={failed} | {el:.0f}s | {rate:.2f}只/s", flush=True)

    el = time.time() - t0
    print(f"[done] ok={ok} empty={empty} skip={skipped} fail={failed} | "
          f"耗时 {el / 60:.1f} 分钟", flush=True)


if __name__ == "__main__":
    main()
