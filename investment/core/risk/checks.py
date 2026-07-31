"""账户 / 组合风控门禁（Q4 + N3 行业集中度 · R3 结构化原因码）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def check_account_risk(
    paper: dict,
    summary: Optional[dict] = None,
    *,
    risk: Optional[dict] = None,
) -> Dict[str, Any]:
    """返回 {ok, blocks, block_items, warnings, exposure, limits}。blocks 非空时应跳过加仓。"""
    from core.paper_costs import resolve_cost_model
    from core.portfolio_optimize import _sector_for, load_sector_map
    from core.risk.exposure import build_exposure_matrix
    from core.strategy import get_strategy_spec

    spec_risk = risk
    if spec_risk is None:
        sid = paper.get("strategy_id") or "short"
        try:
            spec_risk = get_strategy_spec(str(sid)).get("risk") or {}
        except Exception:
            spec_risk = {}

    max_dd = float(spec_risk.get("max_drawdown_pct") or 20.0)
    target_dd = float(spec_risk.get("target_drawdown_pct") or max_dd * 0.6)
    max_pos_pct = float(spec_risk.get("max_position_pct") or 25.0)
    max_sector_pct = float(spec_risk.get("max_sector_pct") or 40.0)
    max_positions = int(spec_risk.get("max_positions") or 5)

    sm = summary or {}
    blocks: List[str] = []
    block_items: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def _block(code: str, message: str, **extra: Any) -> None:
        blocks.append(message)
        item: Dict[str, Any] = {"code": code, "message": message}
        item.update(extra)
        block_items.append(item)

    dd = sm.get("max_drawdown_pct")
    if dd is not None and float(dd) >= max_dd:
        _block(
            "drawdown_limit",
            f"账户回撤 {dd}% ≥ 限额 {max_dd}%：暂停加仓",
            value=float(dd),
            limit=max_dd,
        )
    elif dd is not None and float(dd) >= target_dd:
        warnings.append(f"账户回撤 {dd}% ≥ 目标预警 {target_dd}%")

    holdings = sm.get("holdings") or paper.get("holdings") or []
    equity = float(sm.get("equity") or 0)
    if equity <= 0:
        equity = float(paper.get("cash") or 0)
    n = len([h for h in holdings if float(h.get("shares") or 0) > 0])
    if n >= max_positions:
        warnings.append(f"持仓只数 {n} ≥ 上限 {max_positions}")

    sector_map = load_sector_map()
    sector_mv: Dict[str, float] = {}
    for h in holdings:
        mv = float(h.get("market_value") or 0)
        if equity > 0 and mv > 0:
            pct = mv / equity * 100.0
            code = str(h.get("stock_code") or "?")
            if pct > max_pos_pct:
                _block(
                    "max_position",
                    f"{code} 仓位 {pct:.1f}% > 单票上限 {max_pos_pct}%",
                    stock_code=code,
                    value=round(pct, 2),
                    limit=max_pos_pct,
                )
            sector = str(h.get("sector") or _sector_for(code, sector_map))
            sector_mv[sector] = float(sector_mv.get(sector) or 0.0) + mv

    if equity > 0:
        for sector, mv in sector_mv.items():
            pct = mv / equity * 100.0
            if pct > max_sector_pct:
                _block(
                    "max_sector",
                    f"行业 {sector} 敞口 {pct:.1f}% > 上限 {max_sector_pct}%",
                    sector=sector,
                    value=round(pct, 2),
                    limit=max_sector_pct,
                )

    exposure = build_exposure_matrix(paper, sm, risk=spec_risk, sector_map=sector_map)

    codes = [str(i.get("code") or "") for i in block_items if i.get("code")]
    return {
        "ok": not blocks,
        "blocks": blocks,
        "block_items": block_items,
        "block_codes": codes,
        "warnings": warnings,
        "exposure": exposure,
        "cost_model": resolve_cost_model(paper),
        "limits": {
            "max_drawdown_pct": max_dd,
            "target_drawdown_pct": target_dd,
            "max_position_pct": max_pos_pct,
            "max_sector_pct": max_sector_pct,
            "max_positions": max_positions,
        },
    }
