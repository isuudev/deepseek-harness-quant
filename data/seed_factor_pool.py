# -*- coding: utf-8 -*-
"""data/seed_factor_pool.py — 因子池种子注册（★2026-09-06 新增 · R1/R2 审计修复）

背景（R1 审计 + R2 复核）：本环境缺失 factor_pool.db 与外包因子池产物（health_*.csv /
factor_manifest_*.json），导致：
  · factors/opportunities/scan.py 机会池因子全部「未登记」、icir120 空（信号联动降级）
  · factors/pool/lifecycle.py（dev_auto 每轮调用）因子池全空，无法评估/出报告
  · factors/risk/factor_risk.py 强因子表读不到 health CSV

本脚本把**代码内已固化的因子宇宙**（含主系统实证结论，全部来自仓库内文件，不编造数据）
幂等种子化，产出三件套：

  1. data/cache/factor_pool.db（registry 注册）
       - 强因子白名单 STRONG_TABLE 27 个 → active + locked（主系统 F1 实证结论，锁定人工裁决；
         本地评估只回写 score，不自动改状态）
       - 信号族映射 SIGNAL_FAMILY 全部因子 → candidate
       - 核心引擎 FACTOR_FUNCS（方向取 params.yaml factors.direction）→ candidate
       - 经典技术指标 11 个 → candidate
       - 基本面因子 6 个（fundamental_lowfreq）→ candidate
       - EPU 政策时序族 6 个（复用 lifecycle.register_epu_family）→ candidate
  2. data/factorpool/output/health/health_seed_YYYYMMDD.csv
       factor,icir120,status —— 主系统实证 ICIR120（scan.py 信号有效性权重的数据源）
  3. data/factorpool/output/factor_manifest_seed_YYYYMMDD.json
       {"factors":[{code,category,icir_60,direction,status,usage,name_cn}]} —— 外包契约格式

诚实性约束：种子数据全部标注来源（主系统实证/代码登记），本机未复评的因子状态一律
candidate、score 为空——绝不把「登记」伪装成「本机验证通过」。

用法：
  python data/seed_factor_pool.py            # 幂等，可重复跑
  python factors/pool/lifecycle.py --status  # 查看注册结果
"""
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

POOL_OUT = BASE / "data" / "factorpool" / "output"
HEALTH_DIR = POOL_OUT / "health"
HEALTH_SEED = HEALTH_DIR / f"health_seed_{datetime.now().strftime('%Y%m%d')}.csv"
MANIFEST_SEED = POOL_OUT / f"factor_manifest_seed_{datetime.now().strftime('%Y%m%d')}.json"

# 信号族 → manifest 英文 category（与 signal_family.CATEGORY_TO_FAMILY 反向一致；
# 质量族用 fundamental_lowfreq：与 scan.py EXT 排除逻辑同源——低频质量因子只供排雷/quality_gap）
FAMILY_TO_CATEGORY = {
    "价值": "value", "成长": "growth", "质量": "fundamental_lowfreq",
    "量价": "kline", "情绪": "short_term", "反转动量": "a_share_alpha",
    "资金": "institution", "政策": "policy", "行业": "industry",
    "筹码": "chip", "其他": "other",
}

FUNDAMENTAL_FACTORS = {
    "c_factor": "成长", "sue_factor": "成长", "accel_factor": "成长",
    "a_factor": "成长", "pead_factor": "成长", "profit_ok": "质量",
}

INDICATOR_SKIP = {"ema", "compute_all"}


def seed_registry() -> dict:
    """把因子宇宙注册进 factor_pool.db（幂等）"""
    from factors.pool.registry import FactorRegistry
    from factors.risk.strong_factor_table import STRONG_TABLE
    from factors.signal_family import SIGNAL_FAMILY, signal_family_of
    from factors.factor_engine import FACTOR_FUNCS
    from data.config import load_params

    reg = FactorRegistry()
    direction_cfg = (load_params().get("factors", {}) or {}).get("direction", {}) or {}
    stats = {"strong_active": 0, "candidate": 0, "epu": 0, "skipped": 0}

    # 1) 强因子白名单 → active + locked（主系统 F1 实证结论）
    strong_names = set()
    for name, v in STRONG_TABLE.items():
        reg.register(name=name, family=signal_family_of(name), kind="cross_sectional",
                     source="主系统因子池实证（F1 强因子白名单）", freq="daily",
                     direction=1,
                     note=f"主系统实证 ICIR120={v.get('icir120')} · 拥挤代理={v.get('crowd_proxy')} · "
                          f"风格暴露={v.get('style_exposed')}；本机未复评，结论以主系统为准")
        reg.set_status(name, "active", note=(
            f"主系统实证 ICIR120={v.get('icir120')}（{v.get('f3_family')}）；"
            f"本机未复评——locked 防止本地评估自动改状态"), locked=True)
        strong_names.add(name)
        stats["strong_active"] += 1

    # 2) 信号族映射全集（去重强因子）→ candidate
    for name in SIGNAL_FAMILY:
        if name in strong_names:
            continue
        reg.register(name=name, family=signal_family_of(name), kind="cross_sectional",
                     source="factors/signal_family.py 信号族登记", freq="daily",
                     direction=1, note="本机未评估（状态 candidate）；实证结论见主系统因子池")
        stats["candidate"] += 1

    # 3) 核心引擎因子（方向取 params.yaml factors.direction）
    for name in FACTOR_FUNCS:
        if name in strong_names:
            continue
        d = int(direction_cfg.get(name, 1))
        reg.register(name=name, family=signal_family_of(name), kind="cross_sectional",
                     source="factors/factor_engine.py FACTOR_FUNCS", freq="daily",
                     direction=d, note=f"方向 {d}（params.yaml factors.direction）；本机未评估")
        stats["candidate"] += 1

    # 4) 经典技术指标
    import inspect
    import factors.classic_indicators as ci
    for name, fn in inspect.getmembers(ci, inspect.isfunction):
        if name.startswith("_") or name in INDICATOR_SKIP or name in strong_names:
            continue
        reg.register(name=name, family="量价", kind="cross_sectional",
                     source="factors/classic_indicators.py", freq="daily",
                     direction=1, note="经典技术指标信号；本机未评估")
        stats["candidate"] += 1

    # 5) 基本面因子（fundamental_lowfreq）
    for name, fam in FUNDAMENTAL_FACTORS.items():
        if name in strong_names:
            continue
        reg.register(name=name, family=fam, kind="cross_sectional",
                     source="factors/fundamental.py", freq="daily",
                     direction=1, note="PIT 披露延迟口径；本机未评估")
        stats["candidate"] += 1

    # 6) EPU 政策时序族
    from factors.pool.lifecycle import register_epu_family
    register_epu_family(reg)
    stats["epu"] = 6

    return {"registry": stats, "total": reg.stats()}


