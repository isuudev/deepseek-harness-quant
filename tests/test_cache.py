"""DailyCache 单库读写测试（合成数据，不依赖真实行情库）。"""
import os
import tempfile

import pandas as pd

from data.cache import DailyCache


def _make_df():
    return pd.DataFrame({
        "date": ["2026-01-01", "2026-01-02"],
        "open": [10.0, 11.0], "high": [11.0, 12.0], "low": [9.0, 10.0],
        "close": [10.5, 11.5], "preclose": [10.0, 10.5],
        "volume": [1000, 1100], "amount": [10000, 11000],
        "turn": [1.0, 1.1], "pct_chg": [0.5, 0.6], "is_st": [0, 0],
    })


def test_put_and_get_daily():
    with tempfile.TemporaryDirectory() as d:
        cache = DailyCache(db_path=os.path.join(d, "bars.db"))
        assert cache.put_daily("600519.SH", _make_df(), source="test") == 2
        out = cache.get_daily("600519.SH")
        assert out is not None and len(out) == 2
        assert list(out["close"]) == [10.5, 11.5]


def test_get_daily_batch_and_meta():
    with tempfile.TemporaryDirectory() as d:
        cache = DailyCache(db_path=os.path.join(d, "bars.db"))
        cache.put_daily("600519.SH", _make_df(), source="test")
        assert "600519.SH" in cache.get_daily_batch(["600519.SH"])
        meta = cache.get_meta("600519.SH")
        assert meta["end_date"] == "2026-01-02"
