"""双层 ŷ：τ 特征落盘、attach、公式项与 rem 系数。"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.signal.dual_score.fusion import (
    _as_float,
    cascade_tau_shadow,
    fuse_remaining_heads_meta,
    merge_tau_features,
    oo_remaining_at_tau,
    realized_t1_to_tau_pct,
    resolve_fusion_weights,
    stamp_trade_prev_close,
)
from core.signal.dual_score.resolve import (
    DEFAULT_DUAL_SCORE,
    get_dual_score_cfg,
    is_heuristic_score_scale,
    resolve_predicted_score_oo,
)
from core.signal.yhat_geom import (
    align_rem_yhat_to_clock,
    rem_label_is_open_to_close,
    resolve_oo_prior_var,
)

from core.research.tau_panel import TAU_LAG_FEATURES
from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS, MINUTE_TAU_SHAPE_KEYS

_TAU_FEATURE_KEYS = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
) + MINUTE_TAU_ALL_KEYS + TAU_LAG_FEATURES

_TAU_CORE_Z_KEYS = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
    "mom3_pct",
)



def features_tau_snapshot(feats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from core.signal.minute_tau_feats import attach_ret_vs_sector

    out: Dict[str, Any] = {}
    src = dict(feats or {})
    attach_ret_vs_sector(src)
    for k in _TAU_FEATURE_KEYS:
        if k in src:
            out[k] = src.get(k)
    for k in MINUTE_TAU_SHAPE_KEYS:
        if k in src:
            out[k] = src.get(k)
    return out


def features_tau_fill_diag(feats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """features_tau 非空诊断：刷簿 / tip 验收用。"""
    src = feats if isinstance(feats, dict) else {}
    present = []
    missing = []
    for k in _TAU_CORE_Z_KEYS:
        v = src.get(k)
        if v is None or v == "":
            missing.append(k)
        else:
            present.append(k)
    n = len(_TAU_CORE_Z_KEYS)
    filled = len(present)
    return {
        "filled": filled,
        "total": n,
        "fill_rate": round(filled / float(n), 4) if n else None,
        "present": present,
        "missing": missing,
    }


def apply_tau_score_fields(
    signal_item: Dict[str, Any],
    *,
    rem_yhat: Optional[float],
    gap_pct: Optional[float],
    feats: Optional[Dict[str, Any]] = None,
    event_prior: Optional[dict] = None,
    config: Optional[dict] = None,
    as_of_tau: Optional[str] = None,
    y_spec_override: Optional[Dict[str, Any]] = None,
    rem_model_doc: Optional[Dict[str, Any]] = None,
    residual_delta: Optional[bool] = None,
    fuse_intraday: bool = True,
) -> Dict[str, Any]:
    """写入双层契约字段（不改 predicted_score / score 主值）。

    ``rem_yhat`` 就是独立训出的 ŷ_τ（open→close），不依赖 ŷ_oo。
    ŷ_trade = w·ŷ_oo + w·(缺口∘ŷ_τ)；ŷ_oo_rem 只作派生对照，不进融合。
    ``residual_delta`` 已废弃，忽略。
    ``fuse_intraday=False``（ŷ_oo 已换到下一周期）：τ 不进主排序融合。
    收盘后同一周期仍 fuse：ŷ_oc 钉在 ≤10:00，不是当日已实现 OC。
    """
    _ = residual_delta
    cfg = get_dual_score_cfg(config)
    tau = str(cfg.get("tau") or "open")
    y_spec = dict(cfg.get("y_spec") or DEFAULT_DUAL_SCORE["y_spec"])
    y_spec["tau"] = tau
    if tau == "open":
        y_spec.setdefault("formula", "close[T]/open[T]-1")
    if isinstance(y_spec_override, dict):
        y_spec.update(y_spec_override)
    as_of = as_of_tau if as_of_tau is not None else tau

    prior_ft = (
        signal_item.get("features_tau")
        if isinstance(signal_item.get("features_tau"), dict)
        else None
    )
    feats_merged = merge_tau_features(feats, prior_ft)
    feat_snap = features_tau_snapshot(feats_merged)
    fill_diag = features_tau_fill_diag(feat_snap)
    ret_ot = feat_snap.get("ret_open_to_tau")
    if ret_ot is None and isinstance(feats_merged, dict):
        ret_ot = feats_merged.get("ret_open_to_tau")
    realized = realized_t1_to_tau_pct(gap_pct, ret_ot)
    y_oo = resolve_predicted_score_oo(signal_item)
    fuse = bool(fuse_intraday)
    oo_rem = y_oo if not fuse else oo_remaining_at_tau(y_oo, realized)
    clock = str(as_of or tau) if fuse else "eod"
    y_tau_raw = rem_yhat
    y_tau = (
        align_rem_yhat_to_clock(
            y_tau_raw,
            rem_model_doc=rem_model_doc,
            ret_open_to_tau=ret_ot,
            clock=clock,
        )
        if fuse
        else y_tau_raw
    )
    we, wt, w_note = resolve_fusion_weights(
        cfg, feats=feat_snap, rem_model_doc=rem_model_doc
    )
    y_tau_for_trade = y_tau if fuse else None
    head_meta = fuse_remaining_heads_meta(
        y_oo, y_tau_for_trade, w_oo=we, w_tau=wt
    )
    if not fuse:
        w_note = f"{w_note}|eod_next(tau_stripped)"

    ve, ve_src = resolve_oo_prior_var(
        cfg_var=cfg.get("eod_residual_var"),
    )

    rem_intercept = None
    try:
        doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
        if isinstance(rm, dict) and rm.get("intercept") is not None:
            rem_intercept = float(rm.get("intercept"))
    except (TypeError, ValueError):
        rem_intercept = None
    cascade = None
    if fuse and cfg.get("enable_cascade_shadow", True):
        cascade = cascade_tau_shadow(
            oo_rem, y_tau, rem_intercept=rem_intercept
        )

    if y_oo is not None:
        signal_item["y_oo"] = y_oo
        signal_item["predicted_score_oo"] = y_oo
    signal_item["predicted_score_eod_rem"] = oo_rem
    signal_item["predicted_score_tau_delta"] = None
    signal_item["predicted_score_tau_cascade"] = cascade
    signal_item["realized_t1_to_tau"] = realized
    signal_item["predicted_score_tau"] = y_tau
    # 兼容别名（= ŷ_τ）；新读路径请用 predicted_score_tau
    signal_item["predicted_score_rem"] = y_tau
    signal_item["score_rem"] = y_tau
    # OC 头原始 ŷ（训练/Hub 同口径）；分钟时钟映射前，供画像命中对照
    if y_tau_raw is not None:
        signal_item["y_tau_oc"] = y_tau_raw
        signal_item["predicted_score_tau_oc"] = y_tau_raw
    oc = y_tau_raw if y_tau_raw is not None else y_tau
    if oc is not None:
        signal_item["y_oc"] = oc
        signal_item["predicted_score_oc"] = oc
        signal_item["y_tau"] = signal_item.get("y_tau", y_tau)
    signal_item["as_of_tau"] = as_of
    signal_item["y_spec_tau"] = y_spec
    signal_item["features_tau"] = feat_snap
    signal_item["features_tau_fill"] = fill_diag
    signal_item["gap_pct"] = gap_pct
    # 兼容旧键；等同 as_of_tau / y_spec_tau.formula
    signal_item["rem_tau"] = as_of
    signal_item["rem_y_spec"] = y_spec.get("formula")
    signal_item["dual_score_fusion"] = cfg["fusion_mode"]
    signal_item["dual_score_window"] = "intraday" if fuse else "eod_next"
    # eod_next 不传 y_tau：避免主排序分融合今日已实现收益（缺口∘ŷ_τ≈T日收盘涨幅）
    trade = stamp_trade_prev_close(
        signal_item,
        y_oo=_as_float(y_oo),
        y_tau=_as_float(y_tau_for_trade),
        w_oo=we,
        w_tau=wt,
    )
    if event_prior is not None:
        signal_item["event_prior"] = event_prior
    formula_terms_tau = None
    if feats_merged:
        try:
            from core.research.tau_ridge import explain_tau_prediction

            formula_terms_tau = explain_tau_prediction(
                feats_merged, model_doc=rem_model_doc
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            formula_terms_tau = None
    if not isinstance(formula_terms_tau, dict):
        formula_terms_tau = {}
    else:
        formula_terms_tau = dict(formula_terms_tau)
    formula_terms_tau["oo_remaining"] = oo_rem
    formula_terms_tau["eod_remaining"] = oo_rem  # 遗留键
    formula_terms_tau["y_oo"] = y_oo
    formula_terms_tau["y_tau"] = y_tau
    formula_terms_tau["y_tau_raw"] = y_tau_raw
    formula_terms_tau["y_tau_cc"] = signal_item.get("predicted_score_blend_tau_cc")
    formula_terms_tau["trade"] = trade
    formula_terms_tau["cascade"] = cascade
    formula_terms_tau["rem_oc"] = rem_label_is_open_to_close(rem_model_doc)
    formula_terms_tau["head"] = (
        "tau_oc" if rem_label_is_open_to_close(rem_model_doc) else "tau_rem"
    )
    formula_terms_tau["features_fill"] = fill_diag
    signal_item["formula_terms_tau"] = formula_terms_tau
    signal_item["score_formula_terms_tau"] = formula_terms_tau
    signal_item["score_formula_tau"] = format_tau_formula_string(formula_terms_tau)
    signal_item["predicted_score_blend"] = trade
    signal_item["dual_score_head"] = head_meta.get("dual_score_head")
    signal_item["dual_score_single_head"] = bool(head_meta.get("single_head"))
    for _dead in (
        "predicted_score_nowcast",
        "nowcast_vs",
        "nowcast_as_of",
        "nowcast_revisions",
        "nowcast_K",
        "nowcast_P",
        "nowcast_x_prior",
        "nowcast_q",
        "nowcast_q_note",
        "nowcast_path",
        "y_nowcast",
        "y_nc",
    ):
        signal_item.pop(_dead, None)
    w_mode = str(cfg.get("w_mode") or "fixed").strip().lower()
    if w_mode == "kalman":
        w_mode = "fixed"
    signal_item["dual_score_weights"] = {
        "w_oo": round(we, 6),
        "w_eod": round(we, 6),
        "w_tau": round(0.0 if not fuse else wt, 6),
        "w_mode": w_mode,
        "w_note": w_note,
        "mode": "blend",
        "tau_available": y_tau is not None,
        "tau_in_trade": bool(fuse and y_tau_for_trade is not None),
        "eod_prior_var": round(ve, 6),
        "eod_prior_var_src": ve_src,
        "window": "intraday" if fuse else "eod_next",
    }
    # heuristic 轨：0–100 只留 heuristic_score；组/全局 ŷ% 写入 EOD/blend 兼容字段
    if is_heuristic_score_scale(signal_item):
        hs = signal_item.get("heuristic_score")
        if hs is None:
            hs = signal_item.get("score")
        try:
            if hs is not None and hs != "" and abs(float(hs)) > 20:
                signal_item["heuristic_score"] = float(hs)
        except (TypeError, ValueError):
            pass
        yhat = signal_item.get("score_cluster")
        if yhat is None:
            yhat = signal_item.get("score_global")
        try:
            yhat_f = float(yhat) if yhat is not None and yhat != "" else None
        except (TypeError, ValueError):
            yhat_f = None
        if yhat_f is not None and abs(yhat_f) > 20.0:
            yhat_f = None
        if yhat_f is not None:
            signal_item["predicted_score"] = yhat_f
            signal_item["predicted_score_blend"] = yhat_f
            signal_item["decision_score"] = yhat_f
            signal_item["score"] = yhat_f
        else:
            signal_item["predicted_score"] = None
            signal_item["predicted_score_eod_rem"] = None
            signal_item["predicted_score_blend"] = None
            signal_item["score"] = None
        signal_item["score_scale"] = "heuristic_0_100"
    try:
        from core.signal.y_state import stamp_y_state

        stamp_y_state(signal_item, config=config)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    return signal_item


def attach_dual_score_bulk(
    signal_items: Sequence[Dict[str, Any]],
    *,
    quotes_by_code: Optional[Dict[str, dict]] = None,
    bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    config: Optional[dict] = None,
    rem_model_doc: Optional[Dict[str, Any]] = None,
    sector_gap_breadth: Optional[float] = None,
    fuse_intraday: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """S3 批量包装：对序列内每一项调用 attach_dual_score_pit，返回新列表。

    cross_section.rank_cross_section 排序前统一挂 dual score 时使用此入口，
    与 cluster_rank 单票 attach 的效果保持一致（接口层面给 bulk 友好签名）。
    """
    if not signal_items:
        return []
    _quotes = dict(quotes_by_code or {})
    _bars = dict(bars_by_code or {})
    out: List[Dict[str, Any]] = []
    _rem = rem_model_doc  # 共享单次加载（pit内部会fallback，外层只传已加载的）
    for it in signal_items:
        c = str((it or {}).get("stock_code") or "").strip()
        try:
            out.append(
                attach_dual_score_pit(
                    dict(it) if isinstance(it, dict) else it,
                    quote=_quotes.get(c),
                    bars=_bars.get(c),
                    config=config,
                    rem_model_doc=_rem,
                    sector_gap_breadth=sector_gap_breadth,
                    fuse_intraday=fuse_intraday,
                )
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            # 单票失败不影响整体（attach失败时回退原item，cross_section仍能用predicted_score老路）
            out.append(dict(it) if isinstance(it, dict) else it)
    return out


def attach_dual_score_pit(
    signal_item: Dict[str, Any],
    *,
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    config: Optional[dict] = None,
    rem_model_doc: Optional[Dict[str, Any]] = None,
    sector_gap_breadth: Optional[float] = None,
    fuse_intraday: Optional[bool] = None,
    sector_gap_median: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    sector_ret_to_tau: Optional[float] = None,
    use_minute_tau: Optional[bool] = None,
    minute_tau_hm: Optional[str] = None,
    trade_day: Optional[str] = None,
) -> Dict[str, Any]:
    """历史回测 / 无实时行情时：用 PIT 日线 quote·bars 挂 ŷ_τ + blend。

    ``trade_day``：回测 T 日。不传则 ``resolve_minute_tau_trade_date``（live 会把
    「昨收完整日」抬到当前周期，昨天的回测夜盘会丢缺口）。

    缺口 = open[T]/close[T−1]（与 live ``resolve_open_t`` 同口径）。
    ``sector_gap_breadth`` / ``sector_gap_median``：开盘缺口截面。
    ``sector_ret_to_tau``：开→τ 池中位（与训练 ``attach_cross_section_breadth`` 同口径）；
    缺省时尽力从活跃簿分钟仓聚合，再写 ``ret_vs_sector``。
    ``minute_bars``：可选 ≤τ 分钟线；``enable_minute_tau`` 时并入分钟小包（亦可读本地缓存）。
    ``minute_tau_hm``：因果 τ 钟（固定前缀末根）；无钟则开盘 Z 或调仓因果末根。
    ``use_minute_tau=False``：强制开盘 Z（做 T 开盘预计算）；不读分钟仓/缓存。
    无 ŷ_τ 模型时仍写契约字段（ŷ_τ=None，ŷ_trade 退回 ŷ_oo）。
    ``fuse_intraday=False``：ŷ_oo 已换期（T+1 开盘后），τ 不进主排序。
    收盘后同一周期默认仍 fuse。
    """
    if not isinstance(signal_item, dict):
        return signal_item
    if rem_model_doc is None:
        try:
            from core.research.tau_ridge import load_tau_model

            rem_model_doc = load_tau_model()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            rem_model_doc = None
    q = quote if isinstance(quote, dict) else signal_item.get("_bt_quote")
    b = bars if bars is not None else signal_item.get("_bt_bars")
    cfg = get_dual_score_cfg(config)
    allow_minute = (
        bool(cfg.get("enable_minute_tau"))
        if use_minute_tau is None
        else bool(use_minute_tau)
    )

    def _hm_from_minute_bars(raw_bars: Optional[Sequence[dict]]) -> Optional[str]:
        last = None
        for bar in raw_bars or []:
            if isinstance(bar, dict):
                last = bar
        if not isinstance(last, dict):
            return None
        ts = str(last.get("datetime") or last.get("date") or "")
        if " " in ts:
            return ts.split(" ", 1)[1][:5] or None
        if "T" in ts:
            return ts.split("T", 1)[1][:5] or None
        return None

    # 因果钟：显式参数 > 条目标记；无钟则开盘 Z 或调仓因果末根（不读遗留 minute_tau_hm）
    effective_hm = str(minute_tau_hm or "").strip()[:5] or None
    if not effective_hm:
        raw_hm = signal_item.get("_minute_tau_hm")
        effective_hm = str(raw_hm or "").strip()[:5] or None
    if not effective_hm and minute_bars:
        effective_hm = _hm_from_minute_bars(minute_bars)
    pack_hm = effective_hm
    sector_hm = effective_hm
    gap_v = None
    open_t_px = None
    trade_day0 = str(trade_day or "").strip()[:10]
    try:
        from core.event_prior import get_event_prior_cfg
        from core.signal.session_pit import resolve_minute_tau_trade_date, resolve_open_t

        if len(trade_day0) < 10:
            trade_day0 = resolve_minute_tau_trade_date(q, b)
        open_info = resolve_open_t(
            q, b, trade_day=trade_day0, minute_bars=minute_bars
        )
        gap_v = open_info.get("gap_pct")
        open_t_px = open_info.get("open")
        ep_cfg = get_event_prior_cfg()
        trigger = float(ep_cfg.get("gap_trigger_pct") or 2)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        trigger = 2.0
    theme = 0.0
    breadth = sector_gap_breadth
    if breadth is None and signal_item.get("sector_gap_breadth") is not None:
        try:
            breadth = float(signal_item.get("sector_gap_breadth"))
        except (TypeError, ValueError):
            breadth = None
    try:
        from core.research.tau_theme import resolve_theme_day

        theme = resolve_theme_day(
            gap_pct=gap_v,
            sector_breadth=breadth,
            pool_gaps=signal_item.get("_pool_gaps")
            if isinstance(signal_item.get("_pool_gaps"), (list, tuple))
            else None,
            gap_trigger_pct=trigger,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        theme = 1.0 if (gap_v is not None and abs(float(gap_v)) >= trigger) else 0.0
    feats: Dict[str, Any] = {
        "gap_pct": gap_v,
        "sector_gap_breadth": breadth,
        "theme_day": theme,
    }
    try:
        from core.research.tau_panel import (
            _finite_median,
            gap_atr_from_hist,
            gap_vs_sector_value,
            hist_bars_pit,
        )

        asof = str(trade_day0 or "")[:10]
        hist = hist_bars_pit(b, asof_date=asof)
        feats["gap_atr"] = gap_atr_from_hist(gap_v, hist)
        try:
            from core.research.tau_panel import mom3_pct_from_hist, yclose_loc_from_prev

            open_px = open_t_px
            prev = hist[-1] if hist else None
            if open_px is not None:
                feats["yclose_loc"] = yclose_loc_from_prev(prev, float(open_px))
            feats["mom3_pct"] = mom3_pct_from_hist(hist)
            try:
                from core.research.tau_panel import attach_tau_lag_features

                feats = attach_tau_lag_features(
                    feats, hist_bars=hist, asof_date=asof
                )
            except Exception:  # noqa: BLE001
                logger.debug("tau lag feats fill failed", exc_info=True)
        except Exception:  # noqa: BLE001
            logger.debug("tau yclose/mom3 fill failed", exc_info=True)
        ref = sector_gap_median
        if ref is None and signal_item.get("_sector_gap_median") is not None:
            try:
                ref = float(signal_item.get("_sector_gap_median"))
            except (TypeError, ValueError):
                ref = None
        if ref is None:
            pool = signal_item.get("_pool_gaps")
            if isinstance(pool, (list, tuple)) and pool:
                ref = _finite_median(
                    [float(g) for g in pool if g is not None]
                )
        feats["gap_vs_sector"] = gap_vs_sector_value(gap_v, ref)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    # 保留刷簿已写齐的截面 Z，避免 tip/PIT 路径冲成缺特征
    prior_ft = signal_item.get("features_tau")
    if isinstance(prior_ft, dict):
        # 仅用「算出来的非空」覆盖；空值不冲掉簿上齐套列
        computed = {k: v for k, v in feats.items() if v is not None and v != ""}
        feats = merge_tau_features(computed, prior_ft)
        # 无池广度时不要用弱路径算出的 theme_day=0 盖掉刷簿主题日
        if breadth is None and not isinstance(
            signal_item.get("_pool_gaps"), (list, tuple)
        ):
            if prior_ft.get("theme_day") is not None:
                feats["theme_day"] = prior_ft.get("theme_day")
            if prior_ft.get("sector_gap_breadth") is not None:
                feats["sector_gap_breadth"] = prior_ft.get("sector_gap_breadth")
            if prior_ft.get("gap_vs_sector") is not None and feats.get(
                "gap_vs_sector"
            ) is None:
                feats["gap_vs_sector"] = prior_ft.get("gap_vs_sector")
    # 分钟小包（≤τ）：与 score_stock 同源；T0 选向须 use_minute_tau=False 禁前瞻
    as_of_tau_override: Optional[str] = None
    y_spec_override: Optional[Dict[str, Any]] = None
    trade_day = ""
    if not allow_minute:
        from core.signal.minute_tau_feats import clear_minute_tau_pack_keys

        clear_minute_tau_pack_keys(feats)
        prior_tau = signal_item.get("features_tau")
        if isinstance(prior_tau, dict):
            clear_minute_tau_pack_keys(prior_tau)
    else:
        try:
            from core.signal.minute_tau_feats import merge_minute_tau_pack_into_feats
            from core.signal.session_pit import (
                daily_cache_behind_tau_session,
                recover_gap_pct_from_minute_pack,
                resolve_minute_tau_trade_date,
                tau_open_and_prev_close,
            )

            # 盘中 T=会话日；实时行情无 date 时勿回退到昨收完整 K。
            # 回测已传入 trade_day 时不要再按墙上时钟抬到当前周期。
            trade_day = str(trade_day0 or "").strip()[:10]
            if len(trade_day) < 10:
                trade_day = resolve_minute_tau_trade_date(q, b)
            open_px, prev_c = tau_open_and_prev_close(
                q, b, trade_day, minute_bars=minute_bars
            )
            if open_px is None:
                open_px = open_t_px
            if daily_cache_behind_tau_session(q, b, trade_day):
                if open_px is not None and prev_c:
                    gap_v = round((float(open_px) / float(prev_c) - 1.0) * 100.0, 4)
                else:
                    gap_v = None
                feats["gap_pct"] = gap_v
            # 显式传入分钟（含空前缀）只切该序列；None 才读仓。无钟则开盘 Z。
            no_prefix = minute_bars is None
            if pack_hm:
                feats, as_of_tau_override, y_spec_override = merge_minute_tau_pack_into_feats(
                    feats,
                    code=str(signal_item.get("stock_code") or ""),
                    trade_date=trade_day,
                    open_px=open_px,
                    prev_close=prev_c,
                    tau_hm=pack_hm,
                    minute_bars=minute_bars,
                    load_cache_if_missing=no_prefix,
                    fetch_if_missing=False,
                )
            elif no_prefix:
                feats, as_of_tau_override, y_spec_override = merge_minute_tau_pack_into_feats(
                    feats,
                    code=str(signal_item.get("stock_code") or ""),
                    trade_date=trade_day,
                    open_px=open_px,
                    prev_close=prev_c,
                    causal_rebalance=True,
                    load_cache_if_missing=True,
                    fetch_if_missing=True,
                )
            else:
                from core.signal.minute_tau_feats import clear_minute_tau_pack_keys

                clear_minute_tau_pack_keys(feats)
            if gap_v is None:
                recovered = recover_gap_pct_from_minute_pack(feats)
                if recovered is not None:
                    gap_v = recovered
                    feats["gap_pct"] = gap_v
        except Exception:  # noqa: BLE001
            logger.debug("minute tau pack attach in dual_score_pit failed", exc_info=True)
            from core.signal.minute_tau_feats import clear_minute_tau_pack_keys

            clear_minute_tau_pack_keys(feats)
        # 簿 leftover 不得在 apply_tau_score_fields 里填回分钟键
        prior_tau = signal_item.get("features_tau")
        if isinstance(prior_tau, dict):
            from core.signal.minute_tau_feats import clear_minute_tau_pack_keys as _clear_pack

            _clear_pack(prior_tau)
    # 截面开→τ：训练 panel 有；serve 需显式补，否则 τ/path 头 CS 键恒缺→z=0
    # 开盘选向（use_minute_tau=False）禁开→τ 截面，避免分钟前缀前瞻
    if allow_minute:
        try:
            from core.signal.minute_tau_feats import (
                apply_sector_ret_cs,
                peer_codes_for_sector_ret,
                resolve_sector_ret_to_tau,
                sector_ret_median,
            )

            sret = sector_ret_to_tau
            if sret is None and signal_item.get("_sector_ret_to_tau") is not None:
                try:
                    sret = float(signal_item.get("_sector_ret_to_tau"))
                except (TypeError, ValueError):
                    sret = None
            if sret is None and isinstance(
                signal_item.get("_peer_ret_open_to_tau"), (list, tuple)
            ):
                sret = sector_ret_median(signal_item.get("_peer_ret_open_to_tau") or [])
            clock_cs = bool(str(pack_hm or "").strip()[:5])
            if sret is None and (clock_cs or feats.get("sector_ret_to_tau") is None):
                if not trade_day:
                    trade_day = str(trade_day0 or "").strip()[:10]
                if not trade_day:
                    from core.signal.session_pit import resolve_minute_tau_trade_date

                    trade_day = resolve_minute_tau_trade_date(q, b)
                hm = str(sector_hm or "").strip()[:5] if sector_hm else None
                if (
                    hm
                    and len(trade_day) >= 10
                    and (
                        feats.get("ret_open_to_tau") is not None
                        or bool(cfg.get("enable_minute_tau"))
                    )
                ):
                    sret = resolve_sector_ret_to_tau(
                        trade_day, hm, codes=peer_codes_for_sector_ret() or None
                    )
            if sret is not None:
                feats = apply_sector_ret_cs(feats, sret, overwrite=clock_cs)
        except Exception:  # noqa: BLE001
            logger.debug("sector_ret_to_tau attach in dual_score_pit failed", exc_info=True)
    if not allow_minute:
        from core.signal.minute_tau_feats import stamp_open_clock_minute_z

        stamp_open_clock_minute_z(feats)
    # 只传 τ 头 Z 特征；勿塞全日线 sub_scores（训练未用，易误导）
    rem_yhat = None
    try:
        from core.research.tau_ridge import predict_tau_from_features

        rem_yhat = predict_tau_from_features(feats, model_doc=rem_model_doc)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        rem_yhat = None
    ep = None
    if gap_v is not None:
        try:
            ep = {
                "theme": bool(theme),
                "gap_pct": gap_v,
                "warnings": [],
            }
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            ep = None
    apply_tau_score_fields(
        signal_item,
        rem_yhat=rem_yhat,
        gap_pct=gap_v,
        feats=feats,
        event_prior=ep,
        # 传完整 signal_config（或调用方原 config），勿传已 flatten 的 dual 块
        config=config,
        as_of_tau=as_of_tau_override
        if as_of_tau_override is not None
        else str(cfg.get("tau") or "open"),
        y_spec_override=y_spec_override,
        rem_model_doc=rem_model_doc,
        fuse_intraday=_resolve_fuse_intraday(
            signal_item, fuse_intraday=fuse_intraday, quote=q, bars=b
        ),
    )
    try:
        from core.signal.dual_score.co import attach_co_score_pit

        attach_co_score_pit(
            signal_item,
            quote=q,
            bars=b,
            config=config,
            sector_gap_breadth=breadth,
            theme_day=feats.get("theme_day"),
            gap_pct=gap_v,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score_tau on", exc_info=True)
    return signal_item


def _resolve_fuse_intraday(
    signal_item: dict,
    *,
    fuse_intraday: Optional[bool],
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
) -> bool:
    if fuse_intraday is not None:
        return bool(fuse_intraday)
    try:
        from core.signal.session_pit import prepare_eod_bars, refresh_dual_score_window

        refresh_dual_score_window(signal_item, quote=quote, bars=bars)
        _, pit = prepare_eod_bars(bars, quote)
        return not bool(pit.get("rolled_to_next"))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        return False


def _eod_return_model_for_item(item: dict):
    """取该票打分时同源的 EOD ReturnScoreModel（反推 sub_scores 用）。"""
    code = str(item.get("stock_code") or "").strip()
    src = str(item.get("return_model_source") or "").strip()
    # 主分已降级：τ 反推也用全局，避免失败组 β 污染
    use_cluster = src not in (
        "oos_failed_global",
        "oos_failed_heuristic",
        "global",
        "oos_failed_degrade",
    )
    if use_cluster and src.startswith("oos_failed"):
        use_cluster = False
    try:
        if use_cluster:
            from core.signal.cluster.live import (
                filter_primary_cluster_models_by_code,
                load_cluster_return_models_by_code,
            )

            models = filter_primary_cluster_models_by_code(
                load_cluster_return_models_by_code() or {}
            )
            if code and code in models:
                return models[code]
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    try:
        from core.signal.return_score_store import load_return_model

        rm, _meta = load_return_model(prefer_active=True)
        return rm
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        return None


def recover_sub_scores_for_tau(item: Optional[dict]) -> Dict[str, float]:
    """优先用行内 sub_scores；旧簿缺失时从 EOD formula_terms 的 z 反推 raw。"""
    if not isinstance(item, dict):
        return {}
    subs = item.get("sub_scores") or {}
    out: Dict[str, float] = {}
    if isinstance(subs, dict):
        for k, v in subs.items():
            if v is None or v == "":
                continue
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    if out:
        return out
    expl = item.get("score_formula_terms") or item.get("formula_terms") or {}
    terms = expl.get("terms") if isinstance(expl, dict) else None
    if not isinstance(terms, list) or not terms:
        return {}
    rm = _eod_return_model_for_item(item)
    if rm is None:
        return {}
    means = getattr(rm, "z_means", None) or {}
    stds = getattr(rm, "z_stds", None) or {}
    standardized = bool(getattr(rm, "standardized", True))
    for t in terms:
        if not isinstance(t, dict) or t.get("gated"):
            continue
        key = t.get("key")
        z = t.get("z")
        if key is None or z is None or z == "":
            continue
        try:
            zf = float(z)
        except (TypeError, ValueError):
            continue
        name = str(key)
        if standardized:
            mu = float(means.get(name) or 0.0)
            sd = float(stds.get(name) or 1.0)
            if sd < 1e-12:
                sd = 1.0
            out[name] = zf * sd + mu
        else:
            out[name] = zf
    return out


def format_tau_formula_string(expl: Optional[Dict[str, Any]]) -> str:
    if not isinstance(expl, dict):
        return ""
    y_oo = expl.get("y_oo")
    trade = expl.get("trade")
    y_tau = expl.get("y_tau")
    y_tau_cc = expl.get("y_tau_cc")
    if y_tau is None:
        y_tau = expl.get("total")
    if y_oo is not None and trade is not None and y_tau is not None:
        try:
            tau_bit = y_tau_cc if y_tau_cc is not None else y_tau
            return (
                f"ŷ_trade = w·ŷ_oo({float(y_oo):+.3f}) + "
                f"w·ŷ_τ昨收({float(tau_bit):+.3f}) = {float(trade):.3f}%"
            )
        except (TypeError, ValueError):
            pass
    terms = list(expl.get("terms") or [])
    if not terms and expl.get("intercept") is None:
        return ""
    parts = []
    for t in terms[:10]:
        if not isinstance(t, dict):
            continue
        label = t.get("label") or t.get("key") or "?"
        try:
            parts.append(
                f"{label}(β={float(t.get('beta') or 0):+.3f}, "
                f"z={float(t.get('z') or 0):+.2f} → {float(t.get('contrib') or 0):+.3f})"
            )
        except (TypeError, ValueError):
            continue
    try:
        alpha = float(expl.get("intercept") or 0.0)
        total = float(expl.get("total") or 0.0)
    except (TypeError, ValueError):
        return ""
    body = " + ".join(parts) if parts else "…"
    return f"ŷ_τ = α{alpha:+.3f} + {body} = {total:.3f}%"


def ensure_formula_terms_tau(item: Optional[dict]) -> Optional[Dict[str, Any]]:
    """保证 tip 有 ŷ_τ 组成：有 Z 实值时重拆；勿沿用「全缺特征」旧戳。"""
    if not isinstance(item, dict):
        return None
    existing = item.get("formula_terms_tau") or item.get("score_formula_terms_tau")

    feats: Dict[str, Any] = {}
    ft = item.get("features_tau")
    if isinstance(ft, dict):
        for k, v in ft.items():
            if v is not None and v != "":
                feats[k] = v
    gap = item.get("gap_pct")
    if gap is not None and gap != "":
        feats["gap_pct"] = gap
        feats.setdefault("open_gap", gap)
    for k in (
        "sector_gap_breadth",
        "theme_day",
        "gap_atr",
        "gap_vs_sector",
        "yclose_loc",
        "mom3_pct",
        "tau_lag1",
        "tau_ma5",
        "ret_open_to_tau",
        "loc_hl",
        "ret_last_15m",
        "sector_ret_to_tau",
    ):
        v = item.get(k)
        if v is not None and v != "":
            feats.setdefault(k, v)

    try:
        from core.research.tau_ridge import TAU_Z_FEATURES

        z_keys = set(TAU_Z_FEATURES) | {"open_gap"}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        z_keys = {
            "gap_pct",
            "open_gap",
            "sector_gap_breadth",
            "theme_day",
            "gap_atr",
            "gap_vs_sector",
            "ret_open_to_tau",
            "tau_lag1",
            "tau_ma5",
            "loc_hl",
            "ret_last_15m",
            "sector_ret_to_tau",
        }
    has_z = any(feats.get(k) is not None for k in z_keys)

    def _stale_imputed_z(expl: Optional[dict]) -> bool:
        """簿上已有组成，但缺口等 Z 后来才写入 → 须重拆。"""
        if not isinstance(expl, dict):
            return True
        terms = expl.get("terms") or []
        if not terms:
            return True
        if not has_z:
            return False
        z_in_terms = False
        for t in terms:
            if not isinstance(t, dict):
                continue
            key = str(t.get("key") or "")
            if key not in z_keys:
                continue
            z_in_terms = True
            if t.get("note") and feats.get(key) is not None:
                return True
        return has_z and not z_in_terms

    def _stale_missing_pack(expl: Optional[dict]) -> bool:
        """features_tau 已有开盘→τ / 分钟小包，组成表仍缺这些键 → 须重拆。"""
        if not isinstance(expl, dict):
            return False
        keys = {
            str(t.get("key") or "")
            for t in (expl.get("terms") or [])
            if isinstance(t, dict) and not t.get("note")
        }
        # 注入后常补上、β 偏小的项；只看「开盘→τ」会漏 HL / 截面。
        pack_keys = (
            "ret_open_to_tau",
            "loc_hl",
            "ret_last_15m",
            "sector_ret_to_tau",
            "sector_gap_breadth",
            "gap_vs_sector",
            "gap_pct",
            "theme_day",
            "gap_atr",
            "range_pct",
            "up_extent",
            "down_extent",
        )
        return any(feats.get(k) is not None and k not in keys for k in pack_keys)

    if (
        isinstance(existing, dict)
        and isinstance(existing.get("terms"), list)
        and existing["terms"]
        and not _stale_imputed_z(existing)
        and not _stale_missing_pack(existing)
    ):
        return existing

    try:
        feats.update(recover_sub_scores_for_tau(item))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    if feats:
        try:
            from core.research.tau_ridge import explain_tau_prediction

            expl = explain_tau_prediction(feats)
            if expl is not None:
                return expl
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            pass
    if (
        isinstance(existing, dict)
        and isinstance(existing.get("terms"), list)
        and existing["terms"]
    ):
        return existing
    return None


def rem_factor_coefficients_public() -> Dict[str, float]:
    """τ Ridge β 快照（tip「τ 因子系数」）。"""
    try:
        from core.research.tau_ridge import load_tau_model

        doc = load_tau_model() or {}
        coefs = (doc.get("return_model") or {}).get("coefficients") or {}
        out: Dict[str, float] = {}
        for k, v in coefs.items():
            if v is None:
                continue
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        return {}

