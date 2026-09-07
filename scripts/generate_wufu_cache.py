# -*- coding: utf-8 -*-
"""scripts/generate_wufu_cache.py — 五福轮动因子池缓存自举（★2026-09 外部因子池缺失修复）

背景：
  五福轮动摆件（/api/live/wufu_rotation，factors/policy/global_rotation.py widget()）读取：
    data/factorpool/output/cache/wufu_idx.parquet（4 指数 · 级1 A股弱市 MA10 投票）
    data/factorpool/output/cache/wufu_etf.parquet（9 ETF 代理池 · 级2 25日动量×R² 面板）
  这两个文件本应由外包因子池（Windows 主机）生成后经 scripts/import_factorpool.py 同步进来；
  本机缺外部因子池 → 摆件报 [Errno 2] No such file or directory →「五福轮动数据暂不可用」。

本脚本按「诚实降级」自举生成缓存，每列独立标注数据来源：
  优先级 1) 本地 bars.db（已缓存真实行情，只读，架构=策略只读本地库）
          2) 网络拉取（指数：baostock → 新浪 index_daily；ETF：新浪 fund_etf_hist_sina，
             与 etf/etf_map.py 同通道，均写入本地缓存）
          3) 合成演示数据（GBM，zlib.crc32 固定随机种子，可复现；provenance 明确标注 demo，
             绝不冒充真实——外部因子池同步后自动覆盖）
池定义：config/params.yaml → wufu_rotation（铁律：池进配置；缺失回退内置默认池，
  与 data/fetcher_tushare.py GLOBAL_ETF 同源——因子池实证「弱市切全球 +17.82% vs 沪深300 +4.47%」）。

用法（幂等，可重复跑）：
  python scripts/generate_wufu_cache.py              # 生成/刷新（当日已生成则跳过）
  python scripts/generate_wufu_cache.py --force      # 强制重建
  python scripts/generate_wufu_cache.py --no-fetch   # 只用本地缓存，缺失直接演示兜底（离线安全）
输出：
  data/factorpool/output/cache/wufu_idx.parquet / wufu_etf.parquet / wufu_provenance.json
  另顺带刷新 output/global_rotation.json（compute()，防守卡同源刷新）。
"""
import argparse
import json
import sys
import zlib
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

OUT_DIR = BASE / "data" / "factorpool" / "output" / "cache"
IDX_PATH = OUT_DIR / "wufu_idx.parquet"
ETF_PATH = OUT_DIR / "wufu_etf.parquet"
PROV_PATH = OUT_DIR / "wufu_provenance.json"

START = "2019-01-01"          # 与因子池规格一致（1847 天窗口；ETF 未上市前自然 NaN）
MIN_ROWS = 60                 # 少于 60 天视为不可用（widget 同门槛）

# 内置默认池（config/params.yaml → wufu_rotation 存在时替代）
DEFAULT_INDICES = {
    "沪深300": "sh.000300",
    "中小综指": "sz.399101",
    "创业板指": "sz.399006",
    "中证A500": "sh.000510",
}
DEFAULT_ETFS = {
    "黄金ETF": "518880",
    "纳指ETF": "513100",
    "标普500ETF": "513500",
    "日经ETF": "513520",
    "德国ETF": "513030",
    "有色金属ETF": "512400",
    "豆粕ETF": "159985",
    "中概互联ETF": "513050",
    "纳指ETF2": "159941",
}

# 演示数据年化波动率（GBM 参数，按资产类别取典型值；只影响 demo 列的形态）
DEMO_VOL = {
    "沪深300": 0.19, "中小综指": 0.24, "创业板指": 0.26, "中证A500": 0.20,
    "黄金ETF": 0.15, "纳指ETF": 0.21, "标普500ETF": 0.17, "日经ETF": 0.20,
    "德国ETF": 0.19, "有色金属ETF": 0.26, "豆粕ETF": 0.19, "中概互联ETF": 0.30,
    "纳指ETF2": 0.21,
}
DEMO_DRIFT = 0.02             # 演示 GBM 年化漂移（固定 2%，非预测）


# ---------------- 池定义（配置优先） ----------------

def _pool():
    """config/params.yaml → wufu_rotation 优先；缺失/异常回退内置默认池。"""
    try:
        from data.config import load_params
        cfg = (load_params() or {}).get("wufu_rotation") or {}
        idx = cfg.get("indices") or DEFAULT_INDICES
        etfs = cfg.get("etfs") or DEFAULT_ETFS
        return ({str(k): str(v) for k, v in idx.items() if v},
                {str(k): str(v) for k, v in etfs.items() if v})
    except Exception as e:
        print(f"[pool] config 读取失败（用内置默认池）: {e}")
        return dict(DEFAULT_INDICES), dict(DEFAULT_ETFS)


# ---------------- 交易日历 ----------------

