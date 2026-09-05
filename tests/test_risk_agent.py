"""RiskAgent.check_order 风控逻辑测试（sector 修复 + 集中度）。"""
from risk.risk_agent import RiskAgent, Decision


def test_sector_concentration_rejects_same_sector():
    ra = RiskAgent({})
    cp = {"600519.SH": 0.1, "000858.SZ": 0.1, "000596.SZ": 0.1}  # 3 只 C 行业
    sm = {"600519.SH": "C", "000858.SZ": "C", "000596.SZ": "C", "000568.SZ": "C", "000001.SZ": "J"}
    r = ra.check_order("000568.SZ", 0.1, cp, 0.0, sector="C", sector_map=sm)
    assert r.decision == Decision.REJECT  # 0.3 + 0.1 = 0.4 > 0.30


def test_sector_concentration_allows_diff_sector():
    ra = RiskAgent({})
    cp = {"600519.SH": 0.1, "000858.SZ": 0.1}
    sm = {"600519.SH": "C", "000858.SZ": "C", "000001.SZ": "J"}
    r = ra.check_order("000001.SZ", 0.1, cp, 0.0, sector="J", sector_map=sm)
    assert r.decision == Decision.APPROVE


def test_total_exposure_rejects_over_cap():
    ra = RiskAgent({})
    cp = {"c1": 0.3, "c2": 0.3, "c3": 0.3}  # 0.9 + 0.1 = 1.0 > 0.80
    r = ra.check_order("c4", 0.1, cp, 0.0)
    assert r.decision == Decision.REJECT
