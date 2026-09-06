"""横截面 TopK 调仓回测（研究用，非模拟仓账本）。

默认等权；可选 score_budget / risk_parity_lite（与纸面同源）。
历史文件名曾为 portfolio.py；勿与 paper.json 持仓混淆。
"""


import logging
from functools import lru_cache
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.backtest.engine import _mock_quote_from_bars, _trade_metrics

logger = logging.getLogger(__name__)


from core.backtest.topk_weights import WEIGHT_MODES


def _fill_sample_score_fields(
    rank_score: Any, tip_kw: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """成交样本分数字段：score/predicted=ŷ%；heuristic 另存，不混进 ŷ 列。"""
    tip = tip_kw if isinstance(tip_kw, dict) else {}
    heu = tip.get("heuristic_score")
    try:
        heu_f = float(heu) if heu is not None else None
    except (TypeError, ValueError):
        heu_f = None
    pred = tip.get("predicted_score")
    blend = tip.get("predicted_score_blend")
    try:
        from core.signal.score_display import looks_like_legacy_heuristic_score

        def _yhat_or_none(v: Any) -> Optional[float]:
            if v is None or v == "":
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            if looks_like_legacy_heuristic_score(f, item=tip):
                return None
            return f

    except Exception:  # noqa: BLE001 — 新函数降级兼容，debug 级别即可
        logger.debug("score_display.looks_like_legacy_heuristic_score unavailable, falling back", exc_info=True)

        def _yhat_or_none(v: Any) -> Optional[float]:
            if v is None or v == "":
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            return None if abs(f) >= 10.0 else f

    yhat = _yhat_or_none(rank_score)
    if yhat is None:
        yhat = _yhat_or_none(blend)
    if yhat is None:
        yhat = _yhat_or_none(pred)
    if heu_f is None:
        # 排序分若是 0–100，回收为 heuristic
        try:
            rs = float(rank_score) if rank_score is not None else None
        except (TypeError, ValueError):
            rs = None
        if rs is not None:
            try:
                from core.signal.score_display import looks_like_legacy_heuristic_score

                if looks_like_legacy_heuristic_score(rs, item=tip):
                    heu_f = rs
            except Exception:  # noqa: BLE001 — 打分显示兼容降级
                logger.debug("looks_like_legacy_heuristic_score unavailable in heuristic fallback", exc_info=True)
                if abs(rs) >= 10.0:
                    heu_f = rs
    out: Dict[str, Any] = {
        "score": yhat,
        "predicted_score": _yhat_or_none(pred) if pred is not None else yhat,
        "predicted_score_blend": _yhat_or_none(blend) if blend is not None else yhat,
    }
    if heu_f is not None:
        out["heuristic_score"] = round(heu_f, 4)
    return out


def _close_return_pct(
    date_maps: Dict[str, Dict[str, dict]],
    code: str,
    start_date: str,
    end_date: str,
) -> Optional[float]:
    """两日收盘价简单收益（百分点）；缺 bar 则 None。"""
    dm = date_maps.get(code) or {}
    a = dm.get(start_date)
    b = dm.get(end_date)
    if not a or not b:
        return None
    try:
        pa = float(a.get("close") or 0)
        pb = float(b.get("close") or 0)
    except (TypeError, ValueError):
        return None
    if pa <= 0:
        return None
    return (pb / pa - 1.0) * 100.0


def _fmt_factor_weights(weights: Optional[Dict[str, Any]], *, limit: int = 8) -> str:
    if not isinstance(weights, dict) or not weights:
        return ""
    parts: List[str] = []
    for k, v in sorted(weights.items(), key=lambda kv: (-float(kv[1] or 0), str(kv[0]))):
        try:
            parts.append(f"{k} {float(v):.2f}")
        except (TypeError, ValueError):
            continue
        if len(parts) >= limit:
            break
    return " / ".join(parts)


def _score_factor_weight_meta(
    code: str,
    *,
    global_weights: Optional[Dict[str, Any]] = None,
    cluster_active: Any = None,
) -> Dict[str, Any]:
    """分组标签 / 展示权（|β| 派生）；选股真源仍是 return_model。"""
    try:
        from core.signal.cluster.live import lookup_code_weights

        mapped = lookup_code_weights(str(code), active=cluster_active)
    except Exception:  # noqa: BLE001 — 分组查询失败，不阻塞回测
        logger.debug("lookup_code_weights failed for %s", code, exc_info=True)
        mapped = None
    if mapped and isinstance(mapped.get("weights"), dict):
        fw = {str(k): float(v) for k, v in mapped["weights"].items() if v is not None}
        label = str(mapped.get("cluster_label") or mapped.get("label") or "?")
        src = str(mapped.get("weight_source") or f"cluster:{label}")
        body = _fmt_factor_weights(fw)
        note = f"分组 {label} 展示权(|β|)" + (f"：{body}" if body else "")
        return {
            "cluster_label": label,
            "score_weight_source": src,
            "factor_weights": fw,
            "factor_weights_note": note,
        }
    gw = dict(global_weights or {})
    body = _fmt_factor_weights(gw)
    note = "未入组 · 全局展示权" + (f"：{body}" if body else "")
    return {
        "cluster_label": None,
        "score_weight_source": "global",
        "factor_weights": gw,
        "factor_weights_note": note,
    }


def _resolve_bt_return_model(
    code: str,
    *,
    return_model: Any = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, str]:
    by_code = return_models_by_code or {}
    key = str(code or "").strip()
    model = by_code.get(key) if key and return_models_by_code is not None else None
    if model is not None:
        return model, "cluster_group_beta"
    if return_model is not None:
        # None = 未加载分组映射（回测 walk-forward）；空 dict 也走 walk-forward
        if not return_models_by_code:
            return return_model, "walk_forward"
        return return_model, "global"
    return None, ""


def _score_tooltip_meta(
    item: Optional[Dict[str, Any]],
    *,
    score: Any = None,
    code: str = "",
    return_model: Any = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    cluster_active: Any = None,
    global_weights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """与数据中心 / 交易执行同源的悬浮注释字段（ŷ · β · 分项表）。"""
    fw = _score_factor_weight_meta(
        code,
        global_weights=global_weights,
        cluster_active=cluster_active,
    )
    out: Dict[str, Any] = {
        **fw,
        "score_formula": "",
        "score_formula_terms": None,
        "factor_coefficients": {},
        "return_model_source": "",
        "score_reasons": list((item or {}).get("reasons") or []) if isinstance(item, dict) else [],
        "score_raw": None,
        "predicted_score": None,
    }
    if not isinstance(item, dict):
        return out

    try:
        if item.get("score_raw") is not None:
            out["score_raw"] = float(item["score_raw"])
    except (TypeError, ValueError):
        out["score_raw"] = None

    model, rms = _resolve_bt_return_model(
        code,
        return_model=return_model,
        return_models_by_code=return_models_by_code,
    )
    if not rms and item.get("return_model_source"):
        rms = str(item.get("return_model_source") or "")
    out["return_model_source"] = rms

    pred = item.get("predicted_score")
    # 禁止把 heuristic 0–100 排序分写进 predicted_score
    try:
        from core.signal.score_display import looks_like_legacy_heuristic_score

        if pred is None and score is not None and not looks_like_legacy_heuristic_score(
            float(score) if score is not None else None, item=item
        ):
            pred = score
    except Exception:  # noqa: BLE001 — predicted_score 兼容降级
        logger.debug("looks_like_legacy_heuristic_score unavailable, using raw score fallback", exc_info=True)
        if pred is None and score is not None:
            try:
                if abs(float(score)) < 10.0:
                    pred = score
            except (TypeError, ValueError):
                pass
    try:
        out["predicted_score"] = float(pred) if pred is not None else None
        if out["predicted_score"] is not None:
            from core.signal.score_display import looks_like_legacy_heuristic_score

            if looks_like_legacy_heuristic_score(out["predicted_score"], item=item):
                out["predicted_score"] = None
    except (TypeError, ValueError):
        out["predicted_score"] = None
    except Exception:  # noqa: BLE001 — 显示函数异常，回退基础 float 转换
        logger.debug("predicted_score heuristic guard failed, using basic conversion", exc_info=True)
        try:
            out["predicted_score"] = float(pred) if pred is not None else None
        except (TypeError, ValueError):
            out["predicted_score"] = None

    if model is not None:
        try:
            expl = model.explain_prediction(item.get("sub_scores") or {})
            out["score_formula_terms"] = expl
            coefs = dict(getattr(model, "coefficients", None) or {})
            coefs.pop("intercept", None)
            out["factor_coefficients"] = {str(k): float(v) for k, v in coefs.items()}
            from core.signal.score_view import build_score_formula

            out["score_formula"] = build_score_formula(
                {
                    "sub_scores": item.get("sub_scores"),
                    "return_model": model,
                }
            ) or ""
        except Exception:  # noqa: BLE001 — 分数字段/公式 best-effort，不阻塞主流程
            logger.debug("build_score_formula failed, skipping score_formula/coefficients fields", exc_info=True)
            pass
    return out


def _sim_trade_row(
    *,
    stock_code: str,
    score: Any,
    signal_date: str,
    entry_date: Optional[str],
    exit_date: Optional[str] = None,
    intent_price: Optional[float] = None,
    entry_price: Optional[float] = None,
    exit_price: Optional[float] = None,
    return_pct: Optional[float] = None,
    sector: Optional[str] = None,
    execution_mode: Optional[str] = None,
    exit_deferred_days: int = 0,
    status: str = "filled",
    port_return_pct: Optional[float] = None,
    port_gross_return_pct: Optional[float] = None,
    port_cost_pct: Optional[float] = None,
    cluster_label: Optional[str] = None,
    score_weight_source: Optional[str] = None,
    factor_weights: Optional[Dict[str, Any]] = None,
    factor_weights_note: Optional[str] = None,
    score_formula: Optional[str] = None,
    score_formula_terms: Optional[Dict[str, Any]] = None,
    factor_coefficients: Optional[Dict[str, Any]] = None,
    return_model_source: Optional[str] = None,
    score_reasons: Optional[List[str]] = None,
    score_raw: Optional[float] = None,
    heuristic_score: Optional[float] = None,
    predicted_score: Optional[float] = None,
    predicted_score_tau: Optional[float] = None,
    predicted_score_blend: Optional[float] = None,
    predicted_score_eod_rem: Optional[float] = None,
    realized_t1_to_tau: Optional[float] = None,
    score_rem: Optional[float] = None,
    gap_pct: Optional[float] = None,
    event_prior: Optional[Dict[str, Any]] = None,
    as_of_tau: Optional[str] = None,
    y_spec_tau: Optional[Dict[str, Any]] = None,
    features_tau: Optional[Dict[str, Any]] = None,
    formula_terms_tau: Optional[Dict[str, Any]] = None,
    dual_score_fusion: Optional[str] = None,
    dual_score_weights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    tip = {
        "stock_code": stock_code,
        "score": score,
        "predicted_score": predicted_score if predicted_score is not None else score,
        "predicted_score_tau": predicted_score_tau,
        "predicted_score_blend": predicted_score_blend,
        "predicted_score_eod_rem": predicted_score_eod_rem,
        "realized_t1_to_tau": realized_t1_to_tau,
        "score_rem": score_rem if score_rem is not None else predicted_score_tau,
        "gap_pct": gap_pct,
        "event_prior": event_prior,
        "as_of_tau": as_of_tau,
        "y_spec_tau": y_spec_tau,
        "features_tau": features_tau,
        "formula_terms_tau": formula_terms_tau,
        "dual_score_fusion": dual_score_fusion,
        "dual_score_weights": dual_score_weights,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "intent_price": intent_price,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "return_pct": return_pct,
        "sector": sector,
        "execution_mode": execution_mode,
        "exit_deferred_days": int(exit_deferred_days or 0),
        "status": status,
        "port_return_pct": port_return_pct,
        "port_gross_return_pct": port_gross_return_pct,
        "port_cost_pct": port_cost_pct,
        "cluster_label": cluster_label,
        "score_weight_source": score_weight_source,
        "factor_weights": factor_weights or {},
        "factor_weights_note": factor_weights_note or "",
        "score_formula": score_formula or "",
        "score_formula_terms": score_formula_terms,
        "factor_coefficients": factor_coefficients or {},
        "return_model_source": return_model_source or "",
        "score_reasons": list(score_reasons or []),
        "score_raw": score_raw,
        "heuristic_score": heuristic_score,
    }
    return _enrich_trade_eod_rem_fields(
        tip, intent_price=intent_price, entry_price=entry_price
    )


@lru_cache(maxsize=1)
def _sim_trade_row_keys() -> frozenset:
    import inspect

    return frozenset(
        p.name
        for p in inspect.signature(_sim_trade_row).parameters.values()
        if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)
    )


def _call_sim_trade_row(**kwargs: Any) -> Dict[str, Any]:
    """过滤未知关键字，避免 tip 多字段再次打爆跑分组。"""
    allowed = _sim_trade_row_keys()
    return _sim_trade_row(**{k: v for k, v in kwargs.items() if k in allowed})


def _gap_from_intent_fill(
    intent_price: Optional[float],
    entry_price: Optional[float],
) -> Optional[float]:
    """次日开盘成交：缺口 ≈ fill/intent − 1（intent=信号日收，fill=开盘）。"""
    try:
        intent = float(intent_price) if intent_price is not None else None
        fill = float(entry_price) if entry_price is not None else None
    except (TypeError, ValueError):
        return None
    if intent is None or fill is None or intent <= 0:
        return None
    return round((fill / intent - 1.0) * 100.0, 6)


def _enrich_trade_eod_rem_fields(
    tip: Dict[str, Any],
    *,
    intent_price: Optional[float] = None,
    entry_price: Optional[float] = None,
) -> Dict[str, Any]:
    """补齐 tip 的缺口 / ŷ_EOD_rem（信号打分时常无 open，成交后用意图价→开盘价还原）。"""
    out = dict(tip or {})
    gap = out.get("gap_pct")
    realized = out.get("realized_t1_to_tau")
    derived = False
    if gap is None and realized is None:
        gap = _gap_from_intent_fill(intent_price, entry_price)
        derived = gap is not None
    if realized is None:
        realized = gap
    if gap is not None:
        out["gap_pct"] = gap
    if realized is not None:
        out["realized_t1_to_tau"] = realized
    y_eod = out.get("predicted_score")
    if y_eod is None:
        y_eod = out.get("score")
    rem = out.get("predicted_score_eod_rem")
    # 信号日常把 rem 写成 =ŷ_EOD（尚无缺口）；成交后有已实现则必须重算
    need_remap = rem is None or derived
    if not need_remap and rem is not None and y_eod is not None and realized is not None:
        try:
            if abs(float(rem) - float(y_eod)) < 1e-9 and abs(float(realized)) > 1e-9:
                need_remap = True
        except (TypeError, ValueError):
            need_remap = True
    if need_remap and y_eod is not None:
        try:
            from core.signal.dual_score import eod_remaining_at_tau

            rem = eod_remaining_at_tau(y_eod, realized)
        except Exception:  # noqa: BLE001 — ŷ 剩余量 best-effort
            logger.debug("eod_remaining_at_tau failed, skipping predicted_score_eod_rem", exc_info=True)
            rem = None
    if rem is not None:
        out["predicted_score_eod_rem"] = rem
    if gap is not None or realized is not None:
        try:
            from core.signal.dual_score import ensure_formula_terms_tau

            expl = ensure_formula_terms_tau(out)
            if isinstance(expl, dict):
                out["formula_terms_tau"] = expl
                out["score_formula_terms_tau"] = expl
        except Exception:  # noqa: BLE001 — τ 公示项 best-effort
            logger.debug("ensure_formula_terms_tau failed, skipping tau formula fields", exc_info=True)
            pass
    return out


def _score_formula_for_item(
    item: Optional[Dict[str, Any]],
    *,
    score: Any = None,
    formula_weights: Optional[Dict[str, Any]] = None,
) -> str:
    """兼容旧调用；新路径请用 ``_score_tooltip_meta``。"""
    del formula_weights
    meta = _score_tooltip_meta(item, score=score)
    return str(meta.get("score_formula") or "")


def _intent_and_fill_prices(
    mode: str,
    *,
    signal_bar: Optional[dict],
    entry_bar: Optional[dict],
) -> Tuple[Any, Any]:
    """意图价=决策参照价；买入价=实际入场价。

    next_open：意图=信号日收盘，买入=次日开盘（可不同）。
    close：意图与买入均为当日收盘（相同）。
    """
    sig = signal_bar or {}
    ent = entry_bar or {}
    if (mode or "").strip().lower() == "next_open":
        intent = sig.get("close") or sig.get("open")
        fill = ent.get("open") or ent.get("close")
        return intent, fill
    px = ent.get("close") or ent.get("open")
    return px, px


def _vol_from_window(bars: List[dict], *, window: int = 20) -> Optional[float]:
    from core.backtest.topk_weights import vol_from_window

    return vol_from_window(bars, window=window)


def allocate_topk_weights(
    legs: List[dict],
    *,
    weight_mode: str = "score_budget",
    max_position_pct: float = 25.0,
    max_sector_pct: float = 40.0,
    stock_bars: Optional[Dict[str, List[dict]]] = None,
    renormalize: Optional[bool] = None,
) -> Tuple[Dict[str, float], str]:
    """为已成交腿分配目标权重（%）。失败回退等权。

    实现见 ``core.backtest.topk_weights``（A3 按用例拆出）。
    score_budget / risk_parity_lite 默认**不**再归一到 100%（与纸面 budget 一致，可留现金）。
    equal 始终满仓。``renormalize=True`` 可强制满仓（旧研究口径）。
    """
    from core.backtest.topk_weights import allocate_topk_weights as _alloc

    return _alloc(
        legs,
        weight_mode=weight_mode,
        max_position_pct=max_position_pct,
        max_sector_pct=max_sector_pct,
        stock_bars=stock_bars,
        renormalize=renormalize,
    )


def _weighted_port_return(legs: List[dict], weights: Dict[str, float]) -> float:
    """组合期收益：权重为净值百分比，未分配部分视为现金（收益 0）。

    不再按已选腿归一。等权（权重和=100）与旧口径一致；score_budget
    单票/行业帽留下的现金会压低收益与回撤。
    """
    if not legs:
        return 0.0
    allocated = sum(
        float(weights.get(str(leg["stock_code"])) or 0.0) for leg in legs
    )
    if allocated <= 1e-9:
        return sum(float(leg.get("return_pct") or 0.0) for leg in legs) / len(legs)
    acc = 0.0
    for leg in legs:
        code = str(leg["stock_code"])
        w = float(weights.get(code) or 0.0)
        acc += (w / 100.0) * float(leg.get("return_pct") or 0.0)
    return acc


def _universe_ew_closes(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    upto_i: int,
) -> List[float]:
    """截至信号日的池内等权收盘（PIT；供 regime / 高波缩仓）。"""
    closes: List[float] = []
    end = min(int(upto_i) + 1, len(dates))
    for j in range(end):
        d = dates[j]
        xs: List[float] = []
        for dm in date_maps.values():
            bar = dm.get(d) if isinstance(dm, dict) else None
            if not bar:
                continue
            try:
                px = float(bar.get("close") or 0)
            except (TypeError, ValueError):
                continue
            if px > 0:
                xs.append(px)
        if xs:
            closes.append(sum(xs) / len(xs))
    return closes


def _exposure_scale_at_signal(
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    signal_i: int,
) -> Dict[str, Any]:
    """PIT 高波/弱势 → 仓位上限缩放（不打远端指数）。"""
    from core.backtest.oos_report import regime_label_from_closes
    from core.pro_core import regime_position_scale

    closes = _universe_ew_closes(date_maps, dates, signal_i)
    window = closes[-24:] if len(closes) >= 5 else closes
    label = regime_label_from_closes(window) if len(window) >= 5 else "unknown"
    meta = regime_position_scale(regime={"label": label})
    scale = 1.0
    try:
        scale = max(0.2, min(1.0, float(meta.get("scale") or 1.0)))
    except (TypeError, ValueError):
        scale = 1.0
    return {
        "label": label,
        "scale": scale,
        "source": meta.get("source"),
        "n_closes": len(window),
    }


def _clip_leg_at_close_stop(
    *,
    code: str,
    entry_px: float,
    entry_date: str,
    exit_date: str,
    exit_px: float,
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    date_i: Dict[str, int],
    stop_pct: float,
) -> Tuple[str, float, float, bool]:
    """持有期收盘触及 −stop 则提前平。返回 (exit_date, exit_px, ret_pct, clipped)。"""
    try:
        px0 = float(entry_px or 0)
        px1 = float(exit_px or 0)
        sp = float(stop_pct or 0)
    except (TypeError, ValueError):
        px0, px1, sp = 0.0, 0.0, 0.0
    raw_ret = (px1 / px0 - 1.0) * 100.0 if px0 > 0 else 0.0
    if sp <= 0 or sp >= 1 or px0 <= 0 or not code:
        return str(exit_date), px1, raw_ret, False
    i0 = date_i.get(str(entry_date))
    i1 = date_i.get(str(exit_date))
    dm = date_maps.get(str(code)) or {}
    if i0 is None or i1 is None or i1 <= i0:
        return str(exit_date), px1, raw_ret, False
    floor_px = px0 * (1.0 - sp)
    for k in range(i0 + 1, i1 + 1):
        bar = dm.get(dates[k])
        if not bar:
            continue
        try:
            px = float(bar.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if px > 0 and px <= floor_px:
            used = (px / px0 - 1.0) * 100.0
            return str(dates[k]), px, used, True
    return str(exit_date), px1, raw_ret, False


def _stop_shadow_from_trades(
    trades: Sequence[dict],
    date_maps: Dict[str, Dict[str, dict]],
    dates: Sequence[str],
    *,
    stop_pct: float,
    holding_days: int,
    applied_to_main: bool = False,
) -> Dict[str, Any]:
    """持有期内触及止损则提前平仓的影子净值。"""
    try:
        sp = float(stop_pct)
    except (TypeError, ValueError):
        sp = 0.0
    if sp <= 0 or sp >= 1:
        return {"ok": False, "reason": "stop_pct_off"}
    date_i = {d: i for i, d in enumerate(dates)}
    shadow_rets: List[float] = []
    clipped = 0
    for t in trades or []:
        legs = list(t.get("legs") or [])
        if not legs:
            continue
        weights = {
            str(leg.get("stock_code") or ""): float(leg.get("weight_pct") or 0.0)
            for leg in legs
        }
        shadow_legs: List[dict] = []
        for leg in legs:
            code = str(leg.get("stock_code") or "")
            try:
                entry_px = float(leg.get("fill_price") or leg.get("entry_price") or 0)
            except (TypeError, ValueError):
                entry_px = 0.0
            try:
                exit_px = float(leg.get("exit_price") or 0)
            except (TypeError, ValueError):
                exit_px = 0.0
            raw_ret = float(leg.get("return_pct") or 0.0)
            if not code or entry_px <= 0:
                shadow_legs.append({"stock_code": code, "return_pct": raw_ret})
                continue
            entry_d = str(leg.get("entry_date") or t.get("entry_date") or "")
            exit_d = str(leg.get("exit_date") or t.get("exit_date") or "")
            if exit_px <= 0 and entry_px > 0:
                exit_px = entry_px * (1.0 + raw_ret / 100.0)
            _ed, _ep, used, hit = _clip_leg_at_close_stop(
                code=code,
                entry_px=entry_px,
                entry_date=entry_d,
                exit_date=exit_d,
                exit_px=exit_px,
                date_maps=date_maps,
                dates=dates,
                date_i=date_i,
                stop_pct=sp,
            )
            if hit:
                clipped += 1
            shadow_legs.append({"stock_code": code, "return_pct": used})
        shadow_rets.append(_weighted_port_return(shadow_legs, weights))
    m = _trade_metrics(shadow_rets, holding_days=holding_days) if shadow_rets else {}
    note = (
        "持有期收盘触及 −stop 则该腿提前平；已并入主回测路径。"
        if applied_to_main
        else "持有期收盘触及 −stop 则该腿提前平；未进主回测路径。"
    )
    return {
        "ok": bool(shadow_rets),
        "stop_pct": sp,
        "legs_clipped": clipped,
        "applied_to_main": bool(applied_to_main),
        "trade_count": m.get("trade_count"),
        "total_return_pct": m.get("total_return_pct"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "win_rate_pct": m.get("win_rate_pct"),
        "note": note,
    }


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    common: Optional[set] = None
    for bars in stock_bars.values():
        keys = set(_bars_by_date(bars).keys())
        common = keys if common is None else common & keys
    return sorted(common or [])


def _filter_stock_bars_for_calendar(
    stock_bars: Dict[str, List[dict]],
    *,
    min_bars: int,
) -> Tuple[Dict[str, List[dict]], List[Dict[str, Any]]]:
    """剔除日线过短的票，避免单票把共同交易日交集压垮。"""
    usable: Dict[str, List[dict]] = {}
    dropped: List[Dict[str, Any]] = []
    need = max(2, int(min_bars))
    for code, bars in (stock_bars or {}).items():
        n = len(_bars_by_date(bars))
        if n >= need:
            usable[str(code)] = bars
        else:
            dropped.append(
                {
                    "stock_code": str(code),
                    "bars": n,
                    "reason": f"日线不足 {n}<{need}，已排除以免压垮共同交易日",
                }
            )
    return usable, dropped


def apply_topk_dropout(
    picks: List[Tuple[str, float]],
    prev_codes: Sequence[str],
    *,
    top_k: int,
    dropout_n: int,
) -> List[Tuple[str, float]]:
    """TopK-Dropout：掉出 K+N 才卖；冲进前 max(K-N,1) 才新买；目标持仓约 K。"""
    k = max(1, int(top_k))
    n_buf = max(0, int(dropout_n or 0))
    if n_buf <= 0 or not picks:
        return list(picks[:k])

    score_by = {str(c): float(s) for c, s in picks}
    rank = {str(c): i for i, (c, _) in enumerate(picks)}
    keep_rank = k + n_buf  # 0-based: keep if rank < keep_rank
    enter_rank = max(1, k - n_buf)

    selected: List[Tuple[str, float]] = []
    selected_set = set()
    # 先保留仍在缓冲区内的旧持仓
    for code in prev_codes:
        c = str(code)
        r = rank.get(c)
        if r is None:
            continue
        if r < keep_rank and c not in selected_set:
            selected.append((c, score_by[c]))
            selected_set.add(c)
        if len(selected) >= k:
            break
    # 再从顶尖补齐新票
    for c, s in picks:
        if len(selected) >= k:
            break
        if c in selected_set:
            continue
        if rank[c] < enter_rank:
            selected.append((c, s))
            selected_set.add(c)
    # 若仍不足（缓冲导致空档），按排名补满到 K
    if len(selected) < k:
        for c, s in picks:
            if len(selected) >= k:
                break
            if c not in selected_set:
                selected.append((c, s))
                selected_set.add(c)
    return selected


def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    """决策日 end_idx 及以前，最多 max_window 根（PIT as_of）。"""
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def backtest_topk_equal_weight(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    min_history: int = 12,
    max_window: int = 30,
    apply_costs: bool = False,
    cost_config: Optional[dict] = None,
    neutralize: Optional[bool] = None,
    fundamentals_by_code: Optional[Dict[str, dict]] = None,
    execution_mode: str = "next_open",
    respect_limit: bool = True,
    slippage_tier: Optional[str] = None,
    exit_max_defer: int = 3,
    weight_mode: str = "score_budget",
    max_position_pct: float = 25.0,
    max_sector_pct: float = 40.0,
    dropout_n: int = 0,
    sector_map: Optional[Dict[str, str]] = None,
    rank_mode: str = "predicted_score",
    min_predicted_score: Optional[float] = None,
    return_model_min_samples: int = 24,
    return_model_ridge_lambda: float = 0.0,
    return_model_refit_every: int = 1,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    use_live_cluster_models: bool = True,
    allow_heuristic_baseline: bool = False,
    apply_tau_buy_gate: bool = False,
    exclude_oos_failed: bool = True,
    strategy_id: Optional[str] = None,
    precomputed_ranks: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Callable[..., Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """
    多票横截面：每个调仓日对 watching 打分，持有 TopK，持有 horizon_days。
    weight_mode: equal | score_budget | risk_parity_lite（与纸面 optimize 同源）。
    dropout_n>0 时启用 TopK-Dropout（K±N 缓冲）。

    默认限额 / weight_mode / top_k 上限对齐 StrategySpec（可用 strategy_id 覆盖）。

    rank_mode:
      - predicted_score：walk-forward / 分组因子系数后按收益分（ŷ%）排序（生产默认）
      - heuristic_score：人工线性加权 0–100（仅研究 OOS 基线；须 allow_heuristic_baseline）

    return_models_by_code / use_live_cluster_models：研究 OOS 注入组 β，避免串 live。
    apply_tau_buy_gate：历史日线默认 False（无可靠分钟 τ；开闸易 0 笔）。
    纸面 live 买入闸不走本函数。
    """
    from core.backtest.attribution import attribute_portfolio_trades
    from core.backtest.matching import (
        apply_match_filters,
        cost_config_for_slippage_tier,
        resolve_exit_index,
    )
    from core.data.pit import pit_report_for_backtest
    from core.portfolio_optimize import _sector_for, load_sector_map, sector_map_coverage
    from core.signal.config import load_signal_config
    from core.signal.cross_section_batch import score_and_rank_watching, score_window_as_item
    from core.signal.return_score import (
        ReturnScoreModel,
        clamp_rank_mode,
        fit_return_model_from_panel,
        resolve_research_rank_mode,
    )
    from core.strategy import backtest_portfolio_defaults

    if not stock_bars:
        return {"success": False, "error": "无标的日线"}

    bt_defaults = backtest_portfolio_defaults(strategy_id or "short")
    top_k_cap = int(bt_defaults.get("top_k_cap") or 40)

    cfg = load_signal_config()
    if not apply_tau_buy_gate:
        # 历史 ŷ_EOD：不读分钟仓（live 5m 也不是 PIT）
        cfg = dict(cfg)
        scoring = dict(cfg.get("scoring") or {})
        scoring["skip_minute_io"] = True
        cfg["scoring"] = scoring
        ds = dict(cfg.get("dual_score") or {})
        ds["enable_minute_tau"] = False
        cfg["dual_score"] = ds
    cs_cfg = cfg.get("cross_section") or {}
    use_neutral = cs_cfg.get("neutralize", True) if neutralize is None else bool(neutralize)
    global_factor_weights = dict(cfg.get("weights") or {})
    resolved_rank_mode = (
        resolve_research_rank_mode(rank_mode)
        if allow_heuristic_baseline
        else clamp_rank_mode(rank_mode)
    )
    try:
        from core.signal.cluster.live import load_active_cluster_weights

        cluster_active = load_active_cluster_weights()
    except Exception:  # noqa: BLE001 — 组权是加分项，不阻塞基线回测
        logger.debug("load_active_cluster_weights failed, running without cluster_active", exc_info=True)
        cluster_active = None
    dropout_n = max(0, min(int(dropout_n or 0), 10))

    top_k = max(1, min(int(top_k or bt_defaults["top_k"]), top_k_cap))
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_score = float(min_score or 55.0)
    if min_predicted_score is None:
        min_predicted_score = bt_defaults.get("min_predicted_score")
    min_history = max(5, int(min_history or 12))
    defer_cap = max(0, int(exit_max_defer or 0))
    mode = (execution_mode or "next_open").strip().lower()
    if mode not in ("close", "next_open"):
        mode = "next_open"
    if slippage_tier:
        cost_config = cost_config_for_slippage_tier(slippage_tier, base=cost_config)
    min_fit = max(8, int(return_model_min_samples or 24))
    refit_every = max(1, int(return_model_refit_every or 1))
    ridge_lam = float(return_model_ridge_lambda or 0.0)
    weight_mode = str(weight_mode or bt_defaults["weight_mode"]).strip() or "score_budget"
    max_position_pct = float(max_position_pct if max_position_pct is not None else bt_defaults["max_position_pct"])
    max_sector_pct = float(max_sector_pct if max_sector_pct is not None else bt_defaults["max_sector_pct"])

    # next_open 需要信号日后再留 1 + horizon + 跌停延后
    extra = 1 if mode == "next_open" else 0
    need = min_history + horizon_days + extra + defer_cap

    stock_bars, dropped_thin = _filter_stock_bars_for_calendar(stock_bars, min_bars=need)
    if len(stock_bars) < 2:
        return {
            "success": False,
            "error": f"有效日线标的不足（{len(stock_bars)}，已排除短序列 {len(dropped_thin)} 只）",
            "dropped_stocks": dropped_thin,
            "common_dates": 0,
        }

    date_maps = {code: _bars_by_date(bars) for code, bars in stock_bars.items()}
    dates = _common_dates(stock_bars)
    date_i = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    if n < need:
        return {
            "success": False,
            "error": f"共同交易日不足（{n} 根，需要 {need}+）",
            "common_dates": n,
            "dropped_stocks": dropped_thin,
            "loaded_stocks": list(stock_bars.keys()),
        }

    returns: List[float] = []
    trades: List[dict] = []
    sim_trades: List[dict] = []
    equity_curve: List[dict] = []
    equity = 100.0
    neutralized_rebalances = 0
    skipped_limit = 0
    stop_legs_clipped = 0
    skipped_limit_exit = 0
    exit_deferred = 0
    pit_windows = 0
    fund_resolves: List[dict] = []
    impact_cost_sum_bps = 0.0
    impact_legs = 0
    turnover_cost_sum_pct = 0.0
    prev_codes: List[str] = []
    prev_weights: Dict[str, float] = {}
    resolved_weight_mode = (weight_mode or "equal").strip().lower()
    if resolved_weight_mode not in WEIGHT_MODES:
        resolved_weight_mode = "equal"
    signal_fill_sample: List[dict] = []
    smap = dict(sector_map) if sector_map is not None else load_sector_map()
    try:
        stop_pct = float((cfg.get("invalidation") or {}).get("stop_pct") or 0.0)
    except (TypeError, ValueError):
        stop_pct = 0.0
    oos_fail_excluded_models = 0
    cluster_models_raw = 0
    fund_cfg = cfg.get("fundamentals") or {}
    use_fund_pit = bool(fund_cfg.get("enabled", True)) and bool(
        fund_cfg.get("use_in_backtest", True)
    )
    pit_mode = str(fund_cfg.get("pit_mode") or "as_of").strip().lower()
    pending_signals: List[dict] = []
    train_xs: List[Dict[str, Optional[float]]] = []
    train_ys: List[float] = []
    return_model = None
    cluster_return_models: Dict[str, Any] = {}
    if resolved_rank_mode == "predicted_score":
        if return_models_by_code is not None:
            for code, raw in (return_models_by_code or {}).items():
                key = str(code or "").strip()
                if not key or raw is None:
                    continue
                if isinstance(raw, ReturnScoreModel):
                    cluster_return_models[key] = raw
                elif isinstance(raw, dict):
                    m = ReturnScoreModel.from_dict(raw)
                    if m is not None:
                        cluster_return_models[key] = m
        elif use_live_cluster_models:
            try:
                from core.signal.cluster.live import (
                    cluster_yhat_shadow_compute_allowed,
                    filter_primary_cluster_models_by_code,
                    get_cluster_scoring_cfg,
                    load_cluster_return_models_by_code,
                )

                # 历史回测对齐数据中心：shadow|active 均可算组 ŷ（与 insights 同源）。
                # 纸面 live 主排序仍仅 active（FH0）；此处不放开 paper。
                # OOS 失败组主分与 live 对齐：从 by_code 剔除，回退全局模型。
                cs = get_cluster_scoring_cfg()
                if cluster_yhat_shadow_compute_allowed(str(cs.get("mode") or "off")):
                    raw_models = load_cluster_return_models_by_code()
                    cluster_models_raw = len(raw_models or {})
                    cluster_return_models = filter_primary_cluster_models_by_code(
                        raw_models
                    )
                    oos_fail_excluded_models = max(
                        0, cluster_models_raw - len(cluster_return_models)
                    )
                else:
                    cluster_return_models = {}
            except Exception:  # noqa: BLE001 — 组模型预载失败，退化为无 ŷ 对照
                logger.debug("load cluster return_models failed, falling back to empty", exc_info=True)
                cluster_return_models = {}
    last_model_fit_rebalance = -10**9
    rebalance_idx = 0
    pred_rank_rebalances = 0
    heuristic_fallback_rebalances = 0
    exposure_scales: List[Dict[str, Any]] = []
    i = min_history - 1
    if dates:
        equity_curve.append({"date": dates[i], "equity": equity, "return_pct": 0.0})
    last_signal_i = n - horizon_days - extra - defer_cap - 1
    first_signal_i = i
    n_signal_days = max(0, last_signal_i - first_signal_i + 1)
    while i <= last_signal_i:
        if cancel_check is not None:
            try:
                if cancel_check():
                    return {"success": False, "error": "已取消"}
            except Exception:  # noqa: BLE001 — 回调异常不阻塞
                logger.debug("cancel_check callback raised", exc_info=True)
                pass
        entries: List[dict] = []
        signal_as_of = dates[i]
        cached_rank = (
            (precomputed_ranks or {}).get(signal_as_of)
            if precomputed_ranks is not None
            else None
        )
        day_n = i - first_signal_i + 1
        if (
            progress_cb is not None
            and not cached_rank
            and (day_n <= 1 or day_n % 4 == 0 or i >= last_signal_i)
        ):
            try:
                progress_cb(
                    f"{signal_as_of} · {day_n}/{n_signal_days} 日",
                    max(0, day_n),
                    max(1, n_signal_days),
                )
            except Exception:  # noqa: BLE001 — 进度回调异常不阻塞
                logger.debug("progress_cb callback raised on %s", signal_as_of, exc_info=True)
                pass
        if cached_rank:
            picks = list(cached_rank.get("picks") or [])
            neut_meta = dict(cached_rank.get("neut_meta") or {})
            items_by_code = dict(cached_rank.get("items_by_code") or {})
        else:
            for code in stock_bars:
                window = _window_for_code(code, dates, date_maps, i, max_window)
                pit_windows += 1
                if len(window) < 2:
                    continue
                quote = _mock_quote_from_bars(window, len(window) - 1)
                fund = None
                if use_fund_pit:
                    if pit_mode == "as_of":
                        try:
                            from core.fundamentals_pit import resolve_fundamentals_for_score

                            resolved = resolve_fundamentals_for_score(
                                code,
                                as_of=signal_as_of,
                                fund_cfg=fund_cfg,
                                live_fallback=False,
                            )
                            fund_resolves.append(resolved)
                            fund = resolved.get("metrics")
                        except Exception:  # noqa: BLE001 — 基本面 PIT 失败不阻塞打分
                            logger.debug("resolve_fundamentals_for_score failed for %s as_of %s", code, signal_as_of, exc_info=True)
                            fund = None
                    else:
                        fund = (fundamentals_by_code or {}).get(code)
                item = score_window_as_item(
                    code,
                    window,
                    horizon_days=horizon_days,
                    quote=quote,
                    config=cfg,
                    fundamentals=fund,
                )
                if item:
                    # PIT 双层 ŷ：供 score_and_rank 挂 ŷ_τ / blend（信号日 open 缺口）
                    item["_bt_quote"] = quote
                    item["_bt_bars"] = window[-8:] if len(window) >= 2 else list(window)
                    entries.append(item)

            # 收益分：优先分组 live OLS β；否则 walk-forward 拟合全局模型
            if resolved_rank_mode == "predicted_score" and not cluster_return_models:
                still_pending: List[dict] = []
                for p in pending_signals:
                    j = int(p["signal_i"])
                    if j + horizon_days <= i and j + horizon_days < n:
                        y = _close_return_pct(
                            date_maps,
                            str(p["code"]),
                            str(p["signal_date"]),
                            dates[j + horizon_days],
                        )
                        subs = p.get("sub_scores") or {}
                        if y is not None and subs:
                            train_xs.append(dict(subs))
                            train_ys.append(float(y))
                    else:
                        still_pending.append(p)
                pending_signals = still_pending
                for item in entries:
                    code = str(item.get("stock_code") or "").strip()
                    if not code:
                        continue
                    pending_signals.append(
                        {
                            "signal_i": i,
                            "signal_date": signal_as_of,
                            "code": code,
                            "sub_scores": dict(item.get("sub_scores") or {}),
                        }
                    )
                if len(train_ys) >= min_fit and (
                    return_model is None
                    or (rebalance_idx - last_model_fit_rebalance) >= refit_every
                ):
                    model, _fit_rep = fit_return_model_from_panel(
                        train_xs,
                        train_ys,
                        horizon_days=horizon_days,
                        ridge_lambda=ridge_lam,
                        fitted_as_of=signal_as_of,
                        min_samples=min_fit,
                    )
                    if model is not None:
                        return_model = model
                        last_model_fit_rebalance = rebalance_idx

            picks, neut_meta = score_and_rank_watching(
                entries,
                min_score=min_score,
                config=cfg,
                neutralize=use_neutral,
                rank_mode=resolved_rank_mode,
                return_model=return_model,
                return_models_by_code=cluster_return_models or None,
                min_predicted_score=min_predicted_score,
                allow_heuristic_baseline=allow_heuristic_baseline
                or resolved_rank_mode == "heuristic_score",
                apply_tau_buy_gate=bool(apply_tau_buy_gate),
                exclude_oos_failed=bool(exclude_oos_failed),
            )
            if resolved_rank_mode == "heuristic_score":
                pred_rank_rebalances += 0
            elif resolved_rank_mode == "predicted_score":
                if neut_meta.get("predicted_score_fallback") or (
                    return_model is None and not cluster_return_models
                ):
                    heuristic_fallback_rebalances += 1
                else:
                    pred_rank_rebalances += 1
            if neut_meta.get("applied"):
                neutralized_rebalances += 1
            items_by_code = neut_meta.get("items_by_code") or {}
            if precomputed_ranks is not None:
                precomputed_ranks[signal_as_of] = {
                    "picks": list(picks),
                    "neut_meta": dict(neut_meta),
                    "items_by_code": dict(items_by_code),
                }
        selected = apply_topk_dropout(
            picks, prev_codes, top_k=top_k, dropout_n=dropout_n
        )
        rebalance_idx += 1
        if not selected:
            i += 1
            continue

        if mode == "next_open":
            entry_date = dates[i + 1]
            planned_exit_date = dates[i + 1 + horizon_days]
        else:
            entry_date = dates[i]
            planned_exit_date = dates[i + horizon_days]

        leg_returns: List[float] = []
        legs: List[dict] = []
        leg_exit_dates: List[str] = []
        signal_date = dates[i]
        for code, score in selected:
            scored_item = items_by_code.get(str(code)) or {}
            tip = _score_tooltip_meta(
                scored_item,
                score=score,
                code=str(code),
                return_model=return_model,
                return_models_by_code=cluster_return_models or None,
                cluster_active=cluster_active,
                global_weights=global_factor_weights,
            )
            score_formula = tip.get("score_formula") or ""
            score_reasons = list(tip.get("score_reasons") or [])
            score_raw = tip.get("score_raw")
            tip_kw = {
                "cluster_label": tip.get("cluster_label"),
                "score_weight_source": tip.get("score_weight_source"),
                "factor_weights": tip.get("factor_weights") or {},
                "factor_weights_note": tip.get("factor_weights_note") or "",
                "score_formula": score_formula,
                "score_formula_terms": tip.get("score_formula_terms"),
                "factor_coefficients": tip.get("factor_coefficients") or {},
                "return_model_source": tip.get("return_model_source") or "",
                "score_reasons": score_reasons,
                "score_raw": score_raw,
                "predicted_score": tip.get("predicted_score"),
                "predicted_score_tau": scored_item.get("predicted_score_tau")
                if scored_item.get("predicted_score_tau") is not None
                else scored_item.get("score_rem"),
                "predicted_score_blend": scored_item.get("predicted_score_blend"),
                "predicted_score_eod_rem": scored_item.get("predicted_score_eod_rem"),
                "realized_t1_to_tau": scored_item.get("realized_t1_to_tau"),
                "score_rem": scored_item.get("score_rem"),
                "gap_pct": scored_item.get("gap_pct"),
                "event_prior": scored_item.get("event_prior"),
                "as_of_tau": scored_item.get("as_of_tau") or scored_item.get("rem_tau"),
                "y_spec_tau": scored_item.get("y_spec_tau"),
                "features_tau": scored_item.get("features_tau"),
                "formula_terms_tau": scored_item.get("formula_terms_tau")
                or scored_item.get("score_formula_terms_tau"),
                "dual_score_fusion": scored_item.get("dual_score_fusion"),
                "dual_score_weights": scored_item.get("dual_score_weights"),
            }
            # 表列 / 成交样本 score：只保留 ŷ%；heuristic 排序分另存
            if str(scored_item.get("rank_key") or "") == "predicted_score_eod":
                try:
                    rank_score = float(score)
                except (TypeError, ValueError):
                    rank_score = score
            else:
                try:
                    from core.signal.dual_score import decision_score_for_item

                    rank_score = decision_score_for_item(scored_item)
                except Exception:  # noqa: BLE001 — 决策分降级，用已打分字段兜底
                    logger.debug("decision_score_for_item failed, falling back to predicted_score_blend", exc_info=True)
                    rank_score = tip_kw.get("predicted_score_blend")
                if rank_score is None:
                    rank_score = tip_kw.get("predicted_score_blend")
                if rank_score is None:
                    rank_score = tip_kw.get("predicted_score")
                if rank_score is None:
                    rank_score = score
                else:
                    try:
                        rank_score = float(rank_score)
                    except (TypeError, ValueError):
                        rank_score = score
            try:
                from core.signal.score_display import looks_like_legacy_heuristic_score

                rs_f = float(rank_score) if rank_score is not None else None
            except (TypeError, ValueError):
                rs_f = None
                looks_like_legacy_heuristic_score = lambda v: False  # type: ignore
            heu_rank = None
            if rs_f is not None and looks_like_legacy_heuristic_score(rs_f, item=scored_item):
                heu_rank = rs_f
                rank_score = tip_kw.get("predicted_score") or scored_item.get(
                    "score_cluster"
                )
                try:
                    if rank_score is not None:
                        rank_score = float(rank_score)
                        if looks_like_legacy_heuristic_score(rank_score, item=scored_item):
                            rank_score = None
                except (TypeError, ValueError):
                    rank_score = None
            if heu_rank is not None and tip_kw.get("heuristic_score") is None:
                tip_kw["heuristic_score"] = heu_rank
            if scored_item.get("heuristic_score") is not None:
                tip_kw["heuristic_score"] = scored_item.get("heuristic_score")
            # tip 同步对齐后的 blend，避免成交表悬停仍见塌缩 EOD
            if rank_score is not None and (
                tip_kw.get("predicted_score_blend") is None
                or (
                    tip_kw.get("predicted_score_tau") is not None
                    and tip_kw.get("predicted_score") is not None
                    and tip_kw.get("predicted_score_blend") is not None
                    and abs(
                        float(tip_kw["predicted_score_blend"])
                        - float(tip_kw["predicted_score"])
                    )
                    < 1e-9
                    and abs(
                        float(tip_kw["predicted_score_tau"])
                        - float(tip_kw["predicted_score"])
                    )
                    > 1e-6
                )
            ):
                tip_kw["predicted_score_blend"] = rank_score
            dm = date_maps[code]
            entry_bar = dm.get(entry_date)
            if not entry_bar:
                continue

            # 涨跌停过滤：用该标的按日期序的 bars 序列
            code_bars = stock_bars.get(code) or []
            code_dates = [str(b.get("date") or "") for b in code_bars]
            try:
                match_i = code_dates.index(entry_date)
            except ValueError:
                match_i = -1
            if match_i >= 0:
                match = apply_match_filters(
                    want_buy=True,
                    entry_bars=code_bars,
                    entry_index=match_i,
                    respect_limit=respect_limit,
                    stock_code=code,
                )
                if match.get("blocked"):
                    skipped_limit += 1
                    # R4.2：跳过也进对照样本 / 模拟账
                    signal_bar = dm.get(signal_date) or {}
                    intent, _fill = _intent_and_fill_prices(
                        mode, signal_bar=signal_bar, entry_bar=entry_bar
                    )
                    try:
                        intent_f = round(float(intent), 4) if intent else None
                    except (TypeError, ValueError):
                        intent_f = None
                    skip_row = {
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "stock_code": code,
                        **_fill_sample_score_fields(rank_score, tip_kw),
                        "intent_price": intent_f,
                        "fill_price": None,
                        "exit_price": None,
                        "return_pct": None,
                        "skipped_limit": True,
                        "skipped_limit_exit": False,
                        "exit_deferred_days": 0,
                        "execution_mode": mode,
                        "status": "skipped_limit_entry",
                    }
                    signal_fill_sample.append(skip_row)
                    sim_trades.append(
                        _call_sim_trade_row(
                            stock_code=code,
                            score=rank_score,
                            signal_date=signal_date,
                            entry_date=entry_date,
                            intent_price=intent_f,
                            sector=_sector_for(code, smap),
                            execution_mode=mode,
                            status="skipped_limit_entry",
                            **tip_kw,
                        )
                    )
                    continue

            try:
                planned_exit_i = code_dates.index(planned_exit_date)
            except ValueError:
                planned_exit_i = -1
            if planned_exit_i < 0:
                continue
            exit_res = resolve_exit_index(
                code_bars,
                planned_exit_i,
                respect_limit=respect_limit,
                stock_code=code,
                max_defer=defer_cap,
            )
            if exit_res.get("skipped") or not exit_res.get("ok"):
                skipped_limit_exit += 1
                signal_bar = dm.get(signal_date) or {}
                intent, fill = _intent_and_fill_prices(
                    mode, signal_bar=signal_bar, entry_bar=entry_bar
                )
                try:
                    intent_f = round(float(intent), 4) if intent else None
                except (TypeError, ValueError):
                    intent_f = None
                try:
                    fill_f = round(float(fill), 4) if fill else None
                except (TypeError, ValueError):
                    fill_f = None
                skip_exit = {
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "stock_code": code,
                    **_fill_sample_score_fields(rank_score, tip_kw),
                    "intent_price": intent_f,
                    "fill_price": fill_f,
                    "exit_price": None,
                    "return_pct": None,
                    "skipped_limit": False,
                    "skipped_limit_exit": True,
                    "exit_deferred_days": 0,
                    "execution_mode": mode,
                    "status": "skipped_limit_exit",
                }
                signal_fill_sample.append(skip_exit)
                sim_trades.append(
                    _call_sim_trade_row(
                        stock_code=code,
                        score=rank_score,
                        signal_date=signal_date,
                        entry_date=entry_date,
                        intent_price=intent_f,
                        entry_price=fill_f,
                        sector=_sector_for(code, smap),
                        execution_mode=mode,
                        status="skipped_limit_exit",
                        **tip_kw,
                    )
                )
                continue
            if exit_res.get("deferred"):
                exit_deferred += 1
            exit_idx = int(exit_res["exit_index"])
            exit_bar = code_bars[exit_idx]
            exit_date = str(exit_bar.get("date") or planned_exit_date)

            signal_bar = dm.get(signal_date) or {}
            intent, entry = _intent_and_fill_prices(
                mode, signal_bar=signal_bar, entry_bar=entry_bar
            )
            exit_p = exit_bar.get("close")
            if not entry:
                continue
            try:
                entry_f = float(entry)
                exit_f = float(exit_p) if exit_p is not None else None
                intent_f = float(intent) if intent is not None else entry_f
            except (TypeError, ValueError):
                entry_f, exit_f, intent_f = None, None, None
            ret = ((exit_f / entry_f) - 1.0) * 100.0 if entry_f and exit_f else 0.0
            if stop_pct > 0 and entry_f and exit_f is not None:
                exit_date, exit_px, ret, hit_stop = _clip_leg_at_close_stop(
                    code=code,
                    entry_px=entry_f,
                    entry_date=entry_date,
                    exit_date=exit_date,
                    exit_px=exit_f,
                    date_maps=date_maps,
                    dates=dates,
                    date_i=date_i,
                    stop_pct=stop_pct,
                )
                exit_f = float(exit_px)
                if hit_stop:
                    stop_legs_clipped += 1
            leg_returns.append(ret)
            leg_exit_dates.append(exit_date)
            legs.append(
                {
                    "stock_code": code,
                    "score": rank_score,
                    "heuristic_score": tip_kw.get("heuristic_score"),
                    "score_formula": score_formula,
                    "score_formula_terms": tip_kw.get("score_formula_terms"),
                    "factor_coefficients": tip_kw.get("factor_coefficients") or {},
                    "return_model_source": tip_kw.get("return_model_source") or "",
                    "cluster_label": tip_kw.get("cluster_label"),
                    "score_weight_source": tip_kw.get("score_weight_source"),
                    "factor_weights": tip_kw.get("factor_weights") or {},
                    "factor_weights_note": tip_kw.get("factor_weights_note") or "",
                    "score_reasons": score_reasons,
                    "score_raw": score_raw,
                    "predicted_score": tip_kw.get("predicted_score"),
                    "predicted_score_tau": tip_kw.get("predicted_score_tau"),
                    "predicted_score_blend": tip_kw.get("predicted_score_blend"),
                    "predicted_score_eod_rem": tip_kw.get("predicted_score_eod_rem"),
                    "realized_t1_to_tau": tip_kw.get("realized_t1_to_tau"),
                    "score_rem": tip_kw.get("score_rem"),
                    "gap_pct": tip_kw.get("gap_pct"),
                    "event_prior": tip_kw.get("event_prior"),
                    "as_of_tau": tip_kw.get("as_of_tau"),
                    "y_spec_tau": tip_kw.get("y_spec_tau"),
                    "features_tau": tip_kw.get("features_tau"),
                    "formula_terms_tau": tip_kw.get("formula_terms_tau"),
                    "dual_score_fusion": tip_kw.get("dual_score_fusion"),
                    "dual_score_weights": tip_kw.get("dual_score_weights"),
                    "return_pct": round(ret, 2),
                    "sector": _sector_for(code, smap),
                    "exit_date": exit_date,
                    "exit_deferred_days": exit_res.get("deferred_days") or 0,
                    "intent_price": round(intent_f, 4) if intent_f is not None else None,
                    "fill_price": round(entry_f, 4) if entry_f is not None else None,
                    "exit_price": round(exit_f, 4) if exit_f is not None else None,
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "execution_mode": mode,
                }
            )
            signal_fill_sample.append(
                {
                    "signal_date": signal_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "stock_code": code,
                    **_fill_sample_score_fields(rank_score, tip_kw),
                    "intent_price": round(intent_f, 4) if intent_f is not None else None,
                    "fill_price": round(entry_f, 4) if entry_f is not None else None,
                    "exit_price": round(exit_f, 4) if exit_f is not None else None,
                    "return_pct": round(ret, 2),
                    "skipped_limit": False,
                    "skipped_limit_exit": False,
                    "exit_deferred_days": exit_res.get("deferred_days") or 0,
                    "execution_mode": mode,
                    "status": "filled",
                }
            )

        if not leg_returns:
            i += 1
            continue

        exp = _exposure_scale_at_signal(date_maps, dates, i)
        exposure_scales.append(exp)
        scale = float(exp.get("scale") or 1.0)
        weights_pct, used_mode = allocate_topk_weights(
            legs,
            weight_mode=resolved_weight_mode,
            max_position_pct=max_position_pct * scale,
            max_sector_pct=max_sector_pct * scale,
            stock_bars=stock_bars,
        )
        for leg in legs:
            code = str(leg.get("stock_code") or "")
            leg["weight_pct"] = round(float(weights_pct.get(code) or 0.0), 4)
            leg["weight_mode"] = used_mode

        port_ret = _weighted_port_return(legs, weights_pct)
        gross_ret = port_ret
        curr_codes = [str(leg.get("stock_code") or "") for leg in legs if leg.get("stock_code")]
        curr_weights = {
            str(leg["stock_code"]): float(leg.get("weight_pct") or 0.0) for leg in legs
        }
        cost_pct = 0.0
        if apply_costs:
            from core.backtest.costs import (
                estimate_impact_cost,
                rebalance_cost_pct,
            )

            sample_code = (legs[0].get("stock_code") if legs else None) or ""
            sample_bars = stock_bars.get(sample_code) or []
            entry_bar = (date_maps.get(sample_code) or {}).get(entry_date) or {}
            try:
                px = float(entry_bar.get("close") or entry_bar.get("open") or 0)
                vol = float(entry_bar.get("volume") or 0)
            except (TypeError, ValueError):
                px, vol = 0.0, 0.0
            order_value = float(equity) / max(1, len(leg_returns)) * 1000.0
            daily_turnover = px * vol if px > 0 and vol > 0 else 0.0
            impact_bps = estimate_impact_cost(
                order_value, daily_turnover, config=cost_config
            )
            if impact_bps > 0:
                impact_cost_sum_bps += impact_bps
                impact_legs += 1
            cost_pct = rebalance_cost_pct(
                prev_codes,
                curr_codes,
                config=cost_config,
                bars=sample_bars[-40:] if sample_bars else None,
                order_value=order_value,
                daily_volume=daily_turnover,
                prev_weights=prev_weights,
                curr_weights=curr_weights,
            )
            turnover_cost_sum_pct += cost_pct
            port_ret = round(gross_ret - cost_pct, 4)

        prev_codes = list(curr_codes)
        prev_weights = dict(curr_weights)

        # 组合退出日取各腿最晚卖出日（研究近似）
        exit_date = max(leg_exit_dates) if leg_exit_dates else planned_exit_date
        port_ret_r = round(port_ret, 2)
        gross_ret_r = round(gross_ret, 2)
        cost_pct_r = round(cost_pct, 4) if apply_costs else 0.0

        returns.append(port_ret)
        equity *= 1.0 + port_ret / 100.0
        for leg in legs:
            code_l = str(leg.get("stock_code") or "")
            sim_trades.append(
                _sim_trade_row(
                    stock_code=code_l,
                    score=leg.get("score"),
                    signal_date=str(leg.get("signal_date") or signal_date),
                    entry_date=str(leg.get("entry_date") or entry_date),
                    exit_date=str(leg.get("exit_date") or exit_date),
                    intent_price=leg.get("intent_price"),
                    entry_price=leg.get("fill_price"),
                    exit_price=leg.get("exit_price"),
                    return_pct=leg.get("return_pct"),
                    sector=leg.get("sector"),
                    execution_mode=str(leg.get("execution_mode") or mode),
                    exit_deferred_days=int(leg.get("exit_deferred_days") or 0),
                    status="filled",
                    port_return_pct=port_ret_r,
                    port_gross_return_pct=gross_ret_r,
                    port_cost_pct=cost_pct_r,
                    cluster_label=leg.get("cluster_label"),
                    score_weight_source=leg.get("score_weight_source"),
                    factor_weights=leg.get("factor_weights") or {},
                    factor_weights_note=leg.get("factor_weights_note") or "",
                    score_formula=leg.get("score_formula") or "",
                    score_formula_terms=leg.get("score_formula_terms"),
                    factor_coefficients=leg.get("factor_coefficients") or {},
                    return_model_source=leg.get("return_model_source") or "",
                    score_reasons=leg.get("score_reasons") or [],
                    score_raw=leg.get("score_raw"),
                    heuristic_score=leg.get("heuristic_score"),
                    predicted_score=leg.get("predicted_score"),
                    predicted_score_tau=leg.get("predicted_score_tau"),
                    predicted_score_blend=leg.get("predicted_score_blend"),
                    predicted_score_eod_rem=leg.get("predicted_score_eod_rem"),
                    realized_t1_to_tau=leg.get("realized_t1_to_tau"),
                    score_rem=leg.get("score_rem"),
                    gap_pct=leg.get("gap_pct"),
                    event_prior=leg.get("event_prior"),
                    as_of_tau=leg.get("as_of_tau"),
                    y_spec_tau=leg.get("y_spec_tau"),
                    features_tau=leg.get("features_tau"),
                    formula_terms_tau=leg.get("formula_terms_tau"),
                    dual_score_fusion=leg.get("dual_score_fusion"),
                    dual_score_weights=leg.get("dual_score_weights"),
                )
            )
        trades.append(
            {
                "signal_date": dates[i],
                "entry_date": entry_date,
                "exit_date": exit_date,
                "hold_days": horizon_days,
                "return_pct": port_ret_r,
                "gross_return_pct": gross_ret_r,
                "cost_pct": cost_pct_r,
                "legs": legs,
                "top_k": len(legs),
                "neutralization_applied": bool(neut_meta.get("applied")),
                "execution_mode": mode,
                "weight_mode": used_mode,
                "cash_pct": round(max(0.0, 100.0 - sum(weights_pct.values())), 2),
                "exposure_scale": scale,
                "exposure_regime": exp.get("label"),
            }
        )
        equity_curve.append(
            {
                "date": exit_date,
                "equity": round(equity, 2),
                "return_pct": round(port_ret, 2),
            }
        )
        i += horizon_days

    metrics = _trade_metrics(returns, holding_days=horizon_days)
    strategy = "cross_section_topk_neutral" if use_neutral else "cross_section_topk"
    fund_map = fundamentals_by_code or {}
    attribution = attribute_portfolio_trades(trades)
    as_of_sample = dates[min_history - 1] if dates and min_history - 1 < n else None
    from core.fundamentals_pit import fundamentals_pit_summary

    fund_pit_meta = fundamentals_pit_summary(fund_resolves)
    pit = pit_report_for_backtest(
        as_of=as_of_sample,
        windows_checked=pit_windows,
        lookahead_violations=0,
        fundamentals_pit=bool(fund_pit_meta.get("fundamentals_pit")),
        fundamentals_meta=fund_pit_meta,
    )
    avg_impact = (
        round(impact_cost_sum_bps / impact_legs, 4) if impact_legs else 0.0
    )
    raw = {
        "success": True,
        "strategy": strategy,
        "params": {
            "engine": "topk_research",
            "top_k": top_k,
            "horizon_days": horizon_days,
            "min_score": min_score,
            "min_history": min_history,
            "apply_costs": apply_costs,
            "stock_count": len(stock_bars),
            "common_dates": n,
            "neutralize": use_neutral,
            "neutralized_rebalances": neutralized_rebalances,
            "fundamentals_used": bool(fund_map) or bool(fund_resolves),
            "fundamentals_count": len(fund_map) or len(
                {i for i, r in enumerate(fund_resolves) if r.get("ok")}
            ),
            "fundamentals_pit_mode": pit_mode if use_fund_pit else "off",
            "avg_impact_bps": avg_impact,
            "impact_legs": impact_legs,
            "cost_mode": "turnover" if apply_costs else "zero",
            "turnover_cost_sum_pct": round(turnover_cost_sum_pct, 4) if apply_costs else 0.0,
            "execution_mode": mode,
            "respect_limit": respect_limit,
            "slippage_tier": (cost_config or {}).get("slippage_tier") if cost_config else slippage_tier,
            "skipped_limit": skipped_limit,
            "skipped_limit_exit": skipped_limit_exit,
            "exit_deferred": exit_deferred,
            "exit_max_defer": defer_cap,
            "dropped_thin_count": len(dropped_thin),
            "weight_mode": resolved_weight_mode,
            "max_position_pct": float(max_position_pct),
            "max_sector_pct": float(max_sector_pct),
            "dropout_n": dropout_n,
            "rank_mode": resolved_rank_mode,
            "min_predicted_score": min_predicted_score,
            "strategy_id": bt_defaults.get("strategy_id"),
            "aligned_to_strategy_spec": True,
            "apply_tau_buy_gate": bool(apply_tau_buy_gate),
            "return_model_source": (
                "cluster_group_beta"
                if cluster_return_models
                else ("walk_forward" if resolved_rank_mode == "predicted_score" else None)
            ),
            "cluster_return_models": len(cluster_return_models),
            "cluster_return_models_raw": cluster_models_raw or len(cluster_return_models),
            "oos_fail_excluded_models": oos_fail_excluded_models,
            "cash_aware_weights": True,
            "sector_coverage": sector_map_coverage(
                list(stock_bars.keys()), sector_map=smap
            ),
            "exposure_scale_mean": (
                round(
                    sum(float(x.get("scale") or 1.0) for x in exposure_scales)
                    / len(exposure_scales),
                    3,
                )
                if exposure_scales
                else 1.0
            ),
            "stop_pct": stop_pct or None,
            "stop_applied": bool(stop_pct and stop_pct > 0),
            "stop_legs_clipped": stop_legs_clipped,
            "return_model_min_samples": min_fit,
            "return_model_ridge_lambda": ridge_lam,
            "return_model_refit_every": refit_every,
            "pred_rank_rebalances": pred_rank_rebalances,
            "heuristic_fallback_rebalances": heuristic_fallback_rebalances,
            "return_model_train_samples": len(train_ys),
            "return_model_last": (
                {
                    "sample_count": return_model.sample_count,
                    "fitted_as_of": return_model.fitted_as_of,
                    "intercept": return_model.intercept,
                    "n_coefs": len(return_model.coefficients),
                }
                if return_model is not None
                else None
            ),
        },
        "dropped_stocks": dropped_thin,
        "pit_report": pit,
        "attribution": attribution,
        "stop_shadow": _stop_shadow_from_trades(
            trades,
            date_maps,
            dates,
            stop_pct=stop_pct,
            holding_days=horizon_days,
            applied_to_main=bool(stop_pct and stop_pct > 0),
        ),
        "metrics": metrics,
        "trade_count": len(trades),
        "sim_trade_count": len(sim_trades),
        "equity_curve": equity_curve,
        "trades": trades,
        "trades_sample": trades[-20:],
        "sim_trades": sim_trades,
        "signal_fill_sample": signal_fill_sample[-40:],
        "note": (
            f"横截面 TopK 回测（权重={resolved_weight_mode}；排序={resolved_rank_mode}）"
            + ("（调仓日截面中性化）" if use_neutral else "")
            + f"；成交={mode}；涨跌停过滤={'开' if respect_limit else '关'}；"
            f"跌停卖出延后≤{defer_cap}日；板别阈值；"
            + ("成本按换手计费（续持不扣往返）；" if apply_costs else "零成本；")
            + "权重按净值%计（现金不计收益）；"
            + (
                (
                    "predicted_score=分组 live OLS β→ŷ_EOD；"
                    if cluster_return_models
                    else "predicted_score=walk-forward 拟合→ŷ_EOD；"
                )
                if resolved_rank_mode == "predicted_score"
                else ""
            )
            + "排序=融合分 blend（ŷ_EOD+ŷ_τ）；表列 score=融合分；tip 仍分列 ŷ_EOD / ŷ_τ；"
            + "引擎=topk_research（独立腿聚合，无纸面 T+1/换手/现金底仓）；"
            + "≠纸面可实现收益，可交易验证见 paper_replay。"
        ),
    }
    from core.backtest.oos_report import attach_robustness_fields

    cost_model = "simple_cn" if apply_costs else "zero"
    sample = None
    for bars in stock_bars.values():
        if bars:
            sample = bars
            break
    # D1：TopK 默认挂源审计
    try:
        from core.data.consistency import attach_source_audit

        raw = attach_source_audit(raw, codes=list(stock_bars.keys()))
    except Exception:  # noqa: BLE001 — 源审计 best-effort，不影响回测结果
        logger.debug("attach_source_audit failed, skipping D1 source audit fields", exc_info=True)
        pass
    return attach_robustness_fields(
        raw,
        cost_model=cost_model,
        sample_bars=sample,
    )


def aggregate_stock_backtests(results: List[dict]) -> Dict[str, Any]:
    """将多票单策略回测结果等权聚合（非真实组合调仓）。"""
    ok_rows = [r for r in results if r.get("success")]
    if not ok_rows:
        return {
            "success": False,
            "error": "无有效单票回测结果",
        }

    totals = []
    win_rates = []
    trade_counts = []
    for row in ok_rows:
        m = row.get("metrics") or {}
        if m.get("total_return_pct") is not None:
            totals.append(float(m["total_return_pct"]))
        if m.get("win_rate_pct") is not None:
            win_rates.append(float(m["win_rate_pct"]))
        trade_counts.append(int(m.get("trade_count") or 0))

    avg_total = sum(totals) / len(totals) if totals else None
    avg_win = sum(win_rates) / len(win_rates) if win_rates else None

    return {
        "success": True,
        "stocks": len(ok_rows),
        "avg_total_return_pct": round(avg_total, 2) if avg_total is not None else None,
        "avg_win_rate_pct": round(avg_win, 2) if avg_win is not None else None,
        "total_trades": sum(trade_counts),
        "note": (
            "引擎=single_stock_wf_agg：等权聚合各票独立 walk-forward 累计收益；"
            "产品名含 Top-K 字样，非组合调仓、非 paper_replay 纸面回放。"
        ),
        "params": {"engine": "single_stock_wf_agg"},
    }
