"""观察名单研究摘要：评分/倾向/超额/量比/估值/同业/硬拒绝（不改账本）。

列表页必须轻量：跳过 fundamentals / peer / 独立指数重拉；
超额优先用 score 内已有 factors；超时不阻塞关池。
"""

import logging

logger = logging.getLogger(__name__)
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.numbers import to_float as _f

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
from core.watching.store import WATCHING_MAX_SIZE

# 与观察池上限对齐；勿砍到更小导致尾部无分
_INSIGHT_DEFAULT_LIMIT = WATCHING_MAX_SIZE
_INSIGHT_HARD_CAP = WATCHING_MAX_SIZE + 20

# 进程内 ŷ 摘要：同一会话窗口 + live 映射未变时，避免数据中心每次整池重算
_INSIGHT_MEMO_LOCK = threading.Lock()
_INSIGHT_MEMO: Dict[str, Tuple[float, str, Dict[str, Any]]] = {}
_INSIGHT_MEMO_TTL = 900.0


def reset_insight_memo() -> None:
    with _INSIGHT_MEMO_LOCK:
        _INSIGHT_MEMO.clear()


def _insight_cache_stamp() -> str:
    win = "intraday"
    try:
        from core.signal.session_pit import clock_dual_score_window

        win = clock_dual_score_window()
    except Exception:  # noqa: BLE001
        logger.debug("insight cache stamp window failed", exc_info=True)
    asof = ""
    try:
        from core.market.calendar import expected_latest_daily_bar_date

        asof = str(expected_latest_daily_bar_date() or "")
    except Exception:  # noqa: BLE001
        logger.debug("insight cache stamp asof failed", exc_info=True)
    sess = ""
    try:
        from core.market.calendar import resolve_session_date
        from core.signal.session_pit import shanghai_now

        sess = str(resolve_session_date(now=shanghai_now()) or "")
    except Exception:  # noqa: BLE001
        logger.debug("insight cache stamp session failed", exc_info=True)
    ver = ""
    try:
        from core.signal.cluster.pointer import resolve_cluster_weights_path

        p = resolve_cluster_weights_path()
        if p and os.path.isfile(p):
            ver = f"{os.path.basename(p)}:{int(os.path.getmtime(p))}"
    except Exception:  # noqa: BLE001
        logger.debug("insight cache stamp mapping failed", exc_info=True)
    bars_gen = ""
    try:
        from core.paths import CLUSTER_BARS_REFRESH_JOB_PATH, CLUSTER_MINUTE_REFRESH_JOB_PATH

        bits = []
        for path in (CLUSTER_BARS_REFRESH_JOB_PATH, CLUSTER_MINUTE_REFRESH_JOB_PATH):
            if path and os.path.isfile(path):
                bits.append(str(int(os.path.getmtime(path))))
        bars_gen = ",".join(bits)
    except Exception:  # noqa: BLE001
        logger.debug("insight cache stamp bars gen failed", exc_info=True)
    fusion = ""
    try:
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg
        from core.paper import load_paper
        from core.paths import PAPER_PATH

        paper = load_paper(PAPER_PATH) if os.path.isfile(PAPER_PATH) else None
        cfg = get_path_matrix_cfg(paper=paper)
        fusion = (
            f"{cfg.get('fusion_w_oo')},{cfg.get('fusion_w_oc')},{cfg.get('fusion_w_co')}"
        )
    except Exception:  # noqa: BLE001
        logger.debug("insight cache stamp fusion failed", exc_info=True)
    return f"{win}|{asof}|{sess}|{ver}|{bars_gen}|rk{fusion}|causal_rebal"


def _memo_get(code: str, stamp: str) -> Optional[Dict[str, Any]]:
    with _INSIGHT_MEMO_LOCK:
        hit = _INSIGHT_MEMO.get(str(code))
    if not hit:
        return None
    exp, st, item = hit
    if st != stamp or time.monotonic() > exp:
        return None
    if not isinstance(item, dict) or item.get("ok") is False:
        return None
    return item


def _memo_put(code: str, stamp: str, item: Optional[Dict[str, Any]]) -> None:
    if not isinstance(item, dict) or item.get("ok") is False:
        return
    with _INSIGHT_MEMO_LOCK:
        _INSIGHT_MEMO[str(code)] = (
            time.monotonic() + _INSIGHT_MEMO_TTL,
            stamp,
            item,
        )


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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        return None


