"""现货广度回退：涨停池接口不可用时的 limit up/down 统计。"""


import logging
from typing import Any, Dict, List, Optional

from core.numbers import to_float as _to_float

logger = logging.getLogger(__name__)


def _limit_threshold(code: str, name: str = "") -> float:
    try:
        from core.backtest.matching import limit_up_threshold_for_code

        return float(limit_up_threshold_for_code(code, stock_name=name))
    except Exception:
        logger.exception('unexpected error in _limit_threshold')
        c = str(code or "")
        if c.startswith(("688", "300", "301")):
            return 19.5
        if "ST" in str(name or "").upper():
            return 4.5
        return 9.5


def spot_breadth_stats(rows: Optional[List[dict]] = None) -> Dict[str, Any]:
    """从 A 股现货快照统计涨跌家数 / 涨停跌停（全市场 proxy）。"""
    if rows is None:
        try:
            from core.data_service import get_spot

            pack = get_spot(disk_only=True)
            data = pack.get("data") if isinstance(pack.get("data"), dict) else pack
            rows = list((data or {}).get("rows") or pack.get("rows") or [])
        except Exception:
            logger.debug("spot breadth load failed", exc_info=True)
            rows = []

    up = down = flat = limit_up = limit_down = 0
    for row in rows or []:
        code = str(row.get("code") or row.get("代码") or row.get("stock_code") or "")
        name = str(row.get("name") or row.get("名称") or "")
        chg = _to_float(row.get("change") or row.get("涨跌幅") or row.get("change_pct"))
        if chg is None:
            continue
        if chg > 0.01:
            up += 1
        elif chg < -0.01:
            down += 1
        else:
            flat += 1
        thr = _limit_threshold(code, name)
        if chg >= thr - 0.05:
            limit_up += 1
        elif chg <= -(thr - 0.05):
            limit_down += 1

    total = up + down + flat
    return {
        "source": "spot_disk",
        "total": total,
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "limit_up_ratio": round(limit_up / total, 4) if total else None,
    }
