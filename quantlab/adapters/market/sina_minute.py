"""分钟线近端备选：新浪主 · 腾讯备。

更新路径已改用 ``ak.stock_zh_a_minute``，本模块不从 ``minute_history`` 调用。
分钟只支持从现在往前 ``count`` 根。新浪走 ``quotes.sina.cn`` 的
``getKLineData``，``datalen`` 实测可到 1970（5m ≈ 41 交易日）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from core.http_retry import requests_get_with_retry
from core.numbers import to_float as _to_float
from adapters.market.minute_history import normalize_minute_bars

logger = logging.getLogger(__name__)

SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData"
)
TX_MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline"
_UA = "Mozilla/5.0 (compatible; quantlab-minute/1.0)"
SINA_MAX_COUNT = 1970
_BARS_PER_DAY = {"1": 240, "5": 48, "15": 16, "30": 8, "60": 4}


def to_sina_tx_symbol(bare: str) -> Optional[str]:
    """6 位 A 股 → sh600000 / sz000001 / bj430047。"""
    code = str(bare or "").strip()
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def _suggest_count(period: str, lookback_days: int) -> int:
    per_day = _BARS_PER_DAY.get(str(period or "5"), 48)
    days = max(1, int(lookback_days or 20))
    return max(10, min(days * per_day, SINA_MAX_COUNT))


def _normalize_ts(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().replace("/", "-")
    if not s:
        return None
    if len(s) >= 14 and s[8] == " " and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[9:17]}"
    if len(s) >= 12 and s[:8].isdigit() and ":" in s[8:]:
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:16]}"
    if len(s) == 10:
        return f"{s} 00:00:00"
    if len(s) >= 19:
        return s[:19]
    return s


def _rows_from_sina(payload: Any) -> List[dict]:
    rows: List[dict] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, dict):
            continue
        ts = _normalize_ts(item.get("day") or item.get("date"))
        close = _to_float(item.get("close"))
        if not ts or close is None:
            continue
        rows.append(
            {
                "datetime": ts,
                "date": ts[:10],
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": close,
                "volume": item.get("volume"),
            }
        )
    return rows


def _rows_from_tencent(buf: Any) -> List[dict]:
    rows: List[dict] = []
    if not isinstance(buf, list):
        return rows
    for item in buf:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        ts = _normalize_ts(item[0])
        close = _to_float(item[2])
        if not ts or close is None:
            continue
        rows.append(
            {
                "datetime": ts,
                "date": ts[:10],
                "open": item[1],
                "high": item[3],
                "low": item[4],
                "close": close,
                "volume": item[5],
            }
        )
    return rows


def _sina_payload(resp: Any) -> Any:
    """``quotes.sina.cn`` 返回 JSONP；测试桩和旧 JSON 体仍走 ``json()``。"""
    text = getattr(resp, "text", None)
    if isinstance(text, str) and "=(" in text:
        raw = text.split("=(", 1)[1].rsplit(");", 1)[0]
        return json.loads(raw)
    return resp.json()


def _fetch_sina_minute(sym: str, *, period: str, count: int) -> Tuple[List[dict], Dict[str, Any]]:
    ts = int(period)
    resp = requests_get_with_retry(
        SINA_KLINE_URL,
        params={"symbol": sym, "scale": ts, "ma": "no", "datalen": int(count)},
        headers={"User-Agent": _UA, "Referer": "https://finance.sina.com.cn/"},
        timeout=15,
        retries=1,
    )
    resp.raise_for_status()
    payload = _sina_payload(resp)
    bars = normalize_minute_bars(_rows_from_sina(payload))
    meta: Dict[str, Any] = {
        "data_source": "sina_tx:sina",
        "symbol": sym,
        "period": period,
        "count": int(count),
        "ok": bool(bars),
        "bar_count": len(bars),
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
    return bars, meta


def _fetch_tencent_minute(sym: str, *, period: str, count: int) -> Tuple[List[dict], Dict[str, Any]]:
    ts = int(period)
    param = f"{sym},m{ts},,{int(count)}"
    resp = requests_get_with_retry(
        TX_MKLINE_URL,
        params={"param": param},
        headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"},
        timeout=12,
        retries=1,
    )
    resp.raise_for_status()
    payload = resp.json()
    node = ((payload or {}).get("data") or {}).get(sym) or {}
    buf = node.get(f"m{ts}") or []
    bars = normalize_minute_bars(_rows_from_tencent(buf))
    meta: Dict[str, Any] = {
        "data_source": "sina_tx:tencent",
        "symbol": sym,
        "period": period,
        "count": int(count),
        "ok": bool(bars),
        "bar_count": len(bars),
    }
    if bars:
        meta["date_min"] = bars[0].get("date")
        meta["date_max"] = bars[-1].get("date")
    return bars, meta


def fetch_sina_tx_minute_bars(
    bare: str,
    *,
    period: str = "5",
    lookback_days: int = 20,
) -> Tuple[List[dict], Dict[str, Any]]:
    """新浪分钟主、腾讯备；失败返回空列表 + meta.error。"""
    period_s = str(period or "5").strip()
    if period_s not in {"1", "5", "15", "30", "60"}:
        return [], {"data_source": "empty", "error": f"sina_tx unsupported period={period_s}"}
    sym = to_sina_tx_symbol(bare)
    if not sym:
        return [], {"data_source": "empty", "error": f"invalid bare code: {bare}"}
    count = _suggest_count(period_s, lookback_days)
    last_err: Optional[str] = None
    if period_s != "1":
        try:
            bars, meta = _fetch_sina_minute(sym, period=period_s, count=count)
            if bars:
                return bars, meta
            last_err = "sina_empty"
        except Exception as e:  # noqa: BLE001 — 备路失败后改腾讯
            last_err = str(e)
            logger.info("sina minute failed %s period=%s: %s", bare, period_s, e)
    try:
        bars, meta = _fetch_tencent_minute(sym, period=period_s, count=count)
        if bars:
            if last_err:
                meta["sina_error"] = last_err
                meta["backfill_reason"] = "sina_fail"
            return bars, meta
        return [], {
            "data_source": "empty",
            "error": last_err or "sina_tx_empty",
            "period": period_s,
            "symbol": sym,
        }
    except Exception as e:  # noqa: BLE001
        logger.info("tencent minute failed %s period=%s: %s", bare, period_s, e)
        return [], {
            "data_source": "empty",
            "error": str(e) or last_err or "tencent_fail",
            "period": period_s,
            "symbol": sym,
            "sina_error": last_err,
        }