def _finalize_insight_trade_fields(out: Dict[str, Any]) -> None:
    """对齐表列主分：委托 ``align_trade_score_fields``。

    ``refresh_window=False``：保留 score_one 的 PIT ``dual_score_window``，
    避免无 quote/bars 时被沪市时钟误刷成 intradate/eod_next。
    """
    if not isinstance(out, dict):
        return
    try:
        from core.signal.dual_score import align_trade_score_fields

        align_trade_score_fields(out, write_score=True, refresh_window=False)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        pass


def _scoring_horizon_days() -> int:
    try:
        from core.signal.config import get_scoring_horizon_days

        return int(get_scoring_horizon_days())
    except Exception:  # noqa: BLE001
        logger.debug("get_scoring_horizon_days failed", exc_info=True)
        return 1


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
        "heuristic_score": None,
        "score_scale": None,
        "min_score": None,
        "below_min_score": False,
        "score_formula": None,
        "score_reasons": None,
        "return_model_source": None,
        "oos_failed": False,
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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            return code, None, None
        return code, _f(pack.get("pe")), _f(pack.get("pb"))

    workers = min(6, len(missing))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_one, c) for c in missing]
        for fut in as_completed(futs):
            try:
                code, pe, pb = fut.result()
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        pass

    try:
        _fill_valuation_from_em(want, out)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        pass
    return out


def _attach_oos_flag(out: Dict[str, Any]) -> None:
    """表列徽标：OOS 失败组禁止新买；heuristic / 全局降级都算失败。"""
    if not isinstance(out, dict):
        return
    try:
        from core.signal.rebalance_tracks import OOS_FAIL, resolve_oos_status

        out["oos_failed"] = resolve_oos_status(out) == OOS_FAIL
        return
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        pass
    src = str(out.get("return_model_source") or "")
    out["oos_failed"] = src.startswith("oos_failed") or str(
        out.get("score_scale") or ""
    ) == "heuristic_0_100"


def _is_heuristic_score_scale(item: Optional[dict]) -> bool:
    if not isinstance(item, dict):
        return False
    if str(item.get("score_scale") or "") == "heuristic_0_100":
        return True
    return str(item.get("return_model_source") or "") == "oos_failed_heuristic"


def _sanitize_heuristic_yhat_fields(out: Dict[str, Any], item: Optional[dict] = None) -> bool:
    """OOS→heuristic：0–100 只进 heuristic_score；组/全局 ŷ% 写入表列与 EOD/blend 兼容字段。

    返回 True 表示已按启发式尺度处理（调用方应跳过 τ 融合水合）。
    """
    src = item if isinstance(item, dict) else {}
    if not (_is_heuristic_score_scale(out) or _is_heuristic_score_scale(src)):
        return False
    out["return_model_source"] = (
        out.get("return_model_source")
        or src.get("return_model_source")
        or "oos_failed_heuristic"
    )
    out["score_scale"] = "heuristic_0_100"
    hs = _f(out.get("heuristic_score"))
    if hs is None:
        hs = _f(src.get("heuristic_score"))
    # 旧脏簿把 0–100 写进 score/predicted：回收为 heuristic
    if hs is None:
        cand = _f(out.get("score"))
        if cand is not None and abs(cand) > 20:
            hs = cand
    if hs is None:
        cand = _f(src.get("score"))
        if cand is not None and abs(cand) > 20:
            hs = cand
    if hs is not None:
        out["heuristic_score"] = hs
    # 组/全局 ŷ 对照 → 表列 score + EOD/blend（与 align heuristic 分支一致）
    if out.get("score_cluster") is None:
        out["score_cluster"] = _f(src.get("score_cluster"))
    if out.get("score_global") is None:
        out["score_global"] = _f(src.get("score_global"))
    yhat = out.get("score_cluster")
    if yhat is None:
        yhat = out.get("score_global")
    yhat_f = _f(yhat)
    if yhat_f is not None and abs(yhat_f) > 20:
        yhat_f = None
    out["score"] = yhat_f  # 可为 None → 表列「—」
    if yhat_f is not None:
        out["predicted_score"] = yhat_f
        out["predicted_score_blend"] = yhat_f
        out["decision_score"] = yhat_f
    else:
        out["predicted_score"] = None
        out["predicted_score_eod_rem"] = None
        out["predicted_score_blend"] = None
        out["decision_score"] = None
    return True


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


