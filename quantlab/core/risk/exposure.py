"""持仓行业 / 风格暴露矩阵（R3.1）。"""

from typing import Any, Dict, List, Optional, Tuple


def board_style_for(code: str) -> str:
    """简易风格桶：按板块（非主题行业）。"""
    c = str(code or "").strip()
    if c.isdigit() and len(c) == 6:
        if c.startswith(("688", "689")):
            return "科创"
        if c.startswith("300"):
            return "创业板"
        if c.startswith(("60", "90")):
            return "主板沪"
        if c.startswith(("00", "001", "002", "003")):
            return "主板深"
    if len(c) <= 5 and c.isdigit():
        return "港股"
    return "其他"


def position_size_bucket(weight_pct: float) -> str:
    """按组合权重分仓位档（简易风格补充）。"""
    w = float(weight_pct or 0)
    if w >= 15:
        return "重仓≥15%"
    if w >= 8:
        return "中仓8–15%"
    if w > 0:
        return "轻仓<8%"
    return "空"


def _holding_mv(h: dict) -> float:
    """盯市市值优先；缺省用成本市值（手动仓常无 market_value）。"""
    try:
        mv = float(h.get("market_value") or 0)
    except (TypeError, ValueError):
        mv = 0.0
    if mv > 0:
        return mv
    try:
        return float(h.get("shares") or 0) * float(h.get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def _aggregate(
    holdings: List[dict],
    equity: float,
    *,
    key_fn,
    max_pct: Optional[float] = None,
) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for h in holdings or []:
        mv = _holding_mv(h)
        if mv <= 0:
            continue
        code = str(h.get("stock_code") or "?")
        key = str(key_fn(h, code) or "其他")
        b = buckets.get(key)
        if not b:
            b = {"key": key, "market_value": 0.0, "count": 0, "codes": []}
            buckets[key] = b
        b["market_value"] += mv
        b["count"] += 1
        if code not in b["codes"] and len(b["codes"]) < 8:
            b["codes"].append(code)

    rows: List[Dict[str, Any]] = []
    eq = float(equity or 0)
    for key, b in buckets.items():
        pct = round(b["market_value"] / eq * 100.0, 2) if eq > 0 else 0.0
        over = bool(max_pct is not None and pct > float(max_pct))
        rows.append(
            {
                "name": key,
                "weight_pct": pct,
                "market_value": round(b["market_value"], 2),
                "count": b["count"],
                "codes": b["codes"],
                "over_limit": over,
            }
        )
    rows.sort(key=lambda r: (-float(r["weight_pct"]), str(r["name"])))
    return rows


def build_exposure_matrix(
    paper: Optional[dict] = None,
    summary: Optional[dict] = None,
    *,
    risk: Optional[dict] = None,
    sector_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    持仓 × 行业主题 + 板块风格 + 仓位档。
    over_limit 与 check_account_risk 同源（max_sector_pct / max_position_pct）。
    """
    from core.portfolio_optimize import _sector_for, load_sector_map
    from core.strategy import get_strategy_spec

    paper = paper or {}
    sm = summary or {}
    holdings = list(sm.get("holdings") or paper.get("holdings") or [])
    equity = float(sm.get("equity") or 0)
    if equity <= 0:
        equity = float(paper.get("cash") or 0) + sum(_holding_mv(h) for h in holdings)

    spec_risk = risk
    if spec_risk is None:
        sid = paper.get("strategy_id") or "short_conservative"
        try:
            spec_risk = get_strategy_spec(str(sid)).get("risk") or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            spec_risk = {}
    max_sector = float((spec_risk or {}).get("max_sector_pct") or 40.0)
    max_pos = float((spec_risk or {}).get("max_position_pct") or 25.0)

    smap = sector_map if sector_map is not None else load_sector_map()

    def sector_key(h: dict, code: str) -> str:
        return str(h.get("sector") or _sector_for(code, smap))

    def style_key(_h: dict, code: str) -> str:
        return board_style_for(code)

    sectors = _aggregate(holdings, equity, key_fn=sector_key, max_pct=max_sector)

    # 单票行
    names: List[Dict[str, Any]] = []
    for h in holdings:
        mv = _holding_mv(h)
        if mv <= 0:
            continue
        code = str(h.get("stock_code") or "?")
        pct = round(mv / equity * 100.0, 2) if equity > 0 else 0.0
        names.append(
            {
                "stock_code": code,
                "stock_name": h.get("stock_name"),
                "sector": sector_key(h, code),
                "style": board_style_for(code),
                "size_bucket": position_size_bucket(pct),
                "weight_pct": pct,
                "market_value": round(mv, 2),
                "over_limit": pct > max_pos,
            }
        )
    names.sort(key=lambda r: -float(r["weight_pct"]))

    size_rows: List[Dict[str, Any]] = []
    size_mv: Dict[str, float] = {}
    size_n: Dict[str, int] = {}
    for n in names:
        k = n["size_bucket"]
        size_mv[k] = float(size_mv.get(k) or 0) + float(n["market_value"])
        size_n[k] = int(size_n.get(k) or 0) + 1
    for k, mv in size_mv.items():
        pct = round(mv / equity * 100.0, 2) if equity > 0 else 0.0
        size_rows.append(
            {
                "name": k,
                "weight_pct": pct,
                "market_value": round(mv, 2),
                "count": size_n.get(k, 0),
                "over_limit": False,
            }
        )
    size_rows.sort(key=lambda r: -float(r["weight_pct"]))

    styles = _aggregate(holdings, equity, key_fn=style_key, max_pct=None)

    over_sectors = [r["name"] for r in sectors if r.get("over_limit")]
    over_names = [r["stock_code"] for r in names if r.get("over_limit")]

    return {
        "ok": True,
        "equity": round(equity, 2),
        "limits": {
            "max_sector_pct": max_sector,
            "max_position_pct": max_pos,
        },
        "sectors": sectors,
        "styles": styles,
        "size_buckets": size_rows,
        "names": names[:40],
        "over_limit_sectors": over_sectors,
        "over_limit_names": over_names,
        "sector_count": len(sectors),
        "note": "行业主题仅 sector_map（未映射=未分类）；风格=板别启发式。超限与 check_account_risk 同源。",
    }


def classify_block_message(message: str) -> Tuple[str, Dict[str, Any]]:
    """从历史自由文本块解析原因码（兼容旧流水）。"""
    msg = str(message or "")
    extra: Dict[str, Any] = {}
    if "行业" in msg and "敞口" in msg:
        return "max_sector", extra
    if "仓位" in msg and "单票" in msg:
        return "max_position", extra
    if "回撤" in msg and ("限额" in msg or "暂停加仓" in msg):
        return "drawdown_limit", extra
    if "回撤" in msg and "目标" in msg:
        return "drawdown_target", extra
    if "持仓只数" in msg:
        return "max_positions", extra
    return "unspecified", extra
