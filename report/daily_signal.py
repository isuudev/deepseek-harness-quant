# -*- coding: utf-8 -*-
"""report/daily_signal.py — 今日信号生成器（★2026-09-10 开源重实现）

背景：原 report/ 看板生成器为外包包未随源码分发（main.py 原注明），决策链
「今日信号」环节长期缺文件。本文件为**开源替代版**，按工作区铁律重新设计：

铁律②（同源）：买入指令 = L2 决策卡聚合，无第二套推荐逻辑。
  1. L2 决策卡同源聚合：logs/pitch_v2_*.json（长线）+ logs/tech_pitch_*.json（短线）
     —— 长线优先去重，审批状态取 logs/deck_decisions_*.json 最近 action；
     「待审批」卡片 = 今日买入指令（用户到决策台审批 buy/drop，闭环回卡）。
  2. 无 Pitch 待审批候选 → 设计内降级：机器决策池（output/pool_layers_*.json 的
     decision 层，src=machine_pool）；再缺 → v3 硬过滤池内分层抽样（仅供跟踪兜底）。
  3. 择时档位：regime_cash_ratio ≥ exit_cash(1.0) 离场 / ≥ half_cash(0.5) 减仓（只减不加，
     不生成买入清单）/ 其余满仓。现金与硬过滤池来自主策略 v3（strategy/equal_weight_timing，
     动态择时优先、RegimeDetector 兜底）。
  4. 数据审计闸门（risk/data_audit.DataAuditor.gate()）：FAIL 阻断 → 不生成买入清单。
  5. regime_label：RegimeDetector 五档映射（过渡期代理，宏观库接入后替换）。

输出（写保护免疫：时间戳主写 + 固定名兜底）：
  output/daily_signal_YYYYMMDD_HHMMSS.json（主） + output/daily_signal.json（兜底）
字段契约（消费方）：
  date / generated_at / capital / regime_label / regime_level / regime_cash_ratio /
  n_passed（硬过滤通过数，live_api 漏斗）/ codes（持仓清单，position_monitor、
  paper_tracker）/ buy_order（今日买入指令）/ n_hold / hold_plan（策略决策池建议，
  live_api 机会池「系统建议」补位）/ pitch_degraded / sample_method / gate{ok,reason}
  （live_api 总览）/ advice（持有池「今日指令」面板）

用法：
  python report/daily_signal.py                  # 晚间链默认调用（无参数）
  python report/daily_signal.py --date 2026-09-09 --capital 200000
  python report/daily_signal.py --no-audit       # 调试：跳过数据审计闸门
配置：config/params.yaml 的 daily_signal 段（缺失用下方缺省，改参数不改代码）。
验收：validation/stress_pitch_aggregation.py（A 组聚合边界 + B 组 generate 降级路径）
      validation/stress_same_source.py（D 组：UI loadBuyOrder vs _pitch_candidates 同源对拍）。
"""
import argparse
import hashlib
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "output"   # 压测可运行时覆盖（ds.OUT_DIR = 临时目录）
sys.path.insert(0, str(BASE))

# ★日志目录一律在调用时经 BASE 解析（勿 import 时绑定：压测会改 ds.BASE 指向临时目录）

# ---- 配置缺省（params.yaml daily_signal 段优先；改配置不改代码）----
CFG_DEF = {
    "capital": 200000,      # 资金规模参照（与 pool_layers.capital_to_n 分档一致）
    "max_buy_n": 5,         # 买入清单只数上限（对齐 risk.max_positions ≤5 纪律）
    "max_hold_n": 20,       # hold_plan（策略决策池建议清单）只数上限
    "half_cash": 0.5,       # 现金比例 ≥ half_cash → 减仓档（只减不加）
    "exit_cash": 1.0,       # 现金比例 ≥ exit_cash → 离场档（清仓语义）
    "portfolio_cache": True,  # v3 组合按 date 落盘缓存（同日晚间链多轮复用，性能缓存）
}

