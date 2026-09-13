"""config/params.yaml 兜底测试（load_params）。"""
import shutil
from pathlib import Path

import data.config as config
from data.config import load_params


def test_load_params_has_key_sections():
    cfg = load_params()
    for sec in ("data", "factors", "risk", "backtest", "regime"):
        assert sec in cfg, f"配置段缺失: {sec}"


def test_load_params_cleans_placeholder(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    shutil.copy(config.CONFIG_DIR / "params.yaml.example", cfg_dir / "params.yaml.example")
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    cfg = config.load_params()
    assert cfg["data"].get("tushare_token") is None