def seed_health_csv() -> int:
    """主系统实证 ICIR120 → health CSV（scan.py load_factor_health + factor_risk.build 消费）

    ★列契约与 factor_risk.build() 强因子判定一致：factor,icir120,status,t120,ic_win_rate,crowding
    （缺 t120/胜率/拥挤列会被 t≥4、胜率≥60% 门槛全部过滤——种子必须带全列）。"""
    from factors.risk.strong_factor_table import STRONG_TABLE
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["factor,icir120,status,t120,ic_win_rate,crowding"]
    for name, v in STRONG_TABLE.items():
        lines.append(f"{name},{v.get('icir120')},✅有效（主系统实证）,"
                     f"{v.get('t120')},{v.get('win_rate')},{v.get('crowd_proxy')}")
    HEALTH_SEED.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(STRONG_TABLE)


def seed_manifest() -> int:
    """全因子宇宙 → 外包契约 manifest JSON（scan.py load_factor_manifest 消费）"""
    from factors.risk.strong_factor_table import STRONG_TABLE
    from factors.signal_family import SIGNAL_FAMILY, signal_family_of
    from factors.factor_engine import FACTOR_FUNCS
    from data.config import load_params

    direction_cfg = (load_params().get("factors", {}) or {}).get("direction", {}) or {}
    rows = {}

    def put(code, family, direction, status, usage, name_cn=""):
        rows[code] = {
            "code": code, "category": FAMILY_TO_CATEGORY.get(family, "other"),
            "icir_60": None, "direction": direction, "status": status,
            "usage": usage, "name_cn": name_cn,
        }

    for name, v in STRONG_TABLE.items():
        put(name, signal_family_of(name), 1, "✅有效（主系统实证）",
            "signal", "强因子白名单（ICIR120={}）".format(v.get("icir120")))
    for name in SIGNAL_FAMILY:
        if name not in rows:
            put(name, signal_family_of(name), 1, "candidate（本机未评估）", "signal")
    for name in FACTOR_FUNCS:
        if name not in rows:
            put(name, signal_family_of(name), int(direction_cfg.get(name, 1)),
                "candidate（本机未评估）", "signal")
    import inspect
    import factors.classic_indicators as ci
    for name, fn in inspect.getmembers(ci, inspect.isfunction):
        if name.startswith("_") or name in INDICATOR_SKIP or name in rows:
            continue
        put(name, "量价", 1, "candidate（本机未评估）", "signal")
    for name, fam in FUNDAMENTAL_FACTORS.items():
        if name not in rows:
            put(name, fam, 1, "candidate（本机未评估）", "risk_only")
    for name in ("epu_level", "epu_chg_1m", "epu_chg_3m", "epu_z12",
                 "epu_hl_monetary", "epu_hl_fiscal"):
        put(name, "政策", 1, "candidate（本机未评估）", "timing")

    POOL_OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST_SEED.write_text(
        json.dumps({"factors": list(rows.values())}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return len(rows)


def main():
    print("== 因子池种子注册（data/seed_factor_pool.py）==")
    r = seed_registry()
    print(f"[1/3] factor_pool.db 注册: 强因子 active {r['registry']['strong_active']} · "
          f"candidate {r['registry']['candidate']} · EPU {r['registry']['epu']} · "
          f"池总量 {r['total']}")
    n_h = seed_health_csv()
    print(f"[2/3] health 种子: {HEALTH_SEED}（{n_h} 因子，主系统实证 ICIR120）")
    n_m = seed_manifest()
    print(f"[3/3] manifest 种子: {MANIFEST_SEED}（{n_m} 因子登记）")
    print("提示: 因子状态 honest——本机未复评的均为 candidate/未评分；"
          "强因子 active 来自主系统实证（locked）。")


if __name__ == "__main__":
    main()
