"""双层 ŷ：隔夜 open 链 ŷ_ON 字段挂载（风控旁路，不进主排序）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_Y_SPEC_ON: Dict[str, Any] = {
    "formula": "open[T+1]/open[T]-1",
    "unit": "pct",
    "anchor": "open[T]",
    "note": "隔夜 open 链；风控旁路，不进主排序",
}

_ON_FEATURE_KEYS = (
    "ret_oc",
    "gap_pct",
    "ret_cc",
    "y_on_today",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "ret_open_to_tau",
)


def features_on_snapshot(feats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    src = feats or {}
    for k in _ON_FEATURE_KEYS:
        if k in src and src.get(k) is not None and src.get(k) != "":
            out[k] = src.get(k)
    return out


def merge_on_features(
    computed: Optional[Dict[str, Any]],
    prior: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(prior, dict):
        out.update(prior)
    if isinstance(computed, dict):
        for k, v in computed.items():
            if v is not None and v != "":
                out[k] = v
    return out


def apply_on_score_fields(
    signal_item: Dict[str, Any],
    *,
    on_yhat: Optional[float],
    feats: Optional[Dict[str, Any]] = None,
    y_spec_override: Optional[Dict[str, Any]] = None,
    on_model_doc: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写入 ŷ_ON 契约字段（不改 predicted_score / ŷ_trade）。"""
    y_spec = dict(DEFAULT_Y_SPEC_ON)
    doc = on_model_doc if isinstance(on_model_doc, dict) else None
    if doc:
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
        if isinstance(rm, dict) and isinstance(rm.get("y_spec"), dict):
            y_spec.update(rm["y_spec"])
        elif isinstance(doc.get("y_spec_on"), dict):
            y_spec.update(doc["y_spec_on"])
    if isinstance(y_spec_override, dict):
        y_spec.update(y_spec_override)

    prior = (
        signal_item.get("features_on")
        if isinstance(signal_item.get("features_on"), dict)
        else None
    )
    feat_snap = features_on_snapshot(merge_on_features(feats, prior))

    signal_item["predicted_score_on"] = on_yhat
    signal_item["y_spec_on"] = y_spec
    signal_item["features_on"] = feat_snap
    signal_item["on_y_spec"] = y_spec.get("formula")
    signal_item["dual_score_on_head"] = "predicted_score_on"

    formula_terms_on = None
    if feat_snap:
        try:
            from core.research.on_ridge import explain_on_prediction

            formula_terms_on = explain_on_prediction(
                feat_snap, model_doc=on_model_doc
            )
        except Exception:  # noqa: BLE001
            logger.debug("explain_on_prediction failed", exc_info=True)
            formula_terms_on = None
    if isinstance(formula_terms_on, dict):
        formula_terms_on = dict(formula_terms_on)
        formula_terms_on["y_on"] = on_yhat
        signal_item["formula_terms_on"] = formula_terms_on
        signal_item["score_formula_terms_on"] = formula_terms_on
        try:
            total = float(formula_terms_on.get("total") or on_yhat or 0.0)
            signal_item["score_formula_on"] = (
                f"ŷ_ON = open[T+1]/open[T]-1 ≈ {total:+.3f}%"
            )
        except (TypeError, ValueError):
            pass
    return signal_item


def ensure_formula_terms_on(item: Optional[dict]) -> Optional[Dict[str, Any]]:
    """保证 tip 有 ŷ_ON 组成：features_on 齐时重拆，勿沿用全缺特征旧戳。"""
    if not isinstance(item, dict):
        return None
    existing = item.get("formula_terms_on") or item.get("score_formula_terms_on")

    feats: Dict[str, Any] = {}
    ft = item.get("features_on")
    if isinstance(ft, dict):
        for k, v in ft.items():
            if v is not None and v != "":
                feats[k] = v
    gap = item.get("gap_pct")
    if gap is not None and gap != "":
        feats.setdefault("gap_pct", gap)
    for k in (
        "sector_gap_breadth",
        "theme_day",
        "gap_atr",
        "gap_vs_sector",
        "ret_open_to_tau",
        "ret_oc",
        "ret_cc",
        "y_on_today",
    ):
        v = item.get(k)
        if v is not None and v != "":
            feats.setdefault(k, v)

    z_keys = set(_ON_FEATURE_KEYS)

    def _stale_imputed_z(expl: Optional[dict]) -> bool:
        if not isinstance(expl, dict):
            return True
        terms = expl.get("terms") or []
        if not terms:
            return True
        if not feats:
            return False
        has_path = any(feats.get(k) is not None for k in ("ret_oc", "ret_cc", "y_on_today"))
        if not has_path:
            return False
        for t in terms:
            if not isinstance(t, dict):
                continue
            key = str(t.get("key") or "")
            if key not in z_keys:
                continue
            if t.get("note") and feats.get(key) is not None:
                return True
            if feats.get(key) is not None and float(t.get("z") or 0.0) == 0.0:
                return True
        return False

    if (
        isinstance(existing, dict)
        and isinstance(existing.get("terms"), list)
        and existing["terms"]
        and not _stale_imputed_z(existing)
    ):
        return existing

    if not feats:
        return existing if isinstance(existing, dict) else None
    try:
        from core.research.on_ridge import explain_on_prediction

        expl = explain_on_prediction(feats)
        if isinstance(expl, dict):
            y_on = item.get("predicted_score_on")
            if y_on is not None:
                expl = dict(expl)
                expl["y_on"] = y_on
            return expl
    except Exception:  # noqa: BLE001
        logger.debug("ensure_formula_terms_on failed", exc_info=True)
    return existing if isinstance(existing, dict) else None


