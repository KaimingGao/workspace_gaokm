"""Replay open-day scoring and prefix ŷ_τc rescore.

一日分数包三态（禁止在第三处偷偷 ``apply_*``）：

1. **Open bundle** — ``_score_open_day`` / ``_attach_open_yhat_heads`` 写主头 +
   oo_rank + τc_rank，再 ``save_day``。唯一开盘写头入口。
2. **Cache hit 09:30** — 整包复用；仅当可交易集 **全缺** ``y_tc_rank`` 时
   ``maybe_backfill_open_tc_rank`` 补挂。oo_rank 不补（slim 已丢 ``sub_scores``）。
3. **Prefix（fill_clock≠09:30）** — ``refresh_tau_heads_after_prefix``：只刷新
   ŷ_τc（及对照）并重挂 τc_rank（``zscore_codes``=可交易，避免 B/C 开盘特征混 z）；
   ŷ_oo / oo_rank 冻结开盘信息集。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Collection, Dict, List, Optional, Sequence, Tuple

from core.backtest.replay_contrib import _fpx
from core.backtest.replay_fill import clamp_replay_fill_clock
from core.backtest.replay_sim_rows import _merge_aux_yhat

logger = logging.getLogger(__name__)

def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def mock_quote_from_bar(
    bar: Optional[dict],
    *,
    prev_bar: Optional[dict] = None,
    px_field: str = "open",
) -> dict:
    """日线 → 调仓/盯市用行情 dict（含 prev_close / change_raw，供涨跌停）。"""
    if not isinstance(bar, dict):
        return {}
    try:
        px = float(bar.get(px_field) or bar.get("close") or 0)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        try:
            px = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            px = 0.0
    prev = None
    if isinstance(prev_bar, dict):
        try:
            prev = float(prev_bar.get("close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    if prev is None:
        try:
            prev = float(bar.get("pre_close") or bar.get("prev_close") or 0) or None
        except (TypeError, ValueError):
            prev = None
    change = 0.0
    if prev and prev > 0 and px > 0:
        change = (px / prev - 1.0) * 100.0
    open_px = None
    try:
        open_px = float(bar.get("open") or 0) or None
    except (TypeError, ValueError):
        open_px = None
    return {
        "success": True,
        "price_raw": px,
        "price": px,
        "open_raw": open_px if open_px is not None else px,
        "open": open_px if open_px is not None else px,
        "prev_close": prev,
        "pre_close": prev,
        "yesterday_close": prev,
        "change_raw": round(change, 4),
        "change_pct": round(change, 4),
    }


def _make_batch_query(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day: str,
    *,
    px_field: str,
) -> Callable[[List[str]], Dict[str, dict]]:
    date_i = {d: i for i, d in enumerate(dates)}
    i = date_i.get(str(day))

    def _batch(codes: List[str]) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for code in codes or []:
            c = str(code or "").strip()
            if not c:
                continue
            dm = date_maps.get(c) or {}
            bar = dm.get(str(day))
            prev_bar = None
            if i is not None and i > 0:
                prev_bar = dm.get(dates[i - 1])
            q = mock_quote_from_bar(bar, prev_bar=prev_bar, px_field=px_field)
            if q:
                q["stock_code"] = c
            out[c] = q
        return out

    return _batch


def _make_fill_batch_query(
    prices: Dict[str, float],
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    day: str,
) -> Callable[[List[str]], Dict[str, dict]]:
    """盯市/涨跌停用调仓钟成交价，昨收仍取日 K。"""
    base = _make_batch_query(date_maps, dates, day, px_field="open")

    def _batch(codes: List[str]) -> Dict[str, dict]:
        out = base(codes)
        for c, q in list(out.items()):
            px = _fpx((prices or {}).get(c))
            if px is None or not q:
                continue
            q["price_raw"] = px
            q["price"] = px
        return out

    return _batch


def _load_replay_global_model() -> Any:
    try:
        from core.research.oo_ridge import load_scoring_return_model

        # 跟回测「模型」下拉：研究 → research（缺则 draft 预览）；执行 → active。不跨槽回落。
        model, meta = load_scoring_return_model()
        if meta.get("ok") and model is not None:
            return model
    except Exception:  # noqa: BLE001
        logger.debug("load replay global return model failed", exc_info=True)
    return None


def _required_factor_keys_from_return_models(
    global_model: Any = None,
) -> List[str]:
    """与 score_stock / T0 同源：ŷ β 键并集，供 FS0 补算（含 raw_aoo_*）。
    """
    keys: List[str] = []
    seen = set()
    models: List[Any] = []
    if global_model is not None:
        models.append(global_model)
    for m in models:
        coefs = getattr(m, "coefficients", None)
        if not isinstance(coefs, dict):
            continue
        for k in coefs.keys():
            kk = str(k).strip()
            if kk and kk not in seen:
                seen.add(kk)
                keys.append(kk)
    return keys


def _score_codes_fingerprint(codes: Optional[Collection[str]]) -> str:
    """允许打分的代码集。None（全员打分）返回空串，不改缓存键。"""
    import hashlib

    from core.research.feature_standardize import normalize_score_codes

    allow = normalize_score_codes(codes)
    if allow is None:
        return ""
    raw = ",".join(sorted(allow))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _stamp_tradable_hard_reject(
    scored: Sequence[dict],
    tradable_codes: Optional[Collection[str]],
    *,
    reason: str,
) -> None:
    """分档外的名字留在截面特征里（供 z），但不许新开；已持仓则清仓。"""
    if tradable_codes is None:
        return
    allow = {str(c).strip() for c in tradable_codes if str(c or "").strip()}
    why = str(reason or "").strip() or "可预测性分档外"
    for it in scored or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code or code in allow or it.get("hard_reject"):
            continue
        it["hard_reject"] = True
        it["reject_reason"] = why
        it["predictability_tier_block"] = True


def _attach_open_yhat_heads(
    entries: List[dict],
    *,
    quotes: Dict[str, dict],
    windows: Dict[str, List[dict]],
    global_model: Any = None,
    co_model_doc: Any = None,
    tau_model_doc: Any = None,
    cfg: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
    score_codes: Optional[Collection[str]] = None,
) -> List[dict]:
    """开盘包唯一写头：heuristic→ŷ_oo；09:30 PIT ŷ_τc；ŷ_co；oo_rank；τc_rank。

    对照头 ŷ_hl 走开盘 Z（与 live ``score_stock`` 同路径），不进 y_oo_fuse。
    调仓回测不挂 ŷ_τ30/60/90 / ŷ_τw。nowcast 已退役，不再写 y_nowcast。
    ``score_codes`` 给定时，截面 z 用整批特征，ŷ 与名次只写这份名单。
    产出进 ``save_day``；09:30 命中勿再挂 rank（见模块契约）。
    """
    from core.signal.return_score import apply_predicted_scores

    items = list(entries)
    if global_model is not None:
        items = apply_predicted_scores(
            items, global_model, write_rank_score=False, score_codes=score_codes
        )

    rem_doc = tau_model_doc
    if rem_doc is None:
        try:
            from core.research.tc_ridge import load_tau_model

            rem_doc = load_tau_model()
        except Exception:  # noqa: BLE001
            rem_doc = None
            logger.debug("load_tau_model failed in replay yhat heads", exc_info=True)

    try:
        from core.signal.dual_score import (
            align_trade_score_fields,
            attach_dual_score_pit,
        )
    except Exception:  # noqa: BLE001
        align_trade_score_fields = None  # type: ignore[assignment]
        attach_dual_score_pit = None  # type: ignore[assignment]
        logger.debug("dual_score import failed", exc_info=True)

    try:
        from core.research.co_panel import build_co_features_from_quote_bars
        from core.research.co_ridge import predict_co_from_features
        from core.signal.dual_score.co import apply_co_score_fields
    except Exception:  # noqa: BLE001
        build_co_features_from_quote_bars = None  # type: ignore[assignment]
        predict_co_from_features = None  # type: ignore[assignment]
        apply_co_score_fields = None  # type: ignore[assignment]
        logger.debug("on-head imports failed", exc_info=True)

    try:
        from core.t0.score_policy import _attach_y_path_to_item
    except Exception:  # noqa: BLE001
        _attach_y_path_to_item = None  # type: ignore[assignment]
        logger.debug("path-head import failed", exc_info=True)

    oo_rank_doc = None
    apply_oo_rank_scores = None
    prefer_research = True
    try:
        from core.research.holdout import current_scoring_model_role
        from core.research.oo_rank_lambdarank import (
            apply_oo_rank_scores as _apply_oo_rank,
        )
        from core.research.oo_rank_lambdarank import (
            load_oo_rank_model,
        )

        apply_oo_rank_scores = _apply_oo_rank
        prefer_research = current_scoring_model_role() != "live"
        # 按回测「模型」槽严格加载；缺该槽则本头不挂分，不静默换另一槽。
        oo_rank_doc = load_oo_rank_model(prefer_research=prefer_research)
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank import failed", exc_info=True)

    # ŷ_oo_rank：整日截面批打分后再编 1..n 名次；不改 y_oo_fuse；过入场后作买序
    if apply_oo_rank_scores is not None and oo_rank_doc is not None:
        try:
            apply_oo_rank_scores(
                [it for it in items if isinstance(it, dict)],
                model_doc=oo_rank_doc,
                score_codes=score_codes,
            )
        except Exception:  # noqa: BLE001
            logger.debug("attach y_oo_rank day batch failed", exc_info=True)

    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        pred = it.get("predicted_score")
        if pred is not None:
            it.setdefault("predicted_score_oo", pred)
            it.setdefault("y_oo", pred)
        if attach_dual_score_pit is not None and code:
            try:
                xs: Dict[str, Any] = {}
                try:
                    from core.t0.score_policy import tau_pool_day_score_kwargs

                    xs = tau_pool_day_score_kwargs(tau_pool_day, code) or {}
                except Exception:  # noqa: BLE001
                    logger.debug("replay open tau pool kwargs failed", exc_info=True)
                    xs = {}
                if xs.get("pool_gaps"):
                    it["_pool_gaps"] = xs.get("pool_gaps")
                q = quotes.get(code) or {}
                trade_day = str(
                    q.get("date") or q.get("trade_date") or it.get("date") or ""
                )[:10]
                attach_dual_score_pit(
                    it,
                    quote=q,
                    bars=windows.get(code) or [],
                    config=cfg,
                    rem_model_doc=rem_doc,
                    use_minute_tau=False,
                    fuse_intraday=True,
                    sector_gap_breadth=xs.get("sector_gap_breadth"),
                    sector_gap_median=xs.get("sector_gap_median"),
                    trade_day=trade_day if len(trade_day) >= 10 else None,
                )
            except Exception:  # noqa: BLE001
                logger.debug("attach open yhat heads failed for %s", code, exc_info=True)
        if align_trade_score_fields is not None:
            try:
                align_trade_score_fields(
                    it, write_score=False, refresh_window=False, config=cfg
                )
            except Exception:  # noqa: BLE001
                logger.debug("align_trade_score_fields failed", exc_info=True)
        it.pop("predicted_score_nowcast", None)
        it.pop("y_nowcast", None)
        it.pop("y_nc", None)
        if code:
            q = quotes.get(code) or {}
            d = str(it.get("date") or it.get("as_of") or it.get("trade_date") or "")[:10]
            if len(d) < 10:
                d = str(q.get("date") or q.get("trade_date") or "")[:10]
            if len(d) >= 10:
                it.setdefault("date", d)
                it.setdefault("as_of", d)
                it.setdefault("trade_date", d)
        if _attach_y_path_to_item is not None and code:
            try:
                _attach_y_path_to_item(
                    it,
                    hist_bars=windows.get(code) or [],
                    allow_open_z=True,
                    include_tau_horizons=False,
                )
            except Exception:  # noqa: BLE001
                logger.debug("attach open y_hl failed for %s", code, exc_info=True)
        if (
            apply_co_score_fields is None
            or build_co_features_from_quote_bars is None
            or predict_co_from_features is None
            or not code
        ):
            continue
        quote = quotes.get(code) or {}
        window = windows.get(code) or []
        try:
            from core.signal.dual_score.co import overlay_co_cross_section

            tau_feats = (
                it.get("features_tau")
                if isinstance(it.get("features_tau"), dict)
                else None
            )
            # L1 已灌 features_co：跳过 quote+bars 重建，只叠当日 τ 截面广度
            lake_co = (
                it.get("features_co")
                if str(it.get("_score_source") or "") == "feature_lake"
                and isinstance(it.get("features_co"), dict)
                and it.get("features_co")
                else None
            )
            if lake_co is not None:
                co_feats = overlay_co_cross_section(dict(lake_co), tau_feats)
            else:
                gap = None
                if quote:
                    try:
                        gap = float(quote.get("change_raw"))
                    except (TypeError, ValueError):
                        gap = None
                co_feats = build_co_features_from_quote_bars(
                    quote,
                    window,
                    gap_pct=gap,
                    open_t=quote.get("open_raw") or quote.get("open"),
                    prev_close=quote.get("prev_close") or quote.get("pre_close"),
                    trade_date=quote.get("date") or quote.get("trade_date"),
                )
                if not co_feats:
                    continue
                co_feats = overlay_co_cross_section(co_feats, tau_feats)
            co_yhat = predict_co_from_features(co_feats, model_doc=co_model_doc)
            if co_yhat is not None:
                it["y_co_source"] = "ridge"
            apply_co_score_fields(
                it,
                co_yhat=co_yhat,
                feats=co_feats,
                co_model_doc=co_model_doc,
                source="ridge" if co_yhat is not None else "",
            )
        except Exception:  # noqa: BLE001
            logger.debug("attach y_co failed for %s", code, exc_info=True)
    try:
        from core.research.batch_score import (  # noqa: I001
            enabled as batch_score_enabled,
            rescore_fitted_cross_section_batched,
        )
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        day_key = ""
        for it in items:
            if isinstance(it, dict):
                day_key = str(it.get("as_of") or it.get("date") or "")[:10]
                if len(day_key) >= 10:
                    break
        if batch_score_enabled():
            rescore_fitted_cross_section_batched(
                items,
                score_codes=score_codes,
                date=day_key or None,
                clock="open",
                persist_cube=True,
            )
        else:
            rescore_fitted_cross_section(items, score_codes=score_codes)
    except Exception:  # noqa: BLE001
        logger.debug("replay cross-section rescore failed", exc_info=True)
    # ŷ_τc_rank：须在 features_tau 挂好之后；与 oo_rank 同槽，一并进开盘缓存。
    try:
        from core.research.tc_rank import apply_tc_rank_scores, load_tc_rank_model

        tc_rank_doc = load_tc_rank_model(prefer_research=prefer_research)
        if tc_rank_doc is not None:
            apply_tc_rank_scores(
                [it for it in items if isinstance(it, dict)],
                model_doc=tc_rank_doc,
                score_codes=score_codes,
            )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_tc_rank day batch failed", exc_info=True)

    try:
        from core.signal.cross_section_rescore import drop_unscored_heads

        drop_unscored_heads(items, score_codes)
    except Exception:  # noqa: BLE001
        logger.debug("replay drop unscored heads failed", exc_info=True)
    return items


def _score_open_day(
    *,
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    day_i: int,
    max_window: int,
    horizon_days: int,
    cfg: Optional[dict],
    global_model: Any = None,
    co_model_doc: Any = None,
    tau_model_doc: Any = None,
    tau_pool_day: Optional[dict] = None,
    required_factor_keys: Optional[List[str]] = None,
    score_codes: Optional[Collection[str]] = None,
) -> List[dict]:
    """9:30 信息集：窗口截至昨收，报价用今开（不把今日收盘喂进特征）。

    优先 L1 Feature Lake 灌特征；miss 再 ``score_window_as_item``。
    """
    if day_i <= 0:
        return []
    try:
        from core.research.lake_replay import score_open_day_entries

        entries, quotes, windows, hits, miss = score_open_day_entries(
            stock_bars=stock_bars,
            dates=dates,
            date_maps=date_maps,
            day_i=day_i,
            max_window=max_window,
            horizon_days=horizon_days,
            cfg=cfg,
            required_factor_keys=required_factor_keys,
            window_for_code=_window_for_code,
            mock_quote_from_bar=mock_quote_from_bar,
        )
        if hits or miss:
            logger.debug(
                "replay open day lake hits=%s miss=%s day=%s",
                hits,
                miss,
                dates[day_i] if day_i < len(dates) else "",
            )
    except Exception:  # noqa: BLE001
        logger.debug("replay lake hydrate failed; fallback window score", exc_info=True)
        from core.signal.cross_section_batch import score_window_as_item

        entries = []
        quotes = {}
        windows = {}
        prev_i = day_i - 1
        today = dates[day_i]
        req_keys = list(required_factor_keys or []) or None
        for code in stock_bars:
            window = _window_for_code(code, dates, date_maps, prev_i, max_window)
            if len(window) < 2:
                continue
            dm = date_maps.get(code) or {}
            today_bar = dm.get(today)
            prev_bar = dm.get(dates[prev_i])
            quote = mock_quote_from_bar(today_bar, prev_bar=prev_bar, px_field="open")
            if quote:
                quote["date"] = today
                quote["trade_date"] = today
            item = score_window_as_item(
                code,
                window,
                horizon_days=horizon_days,
                quote=quote,
                config=cfg,
                required_factor_keys=req_keys,
            )
            if item:
                entries.append(item)
                quotes[str(code)] = quote or {}
                windows[str(code)] = window
    if not entries:
        return []
    return _attach_open_yhat_heads(
        entries,
        quotes=quotes,
        windows=windows,
        global_model=global_model,
        co_model_doc=co_model_doc,
        tau_model_doc=tau_model_doc,
        cfg=cfg,
        tau_pool_day=tau_pool_day,
        score_codes=score_codes,
    )


# 前缀写入 ŷ_τc 与 τ 特征；不覆盖 ŷ_oo。
_OC_PREFIX_KEYS = (
    "features_tau",
    "features_tau_fill",
    "formula_terms_tau",
    "score_formula_terms_tau",
    "factor_coefficients_tau",
    "as_of_tau",
    "_score_source",
    "_score_prefix_hm",
    "_score_prefix_bars",
    "remaining_oc",
    "r_hat",
    "residual",
    "y_τc",
    "predicted_score_τc",
    "y_τc_ridge",
    "y_τc_source",
)


def _replay_hist_and_day_bar(
    code: str,
    *,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    day_i: int,
    max_window: int,
) -> Tuple[List[dict], Optional[dict]]:
    """rescore 用：hist=昨收窗；day_bar=今日 K（补 prev_close）。"""
    if day_i <= 0:
        return [], None
    hist = _window_for_code(code, dates, date_maps, day_i - 1, max_window)
    bar = (date_maps.get(code) or {}).get(dates[day_i])
    if not isinstance(bar, dict):
        return hist, None
    day_bar = dict(bar)
    if not day_bar.get("date"):
        day_bar["date"] = dates[day_i]
    if day_bar.get("prev_close") is None and hist:
        prev_c = (hist[-1] or {}).get("close")
        if prev_c is not None:
            day_bar["prev_close"] = prev_c
    return hist, day_bar


def _apply_prefix_oc(item: dict, live: dict, cfg: Optional[dict]) -> bool:
    """把前缀因果 ŷ_τc 写进开盘行，重算 y_oo_fuse；ŷ_oo 保持开盘头。

    对照头 y_hl 随前缀结果覆盖（缺分则保留开盘 Z）。调仓回测不挂 ŷ_τw / ŷ_τ30/60/90。
    接纳 ``prefix_causal``，以及已注入前缀小包并重拆 ŷ_τc 的 ``prefix_open_fallback``
    （否则 fallback 上算好的盘中 ŷ_τc 会被丢掉，09:50 仍用开盘 Z）。
    """
    from core.signal.yhat_windows import pick_y_oo, stamp_window_scores, write_y_oo_fuse

    src = str(live.get("_score_source") or "")
    feats = live.get("features_tau") if isinstance(live.get("features_tau"), dict) else {}
    if src == "prefix_causal" or (
        src == "prefix_open_fallback"
        and isinstance(feats, dict)
        and feats.get("ret_open_to_tau") is not None
        and (live.get("y_τc") is not None or live.get("predicted_score_τc") is not None)
    ):
        pass
    else:
        return False
    y_oo = pick_y_oo(item)
    y_oo_field = item.get("predicted_score_eod")
    if y_oo_field is None:
        y_oo_field = item.get("predicted_score")
    for k in _OC_PREFIX_KEYS:
        if k in live:
            item[k] = live.get(k)
    _merge_aux_yhat(item, live)
    if y_oo is not None:
        item["y_oo"] = y_oo
        ps = y_oo_field if y_oo_field is not None else y_oo
        item["predicted_score_oo"] = ps
        item["predicted_score"] = ps
    if item.get("y_τc") is None and item.get("predicted_score_τc") is not None:
        from core.signal.yhat_windows import write_y_τc

        write_y_τc(item, item.get("predicted_score_τc"))
    win = stamp_window_scores(item, cfg)
    for k, v in win.items():
        if v is not None:
            item[k] = v
    fuse = item.get("y_oo_fuse")
    if fuse is not None:
        write_y_oo_fuse(item, fuse)
    return item.get("y_τc") is not None


def rescore_replay_y_τc_at_clock(
    scored: Sequence[dict],
    *,
    clock: str,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    minute_maps: Dict[str, Dict[str, List[dict]]],
    max_window: int,
    day_i: int,
    cfg: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
    score_codes: Optional[Collection[str]] = None,
) -> Tuple[List[dict], int]:
    """成交钟>09:30：用截至该钟的 5m 前缀重算 ŷ_τc（与做 T rescore 同路径）。

    09:30 或无前缀则保持开盘 Z。返回 (行, 成功重算数)。
    ``score_codes`` 给定时，池子特征仍整批进 μ/σ，只重算名单内的 ŷ_τc。
    调仓回测请用 ``refresh_tau_heads_after_prefix``（本函数 + 重挂 τc_rank）。
    """
    items = [dict(it) if isinstance(it, dict) else it for it in (scored or [])]
    head = clamp_replay_fill_clock(clock)
    if head == "09:30" or day_i <= 0 or day_i >= len(dates):
        return items, 0
    from core.research.feature_standardize import normalize_score_codes
    from core.t0.score_policy import (
        _minute_bars_until_hm,
        rescore_scores_at_fixed_prefix,
        temporary_pool_feature_rows,
    )

    allow = normalize_score_codes(score_codes)
    day = dates[day_i]
    n_ok = 0
    # 分钟钟 L1（tc_HHMM）：先批量取行，灌进池特征再 refresh ŷ_τc
    lake_tc: Dict[str, Dict[str, float]] = {}
    try:
        from core.research.lake_replay import load_tau_feats_map, try_prefix_live_from_lake

        codes_all = [
            str(it.get("stock_code") or "").strip()
            for it in items
            if isinstance(it, dict) and str(it.get("stock_code") or "").strip()
        ]
        lake_tc = load_tau_feats_map(codes_all, day, tau_hm=head)
    except Exception:  # noqa: BLE001
        logger.debug("replay prefix L1 load failed", exc_info=True)
        lake_tc = {}
        try_prefix_live_from_lake = None  # type: ignore[assignment]

    pool_items: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if code and code in lake_tc:
            patched = dict(it)
            patched["features_tau"] = dict(lake_tc[code])
            pool_items.append(patched)
        else:
            pool_items.append(it)

    with temporary_pool_feature_rows(day, pool_items):
        for it in items:
            if not isinstance(it, dict):
                continue
            code = str(it.get("stock_code") or "").strip()
            if not code:
                continue
            if allow is not None and code not in allow:
                continue
            if try_prefix_live_from_lake is not None and code in lake_tc:
                try:
                    live = try_prefix_live_from_lake(
                        dict(it),
                        code=code,
                        day=day,
                        tau_hm=head,
                        feats=lake_tc.get(code),
                        tau_pool_day=tau_pool_day,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "replay prefix L1 hydrate failed %s %s", code, day, exc_info=True
                    )
                    live = None
                if live is not None and _apply_prefix_oc(it, live, cfg):
                    n_ok += 1
                    continue
            mins = (minute_maps.get(code) or {}).get(day) or []
            prefix = _minute_bars_until_hm(mins, tau_hm=head)
            if len(prefix) < 1:
                continue
            hist, day_bar = _replay_hist_and_day_bar(
                code,
                dates=dates,
                date_maps=date_maps,
                day_i=day_i,
                max_window=max_window,
            )
            if not isinstance(day_bar, dict) or len(hist) < 2:
                continue
            try:
                live = rescore_scores_at_fixed_prefix(
                    stock_code=code,
                    minute_prefix=prefix,
                    day_bar=day_bar,
                    hist_bars=hist,
                    tau_pool_day=tau_pool_day,
                    fuse_intraday=True,
                    open_snap=dict(it),
                    include_tau_horizons=False,
                )
            except Exception:  # noqa: BLE001
                logger.debug("replay prefix y_τc rescore failed %s %s", code, day, exc_info=True)
                continue
            if _apply_prefix_oc(it, live if isinstance(live, dict) else {}, cfg):
                n_ok += 1
    return items, n_ok


def refresh_tau_heads_after_prefix(
    scored: Sequence[dict],
    *,
    clock: str,
    dates: Sequence[str],
    date_maps: Dict[str, Dict[str, dict]],
    minute_maps: Dict[str, Dict[str, List[dict]]],
    max_window: int,
    day_i: int,
    cfg: Optional[dict] = None,
    tau_pool_day: Optional[dict] = None,
    score_codes: Optional[Collection[str]] = None,
    tc_rank_doc: Any = None,
) -> Tuple[List[dict], int]:
    """晚钟：前缀重算 ŷ_τc + 重挂 τc_rank（开盘 ŷ_oo / oo_rank 不动）。

    τc_rank 的截面 z 缩到 ``score_codes``（可交易），避免未前缀 B/C 的开盘
    ``features_tau`` 混进 pool μ/σ。返回 (行, 前缀成功数)。
    """
    items, n_pref = rescore_replay_y_τc_at_clock(
        scored,
        clock=clock,
        dates=dates,
        date_maps=date_maps,
        minute_maps=minute_maps,
        max_window=max_window,
        day_i=day_i,
        cfg=cfg,
        tau_pool_day=tau_pool_day,
        score_codes=score_codes,
    )
    if tc_rank_doc is None:
        return items, n_pref
    try:
        from core.research.tc_rank import apply_tc_rank_scores

        # 前缀后：写分与 z 宇宙都锁可交易集（混合钟修复）
        apply_tc_rank_scores(
            items,
            model_doc=tc_rank_doc,
            score_codes=score_codes,
            zscore_codes=score_codes,
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_tc_rank after prefix failed", exc_info=True)
    return items, n_pref


def maybe_backfill_open_tc_rank(
    scored: Sequence[dict],
    *,
    tc_rank_doc: Any = None,
    score_codes: Optional[Collection[str]] = None,
) -> List[dict]:
    """09:30 缓存命中：仅当可交易集 **全缺** y_tc_rank 时整批补挂。

    部分有名次则不动（避免半残日与开盘包劈叉时 silently 混算）。
    oo_rank 不补：slim 已丢 ``sub_scores``，缺则依赖 run_key/schema miss。
    """
    items = list(scored or [])
    if tc_rank_doc is None:
        return items
    from core.research.feature_standardize import normalize_score_codes

    allow = normalize_score_codes(score_codes)
    candidates: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code:
            continue
        if allow is not None and code not in allow:
            continue
        # 无 features_tau 的持仓 stub 不计入「应有名次」
        feats = it.get("features_tau")
        if not isinstance(feats, dict) or not feats:
            continue
        candidates.append(it)
    if not candidates:
        return items
    if any(it.get("y_tc_rank") is not None for it in candidates):
        return items
    try:
        from core.research.tc_rank import apply_tc_rank_scores

        apply_tc_rank_scores(
            items, model_doc=tc_rank_doc, score_codes=score_codes
        )
    except Exception:  # noqa: BLE001
        logger.debug("backfill y_tc_rank on cache hit failed", exc_info=True)
    return items


def _items_from_injected_scores(rows: Sequence[dict]) -> List[dict]:
    """测试/日报注入行：ŷ 当作 y_oo_fuse。"""
    out: List[dict] = []
    for it in rows or []:
        if not isinstance(it, dict) or not it.get("stock_code"):
            continue
        row = dict(it)
        yf = row.get("y_oo_fuse")
        if yf is None:
            yf = row.get("predicted_score")
        if yf is None:
            yf = row.get("predicted_score_eod")
        if yf is None:
            yf = row.get("score")
        if yf is not None:
            row["y_oo_fuse"] = yf
        out.append(row)
    return out