def _insight_quote_bars(
    code: str,
    quote: Optional[dict] = None,
    bars: Optional[list] = None,
    *,
    offline_only: bool = True,
) -> tuple:
    """观察簿 hydrate：缺 bars/quote 时补数。

    ``offline_only=True``：只读本地日线并合成 quote（与策略调仓默认同源）。
    ``offline_only=False``：可走远端日线/实时行情。
    """
    q = dict(quote) if isinstance(quote, dict) else {}
    b = list(bars or [])
    use_offline = bool(offline_only)
    if not b:
        try:
            from core.data.facade import get_bars

            if use_offline:
                pack = get_bars(
                    code,
                    limit=45,
                    offline_only=True,
                    reject_quote_fallback=True,
                )
            else:
                pack = get_bars(
                    code,
                    limit=45,
                    offline_ok=True,
                    cache_max_age_hours=72.0,
                )
            b = list(pack.get("bars") or [])
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            b = []
    if not q.get("success") and q.get("price_raw") is None and q.get("open") is None:
        if use_offline:
            if b:
                try:
                    last = b[-1] if isinstance(b[-1], dict) else {}
                    close = last.get("close")
                    if close is not None:
                        prev = b[-2] if len(b) >= 2 and isinstance(b[-2], dict) else {}
                        prev_c = prev.get("close") if prev else last.get("pre_close")
                        last_d = str(last.get("date") or last.get("trade_date") or "")[:10]
                        q = {
                            "success": True,
                            "stock_code": str(code),
                            "price_raw": close,
                            "price": close,
                            "open": last.get("open"),
                            "high": last.get("high"),
                            "low": last.get("low"),
                            "pre_close": prev_c,
                            "prev_close": prev_c,
                            "date": last_d,
                            "data_source": "offline_daily_synth",
                        }
                except Exception:  # noqa: BLE001
                    logger.debug("synth quote from bars failed", exc_info=True)
        else:
            try:
                from core.data.facade import get_quote

                q2 = get_quote(code) or {}
                if isinstance(q2, dict) and q2.get("success"):
                    q = q2
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
                pass
    return q, b


def _stamp_insight_ranking(
    out: Dict[str, Any], paper_ctx: Optional[dict] = None
) -> None:
    """观察行 ranking 与交易执行同权：fuse − (price(τ)/open−1)。"""
    if not isinstance(out, dict):
        return
    try:
        from core.paper.rebalance.path_matrix import get_path_matrix_cfg
        from core.signal.yhat_windows import (
            ranking_open_px,
            ranking_price_tau,
            stamp_window_scores,
        )

        cfg = get_path_matrix_cfg(paper=paper_ctx)
        stamped = stamp_window_scores(out, cfg)
        for k in ("ranking", "y_oo", "y_oc", "y_co"):
            if stamped.get(k) is not None:
                out[k] = stamped[k]
        out["fusion_w_oo"] = cfg.get("fusion_w_oo")
        out["fusion_w_oc"] = cfg.get("fusion_w_oc")
        out["fusion_w_co"] = cfg.get("fusion_w_co")
        o = ranking_open_px(out)
        p = ranking_price_tau(out)
        if o is not None:
            out["day_open"] = o
        if p is not None:
            out["price_tau"] = p
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("stamp insight ranking failed", exc_info=True)