def _calendar():
    """真实 A 股交易日历（bars.db SH.000300）→ 无则近似 B 日序列。"""
    try:
        from data.cache import DailyCache
        df = DailyCache().get_daily("SH.000300", start=START, adjust="none")
        if df is not None and not df.empty:
            cal = pd.to_datetime(df["date"]).dropna()
            if len(cal) > 200:
                return cal, "bars.db SH.000300（真实A股交易日）"
    except Exception:
        pass
    return pd.bdate_range(START, datetime.now()), "pd.bdate_range（近似，本地无交易日历）"


def _to_series(df, date_col="date", close_col="close"):
    """DataFrame → 去重升序的 close 序列（DatetimeIndex）。"""
    s = pd.Series(pd.to_numeric(df[close_col], errors="coerce").values,
                  index=pd.to_datetime(df[date_col], errors="coerce")).dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s.index >= pd.Timestamp(START)]


# ---------------- 数据源：1) 本地 bars.db ----------------

def _series_local_idx(code_bs):
    """bars.db 已缓存真实指数（adjust=none，与 fetch_index_daily 同口径）。"""
    try:
        from data.cache import DailyCache
        df = DailyCache().get_daily(code_bs.upper(), start=START, adjust="none")
        if df is None or df.empty:
            return None
        s = _to_series(df)
        return s if len(s) >= MIN_ROWS else None
    except Exception:
        return None


# ---------------- 数据源：2) 网络（指数） ----------------

def _series_net_idx_bs(code_bs):
    """baostock 拉指数日线（真实）；失败返回 None。成功则写 bars.db（网络数据只写本地缓存）。"""
    try:
        from data.fetcher_baostock import fetch_daily
        df = fetch_daily(code_bs, start_date=START,
                         end_date=datetime.now().strftime("%Y-%m-%d"), adjust="none")
        if df is None or df.empty:
            return None
        from data.cache import DailyCache
        DailyCache().put_daily(code_bs.upper(), df, adjust="none", source="baostock")
        s = _to_series(df)
        return s if len(s) >= MIN_ROWS else None
    except Exception:
        return None


def _series_net_idx_ak(code_bs):
    """新浪指数日线兜底（真实）；失败返回 None。"""
    try:
        import akshare as ak
        sym = str(code_bs).replace(".", "")        # 'sh.000300' → 'sh000300'
        df = ak.stock_zh_index_daily(symbol=sym)
        if df is None or df.empty:
            return None
        s = _to_series(df)
        return s if len(s) >= MIN_ROWS else None
    except Exception:
        return None


# ---------------- 数据源：2) 网络（ETF） ----------------

def _series_net_etf(code6):
    """新浪 fund_etf_hist_sina（与 etf/etf_map.py 同通道，真实，含本地 CSV 增量缓存）；
    失败返回 None。注：新浪 ETF 日线为不复权（ETF 分红少，动量面板口径可接受，provenance 标注）。"""
    try:
        from etf.etf_map import fetch_etf
        df = fetch_etf(code6)
        if df is None or df.empty:
            return None
        s = _to_series(df)
        return s if len(s) >= MIN_ROWS else None
    except Exception:
        return None


# ---------------- 数据源：3) 合成演示兜底 ----------------

def _series_demo(name, calendar):
    """GBM 合成演示数据（固定种子可复现，起点 1.0）；仅摆件演示，provenance 标注 demo。"""
    seed = zlib.crc32(name.encode("utf-8"))
    rng = np.random.default_rng(seed)
    vol = DEMO_VOL.get(name, 0.20)
    n = len(calendar)
    rets = rng.normal(DEMO_DRIFT / 252.0, vol / np.sqrt(252.0), n)
    return pd.Series(np.exp(np.cumsum(rets)), index=pd.to_datetime(calendar))


# ---------------- 主流程 ----------------

def _pick(name, code, kind, no_fetch):
    """按 本地 → 网络 → 演示 顺序取数，返回 (series, source, note)。"""
    if kind == "idx":
        s = _series_local_idx(code)
        if s is not None:
            return s, "bars.db", "本地缓存真实指数日线（adjust=none）"
        if not no_fetch:
            s = _series_net_idx_bs(code)
            if s is not None:
                return s, "baostock", "baostock 拉取真实指数日线（已写 bars.db）"
            s = _series_net_idx_ak(code)
            if s is not None:
                return s, "akshare(sina)", "新浪指数日线真实数据"
        s = _series_demo(name, _calendar()[0])
        return s, "demo", "合成演示数据（GBM，固定种子）——非真实行情，仅供摆件演示"
    else:
        if not no_fetch:
            s = _series_net_etf(code)
            if s is not None:
                return s, "akshare(sina)", "新浪 ETF 日线真实数据（不复权；ETF 分红小，动量口径可接受）"
        s = _series_demo(name, _calendar()[0])
        return s, "demo", "合成演示数据（GBM，固定种子）——非真实行情，仅供摆件演示"