_LABEL_CN = {"strong_uptrend": "强势上行", "uptrend": "上行", "choppy": "震荡",
             "downtrend": "下行", "strong_downtrend": "深度下行"}


def _load_cfg() -> dict:
    """读 config/params.yaml 的 daily_signal 段（缺失回退缺省，不崩溃）"""
    try:
        from data.config import load_params
        return {**CFG_DEF, **(load_params().get("daily_signal") or {})}
    except Exception:
        return dict(CFG_DEF)


def _latest(pattern: str, sub: Path):
    """sub 目录下 pattern glob 取 mtime 最新文件（写保护免疫：读取方 glob 最新）"""
    import glob as _g
    fs = sorted(_g.glob(str(sub / pattern)), key=os.path.getmtime)
    return Path(fs[-1]) if fs else None


# ============================================================
# 数据源读取（同源铁律：与 UI loadBuyOrder 完全同口径）
# ============================================================
def _latest_decisions() -> list:
    """最近一份 deck 审批记录（logs/deck_decisions_*.json；JSON 数组）"""
    f = _latest("deck_decisions_*.json", BASE / "logs")
    if f is None:
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get("decisions") or d.get("entries") or []
    return []


def _pitch_candidates() -> list:
    """L2 决策卡同源聚合（与 ui pitch.html loadBuyOrder 逐字段一致，stress_same_source 对拍）：
    pitch_v2（pitch 长线）+ tech_pitch（entries 短线），长线优先去重；
    审批状态 = deck_decisions 最近 action（buy→已买入 / drop→已放弃 / 无→待审批）；
    容错：坏 JSON 源跳过、字段缺失兜底（name←code、otype/score←None）、code 大小写归一。"""
    status_map = {}
    for rec in _latest_decisions():
        code = str(rec.get("code", "")).strip().upper()
        act = rec.get("action")
        if code and act in ("buy", "drop"):
            status_map[code] = act   # 同一 code 后写覆盖（最近 action 生效）

    out, seen = [], set()
    for src, pat, sub in (("pitch_long", "pitch_v2_*.json", "pitch"),
                          ("tech_short", "tech_pitch_*.json", "entries")):
        f = _latest(pat, BASE / "logs")
        if f is None:
            continue
        try:
            items = (json.loads(f.read_text(encoding="utf-8")) or {}).get(sub) or []
        except Exception:
            continue   # 坏 JSON 只跳过该源，不拖垮聚合
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, str):
                it = {"code": it}
            if not isinstance(it, dict):
                continue
            code = str(it.get("code", "")).strip().upper()
            if not code or code in seen:
                continue   # ★同源去重：长线优先（pitch_long 先入 seen，短线跳过）
            seen.add(code)
            c = dict(it)
            c["code"] = code
            c.setdefault("name", code)
            c.setdefault("otype", None)   # ★压测 A7 直接键访问：缺失必须显式 None，不能缺键
            c.setdefault("score", None)
            c["src"] = src
            act = status_map.get(code)
            c["status"] = "已买入" if act == "buy" else ("已放弃" if act == "drop" else "待审批")
            out.append(c)
    return out


def _machine_decision() -> list:
    """机器决策池：output/pool_layers_*.json 的 decision 层（三层池 L3，真实筛选结果）"""
    f = _latest("pool_layers_*.json", OUT_DIR)
    if f is None:
        return []
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for it in (d or {}).get("decision") or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip().upper()
        if not code:
            continue
        e = dict(it)
        e["code"] = code
        e.setdefault("name", code)
        e["src"] = "machine_pool"
        out.append(e)
    return out


