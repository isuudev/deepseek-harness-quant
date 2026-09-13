# -*- coding: utf-8 -*-
"""Broker gateway placeholder for future automatic order execution.

Current status: disabled by design.  The quant system may approve a decision,
but it must never place a real order until a broker adapter is implemented,
reviewed, dry-run tested, and explicitly enabled in config/execution.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config" / "execution.yaml"
EXAMPLE = BASE / "config" / "execution.yaml.example"


def load_execution_config() -> dict[str, Any]:
    p = CONFIG if CONFIG.exists() else EXAMPLE
    if not p.exists():
        return {"enabled": False, "provider": None, "dry_run": True}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"enabled": False, "provider": None, "dry_run": True}


@dataclass(frozen=True)
class ExecutionStatus:
    enabled: bool
    implemented: bool
    provider: str | None
    dry_run: bool
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "implemented": self.implemented,
            "provider": self.provider,
            "dry_run": self.dry_run,
            "message": self.message,
        }


class BrokerGateway:
    """Future stable boundary for QMT/miniQMT/Ptrade/official broker APIs."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        self.cfg = cfg or load_execution_config()

    def status(self) -> ExecutionStatus:
        enabled = bool(self.cfg.get("enabled", False))
        provider = self.cfg.get("provider")
        dry_run = bool(self.cfg.get("dry_run", True))
        return ExecutionStatus(
            enabled=enabled,
            implemented=False,
            provider=provider,
            dry_run=dry_run,
            message=(
                "自动下单尚未实现；当前为只读 TODO 入口。"
                if enabled else "自动下单默认关闭；系统只生成研究/审批记录。"
            ),
        )

    def place_order(self, *args, **kwargs):
        """TODO: implement only behind explicit opt-in and multi-layer risk gates."""
        raise NotImplementedError(
            "BrokerGateway.place_order 尚未实现。请先完成券商适配器、"
            "dry-run、订单幂等、成交回报、持仓对账和 Kill Switch。"
        )