def _hydrate_insight_tau_fields(
    out: Dict[str, Any],
    item: dict,
    quote: Optional[dict] = None,
    bars: Optional[list] = None,
    *,
    paper_ctx: Optional[dict] = None,
    force: bool = False,
) -> None:
    """簿行常只有 ŷ_oo：用行情缺口现场写出 ŷ_trade（昨收口径）。

    有新缺口则重算；无缺口且簿上已有 blend 则对齐即可。
    ŷ_τ 仍来自 τ 头（`tau_ridge`），缺则 ŷ_trade 退回 ŷ_oo。
    ŷ_oo_rem 仅派生对照，不进融合。
    """
    if _sanitize_heuristic_yhat_fields(out, item):
        return
    y_oo = out.get("predicted_score")
    if y_oo is None:
        y_oo = out.get("score")
    if y_oo is None and isinstance(item, dict):
        y_oo = item.get("predicted_score")
        if y_oo is None:
            y_oo = item.get("score")
    if y_oo is None:
        return
    q = quote if isinstance(quote, dict) else {}
    b = list(bars or [])
    code = str(
        (item or {}).get("stock_code")
        or out.get("stock_code")
        or (item or {}).get("code")
        or ""
    ).strip()
    if code and (not b or not q.get("success")):
        q, b = _insight_quote_bars(code, quote=q, bars=b)
    gap = None
    try:
        from core.signal.session_pit import resolve_minute_tau_trade_date, resolve_open_t

        trade_day = resolve_minute_tau_trade_date(q, b)
        gap = resolve_open_t(q, b, trade_day=trade_day).get("gap_pct")
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        gap = None
    if gap is None:
        gap = _f(out.get("gap_pct"))
        if gap is None:
            gap = _f((item or {}).get("gap_pct"))
    if gap is not None:
        out["gap_pct"] = gap
    already_rem = out.get("predicted_score_eod_rem") is not None
    if already_rem and gap is None and not force:
        # 无新缺口不重映 rem，但仍对齐 ŷ_trade（修旧簿 eod_next 塌成 EOD）
        _finalize_insight_trade_fields(out)
        return
    sig = dict(item) if isinstance(item, dict) else {}
    # predicted_score 必须是 ŷ_oo；勿把簿上 trade score 喂进 rem 映射
    sig["predicted_score"] = y_oo
    if gap is not None:
        sig["gap_pct"] = gap
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
        from core.signal.dual_score import attach_dual_score_pit
        from core.signal.service import get_default_signal_service
        from core.signal.session_pit import prepare_eod_bars, refresh_dual_score_window

        refresh_dual_score_window(sig, quote=q, bars=b)
        _, pit = prepare_eod_bars(b, q)
        fuse = not bool(pit.get("rolled_to_next"))
        attach_dual_score_pit(sig, quote=q, bars=b, fuse_intraday=fuse)
        out.update(get_default_signal_service().book_fields(sig, paper=paper_ctx))
        _finalize_insight_trade_fields(out)
        _stamp_insight_ranking(out, paper_ctx)
        return
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        pass
    try:
        from core.signal.dual_score import (
            oo_remaining_at_tau,
            realized_t1_to_tau_pct,
        )

        realized = out.get("realized_t1_to_tau")
        if realized is None:
            realized = realized_t1_to_tau_pct(gap)
        rem = oo_remaining_at_tau(y_oo, realized)
        if rem is None:
            _finalize_insight_trade_fields(out)
            return
        out["predicted_score_eod_rem"] = rem
        if out.get("predicted_score_blend") is None:
            win = str(out.get("dual_score_window") or "")
            if win == "eod_next":
                out["predicted_score_blend"] = y_oo
                out["predicted_score_blend_tau_cc"] = None
                out["predicted_score_blend_vs"] = "prev_close"
            else:
                from core.signal.dual_score import trade_blend_vs_prev_close

                cc, tau_cc, vs = trade_blend_vs_prev_close(
                    y_oo, out.get("predicted_score_tau"), gap_pct=gap
                )
                out["predicted_score_blend"] = cc
                out["predicted_score_blend_tau_cc"] = tau_cc
                out["predicted_score_blend_vs"] = vs
        if realized is not None:
            out["realized_t1_to_tau"] = realized
        if gap is not None:
            out["gap_pct"] = gap
        try:
            from core.signal.service import get_default_signal_service

            merge = dict(item or {})
            merge.update(out)
            out.update(get_default_signal_service().book_fields(merge, paper=paper_ctx))
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            pass
        try:
            from core.signal.dual_score import ensure_formula_terms_tau

            merge = dict(item or {})
            merge.update(out)
            expl = ensure_formula_terms_tau(merge)
            if isinstance(expl, dict):
                out["formula_terms_tau"] = expl
                out["score_formula_terms_tau"] = expl
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            pass
        _finalize_insight_trade_fields(out)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        _finalize_insight_trade_fields(out)