def attach_on_score_pit(
    signal_item: Dict[str, Any],
    *,
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    config: Optional[dict] = None,
    on_model_doc: Optional[Dict[str, Any]] = None,
    sector_gap_breadth: Optional[float] = None,
    theme_day: Optional[float] = None,
    gap_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """PIT / 回测：用日线 quote·bars 挂 ŷ_ON。"""
    _ = config
    if not isinstance(signal_item, dict):
        return signal_item
    if on_model_doc is None:
        try:
            from core.research.on_ridge import load_on_model

            on_model_doc = load_on_model()
        except Exception:  # noqa: BLE001
            logger.debug("load_on_model failed", exc_info=True)
            on_model_doc = None

    try:
        from core.research.on_panel import build_on_features_from_quote_bars
        from core.research.on_ridge import predict_on_from_features

        feats = build_on_features_from_quote_bars(quote, bars, gap_pct=gap_pct)
        if gap_pct is not None:
            feats["gap_pct"] = gap_pct
        if sector_gap_breadth is not None:
            feats["sector_gap_breadth"] = sector_gap_breadth
        if theme_day is not None:
            feats["theme_day"] = theme_day
        elif signal_item.get("theme_day") is not None:
            feats.setdefault("theme_day", signal_item.get("theme_day"))
        if signal_item.get("gap_vs_sector") is not None:
            feats.setdefault("gap_vs_sector", signal_item.get("gap_vs_sector"))
        ft = signal_item.get("features_tau")
        if isinstance(ft, dict) and ft.get("gap_vs_sector") is not None:
            feats.setdefault("gap_vs_sector", ft.get("gap_vs_sector"))
        prior = signal_item.get("features_on")
        if isinstance(prior, dict):
            feats = merge_on_features(feats, prior)
        on_yhat = predict_on_from_features(feats, model_doc=on_model_doc)
        apply_on_score_fields(
            signal_item,
            on_yhat=on_yhat,
            feats=feats,
            on_model_doc=on_model_doc,
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach_on_score_pit failed", exc_info=True)
        apply_on_score_fields(
            signal_item,
            on_yhat=None,
            feats=None,
            on_model_doc=on_model_doc,
        )
    return signal_item


def _holding_px_pair(value: Any, unit: str, *, currency: str = "CNY") -> tuple[Optional[float], Optional[str]]:
    """持仓经济字段 → (raw, display)。"""
    if value is None or value == "":
        return None, None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None, None
    if not (n > 0):
        return None, None
    u = str(unit or "元")
    if currency in (None, "", "CNY"):
        return n, f"{n:.2f}{u}"
    return n, f"{u}{n:.2f}"


def holding_row_as_quote(row: dict) -> dict:
    """持仓摘要行 → ON 特征用的 quote 形 dict（与观察簿 hydrate 同源）。"""
    unit = str(row.get("unit") or "元")
    currency = str(row.get("currency") or "CNY")
    pr, ps = _holding_px_pair(row.get("price"), unit, currency=currency)
    oraw, os = _holding_px_pair(row.get("open"), unit, currency=currency)
    chg = row.get("change_pct")
    prev = row.get("prev_close")
    if prev is None and pr is not None and chg is not None:
        try:
            denom = 1.0 + float(chg) / 100.0
            if abs(denom) > 1e-12:
                prev = pr / denom
        except (TypeError, ValueError, ZeroDivisionError):
            prev = None
    q: Dict[str, Any] = {
        "success": pr is not None,
        "stock_code": row.get("stock_code"),
        "price_raw": pr,
        "price": ps,
        "open_raw": oraw,
        "open": os,
        "change_percent": chg,
        "change_pct": chg,
        "unit": unit,
        "currency": currency,
    }
    if prev is not None:
        try:
            pn = float(prev)
            if pn > 0:
                q["prev_close"] = pn
        except (TypeError, ValueError):
            pass
    return q


def hydrate_holding_on_fields(row: dict) -> None:
    """持仓表：簿/打包行缺 ŷ_ON 时用行情+日线补算（in-place）。"""
    if not isinstance(row, dict):
        return
    try:
        from core.signal.dual_score import is_heuristic_score_scale

        if is_heuristic_score_scale(row):
            return
    except Exception:  # noqa: BLE001
        pass
    on = row.get("predicted_score_on")
    if on is not None and on != "":
        try:
            if float(on) == float(on):
                return
        except (TypeError, ValueError):
            pass
    code = str(row.get("stock_code") or "").strip()
    if not code:
        return
    quote = holding_row_as_quote(row)
    bars: list = []
    try:
        from core.data.facade import get_bars

        pack = get_bars(
            code,
            limit=45,
            offline_ok=True,
            cache_max_age_hours=72.0,
        )
        bars = list(pack.get("bars") or [])
    except Exception:  # noqa: BLE001
        logger.debug("hydrate_holding_on_fields get_bars failed", exc_info=True)
        bars = []
    gap = row.get("gap_pct")
    if gap is None:
        try:
            from core.event_prior import gap_pct_from_quote_bars

            gap = gap_pct_from_quote_bars(quote, bars)
            if gap is not None:
                row["gap_pct"] = gap
        except Exception:  # noqa: BLE001
            logger.debug("hydrate_holding_on_fields gap failed", exc_info=True)
    attach_on_score_pit(
        row,
        quote=quote,
        bars=bars,
        gap_pct=gap,
        sector_gap_breadth=row.get("sector_gap_breadth"),
        theme_day=row.get("theme_day"),
    )
