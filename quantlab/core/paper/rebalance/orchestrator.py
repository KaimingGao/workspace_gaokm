"""纸面日循环编排。

Follow / 日报 ``paper.rebalance()`` 走观察池 ``rank_lots``，不经过本模块。
本模块只服务 ``/api/paper/run`` 的持仓规则日循环（``holding_rules``）。
分池簿 / 横截面 TopK 调仓已删除，不再保留停用桩。
"""

from typing import Any, Dict, Literal

RebalanceMode = Literal["holding_rules"]


def resolve_rebalance_mode(paper: dict) -> RebalanceMode:
    """日循环唯一模式。"""
    _ = paper
    return "holding_rules"


def run_paper_rebalance(
    paper: dict,
    *,
    mode: RebalanceMode = "holding_rules",
    dry_run: bool = False,
    simulate_buy: bool = False,
    strategy: str = "short_conservative",
    on_progress=None,
) -> Dict[str, Any]:
    """只跑持仓规则日循环。"""
    if mode != "holding_rules":
        raise ValueError(
            f"未知调仓模式 {mode!r}；纸面日循环仅 holding_rules，"
            "策略调仓请用 /follow 观察池 rank_lots"
        )
    return _run_holding_rules(
        paper,
        simulate_buy=simulate_buy,
        strategy=strategy,
        on_progress=on_progress,
        dry_run=dry_run,
    )


def _run_holding_rules(
    paper: dict,
    *,
    simulate_buy: bool,
    strategy: str,
    on_progress,
    dry_run: bool,
) -> Dict[str, Any]:
    from core.paper.cycle import run_daily_cycle

    result = run_daily_cycle(
        paper,
        simulate_buy=simulate_buy,
        strategy=strategy,
        on_progress=on_progress,
    )
    return {
        **result,
        "mode": "holding_rules",
        "dry_run": dry_run,
        "success": True,
        "ok": True,
    }