def _enrich_book_insight_display_fields(
    out: Dict[str, Any],
    code: str,
    item: dict,
    *,
    quote: Optional[dict] = None,
    fill_stance: bool = True,
) -> None:
    """补倾向 / 超额% / 量比。默认不拉现货：本地日线 + ŷ；涨跌惩罚仅在 quote.success 时生效。"""
    q = quote if isinstance(quote, dict) else {}
    if fill_stance:
        try:
            from core.stance import compute_buy_stance

            sig = dict(item) if isinstance(item, dict) else {}
            if sig.get("predicted_score") is None:
                sig["predicted_score"] = out.get("predicted_score") or out.get("score")
            if sig.get("score") is None:
                sig["score"] = out.get("score")
            # 簿快路径不重拉行情；有 ŷ 仍出倾向，避免表列整列「—」
            stance_quote = q if q.get("success") else {"success": True}
            stance = compute_buy_stance(quote=stance_quote, signal_item=sig)
            code_st = stance.get("stance_code")
            out["stance_code"] = code_st
            out["stance_label"] = stance.get("stance_label")
            out["stance_short"] = STANCE_SHORT.get(str(code_st or ""), "—")
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            out["stance_short"] = out.get("stance_short") or "—"

    if out.get("volume_ratio") is not None and (
        out.get("excess_return_pct") is not None or out.get("excess_label")
    ):
        return

    bars: List[dict] = []
    try:
        from core.data.facade import get_bars

        pack = get_bars(
            code,
            limit=45,
            offline_ok=True,
            cache_max_age_hours=72.0,
        )
        bars = list(pack.get("bars") or [])
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        bars = []

    if out.get("volume_ratio") is None and bars:
        try:
            from core.signal.factors.volume_price import volume_ratio

            vr = volume_ratio(bars)
            if vr is not None:
                out["volume_ratio"] = round(float(vr), 2)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            pass

    if out.get("excess_return_pct") is None and bars:
        try:
            from core.ports.market import resolve_market_code
            from core.signal.factors.relative_strength import excess_return_pct
            from core.signal.live_features import fetch_live_index_bars

            mkt, _pure = resolve_market_code(str(code))
            idx_pack = fetch_live_index_bars(market=mkt or "CN", limit=60) or {}
            idx_bars = list(idx_pack.get("bars") or [])
            ex = excess_return_pct(bars, idx_bars, window_days=20)
            if ex is not None:
                _apply_excess_label(out, float(ex), item)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            pass

    if out.get("excess_return_pct") is None and out.get("excess_label") is None:
        _apply_excess_label(out, None, item)


