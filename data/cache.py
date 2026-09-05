# -*- coding: utf-8 -*-
"""本地缓存唯一读取接口（M1 数据管道 · 基础版）

架构要求（主文档 4.5③）：网络数据（无论哪个源）只负责写入本地 SQLite/Parquet，
策略/回测**只读本地库** —— 断网、断源都不影响已缓存数据。

本轮实现（SQLite 日线缓存 · 单库）：
- `daily_bar` 表：按 (code, date, adjust) 主键 upsert，多源双写安全
- `bar_meta` 表：每只股票缓存覆盖范围（增量更新 / 覆盖率判断）

★单库化（2026-09）：移除 2026-08-10 因「环境写保护锁」引入的双库/时间戳库/写路由绕行。
  - 唯一读写目标 = bars.db；不再有 bars_incr.db / bars_incr_*.db。
  - 不可写时由 SQLite 抛 OperationalError（明确暴露权限/锁问题），不再静默路由到临时库。

统一代码格式：'600519.SH' / '000001.SZ'
adjust：'qfq' 前复权 / 'hfq' 后复权 / 'none' 不复权
"""
import os
import sqlite3
import time
from pathlib import Path

import pandas as pd

# 缓存目录解析（优先级：环境变量 LWQUANT_CACHE_DIR > params.yaml data.cache_dir > 默认 data/cache）
def _resolve_cache_dir() -> Path:
    env = os.environ.get("LWQUANT_CACHE_DIR")
    if env:
        return Path(env)
    try:
        import yaml
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "params.yaml"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            d = (cfg or {}).get("data", {}).get("cache_dir")
            if d:
                p = Path(str(d))
                return p if p.is_absolute() else Path(__file__).resolve().parent.parent / p
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent / "data" / "cache"

CACHE_DIR = _resolve_cache_dir()
DEFAULT_DB = CACHE_DIR / "bars.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bar (
    code       TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       REAL, high REAL, low REAL, close REAL,
    preclose   REAL, volume REAL, amount REAL,
    turn       REAL, pct_chg REAL, is_st INTEGER,
    adjust     TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (code, date, adjust)
);
CREATE INDEX IF NOT EXISTS idx_daily_bar ON daily_bar (code, adjust, date);

