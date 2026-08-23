"""打分账本：realized 回填与 ŷ_τ 水合。"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.score_ledger import io as _lio
from core.io_atomic import atomic_write_json
from core.numbers import date_key


def _realized_from_bars(
    bars: Sequence[dict],
    as_of: str,
    horizon_days: int,
) -> Optional[float]:
    """(close[as_of+h] / close[as_of] - 1) * 100。"""
    from core.market.calendar import next_trading_day

    d0 = date_key(as_of)
    if not d0 or not bars:
        return None
    by_date = {}
    for b in bars:
        if not isinstance(b, dict):
            continue
        k = date_key(b.get("date") or b.get("time") or b.get("datetime"))
        if k:
            by_date[k] = b
    if d0 not in by_date:
        return None
    d1 = next_trading_day(d0, n=max(1, int(horizon_days or 1)))
    if not d1 or d1 not in by_date:
        return None
    c0 = _lio._to_float(by_date[d0].get("close"))
    c1 = _lio._to_float(by_date[d1].get("close"))
    if c0 is None or c1 is None or c0 <= 0:
        return None
    return (c1 / c0 - 1.0) * 100.0


def _realized_tau_from_bars(
    bars: Sequence[dict],
    as_of: str,
    horizon_days: int = 1,
) -> Optional[float]:
    """y_τ：(close[T]/open[T]-1)*100，T=as_of+h（与 open-τ 契约对齐）。"""
    from core.market.calendar import next_trading_day

    d0 = date_key(as_of)
    if not d0 or not bars:
        return None
    by_date = {}
    for b in bars:
        if not isinstance(b, dict):
            continue
        k = date_key(b.get("date") or b.get("time") or b.get("datetime"))
        if k:
            by_date[k] = b
    d1 = next_trading_day(d0, n=max(1, int(horizon_days or 1)))
    if not d1 or d1 not in by_date:
        return None
    o = _lio._to_float(by_date[d1].get("open"))
    c = _lio._to_float(by_date[d1].get("close"))
    if o is None or c is None or o <= 0:
        return None
    return (c / o - 1.0) * 100.0


def _realized_remaining_for_nowcast(
    bars: Sequence[dict],
    as_of: str,
    *,
    horizon_days: int = 1,
    nowcast_as_of: Optional[str] = None,
    code: Optional[str] = None,
) -> Optional[float]:
    """对账 ŷ_nowcast：open/eod 用 OC；分钟 as_of 用 close/price[τ]−1（有分钟缓存时）。"""
    try:
        from core.signal.nowcast_kf import normalize_tau_label

        clock = normalize_tau_label(nowcast_as_of or "open")
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        clock = "open"
    oc = _realized_tau_from_bars(bars, as_of, horizon_days)
    if clock in ("", "eod", "open"):
        return oc
    if oc is None:
        return None
    # 分钟：尝试本地缓存价；失败则退回 OC（并在 outcomes 里可辨）
    if not code:
        return oc
    try:
        from core.market.calendar import next_trading_day
        from core.ports.market import resolve_market_code
        from core.research.rem_panel import price_at_tau_from_minutes
        from core.store import load_minute_cache

        d0 = date_key(as_of)
        d1 = next_trading_day(d0, n=max(1, int(horizon_days or 1)))
        if not d1:
            return oc
        by_date = {}
        for b in bars or []:
            if not isinstance(b, dict):
                continue
            k = date_key(b.get("date") or b.get("time") or b.get("datetime"))
            if k:
                by_date[k] = b
        bar = by_date.get(d1)
        if not bar:
            return oc
        open_px = _lio._to_float(bar.get("open"))
        close_px = _lio._to_float(bar.get("close"))
        if open_px is None or close_px is None or open_px <= 0 or close_px <= 0:
            return oc
        mkt, pure = resolve_market_code(str(code))
        packed = load_minute_cache(
            mkt or "CN",
            pure or str(code),
            period="5",
            min_bars=1,
            max_age_hours=36.0 * 30,
        )
        if not packed:
            return oc
        minute_bars, _meta = packed
        px_tau = price_at_tau_from_minutes(
            minute_bars, trade_date=d1, tau_hm=clock
        )
        if px_tau is None or px_tau <= 0:
            return oc
        # close/price[τ]-1
        return (float(close_px) / float(px_tau) - 1.0) * 100.0
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        return oc


def hydrate_ledger_yhat_tau(
    as_of: str,
    *,
    persist: bool = True,
    max_names: int = 80,
) -> Dict[str, Any]:
    """旧账本缺 ``yhat_tau`` 时，按决策日日线 PIT 重挂 rem ŷ_τ。

    用 as_of 及以前 K 线算因子 + 开盘缺口，再 ``predict_rem``；不拉实时行情。
    """
    d = date_key(as_of)
    if not d:
        return {"success": False, "error": "bad as_of", "hydrated": 0}
    ledger = _lio.load_ledger(d)
    if ledger.get("empty") or not ledger.get("success"):
        return {
            "success": bool(ledger.get("success")),
            "empty": True,
            "as_of": d,
            "hydrated": 0,
            "error": ledger.get("error"),
        }
    rows = list(ledger.get("rows") or [])
    missing = [
        r
        for r in rows
        if isinstance(r, dict) and r.get("code") and _lio._to_float(r.get("yhat_tau")) is None
    ]
    if not missing:
        return {
            "success": True,
            "as_of": d,
            "hydrated": 0,
            "missing": 0,
            "note": "账本已有 yhat_tau",
        }
    rem_doc = None
    try:
        from core.research.rem_ridge import load_rem_model

        rem_doc = load_rem_model()
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        rem_doc = None
    if not rem_doc:
        return {
            "success": True,
            "as_of": d,
            "hydrated": 0,
            "missing": len(missing),
            "note": "无 rem 模型，无法补 ŷ_τ",
        }

    from core.backtest.engine import _mock_quote_from_bars
    from core.data.facade import bars_and_source
    from core.signal.cross_section_batch import score_window_as_item
    from core.signal.dual_score import attach_dual_score_pit

    hydrated = 0
    errors = 0
    for r in missing[: max(1, int(max_names or 80))]:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        try:
            bars, _ = bars_and_source(code, limit=90, offline_ok=True)
            window = [
                b
                for b in (bars or [])
                if isinstance(b, dict)
                and date_key(b.get("date") or b.get("time") or "") <= d
            ]
            if len(window) < 16:
                continue
            quote = _mock_quote_from_bars(window, len(window) - 1)
            item = score_window_as_item(
                code,
                window,
                horizon_days=1,
                quote=quote,
            )
            if not item:
                continue
            # 保持账本冻结的 EOD ŷ，只补 τ
            if r.get("yhat") is not None:
                item["predicted_score"] = r.get("yhat")
                item["score"] = r.get("yhat")
            attach_dual_score_pit(
                item,
                quote=quote,
                bars=window[-8:],
                rem_model_doc=rem_doc,
            )
            yt = _lio._to_float(item.get("predicted_score_tau"))
            if yt is None:
                continue
            r["yhat_tau"] = round(float(yt), 6)
            if r.get("yhat_eod") is None:
                ye = _lio._to_float(r.get("yhat"))
                # 仅收益分口径回填；规则分 0–100 不写入 yhat_eod
                if ye is not None and abs(ye) <= 20.0:
                    r["yhat_eod"] = round(float(ye), 6)
            hydrated += 1
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
            errors += 1
            continue

    if persist and hydrated:
        path = _lio.ledger_path(d)
        payload = {
            "success": True,
            "as_of": d,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "n_rows": len(rows),
            "rows": rows,
            "meta": {
                **(ledger.get("meta") or {}),
                "yhat_tau_hydrated": True,
                "yhat_tau_hydrated_n": hydrated,
            },
        }
        atomic_write_json(path, payload)

    return {
        "success": True,
        "as_of": d,
        "hydrated": hydrated,
        "missing": len(missing),
        "errors": errors,
        "persisted": bool(persist and hydrated),
    }


def _sign_hit(yhat: Optional[float], realized: Optional[float]) -> Optional[bool]:
    if yhat is None or realized is None:
        return None
    if abs(float(yhat)) < _lio._YHAT_EPS:
        return None  # 无方向
    if abs(float(realized)) < 1e-12:
        return None
    return (float(yhat) > 0) == (float(realized) > 0)


def _spearman_ic(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    # 平均秩处理 ties：简化为稳定排序秩
    def _ranks(vals: Sequence[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: float(vals[i]))
        ranks = [0.0] * n
        for r, i in enumerate(order):
            ranks[i] = float(r + 1)
        return ranks

    rx, ry = _ranks(xs), _ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = sum((a - mx) ** 2 for a in rx) ** 0.5
    deny = sum((b - my) ** 2 for b in ry) ** 0.5
    if denx < 1e-12 or deny < 1e-12:
        return None
    return round(num / (denx * deny), 4)


def fill_outcomes(
    as_of: str,
    *,
    horizon_days: int = 3,
) -> Dict[str, Any]:
    """用本地日线回填 realized / sign_hit（含 y_τ = open→close）。"""
    from core.data.facade import bars_and_source

    # 旧账本缺 ŷ_τ 时先补，便于 outcomes 写 yhat_tau / sign_hit_tau
    try:
        hydrate_ledger_yhat_tau(as_of, persist=True)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in score_ledger.py", exc_info=True)
        pass
    ledger = _lio.load_ledger(as_of)
    if not ledger.get("success"):
        return ledger
    rows = list(ledger.get("rows") or [])
    if not rows:
        return {
            "success": False,
            "error": "无账本行，请先冻结打分",
            "as_of": date_key(as_of),
            "filled": 0,
        }
    h = max(1, min(int(horizon_days or 3), 10))
    by_code: Dict[str, Dict[str, Any]] = {}
    filled = 0
    missing = 0
    filled_tau = 0
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        bars, _ = bars_and_source(code, limit=max(40, h + 25), offline_ok=True)
        realized = _realized_from_bars(bars or [], r.get("as_of") or as_of, h)
        realized_tau = _realized_tau_from_bars(
            bars or [], r.get("as_of") or as_of, h
        )
        yhat = _lio._to_float(r.get("yhat"))
        yhat_tau = _lio._to_float(r.get("yhat_tau"))
        yhat_nowcast = _lio._to_float(r.get("yhat_nowcast"))
        nowcast_as_of = r.get("nowcast_as_of")
        realized_remaining = _realized_remaining_for_nowcast(
            bars or [],
            r.get("as_of") or as_of,
            horizon_days=h,
            nowcast_as_of=nowcast_as_of,
            code=code,
        )
        hit = _sign_hit(yhat, realized)
        hit_tau = _sign_hit(yhat_tau, realized_tau)
        hit_nowcast = _sign_hit(yhat_nowcast, realized)
        abs_err = None
        if yhat is not None and realized is not None:
            abs_err = round(abs(float(yhat) - float(realized)), 4)
        abs_err_tau = None
        if yhat_tau is not None and realized_tau is not None:
            abs_err_tau = round(abs(float(yhat_tau) - float(realized_tau)), 4)
        abs_err_nowcast = None
        if yhat_nowcast is not None and realized is not None:
            abs_err_nowcast = round(
                abs(float(yhat_nowcast) - float(realized)), 4
            )
        dominant = None
        terms = r.get("formula_terms_top") or []
        if terms:
            dominant = terms[0].get("key")
        by_code[code] = {
            "code": code,
            "realized_h": round(realized, 4) if realized is not None else None,
            "sign_hit": hit,
            "abs_err": abs_err,
            "realized_tau": (
                round(realized_tau, 4) if realized_tau is not None else None
            ),
            "sign_hit_tau": hit_tau,
            "abs_err_tau": abs_err_tau,
            "yhat_tau": yhat_tau,
            "yhat_nowcast": yhat_nowcast,
            "nowcast_as_of": nowcast_as_of,
            "realized_remaining": (
                round(realized_remaining, 4)
                if realized_remaining is not None
                else None
            ),
            "sign_hit_nowcast": hit_nowcast,
            "abs_err_nowcast": abs_err_nowcast,
            "dominant_factor": dominant,
            "no_direction": bool(yhat is not None and abs(float(yhat)) < _lio._YHAT_EPS),
            "no_direction_tau": bool(
                yhat_tau is not None and abs(float(yhat_tau)) < _lio._YHAT_EPS
            ),
        }
        if realized is not None:
            filled += 1
        else:
            missing += 1
        if realized_tau is not None:
            filled_tau += 1
    path = _lio.outcomes_path(as_of)
    os.makedirs(_lio.ledger_dir(), exist_ok=True)
    payload = {
        "success": True,
        "as_of": date_key(as_of),
        "horizon_days": h,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "filled": filled,
        "missing": missing,
        "filled_tau": filled_tau,
        "by_code": by_code,
        "y_spec_tau": {
            "formula": "close[T]/open[T]-1",
            "T": "as_of+horizon",
            "unit": "pct",
        },
    }
    atomic_write_json(path, payload)
    return {
        "success": True,
        "as_of": date_key(as_of),
        "horizon_days": h,
        "filled": filled,
        "missing": missing,
        "filled_tau": filled_tau,
        "path": path,
    }

