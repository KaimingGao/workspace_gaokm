"""CostPort · simple_cn 费率与计费路径的唯一权威源（T4）。

禁止在纸面 / 回测 / 因子辅助计算器里各写一套隐性费率。

| 路径 | 入口 | 计费形态 |
|------|------|----------|
| 纸面现金账 | `core.paper.costs.calc_trade_fees` | 金额级（含最低佣金） |
| 纸面回放 | `core.backtest.paper_replay` → 同上 | 金额级（经 rank_lots） |
| 回测单票 | `costs.apply_trade_cost` | bps 往返近似 |
| 回测组合 TopK | `costs.rebalance_cost_pct` | **换手**（续持不扣往返；引擎=topk_research） |
| 研究辅助 | `signal.factors.cost.TransactionCostCalculator` | 金额级，须同源 |

撮合约束（涨跌停 / T+1 / 滑点档）见 `core.backtest.matching`（MatchPort 研究近似，非交易所）。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List

# —— simple_cn 法定/券商简化费率（bps 为权威单位）——
SIMPLE_CN_FEE: Dict[str, float] = {
    "commission_bps": 2.5,  # 万 2.5
    "commission_min": 5.0,  # 元
    "stamp_duty_bps_sell": 5.0,  # 卖出万 5（现行）
    "transfer_fee_bps": 0.1,  # 万 0.1
}

# 回测研究默认：在费率之上加动态滑点 / 冲击（纸面默认滑点常为 0）
BACKTEST_RESEARCH_DEFAULTS: Dict[str, Any] = {
    **SIMPLE_CN_FEE,
    "base_slippage_bps": 3.0,
    "max_slippage_bps": 10.0,
    "volatility_slippage_factor": 0.5,
    "impact_coefficient": 0.1,
    "market": "CN",
}

COST_MODELS = ("zero", "simple_cn")
PORTFOLIO_COST_MODE = "turnover"  # TopK 含成本时的唯一合法 cost_mode


def paper_simple_cn_params() -> Dict[str, float]:
    """纸面 `cost_params` 默认（比率，非 bps）。"""
    return {
        "commission_rate": float(SIMPLE_CN_FEE["commission_bps"]) / 10000.0,
        "min_commission": float(SIMPLE_CN_FEE["commission_min"]),
        "stamp_duty_sell": float(SIMPLE_CN_FEE["stamp_duty_bps_sell"]) / 10000.0,
        "slippage_bps": 0.0,
    }


def backtest_default_costs() -> Dict[str, Any]:
    """回测 `DEFAULT_COSTS` 副本。"""
    return dict(BACKTEST_RESEARCH_DEFAULTS)


def factor_cost_defaults() -> Dict[str, Any]:
    """`TransactionCostCalculator` 默认；印花税/佣金/过户与 CostPort 对齐。"""
    fee = SIMPLE_CN_FEE
    base_slip = float(BACKTEST_RESEARCH_DEFAULTS["base_slippage_bps"])
    return {
        "commission_rate": float(fee["commission_bps"]) / 10000.0,
        "commission_min": float(fee["commission_min"]),
        "stamp_tax_rate": float(fee["stamp_duty_bps_sell"]) / 10000.0,
        "transfer_fee_rate": float(fee["transfer_fee_bps"]) / 10000.0,
        "slippage_pct": base_slip / 10000.0,
        "use_slippage": True,
        "cost_port": "simple_cn",
    }


def _almost(a: float, b: float, tol: float = 1e-9) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def cost_port_snapshot() -> Dict[str, Any]:
    """对照纸面 / 回测 / 因子计算器与本端口；供 evals 闸门。"""
    from core.backtest.costs import DEFAULT_COSTS, load_cost_config
    from core.paper.costs import cost_params
    from core.signal.factors.cost import DEFAULT_COST_CONFIG

    mismatches: List[str] = []
    paper = cost_params({})
    bt = load_cost_config()
    fac = dict(DEFAULT_COST_CONFIG or {})

    expected_paper = paper_simple_cn_params()
    for k, v in expected_paper.items():
        if k == "slippage_bps":
            continue  # 纸面允许覆盖；默认应为 0
        if not _almost(float(paper.get(k) or 0), float(v)):
            mismatches.append(f"paper.{k}={paper.get(k)} != {v}")

    for k in (
        "commission_bps",
        "commission_min",
        "stamp_duty_bps_sell",
        "transfer_fee_bps",
        "max_slippage_bps",
    ):
        if not _almost(float(bt.get(k) or 0), float(BACKTEST_RESEARCH_DEFAULTS[k])):
            mismatches.append(f"backtest.{k}={bt.get(k)} != {BACKTEST_RESEARCH_DEFAULTS[k]}")
        if not _almost(float(DEFAULT_COSTS.get(k) or 0), float(BACKTEST_RESEARCH_DEFAULTS[k])):
            mismatches.append(f"DEFAULT_COSTS.{k} drift")

    fac_exp = factor_cost_defaults()
    for k in ("commission_rate", "commission_min", "stamp_tax_rate", "transfer_fee_rate"):
        if not _almost(float(fac.get(k) or 0), float(fac_exp[k])):
            mismatches.append(f"factor.{k}={fac.get(k)} != {fac_exp[k]}")

    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "simple_cn_fee": dict(SIMPLE_CN_FEE),
        "portfolio_cost_mode": PORTFOLIO_COST_MODE,
        "paper": paper,
        "backtest": {
            "commission_bps": bt.get("commission_bps"),
            "stamp_duty_bps_sell": bt.get("stamp_duty_bps_sell"),
            "max_slippage_bps": bt.get("max_slippage_bps"),
        },
        "factor": {
            "commission_rate": fac.get("commission_rate"),
            "stamp_tax_rate": fac.get("stamp_tax_rate"),
        },
    }


def assert_cost_port_aligned() -> Dict[str, Any]:
    snap = cost_port_snapshot()
    if not snap["ok"]:
        raise AssertionError(
            "CostPort 费率漂移（禁止第二套隐性费率）: " + "; ".join(snap["mismatches"])
        )
    return snap


def describe_cost_port() -> str:
    fee = SIMPLE_CN_FEE
    return (
        f"CostPort simple_cn: 佣金 {fee['commission_bps']}bps "
        f"(最低 {fee['commission_min']}元) · 卖出印花税 {fee['stamp_duty_bps_sell']}bps · "
        f"组合含成本 cost_mode={PORTFOLIO_COST_MODE}"
    )
