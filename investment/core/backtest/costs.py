"""交易成本模型（增强版）。

费率权威源：`core.backtest.cost_port`（CostPort）。本模块实现计费算法：
- 佣金 / 印花税 / 过户（bps）
- 滑点（与波动率正相关，有上限）
- 冲击成本（与成交额和流动性相关）
- 组合调仓：按真实换手计费（续持不扣往返）
"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Sequence

from core.backtest.cost_port import backtest_default_costs

DEFAULT_COSTS: Dict[str, Any] = backtest_default_costs()


def load_cost_config(override: Optional[dict] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_COSTS)
    if override:
        cfg.update(override)
    return cfg


def _estimate_volatility(bars: List[dict]) -> float:
    """基于近期K线估算波动率（年化）。"""
    if not bars or len(bars) < 5:
        return 0.02

    closes = [float(b.get("close", 0)) for b in bars[-20:]]
    closes = [c for c in closes if c > 0]
    if len(closes) < 3:
        return 0.02

    returns = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes))]
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / max(1, len(returns) - 1)
    daily_vol = math.sqrt(variance)
    return daily_vol * math.sqrt(252)


def estimate_slippage(
    bars: List[dict],
    config: Optional[dict] = None,
) -> float:
    """
    基于波动率估算动态滑点（bps）。
    波动率越高，滑点越大；上限默认 10 bps。
    """
    cfg = load_cost_config(config)
    base_bps = float(cfg.get("base_slippage_bps", 3.0))
    vol_factor = float(cfg.get("volatility_slippage_factor", 0.5))
    max_bps = float(cfg.get("max_slippage_bps", 10.0))

    annual_vol = _estimate_volatility(bars)
    # 波动率每增加10%，滑点增加vol_factor个bps
    dynamic_bps = base_bps + annual_vol * 100 * vol_factor

    return max(base_bps, min(dynamic_bps, max_bps))


def estimate_impact_cost(
    order_value: float,
    daily_volume: float,
    config: Optional[dict] = None,
) -> float:
    """
    估算冲击成本（bps）。
    冲击成本 = impact_coefficient * sqrt(订单金额/日均成交额)

    基于平方根模型（实证研究常用）。
    """
    cfg = load_cost_config(config)
    coeff = float(cfg.get("impact_coefficient", 0.1))

    if daily_volume <= 0 or order_value <= 0:
        return 0.0

    participation_rate = order_value / daily_volume
    impact = coeff * math.sqrt(max(0, participation_rate)) * 100

    return min(impact, 50.0)


def _slippage_bps(
    cfg: dict,
    bars: Optional[List[dict]] = None,
) -> float:
    if bars:
        return estimate_slippage(bars, cfg)
    return float(cfg.get("base_slippage_bps", 3.0))


def side_cost_bps(
    side: str,
    *,
    config: Optional[dict] = None,
    bars: Optional[List[dict]] = None,
    order_value: float = 0,
    daily_volume: float = 0,
) -> float:
    """单边成本（bps）。side: buy | sell。"""
    cfg = load_cost_config(config)
    key = (side or "buy").strip().lower()
    if key not in ("buy", "sell"):
        key = "buy"

    comm = float(cfg.get("commission_bps", 0))
    transfer = float(cfg.get("transfer_fee_bps", 0))
    slip = _slippage_bps(cfg, bars)
    impact = 0.0
    if order_value > 0 and daily_volume > 0:
        impact = estimate_impact_cost(order_value, daily_volume, cfg)

    total = comm + transfer + slip + impact
    if key == "sell":
        total += float(cfg.get("stamp_duty_bps_sell", 0))
    return total


def round_trip_cost_pct(
    config: Optional[dict] = None,
    bars: Optional[List[dict]] = None,
    order_value: float = 0,
    daily_volume: float = 0,
) -> float:
    """
    买卖各一次的总成本（占名义本金 %）。
    """
    buy = side_cost_bps(
        "buy",
        config=config,
        bars=bars,
        order_value=order_value,
        daily_volume=daily_volume,
    )
    sell = side_cost_bps(
        "sell",
        config=config,
        bars=bars,
        order_value=order_value,
        daily_volume=daily_volume,
    )
    return (buy + sell) / 100.0


def rebalance_cost_pct(
    prev_codes: Optional[Sequence[str]] = None,
    curr_codes: Optional[Sequence[str]] = None,
    *,
    config: Optional[dict] = None,
    bars: Optional[List[dict]] = None,
    order_value: float = 0,
    daily_volume: float = 0,
    prev_weights: Optional[Dict[str, float]] = None,
    curr_weights: Optional[Dict[str, float]] = None,
) -> float:
    """
    组合调仓成本（占组合名义 %）。

    - 无权重：等权近似（续持不扣；按换出/换入只数）
    - 有权重（百分比）：按权重增减的买卖侧换手计费
    """
    prev = [str(c).strip() for c in (prev_codes or []) if str(c).strip()]
    curr = [str(c).strip() for c in (curr_codes or []) if str(c).strip()]
    if not curr and not prev:
        return 0.0

    buy_bps = side_cost_bps(
        "buy",
        config=config,
        bars=bars,
        order_value=order_value,
        daily_volume=daily_volume,
    )
    sell_bps = side_cost_bps(
        "sell",
        config=config,
        bars=bars,
        order_value=order_value,
        daily_volume=daily_volume,
    )

    if prev_weights is not None or curr_weights is not None:
        pw = {str(k): float(v) for k, v in (prev_weights or {}).items() if float(v) > 0}
        cw = {str(k): float(v) for k, v in (curr_weights or {}).items() if float(v) > 0}
        codes = set(pw) | set(cw)
        buy_frac = 0.0
        sell_frac = 0.0
        for c in codes:
            delta = float(cw.get(c) or 0.0) - float(pw.get(c) or 0.0)
            if delta > 0:
                buy_frac += delta
            elif delta < 0:
                sell_frac += -delta
        cost_bps = (buy_frac / 100.0) * buy_bps + (sell_frac / 100.0) * sell_bps
        return round(cost_bps / 100.0, 6)

    prev_set = set(prev)
    curr_set = set(curr)
    sold = prev_set - curr_set
    bought = curr_set - prev_set

    cost_bps = 0.0
    if sold and prev:
        w_sell = 1.0 / len(prev)
        cost_bps += len(sold) * w_sell * sell_bps
    if bought and curr:
        w_buy = 1.0 / len(curr)
        cost_bps += len(bought) * w_buy * buy_bps

    return round(cost_bps / 100.0, 6)


def apply_trade_cost(
    gross_return_pct: float,
    *,
    config: Optional[dict] = None,
    bars: Optional[List[dict]] = None,
    order_value: float = 0,
    daily_volume: float = 0,
) -> float:
    """从毛收益扣除往返成本（单票/旧路径）。"""
    cost = round_trip_cost_pct(config, bars, order_value, daily_volume)
    return round(gross_return_pct - cost, 4)


def apply_rebalance_cost(
    gross_return_pct: float,
    prev_codes: Optional[Sequence[str]] = None,
    curr_codes: Optional[Sequence[str]] = None,
    *,
    config: Optional[dict] = None,
    bars: Optional[List[dict]] = None,
    order_value: float = 0,
    daily_volume: float = 0,
) -> float:
    """从毛收益扣除调仓换手成本。"""
    cost = rebalance_cost_pct(
        prev_codes,
        curr_codes,
        config=config,
        bars=bars,
        order_value=order_value,
        daily_volume=daily_volume,
    )
    return round(float(gross_return_pct) - cost, 4)


def net_metrics_from_gross(
    gross_returns: list,
    *,
    config: Optional[dict] = None,
    bars: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    from core.backtest.engine import _trade_metrics

    net = [apply_trade_cost(r, config=config, bars=bars) for r in gross_returns]
    gross_m = _trade_metrics(gross_returns)
    net_m = _trade_metrics(net)
    return {
        "gross": gross_m,
        "net": net_m,
        "cost_assumption_pct": round_trip_cost_pct(config, bars),
    }


def get_cost_breakdown(
    price: float,
    shares: int,
    bars: Optional[List[dict]] = None,
    config: Optional[dict] = None,
) -> Dict[str, Any]:
    """
    获取交易成本明细。
    """
    cfg = load_cost_config(config)
    gross_value = price * shares

    # 佣金
    commission_bps = float(cfg.get("commission_bps", 2.5))
    commission = max(gross_value * commission_bps / 10000, float(cfg.get("commission_min", 5.0)))

    # 印花税（仅卖出）
    stamp_duty = gross_value * float(cfg.get("stamp_duty_bps_sell", 5.0)) / 10000

    # 滑点
    slippage_bps = estimate_slippage(bars, cfg) if bars else float(cfg.get("base_slippage_bps", 3.0))
    slippage_cost = gross_value * slippage_bps / 10000

    # 冲击成本：优先末根独立成交额，否则量×价
    daily_volume = 0.0
    if bars:
        last = bars[-1] or {}
        try:
            from core.bar_fields import bar_amount

            daily_volume = float(bar_amount(last) or 0.0)
        except (TypeError, ValueError):
            daily_volume = 0.0
    impact_bps = estimate_impact_cost(gross_value, daily_volume, cfg)
    impact_cost = gross_value * impact_bps / 10000

    return {
        "gross_value": round(gross_value, 2),
        "commission": round(commission, 2),
        "commission_bps": commission_bps,
        "stamp_duty": round(stamp_duty, 2),
        "stamp_duty_bps_sell": float(cfg.get("stamp_duty_bps_sell", 5.0)),
        "slippage_cost": round(slippage_cost, 2),
        "slippage_bps": round(slippage_bps, 2),
        "max_slippage_bps": float(cfg.get("max_slippage_bps", 10.0)),
        "impact_cost_bps": round(impact_bps, 2),
        "impact_cost": round(impact_cost, 2),
        "daily_volume_est": round(daily_volume, 2),
        "total_buy_cost": round(commission + slippage_cost + impact_cost, 2),
        "total_sell_cost": round(commission + stamp_duty + slippage_cost + impact_cost, 2),
        "round_trip_pct": round(
            (commission * 2 + stamp_duty + slippage_cost * 2 + impact_cost * 2)
            / max(gross_value, 1e-9)
            * 100,
            4,
        ),
        "cost_mode": "turnover",
        "note": "组合回测按换手计费：续持不扣整轮往返；本明细为单票名义参考。",
    }