def _stratified_sample(codes: list, n: int, date: str) -> list:
    """兜底分层抽样：v3 硬过滤池内按行业分层、日期种子确定可复现。
    定位：仅供模拟盘/持仓跟踪兜底，**不是**真实买入指令源（真实指令 = L2 决策卡同源）。"""
    if not codes or n <= 0:
        return []
    codes = [str(c).upper() for c in codes]
    ind_map, name_map = {}, {}
    try:
        import sqlite3
        from data.cache import CACHE_DIR
        _con = sqlite3.connect(f"file:{CACHE_DIR / 'stock_basic.db'}?mode=ro", uri=True, timeout=3)
        for _code, _name, _ind in _con.execute("SELECT code, name, industry FROM stock_basic").fetchall():
            _c = str(_code).upper()
            ind_map[_c] = (str(_ind or "").strip() or "未知")
            name_map[_c] = _name or _c
        _con.close()
    except Exception:
        pass
    groups = {}
    for c in codes:
        groups.setdefault(ind_map.get(c, "未知"), []).append(c)
    rng = random.Random(int(hashlib.md5(str(date).encode()).hexdigest()[:8], 16))
    for g in groups.values():
        rng.shuffle(g)
    out = []
    while len(out) < n and any(groups.values()):
        for g in list(groups.values()):
            if not g or len(out) >= n:
                continue
            code = g.pop(0)
            out.append({"code": code, "name": name_map.get(code, code),
                        "industry": ind_map.get(code, "未知"), "src": "v3_universe"})
    return out


