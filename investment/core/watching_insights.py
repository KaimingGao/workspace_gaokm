"""观察名单研究摘要：评分/倾向/超额/量比/估值/同业/硬拒绝（不改账本）。

列表页必须轻量：跳过 fundamentals / peer / 独立指数重拉；
超额优先用 score 内已有 factors；超时不阻塞关池。
"""

from __future__ import annotations
from core.numbers import to_float as _f

from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
from datetime import datetime
from typing import Any, Dict, List, Optional

STANCE_SHORT = {
    "buy_light": "轻仓",
    "probe": "关注",
    "wait": "观望",
    "avoid": "观望",
    "insufficient": "—",
}

# 列表页：单票与整批上限（秒）；超时后不等待残余线程
_INSIGHT_STOCK_TIMEOUT = 12.0
_INSIGHT_BATCH_TIMEOUT = 90.0
_INSIGHT_MAX_WORKERS = 12
# 与观察池上限对齐（watching 常见 100）；勿砍到更小导致尾部无分
_INSIGHT_DEFAULT_LIMIT = 100
_INSIGHT_HARD_CAP = 120


def _days_since(iso: Optional[str]) -> Optional[int]:
    raw = str(iso or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
        else:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
        return max(0, (datetime.now() - dt).days)
    except Exception:
        return None


def _blank(code: str, *, added_at: Optional[str] = None, error: Optional[str] = None) -> Dict[str, Any]:
    return {
        "stock_code": code,
        "ok": False,
        "score": None,
        "stance_code": None,
        "stance_short": "—",
        "stance_label": None,
        "hard_reject": False,
        "reject_reason": None,
        "excess_return_pct": None,
        "excess_label": None,
        "volume": None,
        "volume_ratio": None,
        "pe": None,
        "pb": None,
        # 与交易执行持仓分同源：score_stock 读 cluster_scoring
        "cluster_mode": None,
        "weight_source": None,
        "cluster_label": None,
        "cluster_version": None,
        "score_global": None,
        "score_cluster": None,
        "delta_vs_global": None,
        "min_score": None,
        "below_min_score": False,
        "score_formula": None,
        "score_reasons": None,
        "return_model_source": None,
        "factor_coefficients": None,
        "score_formula_terms": None,
        "added_at": added_at,
        "days_watched": _days_since(added_at),
        "peer_rank": None,
        "peer_count": None,
        "peer_line": None,
        "error": error,
    }


def _fill_valuation_from_em(
    want: set, out: Dict[str, Dict[str, Optional[float]]]
) -> None:
    """现货缺失时：按票读缓存 / stock_value_em 补 PE·PB（限流，避免拖死列表）。"""
    missing = [
        c
        for c in sorted(want)
        if c not in out
        or (out[c].get("pe") is None and out[c].get("pb") is None)
    ]
    if not missing:
        return

    def _one(code: str) -> tuple:
        try:
            from core.valuation_em import fetch_valuation_pack

            pack = fetch_valuation_pack(code) or {}
        except Exception:
            return code, None, None
        return code, _f(pack.get("pe")), _f(pack.get("pb"))

    workers = min(6, len(missing))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, c) for c in missing]
        for fut in as_completed(futs):
            try:
                code, pe, pb = fut.result()
            except Exception:
                continue
            if pe is None and pb is None:
                continue
            out[code] = {"pe": pe, "pb": pb}


