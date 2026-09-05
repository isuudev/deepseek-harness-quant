# -*- coding: utf-8 -*-
"""统一配置读取（params.yaml 兜底）。

解决「params.yaml 缺失（开源 clone 后）导致模块级 read_text 崩溃」的问题：
  1. 优先读 config/params.yaml（用户本地真实配置，被 .gitignore 排除）。
  2. 缺失时回退 config/params.yaml.example（开源模板）。
  3. 示例里的 `<your-config>` 占位符统一替换为 None，避免被当作真实 token/路径。
  4. 两者都不存在（极端）返回空 dict。

用法：
  from data.config import load_params
  cfg = load_params()          # 整个配置
  data_cfg = cfg.get("data", {})
"""
import yaml
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE / "config"

_PLACEHOLDER = "<your-config>"


def _clean(v):
    """递归替换 <your-config> 占位符为 None。"""
    if isinstance(v, str) and _PLACEHOLDER in v:
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def load_params() -> dict:
    """读 config/params.yaml；缺失回退 .example（清理占位符）；再缺返回 {}。"""
    for name in ("params.yaml", "params.yaml.example"):
        p = CONFIG_DIR / name
        if p.exists():
            try:
                return _clean(yaml.safe_load(p.read_text(encoding="utf-8")) or {})
            except Exception:
                return {}
    return {}
