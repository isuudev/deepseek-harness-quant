"""统一回测核心 period_backtest 黄金基准测试。"""
import numpy as np
import pandas as pd

from backtest.core import period_backtest, COST_PER_SIDE


def _panel():
    idx = pd.date_range("2020-01-01", periods=120, freq="B")
    rng = np.random.RandomState(42)
    closes = pd.DataFrame(
        {f"c{i}": 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, 120)) for i in range(5)},
        index=idx)
    return closes, closes * 0.99, closes * 1.02


def test_period_backtest_returns_daily_series():
    closes, opens, highs = _panel()
    score = closes.rank(axis=1, pct=True)
    ret = period_backtest(closes, opens, highs, score, topn=3, rebalance="M")
    assert isinstance(ret, pd.Series)
    assert len(ret) == len(closes)


def test_period_backtest_cost_reduces_return():
    closes, opens, highs = _panel()
    score = closes.rank(axis=1, pct=True)
    ret_cost = period_backtest(closes, opens, highs, score, topn=3,
                               rebalance="M", cost_per_side=COST_PER_SIDE)
    ret_free = period_backtest(closes, opens, highs, score, topn=3,
                               rebalance="M", cost_per_side=0.0)
    assert (1 + ret_cost).prod() <= (1 + ret_free).prod() + 1e-9