CREATE TABLE IF NOT EXISTS bar_meta (
    code       TEXT NOT NULL,
    adjust     TEXT NOT NULL,
    start_date TEXT,
    end_date   TEXT,
    rows       INTEGER,
    updated_at TEXT,
    PRIMARY KEY (code, adjust)
);
"""

_NUM_COLS = ["open", "high", "low", "close", "preclose",
             "volume", "amount", "turn", "pct_chg"]


# ★2026-08-15 单位归一（统一标准，消除混源 bug 根因）
#   bars.db 混源：tushare/tushare_backup = amount千元、volume手；baostock/akshare = amount元、volume股
#   消费端凡用 amount/volume 参与计算（换手率/PB/成交额占比等），必须先经本函数归一，禁止各写各的 ×1000。
def normalize_units(df: pd.DataFrame) -> pd.DataFrame:
    """把混源 amount/volume 归一为统一单位：amount=元、volume=股。
    按 source 列判断：tushare/tushare_backup（千元/手）→ amount×1000、volume×100；
    baostock/akshare（元/股）不变。无 source 列时保守不转换（调用方需自证单位）。"""
    if df is None or df.empty or "source" not in df.columns:
        return df
    ts = df["source"].isin(["tushare", "tushare_backup"])
    if ts.any():
        df = df.copy()
        if "amount" in df.columns:
            df.loc[ts, "amount"] = pd.to_numeric(df.loc[ts, "amount"], errors="coerce") * 1000.0
        if "volume" in df.columns:
            df.loc[ts, "volume"] = pd.to_numeric(df.loc[ts, "volume"], errors="coerce") * 100.0
    return df


class DailyCache:
    """SQLite 日线缓存：唯一读取接口（策略/回测只允许经它取数）。

    ★单库：唯一读写目标 = bars.db（db_path 参数显式指定时用于测试/特殊用途）。
    """

    # ★#347 最新交易日缓存（模块级）——latest_trade_date 每调一次 COUNT 全表扫 2.5s，
    #   门户 live_chain/_minute_node 每轮多调导致冷启动 6s；最新交易日每天只变一次，缓存安全
    _LTD_CACHE = {"ts": 0.0, "val": None, "ver": None}

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ---------- 内部 ----------
    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_schema(self):
        with self._conn() as con:
            con.executescript(_SCHEMA)

    # ---------- 写入（单库）----------
    def put_daily(self, code, df, adjust="qfq", source="baostock"):
        """写入/合并日线（按主键 upsert）。df 需含标准列：date,open,high,low,close,preclose,volume,amount,turn,pct_chg,is_st"""
        if df is None or df.empty:
            return 0
        code = code.upper()
        rows = []
        for _, r in df.iterrows():
            rows.append((
                code, r["date"],
                _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close")),
                _f(r.get("preclose")), _f(r.get("volume")), _f(r.get("amount")),
                _f(r.get("turn")), _f(r.get("pct_chg")), int(r.get("is_st") or 0),
                adjust, source,
            ))
        con = sqlite3.connect(self.db_path)
        try:
            con.executemany(
                "INSERT OR REPLACE INTO daily_bar "
                "(code,date,open,high,low,close,preclose,volume,amount,turn,pct_chg,is_st,adjust,source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            # 更新 meta —— ★累计覆盖语义（F-4 修复 2026-08-07）：
            dmin, dmax = str(df["date"].min()), str(df["date"].max())
            old = con.execute(
                "SELECT start_date, end_date FROM bar_meta WHERE code=? AND adjust=?",
                (code, adjust)).fetchone()
            if old and old[0]:
                start, end = min(old[0], dmin), max(old[1], dmax)
            else:
                start, end = dmin, dmax
            cnt = con.execute(
                "SELECT COUNT(*) FROM daily_bar WHERE code=? AND adjust=?",
                (code, adjust)).fetchone()[0]
            con.execute(
                "INSERT OR REPLACE INTO bar_meta (code,adjust,start_date,end_date,rows,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (code, adjust, start, end, cnt, time.strftime("%Y-%m-%d %H:%M:%S")))
            con.commit()
        finally:
            con.close()
        return len(rows)

    def put_daily_batch(self, df, adjust="qfq", source="tushare"):
        """★全市场批量写入（Tushare 日线增量用）：df 需含标准列 + code 列，一次 executemany 写入。"""
        if df is None or df.empty:
            return 0
        if "code" not in df.columns:
            raise ValueError("put_daily_batch 需要 df 含 code 列")
        rows = []
        for _, r in df.iterrows():
            rows.append((
                str(r["code"]).upper(), r["date"],
                _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close")),
                _f(r.get("preclose")), _f(r.get("volume")), _f(r.get("amount")),
                _f(r.get("turn")), _f(r.get("pct_chg")), int(r.get("is_st") or 0),
                adjust, source,
            ))
        con = sqlite3.connect(self.db_path)
        try:
            con.executemany(
                "INSERT OR REPLACE INTO daily_bar "
                "(code,date,open,high,low,close,preclose,volume,amount,turn,pct_chg,is_st,adjust,source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.commit()
        finally:
            con.close()
        return len(rows)

    # ---------- 读取（唯一读取入口，单库）----------
    def latest_trade_date(self):
        """缓存中最新交易日（任意股票），无数据返回 None。
        ★完整性门槛：该日股票数 <4000（残缺占位）视为无效，回退最近一个 ≥4000 只的完整日。
        ★#358 版本缓存：按 bars.db mtime 版本键缓存——数据没变就永久命中。"""
        _c = DailyCache._LTD_CACHE
        _now = time.time()
        try:
            _ver = os.path.getmtime(self.db_path) if self.db_path.exists() else None
        except Exception:
            _ver = None
        if _c["val"] is not None and _c.get("ver") == _ver:
            return _c["val"]
        if not self.db_path.exists():
            return None
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT MAX(date) FROM daily_bar WHERE code NOT LIKE 'sh.%' AND code NOT LIKE 'sz.%'"
            ).fetchone()
            best = row[0] if row and row[0] else None
            # 完整性门槛：最近一个 ≥4000 只的完整交易日
            full = con.execute(
                "SELECT date FROM daily_bar WHERE code NOT LIKE 'sh.%' AND code NOT LIKE 'sz.%' "
                "GROUP BY date HAVING COUNT(DISTINCT code) >= 4000 ORDER BY date DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        val = (full[0] if full and full[0] else best)
        _c["ts"] = _now
        _c["val"] = val
        _c["ver"] = _ver
        return val

    def get_daily(self, code, start=None, end=None, adjust="qfq"):
        """按 code+adjust 读取日线，可选区间过滤，按日期升序。无数据返回 None"""
        code = code.upper()
        if not self.db_path.exists():
            return None
        sql = ("SELECT code,date,open,high,low,close,preclose,volume,amount,"
               "turn,pct_chg,is_st,adjust,source FROM daily_bar "
               "WHERE code=? AND adjust=?")
        args = [code, adjust]
        if start:
            sql += " AND date>=?"
            args.append(start)
        if end:
            sql += " AND date<=?"
            args.append(end)
        sql += " ORDER BY date"
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.execute(sql, args)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        finally:
            con.close()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=cols)
        for c in _NUM_COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def get_daily_batch(self, codes, start=None, end=None, adjust="qfq", fields=None):
        """★批量读取：一次 SQL 全拉（分块 ≤500 只/批，绕 SQLite 变量上限 999）。
        返回 {code.upper(): DataFrame(按日期升序)}；无数据的 code 不在字典。
        ★fields 裁剪：默认 None=全列；传 fields=["close"] 等只取所需列。"""
        codes = [str(c).upper() for c in codes if c]
        if not codes:
            return {}
        if fields is None:
            sel_cols = ["code", "date", "open", "high", "low", "close", "preclose",
                        "volume", "amount", "turn", "pct_chg", "is_st", "adjust", "source"]
            num_cols = list(_NUM_COLS)
        else:
            flds = [f for f in fields if f in _NUM_COLS or f == "is_st"]
            sel_cols = ["code", "date"] + flds + ["adjust"]
            num_cols = [f for f in flds if f in _NUM_COLS]
        if not self.db_path.exists():
            return {}
        sql_head = ("SELECT " + ",".join(sel_cols) +
                    " FROM daily_bar WHERE adjust=? AND code IN (")
        out_all = {}   # code -> DataFrame
        con = sqlite3.connect(self.db_path)
        try:
            for i in range(0, len(codes), 500):
                chunk = codes[i:i + 500]
                sql = sql_head + ",".join("?" * len(chunk)) + ")"
                args = [adjust] + list(chunk)
                if start:
                    sql += " AND date>=?"
                    args.append(start)
                if end:
                    sql += " AND date<=?"
                    args.append(end)
                cur = con.execute(sql, args)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                if not rows:
                    continue
                df = pd.DataFrame(rows, columns=cols)
                for c in num_cols:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
                for code, g in df.groupby("code"):
                    out_all[code] = g
        finally:
            con.close()
        return {code: df.sort_values("date").reset_index(drop=True)
                for code, df in out_all.items()}

    def get_meta(self, code, adjust="qfq"):
        """缓存覆盖范围（增量/覆盖率判断用）；无记录返回 None"""
        code = code.upper()
        if not self.db_path.exists():
            return None
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.execute(
                "SELECT code,adjust,start_date,end_date,rows,updated_at "
                "FROM bar_meta WHERE code=? AND adjust=?", (code, adjust))
            row = cur.fetchone()
        finally:
            con.close()
        if not row:
            return None
        return {"code": row[0], "adjust": row[1], "start_date": row[2],
                "end_date": row[3], "rows": row[4], "updated_at": row[5]}

    def covers(self, code, start, end, adjust="qfq"):
        """缓存是否已覆盖 [start, end] 全区间"""
        meta = self.get_meta(code, adjust)
        if meta is None:
            return False
        return bool(meta["start_date"] and meta["end_date"]
                    and meta["start_date"] <= start and meta["end_date"] >= end)


def _f(x):
    """转 float，NaN/None → None（SQLite 存 NULL）"""
    if x is None:
        return None
    try:
        v = float(x)
        return v if v == v else None  # NaN → None
    except (TypeError, ValueError):
        return None


def minute_download_root() -> Path:
    """分钟数据下载根目录（用户每日 7z 增量数据位置）。

    优先级：环境变量 LWQUANT_MINUTE_DIR > 默认 <repo>/data/minute/download。
    用于替代散落在各脚本里的 data/minute/download 相对路径/反斜杠硬编码。
    """
    env = os.environ.get("LWQUANT_MINUTE_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "data" / "minute" / "download"
