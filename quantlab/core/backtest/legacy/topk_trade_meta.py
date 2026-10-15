"""TopK 研究探针：成交 tip / sim_trade 元数据（从 topk_backtest 拆出 · P3）。"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
) -> Dict[str, Any]:
    """全局展示权（|β| 派生）；选股真源仍是 return_model。历史回测不再查分组。"""
    del code  # 保留形参，兼容旧调用
    gw = dict(global_weights or {})
    body = _fmt_factor_weights(gw)
    note = "全局展示权" + (f"：{body}" if body else "")
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
    """历史回测只用全局 / walk-forward 模型；忽略 per-code 分组 β。"""
    del code, return_models_by_code
    if return_model is not None:
        return return_model, "walk_forward"
    return None, ""


def _score_tooltip_meta(
    item: Optional[Dict[str, Any]],
    *,
    score: Any = None,
    code: str = "",
    return_model: Any = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    global_weights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """与数据中心 / 交易执行同源的悬浮注释字段（ŷ · β · 分项表）。"""
    fw = _score_factor_weight_meta(
        code,
        global_weights=global_weights,
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
    """补齐 tip 的缺口 / ŷ_oo_rem（信号打分时常无 open，成交后用意图价→开盘价还原）。"""
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
    y_oo = out.get("predicted_score")
    if y_oo is None:
        y_oo = out.get("score")
    rem = out.get("predicted_score_eod_rem")
    # 信号日常把 rem 写成 =ŷ_oo（尚无缺口）；成交后有已实现则必须重算
    need_remap = rem is None or derived
    if not need_remap and rem is not None and y_oo is not None and realized is not None:
        try:
            if abs(float(rem) - float(y_oo)) < 1e-9 and abs(float(realized)) > 1e-9:
                need_remap = True
        except (TypeError, ValueError):
            need_remap = True
    if need_remap and y_oo is not None:
        try:
            from core.signal.dual_score import oo_remaining_at_tau

            rem = oo_remaining_at_tau(y_oo, realized)
        except Exception:  # noqa: BLE001 — ŷ 剩余量 best-effort
            logger.debug("oo_remaining_at_tau failed, skipping predicted_score_eod_rem", exc_info=True)
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

