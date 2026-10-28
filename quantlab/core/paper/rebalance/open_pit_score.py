"""Live 开盘窗：会话日官方立方缺失时，用昨收日线 + 行情今开建内存截面。

不写入官方 open L2。覆盖不足返回 None，由调用方拒单（禁止退昨日立方成交）。
"""

from __future__ import annotations

import logging
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_OPEN_PIT_MAX_BARS = 80
_OPEN_PIT_MIN_HIST = 61


def _fpx(*vals: Any) -> Optional[float]:
    for v in vals:
        if v is None or v == "":
            continue
        try:
            n = float(str(v).replace("元", "").replace(",", "").strip())
        except (TypeError, ValueError):
            continue
        if n == n and n > 0:
            return float(n)
    return None


def _hist_through_prev(
    bars: Sequence[dict],
    *,
    cycle_day: str,
) -> List[dict]:
    """只用 date < 会话日的完整日线（今开由行情另接）。"""
    day = str(cycle_day or "")[:10]
    out: List[dict] = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or b.get("trade_date") or "")[:10]
        if len(d) >= 10 and day and d >= day:
            continue
        out.append(b)
    return out


def _batch_quotes(codes: Sequence[str]) -> Dict[str, dict]:
    uniq = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if not uniq:
        return {}
    try:
        from core.paper.rebalance.match import _batch_query_quotes

        return dict(_batch_query_quotes(uniq) or {})
    except Exception:  # noqa: BLE001
        logger.warning("open_pit batch quotes failed", exc_info=True)
        return {}


def _load_hist_bars(
    codes: Sequence[str],
    *,
    min_bars: int = _OPEN_PIT_MIN_HIST,
    max_bars: int = _OPEN_PIT_MAX_BARS,
) -> Dict[str, List[dict]]:
    pairs = [("CN", str(c).strip()) for c in codes if str(c or "").strip()]
    if not pairs:
        return {}
    try:
        from core.store import load_daily_cache_many

        packed = load_daily_cache_many(
            pairs,
            min_bars=max(1, int(min_bars)),
            ignore_age=True,
            max_bars=int(max_bars),
        )
    except Exception:  # noqa: BLE001
        logger.warning("open_pit load_daily_cache_many failed", exc_info=True)
        return {}
    out: Dict[str, List[dict]] = {}
    for (_mkt, code), (bars, _meta) in (packed or {}).items():
        key = str(code or "").strip()
        if key and isinstance(bars, list) and bars:
            out[key] = list(bars)
    return out


