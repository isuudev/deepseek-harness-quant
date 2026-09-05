"""config/params.yaml 兜底测试（load_params）。"""
from data.config import load_params


def test_load_params_has_key_sections():
    cfg = load_params()
    for sec in ("data", "factors", "risk", "backtest", "regime"):
        assert sec in cfg, f"配置段缺失: {sec}"


def test_load_params_cleans_placeholder():
    cfg = load_params()
    # params.yaml 缺失时回退 .example，<your-config> 占位符应清理为 None
    assert cfg["data"].get("tushare_token") is None