def _spot_valuation_map(codes: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """A 股 PE/PB：本地现货优先；缺票再用 valuation_em 缓存 / 单票序列（不阻塞拉全表）。"""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    want = {str(c).zfill(6) for c in codes if str(c).isdigit() and len(str(c).strip()) <= 6}
    if not want:
        return out
    try:
        from core.ports.market import load_disk_spot, spot_row_get, spot_to_float

        # 仅用已落盘现货；东财全表常挂，列表路径不在此阻塞重拉
        rows = load_disk_spot(max_age_hours=24 * 14) or []
        for row in rows:
            c = str(spot_row_get(row, "code") or "").zfill(6)
            if c not in want:
                continue
            out[c] = {
                "pe": spot_to_float(spot_row_get(row, "pe")),
                "pb": spot_to_float(spot_row_get(row, "pb")),
            }
    except Exception:
        pass

    try:
        _fill_valuation_from_em(want, out)
    except Exception:
        pass
    return out


def _apply_excess_label(out: Dict[str, Any], excess: Optional[float], item: dict) -> None:
    if excess is not None:
        out["excess_return_pct"] = round(float(excess), 2)
        if excess >= 2:
            out["excess_label"] = "强"
        elif excess <= -2:
            out["excess_label"] = "弱"
        else:
            out["excess_label"] = "平"
        return
    if out.get("excess_label"):
        return
    rs = _f((item.get("sub_scores") or {}).get("relative_strength"))
    if rs is not None:
        out["excess_label"] = "RS" + str(int(round(rs)))


def _hydrate_insight_tau_fields(
    out: Dict[str, Any],
    item: dict,
    quote: Optional[dict] = None,
    bars: Optional[list] = None,
) -> None:
    """簿行常只有 ŷ_EOD：用行情缺口现场写出 ŷ_EOD_rem / ŷ_trade。

    有新缺口则重算（开盘后已实现会变）；无缺口且簿上已有 rem 则保留。
    无已实现时 ŷ_EOD_rem = ŷ_EOD（视作尚未开盘）。
    ŷ_τ 仍来自 rem 头，缺则保持空，ŷ_trade 退回 ŷ_EOD_rem。
    """
    y_eod = out.get("predicted_score")
    if y_eod is None:
        y_eod = out.get("score")
    if y_eod is None and isinstance(item, dict):
        y_eod = item.get("predicted_score")
        if y_eod is None:
            y_eod = item.get("score")
    if y_eod is None:
        return
    q = quote if isinstance(quote, dict) else {}
    b = list(bars or [])
    gap = None
    try:
        from core.event_prior import gap_pct_from_quote_bars

        gap = gap_pct_from_quote_bars(q, b)
    except Exception:
        gap = None
    if gap is None:
        gap = _f(out.get("gap_pct"))
        if gap is None:
            gap = _f((item or {}).get("gap_pct"))
    already_rem = out.get("predicted_score_eod_rem") is not None
    if already_rem and gap is None:
        return
    sig = dict(item) if isinstance(item, dict) else {}
    if sig.get("predicted_score") is None:
        sig["predicted_score"] = y_eod
    if sig.get("score") is None:
        sig["score"] = out.get("score") or y_eod
    # tip 常无池上下文：保留簿上已齐的 features_tau / 池缺口，避免冲成缺特征
    book_ft = out.get("features_tau")
    if not isinstance(book_ft, dict):
        book_ft = (item or {}).get("features_tau") if isinstance(item, dict) else None
    if isinstance(book_ft, dict) and book_ft:
        prior = sig.get("features_tau") if isinstance(sig.get("features_tau"), dict) else {}
        merged = dict(prior)
        merged.update({k: v for k, v in book_ft.items() if v is not None and v != ""})
        sig["features_tau"] = merged
    for key in ("_pool_gaps", "sector_gap_breadth"):
        if sig.get(key) is None and isinstance(item, dict) and item.get(key) is not None:
            sig[key] = item.get(key)
        if sig.get(key) is None and out.get(key) is not None:
            sig[key] = out.get(key)
    try:
        from core.signal.dual_score import attach_dual_score_pit, dual_score_book_fields

        attach_dual_score_pit(sig, quote=q, bars=b)
        out.update(dual_score_book_fields(sig))
        return
    except Exception:
        pass
    try:
        from core.signal.dual_score import (
            eod_remaining_at_tau,
            realized_t1_to_tau_pct,
        )

        realized = out.get("realized_t1_to_tau")
        if realized is None:
            realized = realized_t1_to_tau_pct(gap)
        rem = eod_remaining_at_tau(y_eod, realized)
        if rem is None:
            return
        out["predicted_score_eod"] = y_eod
        out["predicted_score_eod_rem"] = rem
        if out.get("predicted_score_blend") is None:
            from core.signal.dual_score import fuse_remaining_heads

            out["predicted_score_blend"] = fuse_remaining_heads(
                rem, out.get("predicted_score_tau")
            )
        if realized is not None:
            out["realized_t1_to_tau"] = realized
        if gap is not None:
            out["gap_pct"] = gap
        try:
            from core.signal.dual_score import dual_score_book_fields

            # 回退路径也补 tip 对照字段（*_cal / OOR / note）
            merge = dict(item or {})
            merge.update(out)
            out.update(dual_score_book_fields(merge))
        except Exception:
            pass
        try:
            from core.signal.dual_score import ensure_formula_terms_tau

            merge = dict(item or {})
            merge.update(out)
            expl = ensure_formula_terms_tau(merge)
            if isinstance(expl, dict):
                out["formula_terms_tau"] = expl
                out["score_formula_terms_tau"] = expl
        except Exception:
            pass
    except Exception:
        pass


def _enrich_book_insight_display_fields(
    out: Dict[str, Any],
    code: str,
    item: dict,
) -> None:
    """簿快路径补倾向 / 超额% / 量比（行情 + 本地 bars，不重打分）。"""
    quote: Dict[str, Any] = {}
    try:
        from core.ports.market import query_quote

        quote = query_quote(code) or {}
    except Exception:
        quote = {}

    try:
        from core.stance import compute_buy_stance

        sig = dict(item) if isinstance(item, dict) else {}
        if sig.get("predicted_score") is None:
            sig["predicted_score"] = out.get("predicted_score") or out.get("score")
        if sig.get("score") is None:
            sig["score"] = out.get("score")
        stance = compute_buy_stance(quote=quote, signal_item=sig)
        code_st = stance.get("stance_code")
        out["stance_code"] = code_st
        out["stance_label"] = stance.get("stance_label")
        out["stance_short"] = STANCE_SHORT.get(str(code_st or ""), "—")
    except Exception:
        out["stance_short"] = out.get("stance_short") or "—"

    bars: List[dict] = []
    try:
        from core.data_service import get_bars

        pack = get_bars(
            code,
            limit=45,
            offline_ok=True,
            cache_max_age_hours=72.0,
        )
        bars = list(pack.get("bars") or [])
    except Exception:
        bars = []

    if out.get("volume_ratio") is None and bars:
        try:
            from core.signal.factors.volume_price import volume_ratio

            vr = volume_ratio(bars)
            if vr is not None:
                out["volume_ratio"] = round(float(vr), 2)
        except Exception:
            pass

    if out.get("excess_return_pct") is None and bars:
        try:
            from core.signal.factors.relative_strength import excess_return_pct
            from core.signal.live_features import fetch_live_index_bars
            from skills.common.history import resolve_market_code

            mkt, _pure = resolve_market_code(str(code))
            idx_pack = fetch_live_index_bars(market=mkt or "CN", limit=60) or {}
            idx_bars = list(idx_pack.get("bars") or [])
            ex = excess_return_pct(bars, idx_bars, window_days=20)
            if ex is not None:
                _apply_excess_label(out, float(ex), item)
        except Exception:
            pass

    if out.get("excess_return_pct") is None and out.get("excess_label") is None:
        _apply_excess_label(out, None, item)

    _hydrate_insight_tau_fields(out, item, quote, bars)


def _insight_from_book_row(
    code: str,
    row: dict,
    *,
    added_at: Optional[str] = None,
    valuation: Optional[Dict[str, Optional[float]]] = None,
    paper_ctx: Optional[dict] = None,
) -> Dict[str, Any]:
    """分池簿快路径：tip 字段与交易执行同源，免 live score_stock。"""
    out = _blank(code, added_at=added_at)
    item = row if isinstance(row, dict) else {}
    out["score"] = _f(item.get("score"))
    if out["score"] is None:
        out["score"] = _f(item.get("predicted_score"))
    if out["score"] is None:
        out["score"] = _f(item.get("score_cluster"))
    out["predicted_score"] = _f(item.get("predicted_score"))
    if out["predicted_score"] is None:
        out["predicted_score"] = out["score"]
    out["hard_reject"] = bool(item.get("hard_reject"))
    out["reject_reason"] = (item.get("reject_reason") or None) if out["hard_reject"] else None
    out["cluster_mode"] = item.get("cluster_mode")
    out["weight_source"] = item.get("weight_source")
    out["cluster_label"] = item.get("cluster_label")
    out["cluster_version"] = item.get("cluster_version")
    out["score_global"] = _f(item.get("score_global"))
    out["score_cluster"] = _f(item.get("score_cluster"))
    out["delta_vs_global"] = _f(item.get("delta_vs_global"))
    out["return_model_source"] = item.get("return_model_source")
    out["factor_coefficients"] = item.get("factor_coefficients")
    out["sub_scores"] = item.get("sub_scores") or {}
    out["score_formula_terms"] = item.get("score_formula_terms")
    out["score_formula"] = item.get("score_formula")
    # ensure 反推 τ 组成需要 stock_code
    if not item.get("stock_code"):
        item = dict(item)
        item["stock_code"] = code
    reasons = item.get("reasons") or item.get("score_reasons") or []
    out["score_reasons"] = list(reasons) if isinstance(reasons, list) else []
    try:
        from core.signal.dual_score import dual_score_book_fields

        out.update(dual_score_book_fields(item))
    except Exception:
        pass
    try:
        from core.signal.score_display import annotate_score_gate

        gate = annotate_score_gate(out["score"], paper=paper_ctx, item=out)
        out["min_score"] = gate["min_score"]
        out["below_min_score"] = gate["below_min_score"]
    except Exception:
        out["min_score"] = None
        out["below_min_score"] = False

    factors = item.get("factors") or {}
    out["volume_ratio"] = _f(factors.get("volume_ratio") or factors.get("turnover_ratio"))
    pe = _f(factors.get("value_pe") or factors.get("pe"))
    pb = _f(factors.get("value_pb") or factors.get("pb"))
    if pe is None and valuation:
        pe = _f(valuation.get("pe"))
    if pb is None and valuation:
        pb = _f(valuation.get("pb"))
    out["pe"] = round(pe, 1) if pe is not None else None
    out["pb"] = round(pb, 2) if pb is not None else None
    excess = _f(factors.get("excess_return_pct") or item.get("excess_return_pct"))
    if excess is not None:
        _apply_excess_label(out, excess, item)

    # 簿行通常无 factors / stance：用行情 + 本地 K 线补三列
    _enrich_book_insight_display_fields(out, code, item)

    out["ok"] = out["score"] is not None
    out["stance_short"] = out.get("stance_short") or "—"
    return out


def _insight_one(
    code: str,
    *,
    added_at: Optional[str] = None,
    valuation: Optional[Dict[str, Optional[float]]] = None,
    paper_ctx: Optional[dict] = None,
) -> Dict[str, Any]:
    """单票轻量摘要：与交易执行同源 ``score_stock``（含分组因子系数）；不二次打指数/基本面/同业。"""
    out = _blank(code, added_at=added_at)
    try:
        from core.signal.score_stock import score_stock
        from core.stance import compute_buy_stance
        from core.ports.market import query_quote

        # cluster_mode=None → 读 signal_config.cluster_scoring（active 时用组 β，同持仓表）
        scored = score_stock(code, horizon_days=3, skip_fundamentals=True)
        quote = (scored or {}).get("quote") or {}
        if not quote:
            quote = query_quote(code)
        item = (scored or {}).get("signal_item") or {}
        out["score"] = _f(item.get("score"))
        if out["score"] is None:
            # 兼容：主分为空时回退组 ŷ / predicted（避免观察表全「—」）
            out["score"] = _f(item.get("predicted_score"))
        if out["score"] is None:
            out["score"] = _f(item.get("score_cluster"))
        out["predicted_score"] = _f(item.get("predicted_score"))
        if out["predicted_score"] is None:
            out["predicted_score"] = out["score"]
        out["hard_reject"] = bool(item.get("hard_reject"))
        out["reject_reason"] = (item.get("reject_reason") or None) if out["hard_reject"] else None
        out["cluster_mode"] = item.get("cluster_mode") or scored.get("cluster_mode")
        out["weight_source"] = item.get("weight_source")
        out["cluster_label"] = item.get("cluster_label")
        out["cluster_version"] = item.get("cluster_version")
        out["score_global"] = _f(item.get("score_global"))
        out["score_cluster"] = _f(item.get("score_cluster"))
        out["delta_vs_global"] = _f(item.get("delta_vs_global"))
        out["return_model_source"] = item.get("return_model_source")
        out["factor_coefficients"] = item.get("factor_coefficients")
        out["sub_scores"] = item.get("sub_scores") or {}
        out["score_formula_terms"] = item.get("score_formula_terms")
        try:
            from core.signal.dual_score import dual_score_book_fields

            out.update(dual_score_book_fields(item))
        except Exception:
            pass
        if out.get("predicted_score_eod_rem") is None:
            _hydrate_insight_tau_fields(out, item, quote, None)
        # 选股门槛仅标注，不抹掉分数
        try:
            from core.signal.score_display import annotate_score_gate

            gate = annotate_score_gate(out["score"], paper=paper_ctx, item=out)
            out["min_score"] = gate["min_score"]
            out["below_min_score"] = gate["below_min_score"]
        except Exception:
            out["min_score"] = None
            out["below_min_score"] = False
        reasons = item.get("reasons") or []
        out["score_reasons"] = list(reasons) if isinstance(reasons, list) else []
        # 缺全局模型时提示（不阻断）
        warns = list(item.get("warnings") or scored.get("warnings") or [])
        if any("no_global_return_model" in str(w) for w in warns):
            out["score_reasons"] = list(out["score_reasons"]) + [
                "缺全局 return_model · 暂用组 ŷ（shadow）"
            ]
        out["score_formula"] = item.get("score_formula") or None
        if not out["score_formula"]:
            try:
                from core.signal.score_view import build_score_formula

                out["score_formula"] = build_score_formula(
                    {
                        "sub_scores": item.get("sub_scores"),
                        "return_model": item.get("return_model"),
                    }
                ) or None
            except Exception:
                out["score_formula"] = None
        out["volume"] = quote.get("volume")
        factors = item.get("factors") or {}
        out["volume_ratio"] = _f(factors.get("volume_ratio") or factors.get("turnover_ratio"))
        pe = _f(factors.get("value_pe") or factors.get("pe"))
        pb = _f(factors.get("value_pb") or factors.get("pb"))
        if pe is None and valuation:
            pe = _f(valuation.get("pe"))
        if pb is None and valuation:
            pb = _f(valuation.get("pb"))
        out["pe"] = round(pe, 1) if pe is not None else None
        out["pb"] = round(pb, 2) if pb is not None else None

        stance = compute_buy_stance(quote=quote, signal_item=item)
        code_st = stance.get("stance_code")
        out["stance_code"] = code_st
        out["stance_label"] = stance.get("stance_label")
        out["stance_short"] = STANCE_SHORT.get(str(code_st or ""), "—")

        excess = _f(factors.get("excess_return_pct"))
        if excess is not None:
            out["excess_return_pct"] = excess
            if excess >= 2:
                out["excess_label"] = "强"
            elif excess <= -2:
                out["excess_label"] = "弱"
            else:
                out["excess_label"] = "平"
        else:
            rs = _f((item.get("sub_scores") or {}).get("relative_strength"))
            if rs is not None:
                out["excess_label"] = "RS" + str(int(round(rs)))

        out["ok"] = bool((scored or {}).get("success")) or quote.get("success") is True
        if not out["ok"] and (scored or {}).get("error"):
            out["error"] = scored.get("error")
    except Exception as e:
        out["error"] = str(e)
    return out


def build_watching_insights(
    codes: List[str],
    *,
    added_at_by_code: Optional[Dict[str, str]] = None,
    limit: int = _INSIGHT_DEFAULT_LIMIT,
) -> Dict[str, Any]:
    added = added_at_by_code or {}
    cleaned = [
        str(c).strip() for c in (codes or []) if str(c).strip()
    ][: max(1, min(int(limit or _INSIGHT_DEFAULT_LIMIT), _INSIGHT_HARD_CAP))]

    if not cleaned:
        return {
            "ok": True,
            "count": 0,
            "items": [],
            "note": "观察摘要为空",
        }

    # paper 门槛只读一次，避免每票 load_paper
    paper_ctx = None
    try:
        from core.paths import PAPER_PATH
        from core.paper import load_paper
        import os

        if os.path.isfile(PAPER_PATH):
            paper_ctx = load_paper(PAPER_PATH)
    except Exception:
        paper_ctx = None

    valuation_by = _spot_valuation_map(cleaned)
    items_by_code: Dict[str, Dict[str, Any]] = {}

    # 分池簿快路径：观察 tip / score 与交易执行同源，避免 100 票 live 打分拖死悬浮
    book_by_code: Dict[str, dict] = {}
    try:
        from core.signal.cluster_live import load_active_cluster_book

        book_doc = load_active_cluster_book() or {}
        for row in list(book_doc.get("scored_all") or []) + list(book_doc.get("book") or []):
            if not isinstance(row, dict):
                continue
            c = str(row.get("stock_code") or row.get("code") or "").strip()
            if not c or c in book_by_code:
                continue
            book_by_code[c] = row
    except Exception:
        book_by_code = {}

    missing: List[str] = []
    book_hits: List[tuple] = []  # (code, row, key)
    for c in cleaned:
        key = str(c).zfill(6) if str(c).isdigit() else str(c)
        row = book_by_code.get(c) or book_by_code.get(key)
        if row and (
            row.get("score") is not None
            or row.get("predicted_score") is not None
            or row.get("score_formula_terms")
        ):
            book_hits.append((c, row, key))
        else:
            missing.append(c)

    n_jobs = len(book_hits) + len(missing)
    workers = min(_INSIGHT_MAX_WORKERS, max(1, n_jobs))
    pool = ThreadPoolExecutor(max_workers=workers) if n_jobs else None
    try:
        futures = {}
        if pool is not None:
            for c, row, key in book_hits:
                futures[
                    pool.submit(
                        _insight_from_book_row,
                        c,
                        row,
                        added_at=added.get(c) or added.get(str(c)),
                        valuation=valuation_by.get(key),
                        paper_ctx=paper_ctx,
                    )
                ] = c
            for c in missing:
                key = str(c).zfill(6) if str(c).isdigit() else str(c)
                futures[
                    pool.submit(
                        _insight_one,
                        c,
                        added_at=added.get(c) or added.get(str(c)),
                        valuation=valuation_by.get(key),
                        paper_ctx=paper_ctx,
                    )
                ] = c
        try:
            for fut in as_completed(futures, timeout=_INSIGHT_BATCH_TIMEOUT) if futures else []:
                code = futures[fut]
                try:
                    items_by_code[code] = fut.result(timeout=_INSIGHT_STOCK_TIMEOUT)
                except Exception as e:
                    items_by_code[code] = _blank(
                        code,
                        added_at=added.get(code),
                        error=f"超时或失败: {e}",
                    )
        except FuturesTimeout:
            for fut, code in futures.items():
                if code in items_by_code:
                    continue
                if fut.done():
                    try:
                        items_by_code[code] = fut.result(timeout=0)
                    except Exception as e:
                        items_by_code[code] = _blank(
                            code,
                            added_at=added.get(code),
                            error=f"超时或失败: {e}",
                        )
                else:
                    items_by_code[code] = _blank(
                        code,
                        added_at=added.get(code),
                        error="整批超时",
                    )
    finally:
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)

    items = [
        items_by_code.get(c)
        or _blank(c, added_at=added.get(c), error="未完成")
        for c in cleaned
    ]
    truncated = max(0, len([str(c).strip() for c in (codes or []) if str(c).strip()]) - len(cleaned))
    book_n = sum(1 for c in cleaned if c in items_by_code and c not in missing)
    note = (
        "轻量观察摘要（跳过基本面/同业/独立指数重拉）；不改 stance 主契约。"
        + (f" 分池簿快路径 {book_n}/{len(cleaned)}。" if book_n else "")
    )
    if truncated:
        note += f" 本次仅返回前 {len(cleaned)} 只（截断 {truncated}）。"
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "note": note,
        "book_hit": book_n,
        "live_scored": len(missing),
    }


def load_insights_cache() -> Optional[List[Dict[str, Any]]]:
    """加载观察池 insights items（即时构建，供因子相关性/IR 等只读分析复用）。"""
    try:
        from core.watching_store import read_watching, watchlist_added_map

        uni = read_watching()
        added_map = watchlist_added_map(uni)
        fallback = str(uni.get("updated_at") or "").strip()
        if fallback:
            for c in uni.get("watchlist") or []:
                key = str(c).strip()
                if key and key not in added_map:
                    added_map[key] = fallback
        codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    except Exception:
        return None
    if not codes:
        return None
    result = build_watching_insights(codes, added_at_by_code=added_map)
    return list(result.get("items") or [])