def _open_and_prev(
    *,
    cycle_day: str,
    quote: Optional[dict],
    hist: Sequence[dict],
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    from core.signal.session_pit import resolve_open_t

    got = resolve_open_t(quote, hist, trade_day=cycle_day)
    ot = got.get("open")
    pc = got.get("prev_close")
    src = got.get("source")
    if ot is None:
        ot = _fpx(
            (quote or {}).get("open_raw"),
            (quote or {}).get("open"),
            (quote or {}).get("open_price"),
        )
        if ot is not None:
            src = src or "quote_open"
    if pc is None and hist:
        pc = _fpx(hist[-1].get("close"))
    return (
        float(ot) if ot is not None else None,
        float(pc) if pc is not None else None,
        str(src) if src else None,
    )


def _feature_item(
    code: str,
    *,
    cycle_day: str,
    hist: Sequence[dict],
    open_t: float,
    prev_close: Optional[float],
    quote: Optional[dict],
    open_source: Optional[str],
) -> Optional[dict]:
    from core.signal.factors.alpha_oo import inject_raw_aoo
    from core.signal.factors.alpha_tc import inject_raw_atc

    if len(hist) < _OPEN_PIT_MIN_HIST:
        return None
    feats: Dict[str, Any] = {}
    inject_raw_aoo(
        feats,
        hist,
        open_t=open_t,
        prev_close=prev_close,
        quote=quote,
    )
    inject_raw_atc(
        feats,
        hist,
        open_t=open_t,
        prev_close=prev_close,
        price_tau=open_t,
        minute_bars=None,
        tau_hm="open",
        quote=quote,
    )
    oo = {
        k: float(v)
        for k, v in feats.items()
        if str(k).startswith(("raw_aoo_", "z_oo_"))
        and isinstance(v, (int, float))
        and not isinstance(v, bool)
        and float(v) == float(v)
    }
    tau = {
        k: float(v)
        for k, v in feats.items()
        if str(k).startswith(("raw_atc_", "z_τc_", "z_tc_", "z_τh_"))
        and isinstance(v, (int, float))
        and not isinstance(v, bool)
        and float(v) == float(v)
    }
    if not oo and not tau:
        return None
    co = {k: v for k, v in oo.items() if str(k).startswith("raw_aoo_")}
    eod_as_of = ""
    if hist:
        eod_as_of = str(hist[-1].get("date") or "")[:10]
    item: Dict[str, Any] = {
        "stock_code": code,
        "as_of": cycle_day,
        "date": cycle_day,
        "trade_date": cycle_day,
        "trade_day": cycle_day,
        "success": True,
        "_score_source": "open_pit",
        "lake_score_day": cycle_day,
        "day_open": float(open_t),
        "open_t": float(open_t),
    }
    if open_source:
        item["open_t_source"] = open_source
    if len(eod_as_of) >= 10:
        item["eod_feature_as_of"] = eod_as_of
    if oo:
        item["sub_scores"] = oo
    if tau:
        item["features_tau"] = tau
    if co:
        item["features_co"] = co
        item["features_on"] = dict(co)
    return item


def build_open_pit_feature_items(
    codes: Sequence[str],
    *,
    cycle_day: str,
    universe_codes: Optional[Sequence[str]] = None,
    quotes: Optional[Dict[str, dict]] = None,
) -> Tuple[List[dict], List[dict], Dict[str, Any]]:
    """全池抽 raw 特征；覆盖不足时 items 可能仍返回，由调用方按 meta 拒单。"""
    from core.research.materialize.cover_policy import LAKE_COVER_FRAC, cover_need

    day = str(cycle_day or "")[:10]
    pool = [str(c or "").strip() for c in (universe_codes or codes) if str(c or "").strip()]
    focus = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if len(day) < 10 or len(pool) < 2:
        return [], [], {"ok": False, "reason": "bad_day_or_pool", "cycle_day": day}

    qmap = dict(quotes) if isinstance(quotes, dict) else _batch_quotes(pool)
    bars_by = _load_hist_bars(pool)
    items: List[dict] = []
    rejected: List[dict] = []
    n_open = 0
    for code in pool:
        hist = _hist_through_prev(bars_by.get(code) or [], cycle_day=day)
        quote = qmap.get(code) if isinstance(qmap.get(code), dict) else None
        ot, pc, src = _open_and_prev(cycle_day=day, quote=quote, hist=hist)
        if ot is None:
            rejected.append({"stock_code": code, "reason": f"缺今开 {day}"})
            continue
        n_open += 1
        item = _feature_item(
            code,
            cycle_day=day,
            hist=hist,
            open_t=ot,
            prev_close=pc,
            quote=quote,
            open_source=src,
        )
        if item is None:
            rejected.append({"stock_code": code, "reason": f"open-pit 特征不足 {day}"})
            continue
        items.append(item)

    need = cover_need(len(pool), frac=LAKE_COVER_FRAC, minimum=2)
    ok = n_open >= need and len(items) >= 2
    meta: Dict[str, Any] = {
        "ok": ok,
        "cycle_day": day,
        "n_universe": len(pool),
        "n_open": n_open,
        "n_items": len(items),
        "need_open": need,
        "cover_frac": LAKE_COVER_FRAC,
        "quotes": qmap,
    }
    if not ok:
        meta["reason"] = "open_cover_short"
        logger.info(
            "open_pit cover short day=%s open=%s/%s need=%s items=%s",
            day,
            n_open,
            len(pool),
            need,
            len(items),
        )
        return [], rejected, meta

    want = set(focus) if focus else set(pool)
    scored = [it for it in items if it.get("stock_code") in want] if want else list(items)
    # 截面 μ/σ 必须用全池 items；调用方 rescore 前要拿齐 pool 行
    meta["pool_items"] = items
    have = {str(it.get("stock_code") or "") for it in scored}
    for code in focus:
        if code not in have and code not in {r.get("stock_code") for r in rejected}:
            rejected.append({"stock_code": code, "reason": f"open-pit 缺 {day}"})
    return scored, rejected, meta


def score_open_pit_pool(
    codes: Sequence[str],
    *,
    cycle_day: str,
    cube_codes: Optional[Sequence[str]] = None,
    score_codes: Optional[Collection[str]] = None,
    paper: Optional[dict] = None,
    quotes: Optional[Dict[str, dict]] = None,
    stamp_heads,
) -> Optional[Tuple[List[dict], List[dict], Any]]:
    """建内存 DayCube（不落盘）并打 ŷ；覆盖不足返回 None。

    返回 ``(scored, rejected, cube)``，cube 须显式交给 rank 头，勿只靠 remember。
    """
    from core.research.batch_score import features_by_code_from_items
    from core.research.day_cube import build_day_cube, remember

    uni = [str(c or "").strip() for c in (cube_codes or []) if str(c or "").strip()]
    _scored_focus, rejected, meta = build_open_pit_feature_items(
        codes,
        cycle_day=cycle_day,
        universe_codes=uni or codes,
        quotes=quotes,
    )
    if not meta.get("ok"):
        return None
    pool_items = list(meta.get("pool_items") or [])
    if len(pool_items) < 2:
        return None

    by_code = features_by_code_from_items(pool_items)
    cube = build_day_cube(
        cycle_day,
        by_code,
        clock="open",
        universe_codes=uni or list(by_code),
    )
    if cube is None:
        logger.info("open_pit build_day_cube failed day=%s", cycle_day)
        return None
    # 进程内可读；rank / 二次 resolve 仍应优先用返回的 cube 引用
    remember(cube)

    work = list(pool_items)
    stamp_heads(
        cube,
        work,
        score_codes=score_codes,
        explain_codes=codes,
        paper=paper,
    )

    want = {str(c or "").strip() for c in codes if str(c or "").strip()}
    scored = (
        [it for it in work if str(it.get("stock_code") or "") in want] if want else work
    )
    for it in scored:
        it["_score_source"] = "open_pit"
        it["lake_score_day"] = str(cycle_day)[:10]
        it.pop("lake_score_fallback_from", None)
    logger.info(
        "open_pit score day=%s n_pool=%s n_scored=%s rejected=%s",
        cycle_day,
        len(pool_items),
        len(scored),
        len(rejected),
    )
    return scored, rejected, cube
