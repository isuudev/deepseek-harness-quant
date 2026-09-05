# -*- coding: utf-8 -*-
"""backtest/core.py — 统一回测核心（成本模型 + 向量化调仓回测 + 指标口径）

★回测引擎统一（2026-09）：
  1. 成本模型统一：单边 COST_PER_SIDE（佣金万2.6 + 卖出印花税0.05% + 滑点0.1%），
     每次全换仓扣 2×COST_PER_SIDE，发生在换仓日（非「摊到每天」的粗糙近似）。
  2. 执行口径统一：T+1 open 买入 + 一字板涨停过滤（open≈high 买不进）；
     未提供 opens/highs 时降级为 close-to-close（首日不产生价差，仍扣成本）。
  3. 指标口径统一：一律走 backtest/bt_report.compute_metrics
     （年化因子自适应日/周/月 + sortino/calmar/win_rate）。

供 bt_runner（前端动态回测）与 bt_engine（方向化/分类回测）共用，消除两套口径。
"""
import pandas as pd

from backtest.bt_report import compute_metrics  # noqa: F401  统一指标入口（re-export）

# 单边成本：佣金万2.6 + 卖出印花税0.05% + 滑点0.1%（与 params.yaml backtest 段一致）
COST_PER_SIDE = 0.00026 + 0.0005 + 0.001


def _fill_ret(ret, closes, opens, highs, picks, buy_pos, nxt_pos, cost_per_side):
    """单次调仓：T+1 open 买入 + 一字板过滤 + 换仓日扣双边成本，写入 ret。"""
    buyable = list(picks)
    if opens is not None and highs is not None:
        bo = opens.iloc[buy_pos].reindex(picks)
        bh = highs.iloc[buy_pos].reindex(picks)
        buyable = bo[(bo < bh - 1e-9)].index  # 一字板涨停（open≈high）买不进
    if len(buyable) < 1:
        return
    seg = closes[buyable].iloc[buy_pos: nxt_pos + 1].pct_change().fillna(0)
    # 首日收益 = close/open - 1（T+1 open 买入）；无 open 时降级为 0（close-to-close 由后续 pct_change 体现）
    if opens is not None:
        seg.iloc[0] = (closes.iloc[buy_pos][buyable] / opens.iloc[buy_pos][buyable] - 1).values
    else:
        seg.iloc[0] = 0.0
    seg.iloc[0] = seg.iloc[0] - 2.0 * cost_per_side  # 换仓日扣双边成本（卖旧 + 买新）
    if len(seg):
        ret.loc[seg.index] = seg.mean(axis=1)


def period_backtest(closes, opens, highs, score, topn, rebalance="M", top_pct=None,
                    cost_per_side=COST_PER_SIDE):
    """向量化调仓回测（统一口径）。

    参数：
      closes/opens/highs: DataFrame(index=日期, columns=代码)；opens/highs 可为 None（降级 close-to-close）
      score: DataFrame(index=日期, columns=代码) 截面评分（越大越好）
      topn: 每期持仓数（top_pct=None 时生效）
      rebalance: 'M' 月频 / 'Q' 季度 / int 固定交易日数
      top_pct: 分位选股（如 turn_low 低换手 top20%）；此时 score 为「取反后的 rank」，
               取 score >= -top_pct 即 rank <= top_pct
      cost_per_side: 单边成本率（每次全换仓扣 2×）

    返回：日收益序列（已扣真实换手成本）。
    """
    def _pick(sc):
        if top_pct is None:
            return sc.nlargest(topn).index.tolist()
        return sc[sc >= -top_pct].index.tolist()

    if isinstance(rebalance, int):
        # 固定交易日调仓：每 rebalance 个交易日选一次（跳过前 60 日热身）
        positions = list(range(60, len(closes.index), rebalance))
        if positions and positions[-1] < len(closes.index) - 1:
            positions.append(len(closes.index) - 1)
        ret = pd.Series(0.0, index=closes.index)
        for i, pos in enumerate(positions[:-1]):
            nxt_pos = positions[i + 1]
            sc = score.iloc[pos].dropna()
            if top_pct is None and len(sc) < topn:
                continue
            picks = _pick(sc)
            buy_pos = pos + 1
            if buy_pos > nxt_pos:
                continue
            _fill_ret(ret, closes, opens, highs, picks, buy_pos, nxt_pos, cost_per_side)
        return ret

    ym = closes.index.astype(str).str[:7]
    if rebalance == "Q":
        qm = pd.Series(closes.index).dt.to_period("Q").astype(str)
        period_ends = pd.Series(closes.index).groupby(qm.values).max().tolist()
    else:
        period_ends = pd.Series(closes.index).groupby(ym).max().tolist()
    ret = pd.Series(0.0, index=closes.index)
    for i, me in enumerate(period_ends):
        pos = closes.index.get_loc(me)
        if pos < 60:
            continue
        sc = score.iloc[pos].dropna()
        if top_pct is None and len(sc) < topn:
            continue
        picks = _pick(sc)
        nxt = period_ends[i + 1] if i + 1 < len(period_ends) else closes.index[-1]
        nxt_pos = closes.index.get_loc(nxt) if nxt in closes.index else len(closes) - 1
        buy_pos = pos + 1
        if buy_pos > nxt_pos:
            continue
        _fill_ret(ret, closes, opens, highs, picks, buy_pos, nxt_pos, cost_per_side)
    return ret
