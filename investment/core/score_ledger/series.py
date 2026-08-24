"""打分账本：序列查询与日更任务。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from core.score_ledger import io as _lio
from core.numbers import date_key
from core.score_ledger.asof import (
    default_as_of,
)
from core.score_ledger.freeze import (
    freeze_from_cluster_book,
)
from core.score_ledger.outcomes import (
    _sign_hit,
    fill_outcomes,
)


def code_yhat_series(
    code: str,
    *,
    limit: int = 40,
) -> Dict[str, Any]:
    """单票跨决策日 ŷ 时间线（读已冻结账本）。"""
    raw = str(code or "").strip()
    if not raw:
        return {"success": False, "error": "code 无效", "points": []}
    key = raw.zfill(6) if raw.isdigit() else raw
    dates = _lio.list_ledger_dates(limit=max(5, min(int(limit or 40), 90)))
    points: List[Dict[str, Any]] = []
    name = None
    for d in reversed(dates):  # 时间正序
        led = _lio.load_ledger(d)
        if not led.get("success") or led.get("empty"):
            continue
        for r in led.get("rows") or []:
            if str(r.get("code") or "") != key:
                continue
            y = _lio._to_float(r.get("yhat"))
            if y is None:
                break
            if name is None and r.get("name"):
                name = r.get("name")
            oc = (_lio.load_outcomes(d).get("by_code") or {}).get(key) or {}
            points.append(
                {
                    "date": d,
                    "yhat": y,
                    "realized_h": _lio._to_float(oc.get("realized_h")),
                    "sign_hit": oc.get("sign_hit"),
                    "cluster_label": r.get("cluster_label"),
                }
            )
            break
    return {
        "success": True,
        "code": key,
        "name": name,
        "n": len(points),
        "points": points,
        "note": "来自 score_ledger 冻结行；无账本日不出现。",
    }


def stock_panel_series(
    code: str,
    *,
    lookback: int = 10,
    yhat_limit: int = 90,
) -> Dict[str, Any]:
    """复盘单票三面板：收盘价 / 日涨跌% / 冻结 ŷ%（按日期对齐，ŷ 稀疏不插值）。"""
    raw = str(code or "").strip()
    if not raw:
        return {"success": False, "error": "code 无效", "points": []}
    key = raw.zfill(6) if raw.isdigit() else raw
    lb = max(5, min(int(lookback or 10), 120))
    # 多取 1 根用于首日涨跌计算，展示仍截到 lookback 日
    fetch_n = min(lb + 1, 120)
    yhat_lim = max(5, min(int(yhat_limit or 90), 120))

    try:
        from core.data.facade import bars_and_source

        bars, src = bars_and_source(key, limit=fetch_n)
    except Exception as exc:
        logger.exception('unexpected error in stock_panel_series')
        return {
            "success": False,
            "error": f"日线加载失败：{exc}",
            "code": key,
            "points": [],
        }

    yhat_pack = code_yhat_series(key, limit=yhat_lim)
    yhat_by_date: Dict[str, float] = {}
    name = yhat_pack.get("name")
    for p in yhat_pack.get("points") or []:
        d = date_key(p.get("date"))
        y = _lio._to_float(p.get("yhat"))
        if d and y is not None:
            yhat_by_date[d] = y

    raw_points: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None

    for b in bars or []:
        if not isinstance(b, dict):
            continue
        d = date_key(b.get("date") or b.get("time") or b.get("datetime"))
        px = _lio._to_float(b.get("close"))
        if not d or px is None or px <= 0:
            continue
        if name is None and b.get("stock_name"):
            name = b.get("stock_name")
        chg = None
        if prev_close is not None and prev_close > 0:
            chg = round((px / prev_close - 1.0) * 100.0, 2)
        prev_close = px
        y = yhat_by_date.get(d)
        raw_points.append(
            {
                "date": d,
                "close": round(px, 4),
                "change_pct": chg,
                "yhat": round(y, 4) if y is not None else None,
            }
        )

    points = raw_points[-lb:] if len(raw_points) > lb else raw_points
    close_points: List[Dict[str, Any]] = []
    change_points: List[Dict[str, Any]] = []
    yhat_points: List[Dict[str, Any]] = []
    for row in points:
        close_points.append({"date": row["date"], "value": row["close"]})
        if row.get("change_pct") is not None:
            change_points.append({"date": row["date"], "value": row["change_pct"]})
        if row.get("yhat") is not None:
            yhat_points.append({"date": row["date"], "value": row["yhat"]})

    return {
        "success": True,
        "code": key,
        "name": name,
        "lookback": lb,
        "data_source": src,
        "points": points,
        "close_points": close_points,
        "change_points": change_points,
        "yhat_points": yhat_points,
        "n_close": len(close_points),
        "n_change": len(change_points),
        "n_yhat": len(yhat_points),
        "note": "ŷ 仅来自已冻结账本；无账本日断点，不插值。需先落书或点「冻结今日打分」。近 "
        f"{lb} 个交易日。",
    }


def hit_rate_series(
    *,
    horizon_days: int = 3,
    limit: int = 20,
    autofill: bool = False,
) -> Dict[str, Any]:
    """跨决策日方向命中率序列（复盘 sparkline）。"""
    h = max(1, min(int(horizon_days or 3), 10))
    dates = _lio.list_ledger_dates(limit=max(5, min(int(limit or 20), 60)))
    series: List[Dict[str, Any]] = []
    for d in reversed(dates):
        # 轻量：不跑全量 build_score_review 文案，只算命中
        led = _lio.load_ledger(d)
        if not led.get("success") or led.get("empty"):
            continue
        outcomes = _lio.load_outcomes(d)
        if autofill and (
            outcomes.get("empty")
            or int(outcomes.get("horizon_days") or 0) != h
            or not outcomes.get("by_code")
        ):
            try:
                fill_outcomes(d, horizon_days=h)
                outcomes = _lio.load_outcomes(d)
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
                pass
        by_code = outcomes.get("by_code") or {}
        n = 0
        hits = 0
        for r in _lio.rows_for_book_review(led.get("rows") or []):
            code = str(r.get("code") or "")
            yhat = _lio._to_float(r.get("yhat"))
            if yhat is None or abs(float(yhat)) < _lio._YHAT_EPS:
                continue
            oc = by_code.get(code) or {}
            realized = _lio._to_float(oc.get("realized_h"))
            if realized is None:
                continue
            hit = oc.get("sign_hit")
            if hit is None:
                hit = _sign_hit(yhat, realized)
            if not isinstance(hit, bool):
                continue
            n += 1
            if hit:
                hits += 1
        if n <= 0:
            continue
        series.append(
            {
                "date": d,
                "hit_rate": round(hits / n, 4),
                "hits": hits,
                "n_scored": n,
            }
        )
    return {
        "success": True,
        "horizon_days": h,
        "n": len(series),
        "points": series,
        "note": "仅含已回填 realized 的决策日；默认按簿内行；autofill=false 时不拉日线。",
    }


def run_score_ledger_daily(
    *,
    as_of: Optional[str] = None,
    horizon_days: Optional[int] = None,
    fill_lookback: int = 5,
) -> Dict[str, Any]:
    """日更钩子：按因子截止冻结账本 + 回填已到期 outcomes。

    不抛异常给调度层；失败写进返回字段。
    """
    from core.market.calendar import prev_trading_day, resolve_session_date

    sess = resolve_session_date()
    requested = date_key(as_of)
    h = horizon_days
    if h is None:
        try:
            from core.signal.config import load_signal_config

            scoring = (load_signal_config() or {}).get("scoring") or {}
            h = int(scoring.get("horizon_days") or 3)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            h = 3
    h = max(1, min(int(h or 3), 10))

    freeze_out: Dict[str, Any]
    try:
        # 默认不传 as_of，由 resolve_freeze_as_of 对齐因子截止；盘中自动跳过
        freeze_out = freeze_from_cluster_book(as_of=requested, auto=True)
        if not freeze_out.get("success") or int(freeze_out.get("n_rows") or 0) <= 0:
            pass
    except Exception as exc:
        logger.exception('unexpected error in run_score_ledger_daily')
        freeze_out = {
            "success": False,
            "error": str(exc),
            "as_of": requested or default_as_of(),
            "n_rows": 0,
        }

    freeze_day = date_key((freeze_out or {}).get("as_of")) or default_as_of()

    fills: List[Dict[str, Any]] = []
    look = max(1, min(int(fill_lookback or 5), 20))
    for i in range(1, look + 1):
        target = prev_trading_day(sess, n=h + i - 1) or None
        if not target:
            continue
        led = _lio.load_ledger(target)
        if led.get("empty") or not led.get("success"):
            continue
        try:
            fo = fill_outcomes(target, horizon_days=h)
            fills.append(
                {
                    "as_of": target,
                    "success": bool(fo.get("success")),
                    "filled": fo.get("filled"),
                    "missing": fo.get("missing"),
                    "error": fo.get("error"),
                }
            )
        except Exception as exc:
            logger.exception('unexpected error in run_score_ledger_daily')
            fills.append({"as_of": target, "success": False, "error": str(exc)})

    return {
        "success": True,
        "as_of": freeze_day,
        "session_date": sess,
        "horizon_days": h,
        "freeze": freeze_out,
        "fills": fills,
        "filled_days": sum(1 for f in fills if f.get("success")),
        "note": "日更：按因子截止冻结 ŷ 账本；回填到期决策日 realized（不改权）。",
    }