def _prov_entry(s, source, note):
    return {
        "source": source,
        "demo": source == "demo",
        "start": str(s.index.min().date()),
        "end": str(s.index.max().date()),
        "rows": int(len(s)),
        "note": note,
    }


def build(force=False, no_fetch=False):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if (not force and PROV_PATH.exists() and IDX_PATH.exists() and ETF_PATH.exists()):
        try:
            prev = json.loads(PROV_PATH.read_text(encoding="utf-8"))
            if str(prev.get("generated_at", "")).startswith(datetime.now().strftime("%Y-%m-%d")):
                print("今日已生成（" + str(prev.get("generated_at")) + "）→ 跳过；--force 可强制重建")
                return 0
        except Exception:
            pass

    indices, etfs = _pool()
    idx_series = {name: _pick(name, code, "idx", no_fetch) for name, code in indices.items()}
    etf_series = {name: _pick(name, code, "etf", no_fetch) for name, code in etfs.items()}

    # 交易日历：bars.db 真实交易日 → 真实序列日期并集（精化，避免 B 日近似把周末当 NaN 稀释 MA 窗口）
    cal, cal_src = _calendar()
    if cal_src.startswith("pd.bdate_range"):
        real_dates = sorted({d for s, src, _ in list(idx_series.values()) + list(etf_series.values())
                             if src != "demo" for d in s.index})
        if len(real_dates) > 200:
            cal = pd.DatetimeIndex(real_dates)
            cal_src = "真实行情日期并集（本地无交易日历时的精化）"
    print(f"== 五福轮动缓存自举 ==  日历: {cal_src}（{len(cal)} 天，{cal.min().date()}~{cal.max().date()}）")

    prov = {
        "generated_at": now,
        "calendar": {"source": cal_src, "days": int(len(cal)),
                     "start": str(cal.min().date()), "end": str(cal.max().date())},
        "wufu_idx": {}, "wufu_etf": {},
        "note": "demo=合成演示数据（GBM 固定种子），非真实行情，仅供摆件演示；"
                "外部因子池同步（scripts/import_factorpool.py）后本缓存自动覆盖。",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx_frame = pd.DataFrame(index=cal)
    for name, (s, src, note) in idx_series.items():
        idx_frame[name] = s.reindex(cal).astype(float)
        prov["wufu_idx"][name] = _prov_entry(s, src, note)
        print(f"  [idx] {name:6s} {src:14s} {len(s):5d} 行 {s.index.min().date()}~{s.index.max().date()}"
              f"{'  ⚠演示' if src == 'demo' else ''}")

    etf_frame = pd.DataFrame(index=cal)
    for name, (s, src, note) in etf_series.items():
        etf_frame[name] = s.reindex(cal).astype(float)
        prov["wufu_etf"][name] = _prov_entry(s, src, note)
        print(f"  [etf] {name:8s} {src:14s} {len(s):5d} 行 {s.index.min().date()}~{s.index.max().date()}"
              f"{'  ⚠演示' if src == 'demo' else ''}")

    for frame in (idx_frame, etf_frame):
        frame.index = pd.to_datetime(frame.index)
        frame.index.name = "date"
    idx_frame.to_parquet(IDX_PATH)
    etf_frame.to_parquet(ETF_PATH)
    PROV_PATH.write_text(json.dumps(prov, ensure_ascii=False, indent=1), encoding="utf-8")

    n_demo = sum(1 for v in prov["wufu_idx"].values() if v["demo"]) + \
             sum(1 for v in prov["wufu_etf"].values() if v["demo"])
    print(f"✅ 已写 {IDX_PATH}")
    print(f"✅ 已写 {ETF_PATH}")
    print(f"✅ 已写 {PROV_PATH}（{len(prov['wufu_idx']) + len(prov['wufu_etf'])} 列，其中 demo {n_demo} 列）")

    # 顺带刷新防守卡（compute() 读同一批 parquet；失败不影响缓存落地）
    try:
        from factors.policy.global_rotation import compute
        r = compute()
        print(f"✅ 防守卡已刷新: a_share_weak={r['a_share_weak']}（投票 {r['weak_vote']}/4）")
    except Exception as e:
        print(f"⚠ 防守卡刷新失败（不影响缓存）: {str(e)[:120]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="五福轮动因子池缓存自举（本地→网络→演示）")
    ap.add_argument("--force", action="store_true", help="忽略当日已生成标记，强制重建")
    ap.add_argument("--no-fetch", action="store_true", help="禁止网络拉取（只用本地缓存，缺失直接演示兜底）")
    args = ap.parse_args()
    return build(force=args.force, no_fetch=args.no_fetch)


if __name__ == "__main__":
    sys.exit(main())