# ============================================================
# 主策略 v3 组合（等权 + Regime 择时 + 硬过滤）
# ============================================================
def v3_portfolio(date) -> dict:
    """主策略 v3 持仓清单（strategy/equal_weight_timing.portfolio 同源）：
    {date, regime_cash_ratio, n_stocks, codes, target_position_pct}
    ★性能缓存：按 date 落盘 output/v3_portfolio_cache_{date}.json——全市场硬过滤约
    数分钟，同日晚间链（17:30/22:00/dev_auto 4h）多轮复用；次日 date 变化自动失效。
    数据缺失时由调用方兜底（不在此抛异常语义）。"""
    cfg = _load_cfg()
    date = str(date)
    if cfg.get("portfolio_cache", True):
        _p = OUT_DIR / f"v3_portfolio_cache_{date}.json"
        if _p.exists():
            try:
                return json.loads(_p.read_text(encoding="utf-8"))
            except Exception:
                pass
    from strategy.equal_weight_timing import portfolio as _portfolio
    port = _portfolio(date) or {}
    if cfg.get("portfolio_cache", True) and port.get("codes"):
        try:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / f"v3_portfolio_cache_{date}.json").write_text(
                json.dumps(port, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return port


def _regime_label(date) -> str:
    """Regime 五档标签（过渡期代理，宏观数据库接入后由 timing_system 替换）。
    RegimeDetector 原始档位 → 消费方标签集（live_api/timing_system 映射表）：
    panic→strong_downtrend / uptrend_volatile→uptrend；数据缺失 → ""（消费方兜底「—/中性」）。"""
    try:
        import pandas as pd
        from data.config import load_params
        from strategy.equal_weight_timing import load_index_ohlc
        from strategy.timing import RegimeDetector
        rg = load_params().get("regime") or {}
        rd = RegimeDetector({"confirm_days": rg.get("confirm_days", 5),
                             "cooldown_days": rg.get("cooldown_days", 0)})
        d = load_index_ohlc(str(date))
        hist = d[d.index <= pd.Timestamp(str(date))]
        if len(hist) < 60:
            return ""
        win = hist.iloc[-500:]
        state = "choppy"
        for i in range(len(win)):
            state = rd.update(win.iloc[: i + 1])
        return {"panic": "strong_downtrend", "downtrend": "downtrend", "choppy": "choppy",
                "uptrend_volatile": "uptrend", "strong_uptrend": "strong_uptrend"}.get(state, state)
    except Exception:
        return ""


# ============================================================
# 今日信号主流程
# ============================================================
def generate(date=None, capital=None, skip_audit=False) -> dict:
    """生成今日信号（含落盘）：
    数据审计闸门 → v3 组合（现金档位/硬过滤池）→ L2 决策卡同源聚合（降级链）→ 落盘。"""
    cfg = _load_cfg()
    if date is None:
        try:
            from data.cache import DailyCache
            date = str(DailyCache().latest_trade_date() or datetime.now().strftime("%Y-%m-%d"))
        except Exception:
            date = datetime.now().strftime("%Y-%m-%d")
    date = str(date)
    if capital is None:
        try:
            capital = float(cfg.get("capital", 200000))
        except Exception:
            capital = 200000.0
    max_buy = int(cfg.get("max_buy_n") or 5)
    max_hold = int(cfg.get("max_hold_n") or 20)
    half_cash = float(cfg.get("half_cash") or 0.5)
    exit_cash = float(cfg.get("exit_cash") or 1.0)

    # 1) 数据审计闸门（风控前置：数据不可信则策略不可信）
    gate = {"ok": True, "reason": ""}
    if not skip_audit:
        try:
            import risk.data_audit as _da
            _ok, _res = _da.DataAuditor(_da._load_config()).gate()
            gate = {"ok": bool(_ok),
                    "reason": (_res or {}).get("block_reason", ""),
                    "health": (_res or {}).get("health")}
        except Exception as e:
            gate = {"ok": False, "reason": f"数据审计不可用（{type(e).__name__}: {str(e)[:80]}）"}

    # 2) 主策略 v3 组合（等权 + Regime 现金 + 硬过滤池）
    portfolio, port_err = {}, ""
    if gate["ok"]:
        try:
            portfolio = v3_portfolio(date) or {}
        except Exception as e:
            port_err = f"{type(e).__name__}: {str(e)[:100]}"
    _cash = portfolio.get("regime_cash_ratio")
    cash = float(_cash) if _cash is not None else 1.0   # 缺省保守：全现金
    codes = list(portfolio.get("codes") or [])
    if port_err:   # 组合不可算 → 保守离场，不生成任何买入
        cash, codes = 1.0, []
    regime_level = "exit" if cash >= exit_cash else ("half" if cash >= half_cash else "full")
    regime_label = _regime_label(date) if gate["ok"] else ""

    # 3) L2 决策卡同源聚合 + 设计内降级链（铁律②：无第二套推荐逻辑）
    cands = _pitch_candidates()
    pending = [c for c in cands if c["status"] == "待审批"]
    machine = _machine_decision()
    buy_order, hold_plan = [], []
    pitch_degraded = False
    sample_method = ""
    if not gate["ok"]:
        sample_method = "闸门阻断：不生成买入清单"
    elif regime_level != "full":
        sample_method = "减仓/离场档：只减不加，不生成买入清单"
    elif pending:
        buy_order = sorted(pending,
                           key=lambda c: (c.get("score") is None, -(c.get("score") or 0), c["code"]))[:max_buy]
        sample_method = f"L2 决策卡同源聚合（pitch_v2 长线 + tech_pitch 短线，{len(cands)} 卡 → 待审批 {len(buy_order)} 只）"
    elif machine:
        buy_order = machine[:max_buy]
        pitch_degraded = True
        sample_method = f"降级：机器决策池（pool_layers decision {len(machine)} 只）"
    else:
        buy_order = _stratified_sample(codes, max_buy, date)
        pitch_degraded = True
        sample_method = f"降级：分层抽样（v3 硬过滤池 {len(codes)} 只，日期种子可复现）"
    # hold_plan = 策略决策池建议（live_api 机会池「系统建议」补位；防守档为空）
    if gate["ok"] and regime_level == "full":
        hold_plan = machine[:max_hold] if machine else [dict(x) for x in buy_order]

    # 4) 建议文案（持有池「今日指令」面板 advice 字段）
    if not gate["ok"]:
        advice = f"闸门阻断：{gate.get('reason') or '数据审计 FAIL'}——数据不可信，今日信号不生成买入清单"
    elif regime_level == "half":
        advice = f"减仓档（现金 {cash:.0%}≥{half_cash:.0%}）：只减不加，今日无买入指令"
    elif regime_level == "exit":
        advice = f"离场档（现金 {cash:.0%}）：清仓语义，今日无买入指令"
    elif not buy_order:
        advice = f"满仓档：无待审批 Pitch 且无机器决策池候选——宁缺毋滥，今日无买入指令（{sample_method}）"
    elif pitch_degraded:
        advice = f"满仓档：Pitch 无待审批候选，今日买入指令降级 {len(buy_order)} 只（{sample_method}）"
    else:
        advice = (f"满仓档（{_LABEL_CN.get(regime_label, regime_label or '—')}）：今日买入指令 "
                  f"{len(buy_order)} 只（L2 决策卡同源聚合，待审批执行）；池外一律不碰")

    result = {
        "date": date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "capital": capital,
        "regime_label": regime_label,
        "regime_level": regime_level,
        "regime_cash_ratio": cash,
        "n_passed": len(codes) if (gate["ok"] and not port_err) else 0,
        "codes": codes,
        "n_hold": len(buy_order),
        "buy_order": buy_order,
        "hold_plan": hold_plan,
        "pitch_degraded": pitch_degraded,
        "sample_method": sample_method,
        "gate": gate,
        "advice": advice,
        "sources": {
            "pitch_v2": (_latest("pitch_v2_*.json", BASE / "logs") or _P("")).name,
            "tech_pitch": (_latest("tech_pitch_*.json", BASE / "logs") or _P("")).name,
            "deck_decisions": (_latest("deck_decisions_*.json", BASE / "logs") or _P("")).name,
            "pool_layers": (_latest("pool_layers_*.json", OUT_DIR) or _P("")).name,
        },
        "note": ("组合计算失败：" + port_err) if port_err else "",
    }

    # 5) 落盘（★写保护免疫：时间戳主写 + 固定名兜底；消费方 glob 最新）
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _out = OUT_DIR / f"daily_signal_{_ts}.json"
    _payload = json.dumps(result, ensure_ascii=False, indent=1)
    _out.write_text(_payload, encoding="utf-8")
    try:
        (OUT_DIR / "daily_signal.json").write_text(_payload, encoding="utf-8")
    except Exception:
        pass
    result["file"] = _out.name
    return result


def _P(name: str) -> Path:
    return Path(name)


def main() -> int:
    ap = argparse.ArgumentParser(description="今日信号（v3 开源替代版，L2 决策卡同源聚合）")
    ap.add_argument("--date", default=None, help="基准日（默认最新交易日）")
    ap.add_argument("--capital", type=float, default=None, help="资金规模（默认 params daily_signal.capital）")
    ap.add_argument("--no-audit", action="store_true", help="调试：跳过数据审计闸门")
    args = ap.parse_args()
    r = generate(date=args.date, capital=args.capital, skip_audit=args.no_audit)
    print(f"== 今日信号 {r['date']} ==")
    print(f"闸门: {'🟢 放行' if r['gate']['ok'] else '⛔ 阻断'}"
          + (f" — {r['gate']['reason']}" if not r['gate']['ok'] else ""))
    print(f"Regime: {r['regime_label'] or '—'} / {r['regime_level']} / 现金 {r['regime_cash_ratio']:.0%}")
    print(f"硬过滤通过: {r['n_passed']} 只（v3 等权持仓池）")
    print(f"买入清单: {r['n_hold']} 只 {[c['code'] for c in r['buy_order']]}（{r['sample_method']}）")
    print(f"策略决策池建议: {len(r['hold_plan'])} 只")
    print(f"建议: {r['advice']}")
    print(f"产物: {r['file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
