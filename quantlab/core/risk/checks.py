"""账户 / 组合风控门禁（Q4 + N3 行业集中度 · R3 结构化原因码）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional


def check_account_risk(
    paper: dict,
    summary: Optional[dict] = None,
    *,
    risk: Optional[dict] = None,
) -> Dict[str, Any]:
    """返回 {ok, blocks, block_items, warnings, exposure, limits}。blocks 非空时应跳过加仓。"""
    from core.data.policy import (
        DEFAULT_REQUIRE_SECTOR_MAP,
        MIN_SECTOR_MAP_COVERAGE_BLOCK,
        MIN_SECTOR_MAP_COVERAGE_WARN,
        UNMAPPED_SECTOR,
    )
    from core.paper.costs import resolve_cost_model
    from core.portfolio_optimize import _sector_for, load_sector_map, sector_map_coverage
    from core.risk.exposure import build_exposure_matrix
    from core.strategy import get_strategy_spec

    spec_risk = risk
    if spec_risk is None:
        sid = paper.get("strategy_id") or "short_conservative"
        try:
            spec_risk = get_strategy_spec(str(sid)).get("risk") or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in checks.py", exc_info=True)
            spec_risk = {}

    max_dd = float(spec_risk.get("max_drawdown_pct") or 20.0)
    target_dd = float(spec_risk.get("target_drawdown_pct") or max_dd * 0.6)
    max_pos_pct = float(spec_risk.get("max_position_pct") or 25.0)
    max_sector_pct = float(spec_risk.get("max_sector_pct") or 40.0)
    max_positions = int(spec_risk.get("max_positions") or 5)

    min_cov_warn = float(
        spec_risk.get("min_sector_map_coverage")
        if spec_risk.get("min_sector_map_coverage") is not None
        else MIN_SECTOR_MAP_COVERAGE_WARN
    )
    min_cov_block = float(
        spec_risk.get("min_sector_map_coverage_block")
        if spec_risk.get("min_sector_map_coverage_block") is not None
        else MIN_SECTOR_MAP_COVERAGE_BLOCK
    )
    require_map = bool(
        spec_risk.get("require_sector_map")
        if spec_risk.get("require_sector_map") is not None
        else DEFAULT_REQUIRE_SECTOR_MAP
    )

    sm = summary or {}
    blocks: List[str] = []
    block_items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    recovery: Optional[Dict[str, Any]] = None

    def _block(code: str, message: str, **extra: Any) -> None:
        blocks.append(message)
        item: Dict[str, Any] = {"code": code, "message": message}
        item.update(extra)
        block_items.append(item)

    # 优先用当前回撤（会随反弹收窄）；回退到历史最大回撤
    dd_hist = sm.get("max_drawdown_pct")
    dd_now = sm.get("current_drawdown_pct")
    if dd_now is not None:
        dd = float(dd_now)
        dd_label = "当前回撤"
    elif dd_hist is not None:
        dd = float(dd_hist)
        dd_label = "历史最大回撤"
    else:
        dd = None
        dd_label = "回撤"

    if dd is not None and dd >= max_dd:
        _block(
            "drawdown_limit",
            f"账户{dd_label} {dd:.2f}% ≥ 限额 {max_dd}%：暂停加仓",
            value=dd,
            limit=max_dd,
            metric="current_drawdown_pct" if dd_now is not None else "max_drawdown_pct",
        )
    elif dd is not None and dd >= target_dd:
        warnings.append(f"账户{dd_label} {dd:.2f}% ≥ 目标预警 {target_dd}%")
    elif dd is not None and dd < target_dd:
        # 回撤已恢复到目标预警线下方 → 可以恢复加仓
        recovery = {
            "recovered": True,
            "current_dd": round(dd, 2),
            "recovery_threshold": target_dd,
            "note": f"{dd_label} {dd:.2f}% 已低于恢复阈值 {target_dd}%，加仓恢复",
        }
    else:
        recovery = None

    holdings = sm.get("holdings") or paper.get("holdings") or []
    equity = float(sm.get("equity") or 0)
    if equity <= 0:
        equity = float(paper.get("cash") or 0)
    n = len([h for h in holdings if float(h.get("shares") or 0) > 0])
    if n >= max_positions:
        warnings.append(f"持仓只数 {n} ≥ 上限 {max_positions}")

    sector_map = load_sector_map()
    sector_mv: Dict[str, float] = {}
    holding_codes: List[str] = []
    for h in holdings:
        mv = float(h.get("market_value") or 0)
        code = str(h.get("stock_code") or "?").strip()
        if code and code != "?":
            holding_codes.append(code)
        if equity > 0 and mv > 0:
            pct = mv / equity * 100.0
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

    # DS-R2.2：真实行业覆盖不足 → warn；require_sector_map 时硬拦
    cov = sector_map_coverage(holding_codes, sector_map=sector_map) if holding_codes else {
        "coverage": None,
        "mapped": 0,
        "total": 0,
        "unmapped": 0,
        "empty_universe": True,
    }
    cov_r = float(cov["coverage"]) if cov.get("coverage") is not None else 1.0
    unmapped_n = int(cov.get("unmapped") or 0)
    if holding_codes and unmapped_n > 0:
        msg = (
            f"持仓真实行业覆盖 {cov.get('mapped')}/{cov.get('total')} "
            f"（{cov_r:.0%}）· 未分类 {unmapped_n}"
        )
        if require_map and cov_r < min_cov_block:
            _block(
                "sector_map_thin",
                f"{msg} < 硬拦阈值 {min_cov_block:.0%}：暂停加仓（require_sector_map）",
                coverage=cov_r,
                limit=min_cov_block,
            )
        elif cov_r < min_cov_warn:
            warnings.append(f"{msg} < 预警 {min_cov_warn:.0%} · 建议 sector_map_enrich")

    if equity > 0:
        for sector, mv in sector_mv.items():
            pct = mv / equity * 100.0
            # 「未分类」桶过大也告警（不当行业限额键滥用）
            if sector == UNMAPPED_SECTOR and pct > max_sector_pct:
                warnings.append(
                    f"未分类敞口 {pct:.1f}% > 行业上限 {max_sector_pct}% · 补 sector_map"
                )
            elif sector != UNMAPPED_SECTOR and pct > max_sector_pct:
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
        "recovery": recovery,
        "exposure": exposure,
        "sector_coverage": cov,
        "cost_model": resolve_cost_model(paper),
        "limits": {
            "max_drawdown_pct": max_dd,
            "target_drawdown_pct": target_dd,
            "max_position_pct": max_pos_pct,
            "max_sector_pct": max_sector_pct,
            "max_positions": max_positions,
            "min_sector_map_coverage": min_cov_warn,
            "min_sector_map_coverage_block": min_cov_block,
            "require_sector_map": require_map,
        },
    }