def _insight_one(
    code: str,
    *,
    added_at: Optional[str] = None,
    valuation: Optional[Dict[str, Optional[float]]] = None,
    paper_ctx: Optional[dict] = None,
    offline_only: bool = True,
) -> Dict[str, Any]:
    """单票轻量摘要：SignalService；默认 ``offline_only`` 与策略调仓对齐。
    表列 ŷ 为 09:30–10:00 调仓因果前缀（``use_minute_tau=True``）。
    """
    out = _blank(code, added_at=added_at)
    use_offline = bool(offline_only)
    try:
        from core.signal.service import get_default_signal_service
        from core.stance import compute_buy_stance

        # cluster_mode=None → 读 signal_config.cluster_scoring（active 时用组 β，同持仓表）
        result = get_default_signal_service().score_one(
            code,
            horizon_days=_scoring_horizon_days(),
            skip_fundamentals=True,
            skip_sentiment=True,
            offline_only=use_offline,
            quote_timeout=5.0 if use_offline else 8.0,
            use_minute_tau=True,
        )
        scored = result.as_dict()
        quote = (scored or {}).get("quote") or {}
        if not quote:
            quote, _bars = _insight_quote_bars(code, offline_only=use_offline)
        item = dict(result.item or {})
        o = _f((quote or {}).get("open_raw"))
        if o is None:
            o = _f((quote or {}).get("open"))
        if o is None:
            o = _f(item.get("open_t"))
        p = _f((quote or {}).get("price_raw"))
        if p is None:
            p = _f((quote or {}).get("price"))
        if o is not None and o > 0:
            out["day_open"] = o
            out.setdefault("open", o)
        if p is not None and p > 0:
            out["price_tau"] = p
            out.setdefault("price", p)
        out["score"] = _f(result.rank_key)
        if out["score"] is None:
            out["score"] = _f(item.get("score"))
        if out["score"] is None:
            out["score"] = _f(item.get("predicted_score_blend"))
        if out["score"] is None:
            # 兼容：主分为空时回退组 ŷ / predicted（避免观察表全「—」）
            out["score"] = _f(item.get("predicted_score"))
        if out["score"] is None:
            out["score"] = _f(item.get("score_cluster"))
        out["predicted_score"] = _f(result.predicted_score)
        if out["predicted_score"] is None:
            out["predicted_score"] = _f(item.get("predicted_score"))
        if out["predicted_score"] is None:
            out["predicted_score"] = _f(item.get("predicted_score_eod"))
        if out["predicted_score"] is None and _f(item.get("predicted_score_blend")) is None:
            out["predicted_score"] = out["score"]
        out["hard_reject"] = bool(item.get("hard_reject"))
        out["reject_reason"] = (item.get("reject_reason") or None) if out["hard_reject"] else None
        out["production_ok"] = bool(result.production_ok)
        if result.gate_reason:
            out["gate_reason"] = result.gate_reason
        out["cluster_mode"] = item.get("cluster_mode") or scored.get("cluster_mode")
        out["weight_source"] = item.get("weight_source")
        out["cluster_label"] = item.get("cluster_label")
        out["cluster_version"] = item.get("cluster_version")
        out["score_global"] = _f(item.get("score_global"))
        out["score_cluster"] = _f(item.get("score_cluster"))
        out["delta_vs_global"] = _f(item.get("delta_vs_global"))
        out["return_model_source"] = item.get("return_model_source")
        out["score_scale"] = item.get("score_scale") or result.scale
        out["heuristic_score"] = _f(item.get("heuristic_score"))
        out["factor_coefficients"] = item.get("factor_coefficients")
        out["sub_scores"] = item.get("sub_scores") or {}
        out["score_formula_terms"] = item.get("score_formula_terms")
        asof_eod = item.get("eod_feature_as_of")
        _sanitize_heuristic_yhat_fields(out, item)
        try:
            if not _is_heuristic_score_scale(out):
                # 只合并双层 ŷ 字段；勿 annotate_item 整包，以免污染 score/stance
                out.update(get_default_signal_service().book_fields(item, paper=paper_ctx))
                _sanitize_heuristic_yhat_fields(out, item)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            pass
        tau_asof = str(out.get("as_of_tau") or item.get("as_of_tau") or "")[:10]
        sess = ""
        try:
            from core.market.calendar import resolve_session_date
            from core.signal.session_pit import shanghai_now

            sess = str(resolve_session_date(now=shanghai_now()) or "")[:10]
        except Exception:  # noqa: BLE001
            logger.debug("insight session date failed", exc_info=True)
        tau_stale = bool(sess and len(tau_asof) >= 10 and tau_asof < sess)
        if (
            not _is_heuristic_score_scale(out)
            and (out.get("predicted_score_eod_rem") is None or tau_stale)
        ):
            _hydrate_insight_tau_fields(
                out, item, quote, None, paper_ctx=paper_ctx, force=tau_stale
            )
        else:
            _finalize_insight_trade_fields(out)
        if asof_eod:
            out["eod_feature_as_of"] = asof_eod
        trade_day = str(item.get("trade_day") or "")[:10]
        if len(trade_day) < 10:
            trade_day = str(out.get("as_of_tau") or item.get("as_of_tau") or "")[:10]
        if len(trade_day) < 10:
            trade_day = sess
        if len(trade_day) >= 10:
            out["trade_day"] = trade_day
        if item.get("factor_anomaly"):
            out["factor_anomaly"] = item.get("factor_anomaly")
        if item.get("open_t") is not None:
            out["open_t"] = item.get("open_t")
        if item.get("open_t_source"):
            out["open_t_source"] = item.get("open_t_source")
        _stamp_insight_ranking(out, paper_ctx)
        # 选股门槛仅标注，不抹掉分数
        try:
            from core.signal.score_display import annotate_score_gate

            gate = annotate_score_gate(out["score"], paper=paper_ctx, item=out)
            out["min_score"] = gate["min_score"]
            out["below_min_score"] = gate["below_min_score"]
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
            out["min_score"] = None
            out["below_min_score"] = False
        _finalize_insight_trade_fields(out)
        _stamp_insight_ranking(out, paper_ctx)
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
            except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
                logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
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
            _apply_excess_label(out, excess, item)
        else:
            rs = _f((item.get("sub_scores") or {}).get("relative_strength"))
            if rs is not None:
                out["excess_label"] = "RS" + str(int(round(rs)))
        if out.get("volume_ratio") is None or (
            out.get("excess_return_pct") is None and out.get("excess_label") is None
        ):
            _enrich_book_insight_display_fields(
                out, code, item, quote=quote, fill_stance=False
            )

        out["ok"] = bool((scored or {}).get("success")) or quote.get("success") is True
        if not out["ok"] and (scored or {}).get("error"):
            out["error"] = scored.get("error")
    except Exception as e:
        logger.exception('unexpected error in _insight_one')
        out["error"] = str(e)
    _attach_oos_flag(out)
    return out


