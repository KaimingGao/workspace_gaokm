"""纸面日循环编排（已下线）。

Follow / 日报 ``paper.rebalance()`` 走观察池 ``rank_lots``，不经过本模块。
持仓规则日循环不再成交。分池簿 / 横截面 TopK 调仓已删除。
"""

from typing import Any, Dict, Literal

RebalanceMode = Literal["holding_rules"]

_RETIRED = "持仓规则日循环已下线。开盘调仓在交易执行。"


def resolve_rebalance_mode(paper: dict) -> RebalanceMode:
    """历史日循环模式名。调用不再成交。"""
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
    """拒绝持仓规则日循环，不写账。"""
    _ = (paper, dry_run, simulate_buy, strategy, on_progress)
    if mode != "holding_rules":
        raise ValueError(
            f"未知调仓模式 {mode!r}；纸面日循环已下线，"
            "策略调仓请用 /follow 观察池 rank_lots"
        )
    return {
        "ok": False,
        "success": False,
        "retired": True,
        "mode": "holding_rules",
        "dry_run": bool(dry_run),
        "error": _RETIRED,
    }
