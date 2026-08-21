"""A 股市场情绪统计：涨停池 / 连板溢价 / 炸板率代理。"""


import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.numbers import to_float as _to_float

logger = logging.getLogger(__name__)


def _safe_df_records(fn, **kwargs) -> List[dict]:
    try:
        df = fn(**kwargs)
        if df is None or getattr(df, "empty", True):
            return []
        return df.to_dict(orient="records") if hasattr(df, "to_dict") else list(df)
    except Exception:
        logger.debug("akshare call failed: %s", fn, exc_info=True)
        return []


def _pick(row: dict, *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _fetch_zt_pool(date_s: str) -> List[dict]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_zt_pool_em", None)
    if not fn:
        return []
    return _safe_df_records(fn, date=date_s)


def _fetch_zt_previous(date_s: str) -> List[dict]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_zt_pool_previous_em", None)
    if not fn:
        return []
    return _safe_df_records(fn, date=date_s)


def _fetch_zt_broken(date_s: str) -> List[dict]:
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_zt_pool_zbgc_em", None)
    if not fn:
        return []
    return _safe_df_records(fn, date=date_s)


def _mean_open_premium(rows: List[dict]) -> Optional[float]:
    vals: List[float] = []
    for row in rows or []:
        v = _to_float(
            _pick(row, "涨跌幅", "change_pct", "今日涨跌幅", "open_change", "竞价涨幅")
        )
        if v is not None:
            vals.append(float(v))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 4)


def build_market_sentiment_snapshot(*, trade_date: Optional[str] = None) -> Dict[str, Any]:
    """构建市场情绪快照。"""
    if trade_date:
        date_s = str(trade_date).replace("-", "")[:8]
    else:
        date_s = datetime.now().strftime("%Y%m%d")

    zt_rows = _fetch_zt_pool(date_s)
    prev_rows = _fetch_zt_previous(date_s)
    broken_rows = _fetch_zt_broken(date_s)

    spot_fallback = None
    if not zt_rows and not prev_rows:
        try:
            from skills.market_sentiment.spot_breadth import spot_breadth_stats

            spot_fallback = spot_breadth_stats()
        except Exception:
            logger.debug("spot breadth fallback skipped", exc_info=True)

    limit_up_count = len(zt_rows) or int((spot_fallback or {}).get("limit_up_count") or 0)
    prev_limit_count = len(prev_rows)
    broken_count = len(broken_rows)

    open_premium = _mean_open_premium(prev_rows)
    touch_total = limit_up_count + broken_count
    broken_limit_rate = (
        round(broken_count / touch_total, 4) if touch_total > 0 else None
    )

    # 连板高度代理：涨停池内连板数均值
    board_heights: List[float] = []
    for row in zt_rows:
        h = _to_float(_pick(row, "连板数", "连续涨停天数", "lb"))
        if h is not None:
            board_heights.append(float(h))
    avg_board_height = (
        round(sum(board_heights) / len(board_heights), 2) if board_heights else None
    )

    # 退潮：连板溢价连续偏低 + 炸板率高
    sentiment_score = 50.0
    if open_premium is not None:
        if open_premium >= 3.0:
            sentiment_score += 15.0
        elif open_premium >= 1.0:
            sentiment_score += 5.0
        elif open_premium <= -2.0:
            sentiment_score -= 20.0
        elif open_premium <= 0.0:
            sentiment_score -= 10.0
    if broken_limit_rate is not None:
        if broken_limit_rate >= 0.35:
            sentiment_score -= 15.0
        elif broken_limit_rate >= 0.2:
            sentiment_score -= 8.0
    sentiment_score = max(0.0, min(100.0, sentiment_score))

    as_of = f"{date_s[:4]}-{date_s[4:6]}-{date_s[6:8]}"
    out = {
        "success": True,
        "as_of": as_of,
        "trade_date": date_s,
        "limit_up_count": limit_up_count,
        "prev_limit_count": prev_limit_count,
        "broken_limit_count": broken_count,
        "broken_limit_rate": broken_limit_rate,
        "limit_up_open_premium_pct": open_premium,
        "avg_board_height": avg_board_height,
        "sentiment_cycle_score": round(sentiment_score, 1),
        "spot_fallback": spot_fallback,
        "data_source": "zt_pool" if zt_rows or prev_rows else "spot_fallback",
        "note": "涨停池统计；非 PIT，供 market_sentiment_prior。",
    }
    if spot_fallback:
        out["market_breadth"] = {
            "up_count": spot_fallback.get("up_count"),
            "down_count": spot_fallback.get("down_count"),
            "limit_up_ratio": spot_fallback.get("limit_up_ratio"),
        }
    return out