def build_watching_insights(
    codes: List[str],
    *,
    added_at_by_code: Optional[Dict[str, str]] = None,
    limit: int = _INSIGHT_DEFAULT_LIMIT,
    offline_only: bool = True,
) -> Dict[str, Any]:
    added = added_at_by_code or {}
    use_offline = bool(offline_only)
    cleaned = [
        str(c).strip() for c in (codes or []) if str(c).strip()
    ][: max(1, min(int(limit or _INSIGHT_DEFAULT_LIMIT), _INSIGHT_HARD_CAP))]

    if not cleaned:
        return {
            "ok": True,
            "count": 0,
            "items": [],
            "note": "观察摘要为空",
            "offline_only": use_offline,
        }

    # paper 门槛只读一次，避免每票 load_paper
    paper_ctx = None
    try:
        from core.paper import load_paper
        from core.paths import PAPER_PATH

        if os.path.isfile(PAPER_PATH):
            paper_ctx = load_paper(PAPER_PATH)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        paper_ctx = None

    valuation_by = _spot_valuation_map(cleaned)
    items_by_code: Dict[str, Dict[str, Any]] = {}

    stamp = _insight_cache_stamp()
    missing: List[str] = []
    cached_n = 0
    for c in cleaned:
        hit = _memo_get(c, stamp)
        if hit is not None:
            items_by_code[c] = hit
            cached_n += 1
        else:
            missing.append(c)

    n_jobs = len(missing)
    workers = min(_INSIGHT_MAX_WORKERS, max(1, n_jobs)) if n_jobs else 0

    pool = ThreadPoolExecutor(max_workers=workers) if n_jobs else None
    try:
        futures = {}
        if pool is not None:
            for c in missing:
                key = str(c).zfill(6) if str(c).isdigit() else str(c)
                futures[
                    pool.submit(
                        _insight_one,
                        c,
                        added_at=added.get(c) or added.get(str(c)),
                        valuation=valuation_by.get(key),
                        paper_ctx=paper_ctx,
                        offline_only=use_offline,
                    )
                ] = c
        try:
            for fut in as_completed(futures, timeout=_INSIGHT_BATCH_TIMEOUT) if futures else []:
                code = futures[fut]
                try:
                    row = fut.result(timeout=_INSIGHT_STOCK_TIMEOUT)
                    items_by_code[code] = row
                    _memo_put(code, stamp, row)
                except Exception as e:
                    logger.exception('unexpected error in build_watching_insights')
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
                        row = fut.result(timeout=0)
                        items_by_code[code] = row
                        _memo_put(code, stamp, row)
                    except Exception as e:
                        logger.exception('unexpected error in build_watching_insights')
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
    note = "轻量观察摘要（跳过基本面/同业/独立指数重拉）；不改 stance 主契约。"
    if cached_n:
        note += f" 缓存 {cached_n}/{len(cleaned)}。"
    if truncated:
        note += f" 本次仅返回前 {len(cleaned)} 只（截断 {truncated}）。"
    return {
        "ok": True,
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "note": note,
        "live_scored": len(missing),
        "cached_count": cached_n,
        "cache_stamp": stamp,
        "offline_only": use_offline,
    }


def load_insights_cache() -> Optional[List[Dict[str, Any]]]:
    """加载观察池 insights items（走 ``build_watching_insights``，命中进程内 memo 则不再打分）。"""
    try:
        from core.watching.store import read_watching, watchlist_added_map

        uni = read_watching()
        added_map = watchlist_added_map(uni)
        fallback = str(uni.get("updated_at") or "").strip()
        if fallback:
            for c in uni.get("watchlist") or []:
                key = str(c).strip()
                if key and key not in added_map:
                    added_map[key] = fallback
        codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in watching_insights.py", exc_info=True)
        return None
    if not codes:
        return None
    result = build_watching_insights(codes, added_at_by_code=added_map)
    return list(result.get("items") or [])
