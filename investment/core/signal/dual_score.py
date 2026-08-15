"""双层 predicted_score：ŷ_EOD + ŷ_τ；同目标正交加权融合。

ŷ_EOD      预估 close[T]/close[T-1]−1
ŷ_τ        预估 close[T]/open[T]−1（rem 头，独立）
ŷ_EOD_rem  = ŷ_EOD − r_{开盘相对昨收}   # 映到同一 OC 目标
ŷ_trade    = (w_eod·ŷ_EOD_rem + w_τ·ŷ_τ) / (w_eod+w_τ)

规范见 docs/predicted-score-chain.md §2.5。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_DUAL_SCORE: Dict[str, Any] = {
    # 正交加权：簿排序用 ŷ_trade=blend(ŷ_EOD_rem, ŷ_τ)；买入另须 ŷ_τ≥floor
    "fusion_mode": "blend",
    "tau": "open",
    "min_predicted_score_tau": 0.0,
    "block_buy_if_tau_missing": False,
    "w_eod": 0.5,
    "w_tau": 0.5,
    # fixed | theme_boost | variance — 决策层合成权，不改两套 Ridge
    "w_mode": "fixed",
    # theme_boost：主题日把 w_τ 乘以此系数后再归一
    "theme_w_tau_boost": 1.25,
    # variance：EOD 侧残差方差代理（百分点²）；τ 侧优先用 rem OOS
    "eod_residual_var": 1.0,
    # 研究影子：ŷ_cascade = ŷ_EOD_rem + (ŷ_τ − α)；不进主排序
    "enable_cascade_shadow": True,
    # A2：刷簿时同池按 ŷ_τ 另写影子簿（不进 execution）
    "enable_tau_shadow_book": True,
    # 有本地分钟缓存时附加 ret_open_to_tau（默认关；开后仍不拉网）
    "enable_minute_tau": False,
    "minute_tau_hm": "09:45",
    "y_spec": {
        "formula": "close[T]/open[T]-1",
        "unit": "pct",
        "tau": "open",
        "note": "ŷ_trade = w·ŷ_EOD_rem + w·ŷ_τ；不替换 EOD predicted_score",
    },
}

_TAU_FEATURE_KEYS = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
    "ret_open_to_tau",
    "sector_ret_to_tau",
)


def normalize_fusion_mode(raw: Any) -> str:
    """产品仅正交加权；residual/f0/f1/f2 入参一律归一到 blend。"""
    _ = raw
    return "blend"


def realized_t1_to_tau_pct(
    gap_pct: Optional[float],
    ret_open_to_tau: Optional[float] = None,
) -> Optional[float]:
    """昨收→τ 已实现 %。开盘 τ 即缺口；更晚再复合 open→τ。"""
    if gap_pct is None and ret_open_to_tau is None:
        return None
    try:
        g = 0.0 if gap_pct is None else float(gap_pct)
        r = 0.0 if ret_open_to_tau is None else float(ret_open_to_tau)
    except (TypeError, ValueError):
        return None
    return round(((1.0 + g / 100.0) * (1.0 + r / 100.0) - 1.0) * 100.0, 6)


def eod_remaining_at_tau(
    y_eod: Optional[float],
    realized_pct: Optional[float],
) -> Optional[float]:
    """把 ŷ_EOD（昨收→收）严格映成与 ŷ_τ 同一目标：T 收相对 T 开。

    (1+ŷ_EOD/100)/(1+r/100)−1；与 ``realized_t1_to_tau_pct`` 同一复合口径。
    无已实现时退回 ŷ_EOD（视作尚未开盘）。
    """
    if y_eod is None:
        return None
    try:
        ye = float(y_eod)
    except (TypeError, ValueError):
        return None
    if realized_pct is None:
        return round(ye, 6)
    try:
        r = float(realized_pct)
    except (TypeError, ValueError):
        return round(ye, 6)
    denom = 1.0 + r / 100.0
    if abs(denom) < 1e-12:
        return None
    return round(((1.0 + ye / 100.0) / denom - 1.0) * 100.0, 6)


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fuse_remaining_heads(
    eod_rem: Optional[float],
    y_tau: Optional[float],
    *,
    w_eod: float = 0.5,
    w_tau: float = 0.5,
) -> Optional[float]:
    """ŷ_trade = 加权融合两个正交的 OC 预估。缺一侧用另一侧。

    两套模型独立训练、互不依赖；只在决策时用权重合成。
    """
    a = _as_float(eod_rem)
    b = _as_float(y_tau)
    if a is None and b is None:
        return None
    if a is None:
        return round(b, 6)  # type: ignore[arg-type]
    if b is None:
        return round(a, 6)
    try:
        we = float(w_eod)
    except (TypeError, ValueError):
        we = 0.5
    try:
        wt = float(w_tau)
    except (TypeError, ValueError):
        wt = 0.5
    s = we + wt
    if abs(s) < 1e-12:
        we, wt, s = 0.5, 0.5, 1.0
    return round((we * a + wt * b) / s, 6)


def merge_tau_features(
    base: Optional[Dict[str, Any]],
    prior: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """合并 Z：base 优先有值；prior 仅填补 base 缺失（避免 tip hydrate 冲掉刷簿截面）。"""
    out: Dict[str, Any] = {}
    if isinstance(base, dict):
        for k, v in base.items():
            if v is not None and v != "":
                out[k] = v
    if isinstance(prior, dict):
        for k, v in prior.items():
            if k not in out and v is not None and v != "":
                out[k] = v
    return out


def resolve_fusion_weights(
    cfg: Dict[str, Any],
    *,
    feats: Optional[Dict[str, Any]] = None,
    rem_model_doc: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float, str]:
    """决策层合成权。返回 (w_eod, w_tau, mode_note)。"""
    try:
        we = float(cfg.get("w_eod") if cfg.get("w_eod") is not None else 0.5)
    except (TypeError, ValueError):
        we = 0.5
    try:
        wt = float(cfg.get("w_tau") if cfg.get("w_tau") is not None else 0.5)
    except (TypeError, ValueError):
        wt = 0.5
    mode = str(cfg.get("w_mode") or "fixed").strip().lower()
    note = "fixed"
    if mode == "theme_boost":
        theme = 0.0
        if isinstance(feats, dict) and feats.get("theme_day") is not None:
            try:
                theme = float(feats.get("theme_day") or 0.0)
            except (TypeError, ValueError):
                theme = 0.0
        if theme >= 0.5:
            try:
                boost = float(cfg.get("theme_w_tau_boost") or 1.25)
            except (TypeError, ValueError):
                boost = 1.25
            wt = wt * max(0.5, boost)
            note = f"theme_boost×{boost:g}"
        else:
            note = "theme_boost(off)"
    elif mode == "variance":
        try:
            ve = float(cfg.get("eod_residual_var") or 1.0)
        except (TypeError, ValueError):
            ve = 1.0
        ve = max(1e-6, ve)
        vt = None
        doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
        oos = doc.get("oos") if isinstance(doc.get("oos"), dict) else {}
        if oos.get("residual_var") is not None:
            try:
                vt = float(oos.get("residual_var"))
            except (TypeError, ValueError):
                vt = None
        if vt is None:
            rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
            oos2 = rm.get("oos") if isinstance(rm.get("oos"), dict) else {}
            if oos2.get("residual_var") is not None:
                try:
                    vt = float(oos2.get("residual_var"))
                except (TypeError, ValueError):
                    vt = None
        if vt is None:
            vt = 1.0
        vt = max(1e-6, float(vt))
        # 精度 ∝ 1/var
        we = 1.0 / ve
        wt = 1.0 / vt
        note = f"variance(ve={ve:g},vt={vt:g})"
    else:
        mode = "fixed"
        note = "fixed"
    s = we + wt
    if abs(s) < 1e-12:
        return 0.5, 0.5, note
    return we / s, wt / s, note


def cascade_tau_shadow(
    eod_rem: Optional[float],
    y_tau: Optional[float],
    *,
    rem_intercept: Optional[float] = None,
) -> Optional[float]:
    """研究影子：ŷ_cascade = ŷ_EOD_rem + (ŷ_τ − α)。α 缺省按 0。"""
    a = _as_float(eod_rem)
    b = _as_float(y_tau)
    if a is None or b is None:
        return None
    try:
        alpha = 0.0 if rem_intercept is None else float(rem_intercept)
    except (TypeError, ValueError):
        alpha = 0.0
    return round(a + (b - alpha), 6)


def _dual_patch_from_config(config: Optional[dict]) -> Dict[str, Any]:
    """从完整 signal_config 或已展开的 dual 块提取补丁。

    回测路径曾把 ``get_dual_score_cfg()`` 结果再传入 ``apply_tau_score_fields``，
    若只认 ``config["dual_score"]`` 嵌套，会丢掉 w_* 并回落到默认 0.5/0.5。
    """
    if not isinstance(config, dict):
        return {}
    nested = config.get("dual_score")
    if isinstance(nested, dict):
        return dict(nested)
    # 已展开：含 w_eod / fusion 等，且不像完整 signal_config（无 weights/scoring）
    markers = (
        "w_eod",
        "w_tau",
        "fusion_mode",
        "min_predicted_score_tau",
        "tau",
        "enable_tau_shadow_book",
    )
    if any(k in config for k in markers) and "weights" not in config and "scoring" not in config:
        patch = {k: config[k] for k in DEFAULT_DUAL_SCORE if k in config}
        if "y_spec" in config:
            patch["y_spec"] = config["y_spec"]
        return patch
    return {}


def get_dual_score_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            config = {}
    raw = dict(DEFAULT_DUAL_SCORE)
    patch = _dual_patch_from_config(config)
    y_spec_patch = patch.pop("y_spec", None)
    raw.update(patch)
    if isinstance(y_spec_patch, dict):
        ys = dict(DEFAULT_DUAL_SCORE.get("y_spec") or {})
        ys.update(y_spec_patch)
        raw["y_spec"] = ys
    raw["fusion_mode"] = normalize_fusion_mode(raw.get("fusion_mode"))
    tau = str(raw.get("tau") or "open").strip().lower() or "open"
    raw["tau"] = tau
    try:
        raw["min_predicted_score_tau"] = float(
            raw["min_predicted_score_tau"]
            if raw.get("min_predicted_score_tau") is not None
            else 0.0
        )
    except (TypeError, ValueError):
        raw["min_predicted_score_tau"] = 0.0
    raw["block_buy_if_tau_missing"] = bool(raw.get("block_buy_if_tau_missing", False))
    try:
        raw["w_eod"] = float(raw["w_eod"] if raw.get("w_eod") is not None else 0.5)
    except (TypeError, ValueError):
        raw["w_eod"] = 0.5
    try:
        raw["w_tau"] = float(raw["w_tau"] if raw.get("w_tau") is not None else 0.5)
    except (TypeError, ValueError):
        raw["w_tau"] = 0.5
    raw["enable_tau_shadow_book"] = bool(raw.get("enable_tau_shadow_book", True))
    raw["enable_cascade_shadow"] = bool(raw.get("enable_cascade_shadow", True))
    raw["enable_minute_tau"] = bool(raw.get("enable_minute_tau", False))
    hm = str(raw.get("minute_tau_hm") or "09:45").strip() or "09:45"
    if ":" not in hm and len(hm) == 4 and hm.isdigit():
        hm = f"{hm[:2]}:{hm[2:]}"
    raw["minute_tau_hm"] = hm
    w_mode = str(raw.get("w_mode") or "fixed").strip().lower()
    if w_mode not in ("fixed", "theme_boost", "variance"):
        w_mode = "fixed"
    raw["w_mode"] = w_mode
    try:
        raw["theme_w_tau_boost"] = float(
            raw["theme_w_tau_boost"]
            if raw.get("theme_w_tau_boost") is not None
            else 1.25
        )
    except (TypeError, ValueError):
        raw["theme_w_tau_boost"] = 1.25
    try:
        raw["eod_residual_var"] = float(
            raw["eod_residual_var"]
            if raw.get("eod_residual_var") is not None
            else 1.0
        )
    except (TypeError, ValueError):
        raw["eod_residual_var"] = 1.0
    return raw


def resolve_predicted_score_eod(item: Optional[dict]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    for k in ("predicted_score_eod", "predicted_score", "score"):
        v = item.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def resolve_predicted_score_tau(item: Optional[dict]) -> Optional[float]:
    """从打分行读取 ŷ_τ（兼容 score_rem / predicted_score_rem）。"""
    if not isinstance(item, dict):
        return None
    for k in ("predicted_score_tau", "score_rem", "predicted_score_rem"):
        v = item.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def compute_predicted_score_blend(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """ŷ_trade：w_eod·ŷ_EOD_rem + w_τ·ŷ_τ（已归一）。"""
    if not isinstance(item, dict):
        return None
    cfg = get_dual_score_cfg(config)
    eod_rem = item.get("predicted_score_eod_rem")
    y_t = resolve_predicted_score_tau(item)
    we, wt, _note = resolve_fusion_weights(
        cfg,
        feats=item.get("features_tau")
        if isinstance(item.get("features_tau"), dict)
        else None,
    )
    fused = fuse_remaining_heads(eod_rem, y_t, w_eod=we, w_tau=wt)
    if fused is not None:
        return fused
    return resolve_predicted_score_eod(item)


def buy_passes_tau_gate(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Tuple[bool, Optional[str]]:
    """ŷ_τ 买入闸（rem 头的 OC 预估）。返回 (ok, skip_reason)。

    始终用原始 ŷ_τ（方案 A：校准 g 只做 tip/研究对照，不进买卖闸）。
    """
    cfg = get_dual_score_cfg(config)
    y_tau = resolve_predicted_score_tau(item)
    floor = float(cfg["min_predicted_score_tau"])
    if y_tau is None:
        if cfg.get("block_buy_if_tau_missing"):
            return False, "ŷ_τ 缺失（dual_score 硬闸）"
        return True, None
    if y_tau < floor:
        return (
            False,
            f"ŷ_τ={y_tau:.3f}% < min_predicted_score_tau({floor})",
        )
    return True, None


def features_tau_snapshot(feats: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    src = feats or {}
    for k in _TAU_FEATURE_KEYS:
        if k in src:
            out[k] = src.get(k)
    return out


# 开盘 τ 验收用的核心五列（不含可选分钟列）
_TAU_CORE_Z_KEYS = (
    "gap_pct",
    "sector_gap_breadth",
    "theme_day",
    "gap_atr",
    "gap_vs_sector",
)


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
) -> Dict[str, Any]:
    """写入双层契约字段（不改 predicted_score / score 主值）。

    ``rem_yhat`` 就是独立训出的 ŷ_τ（open→close），不依赖 ŷ_EOD。
    ŷ_EOD_rem 只是把 ŷ_EOD 减缺口，映到同一目标；ŷ_trade 决策时加权。
    ``residual_delta`` 已废弃，忽略。
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
    y_eod = resolve_predicted_score_eod(signal_item)
    eod_rem = eod_remaining_at_tau(y_eod, realized)
    y_tau = rem_yhat
    we, wt, w_note = resolve_fusion_weights(
        cfg, feats=feat_snap, rem_model_doc=rem_model_doc
    )
    trade = fuse_remaining_heads(eod_rem, y_tau, w_eod=we, w_tau=wt)

    rem_intercept = None
    try:
        doc = rem_model_doc if isinstance(rem_model_doc, dict) else {}
        rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
        if isinstance(rm, dict) and rm.get("intercept") is not None:
            rem_intercept = float(rm.get("intercept"))
    except (TypeError, ValueError):
        rem_intercept = None
    cascade = None
    if cfg.get("enable_cascade_shadow", True):
        cascade = cascade_tau_shadow(
            eod_rem, y_tau, rem_intercept=rem_intercept
        )

    signal_item["predicted_score_eod"] = signal_item.get("predicted_score")
    signal_item["predicted_score_eod_rem"] = eod_rem
    signal_item["predicted_score_tau_delta"] = None
    signal_item["predicted_score_tau_cascade"] = cascade
    signal_item["realized_t1_to_tau"] = realized
    signal_item["predicted_score_tau"] = y_tau
    signal_item["predicted_score_rem"] = y_tau
    signal_item["score_rem"] = y_tau
    signal_item["as_of_tau"] = as_of
    signal_item["y_spec_tau"] = y_spec
    signal_item["features_tau"] = feat_snap
    signal_item["features_tau_fill"] = fill_diag
    signal_item["gap_pct"] = gap_pct
    signal_item["rem_tau"] = as_of
    signal_item["rem_y_spec"] = y_spec.get("formula")
    signal_item["dual_score_fusion"] = cfg["fusion_mode"]
    if event_prior is not None:
        signal_item["event_prior"] = event_prior
    formula_terms_tau = None
    if feats_merged:
        try:
            from quant.research.rem_ridge import explain_rem_prediction

            formula_terms_tau = explain_rem_prediction(
                feats_merged, model_doc=rem_model_doc
            )
        except Exception:
            formula_terms_tau = None
    if isinstance(formula_terms_tau, dict):
        formula_terms_tau = dict(formula_terms_tau)
        formula_terms_tau["eod_remaining"] = eod_rem
        formula_terms_tau["y_tau"] = y_tau
        formula_terms_tau["trade"] = trade
        formula_terms_tau["cascade"] = cascade
        formula_terms_tau["head"] = "tau_oc"
        formula_terms_tau["features_fill"] = fill_diag
    signal_item["formula_terms_tau"] = formula_terms_tau
    signal_item["score_formula_terms_tau"] = formula_terms_tau
    signal_item["score_formula_tau"] = format_tau_formula_string(formula_terms_tau)
    signal_item["predicted_score_blend"] = trade
    signal_item["dual_score_weights"] = {
        "w_eod": round(we, 6),
        "w_tau": round(wt, 6),
        "w_mode": cfg.get("w_mode") or "fixed",
        "w_note": w_note,
        "mode": "blend",
        "tau_available": y_tau is not None,
    }
    return signal_item


def attach_dual_score_pit(
    signal_item: Dict[str, Any],
    *,
    quote: Optional[dict] = None,
    bars: Optional[Sequence[dict]] = None,
    config: Optional[dict] = None,
    rem_model_doc: Optional[Dict[str, Any]] = None,
    sector_gap_breadth: Optional[float] = None,
) -> Dict[str, Any]:
    """历史回测 / 无实时行情时：用 PIT 日线 quote·bars 挂 ŷ_τ + blend。

    缺口 = open[T]/close[T−1]（与 live ``gap_pct_from_quote_bars`` 同口径）。
    不拉同伴行情；``sector_gap_breadth`` 可由调用方截面预计算后传入。
    无 rem 模型时仍写契约字段（ŷ_τ=None，ŷ_trade 退回 ŷ_EOD_rem）。
    """
    if not isinstance(signal_item, dict):
        return signal_item
    q = quote if isinstance(quote, dict) else signal_item.get("_bt_quote")
    b = bars if bars is not None else signal_item.get("_bt_bars")
    cfg = get_dual_score_cfg(config)
    gap_v = None
    try:
        from core.event_prior import gap_pct_from_quote_bars, get_event_prior_cfg

        gap_v = gap_pct_from_quote_bars(q, b)
        ep_cfg = get_event_prior_cfg()
        trigger = float(ep_cfg.get("gap_trigger_pct") or 2)
    except Exception:
        trigger = 2.0
    theme = 0.0
    breadth = sector_gap_breadth
    if breadth is None and signal_item.get("sector_gap_breadth") is not None:
        try:
            breadth = float(signal_item.get("sector_gap_breadth"))
        except (TypeError, ValueError):
            breadth = None
    try:
        from core.research.rem_theme import resolve_theme_day

        theme = resolve_theme_day(
            gap_pct=gap_v,
            sector_breadth=breadth,
            pool_gaps=signal_item.get("_pool_gaps")
            if isinstance(signal_item.get("_pool_gaps"), (list, tuple))
            else None,
            gap_trigger_pct=trigger,
        )
    except Exception:
        theme = 1.0 if (gap_v is not None and abs(float(gap_v)) >= trigger) else 0.0
    feats: Dict[str, Any] = {
        "gap_pct": gap_v,
        "sector_gap_breadth": breadth,
        "theme_day": theme,
    }
    try:
        from core.research.rem_panel import (
            gap_atr_from_hist,
            gap_vs_sector_value,
            hist_bars_pit,
            _finite_median,
        )

        asof = ""
        if isinstance(q, dict):
            asof = str(q.get("date") or q.get("trade_date") or "")[:10]
        hist = hist_bars_pit(b, asof_date=asof)
        feats["gap_atr"] = gap_atr_from_hist(gap_v, hist)
        pool = signal_item.get("_pool_gaps")
        ref = None
        if isinstance(pool, (list, tuple)) and pool:
            ref = _finite_median(
                [float(g) for g in pool if g is not None]
            )
        feats["gap_vs_sector"] = gap_vs_sector_value(gap_v, ref)
    except Exception:
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
    # 只传 rem 头 Z 特征；勿塞全日线 sub_scores（训练未用，易误导）
    rem_yhat = None
    try:
        from quant.research.rem_ridge import predict_rem_from_features

        rem_yhat = predict_rem_from_features(feats, model_doc=rem_model_doc)
    except Exception:
        rem_yhat = None
    ep = None
    if gap_v is not None:
        try:
            ep = {
                "theme": bool(theme),
                "gap_pct": gap_v,
                "warnings": [],
            }
        except Exception:
            ep = None
    apply_tau_score_fields(
        signal_item,
        rem_yhat=rem_yhat,
        gap_pct=gap_v,
        feats=feats,
        event_prior=ep,
        # 传完整 signal_config（或调用方原 config），勿传已 flatten 的 dual 块
        config=config,
        as_of_tau=str(cfg.get("tau") or "open"),
        rem_model_doc=rem_model_doc,
    )
    return signal_item


def build_tau_shadow_book(
    eligible_rows: Sequence[Dict[str, Any]],
    *,
    max_names: int,
    eod_book: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """同 EOD 入池集合按 ŷ_τ 降序截断；缺失 τ 排末。不改主簿。"""
    max_n = max(1, int(max_names or 1))
    rows: List[Dict[str, Any]] = []
    missing_tau = 0
    for r in eligible_rows or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        y_t = resolve_predicted_score_tau(row)
        if y_t is None:
            missing_tau += 1
        row["_tau_rank_key"] = (
            float(y_t) if y_t is not None else float("-inf")
        )
        rows.append(row)
    rows.sort(key=lambda x: float(x.get("_tau_rank_key") or float("-inf")), reverse=True)
    book: List[Dict[str, Any]] = []
    for i, r in enumerate(rows[:max_n]):
        r.pop("_tau_rank_key", None)
        r["rank"] = i + 1
        r["rank_key"] = "predicted_score_tau"
        book.append(r)
    for r in rows[max_n:]:
        r.pop("_tau_rank_key", None)

    compare = compare_book_overlap(eod_book or [], book)
    meta = {
        "mode": "tau_shadow",
        "rank_key": "predicted_score_tau",
        "max_names": max_n,
        "eligible_count": len(rows),
        "missing_tau_count": missing_tau,
        "note": "A2 影子簿：同池按 ŷ_τ 重排；不驱动 execution / 纸面买入",
        "vs_eod_book": compare,
    }
    return book, meta


def compare_book_overlap(
    book_a: Sequence[Dict[str, Any]],
    book_b: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """两簿代码重叠与 Top 秩对照（A2 验收用）。"""

    def _codes(book: Sequence[Dict[str, Any]]) -> List[str]:
        out: List[str] = []
        for r in book or []:
            if not isinstance(r, dict):
                continue
            c = str(r.get("stock_code") or "").strip()
            if c:
                out.append(c)
        return out

    a = _codes(book_a)
    b = _codes(book_b)
    set_a, set_b = set(a), set(b)
    inter = set_a & set_b
    union = set_a | set_b
    jaccard = (len(inter) / len(union)) if union else None
    # 共享代码在两簿中的秩 Spearman（近似：秩差平方）
    rank_a = {c: i + 1 for i, c in enumerate(a)}
    rank_b = {c: i + 1 for i, c in enumerate(b)}
    shared = [c for c in a if c in rank_b]
    spearman = None
    if len(shared) >= 2:
        n = len(shared)
        d2 = sum((rank_a[c] - rank_b[c]) ** 2 for c in shared)
        spearman = round(1.0 - (6.0 * d2) / (n * (n * n - 1)), 4)
    return {
        "n_a": len(a),
        "n_b": len(b),
        "overlap": len(inter),
        "jaccard": round(jaccard, 4) if jaccard is not None else None,
        "spearman_shared": spearman,
        "only_eod": sorted(set_a - set_b)[:12],
        "only_tau": sorted(set_b - set_a)[:12],
    }


def _eod_return_model_for_item(item: dict):
    """取该票打分时同源的 EOD ReturnScoreModel（反推 sub_scores 用）。"""
    code = str(item.get("stock_code") or "").strip()
    try:
        from core.signal.cluster_live import load_cluster_return_models_by_code

        models = load_cluster_return_models_by_code() or {}
        if code and code in models:
            return models[code]
    except Exception:
        pass
    try:
        from core.signal.return_score_store import load_return_model

        rm, _meta = load_return_model(prefer_active=True)
        return rm
    except Exception:
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
    eod_rem = expl.get("eod_remaining")
    trade = expl.get("trade")
    y_tau = expl.get("y_tau")
    if y_tau is None:
        y_tau = expl.get("total")
    if eod_rem is not None and trade is not None and y_tau is not None:
        try:
            return (
                f"ŷ_trade = w·ŷ_EOD_rem({float(eod_rem):+.3f}) + "
                f"w·ŷ_τ({float(y_tau):+.3f}) = {float(trade):.3f}%"
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
    for k in ("sector_gap_breadth", "theme_day", "gap_atr", "gap_vs_sector", "ret_open_to_tau"):
        v = item.get(k)
        if v is not None and v != "":
            feats.setdefault(k, v)

    try:
        from quant.research.rem_ridge import REM_Z_FEATURES

        z_keys = set(REM_Z_FEATURES) | {"open_gap"}
    except Exception:
        z_keys = {
            "gap_pct",
            "open_gap",
            "sector_gap_breadth",
            "theme_day",
            "gap_atr",
            "gap_vs_sector",
            "ret_open_to_tau",
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

    if (
        isinstance(existing, dict)
        and isinstance(existing.get("terms"), list)
        and existing["terms"]
        and not _stale_imputed_z(existing)
    ):
        return existing

    try:
        feats.update(recover_sub_scores_for_tau(item))
    except Exception:
        pass
    if feats:
        try:
            from quant.research.rem_ridge import explain_rem_prediction

            expl = explain_rem_prediction(feats)
            if expl is not None:
                return expl
        except Exception:
            pass
    if (
        isinstance(existing, dict)
        and isinstance(existing.get("terms"), list)
        and existing["terms"]
    ):
        return existing
    return None


def rem_factor_coefficients_public() -> Dict[str, float]:
    """rem Ridge β 快照（tip「τ 因子系数」）。"""
    try:
        from quant.research.rem_ridge import load_rem_model

        doc = load_rem_model() or {}
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
    except Exception:
        return {}


def dual_score_book_fields(item: Optional[dict]) -> Dict[str, Any]:
    """簿/观察行透传字段。

    ``dual_score_fusion`` / ``dual_score_weights`` 一律用当前配置，
    避免簿内旧戳（如 f1 / 旧 w_*）误导 tip。
    旧簿无 ``formula_terms_tau`` 时现场补全组成表。
    """
    if not isinstance(item, dict):
        return {}
    try:
        cfg = get_dual_score_cfg()
        live_fusion = cfg.get("fusion_mode")
        book_w = item.get("dual_score_weights")
        if isinstance(book_w, dict) and book_w.get("w_eod") is not None:
            live_w = {
                "w_eod": book_w.get("w_eod"),
                "w_tau": book_w.get("w_tau"),
                "w_mode": book_w.get("w_mode") or cfg.get("w_mode") or "fixed",
                "w_note": book_w.get("w_note"),
                "mode": "blend",
                "tau_available": book_w.get("tau_available"),
            }
        else:
            live_w = {
                "w_eod": cfg.get("w_eod"),
                "w_tau": cfg.get("w_tau"),
                "w_mode": cfg.get("w_mode") or "fixed",
                "mode": "blend",
            }
    except Exception:
        live_fusion = "blend"
        live_w = {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "fixed", "mode": "blend"}
    formula_terms_tau = ensure_formula_terms_tau(item)
    score_formula_tau = item.get("score_formula_tau") or format_tau_formula_string(
        formula_terms_tau
    )
    coefs_tau = item.get("factor_coefficients_tau")
    if not isinstance(coefs_tau, dict) or not coefs_tau:
        coefs_tau = rem_factor_coefficients_public()
    fill = item.get("features_tau_fill")
    if not isinstance(fill, dict):
        fill = features_tau_fill_diag(item.get("features_tau"))
    # tip：有 live 模型就补 g(ŷ) 对照（force）；排序/买卖闸始终用 raw
    cal_applied = False
    cal_enabled = False
    try:
        from core.signal.score_calibration import (
            attach_calibrated_scores,
            calibration_enabled,
            load_calibration_model,
        )

        live = load_calibration_model()
        cal_enabled = calibration_enabled(model_doc=live)
        if isinstance(live, dict) and isinstance(live.get("heads"), dict):
            attach_calibrated_scores(item, model_doc=live, force=True)
        cal_applied = bool(item.get("score_calibration_applied"))
        cal_enabled = bool(item.get("score_calibration_enabled", cal_enabled))
    except Exception:
        cal_applied = bool(item.get("score_calibration_applied"))
        cal_enabled = bool(item.get("score_calibration_enabled"))
    # 权重优先簿内已算（含 theme/variance）；缺则用当前配置
    return {
        "predicted_score_eod": item.get("predicted_score_eod", item.get("predicted_score")),
        "predicted_score_eod_rem": item.get("predicted_score_eod_rem"),
        "predicted_score_tau": item.get("predicted_score_tau", item.get("score_rem")),
        "predicted_score_tau_delta": item.get("predicted_score_tau_delta"),
        "predicted_score_tau_cascade": item.get("predicted_score_tau_cascade"),
        "predicted_score_blend": item.get("predicted_score_blend"),
        "predicted_score_cal": item.get("predicted_score_cal"),
        "predicted_score_eod_rem_cal": item.get("predicted_score_eod_rem_cal"),
        "predicted_score_tau_cal": item.get("predicted_score_tau_cal"),
        "predicted_score_blend_cal": item.get("predicted_score_blend_cal"),
        "score_calibration_applied": cal_applied,
        "score_calibration_enabled": cal_enabled,
        "score_calibration_eod_oor": bool(item.get("score_calibration_eod_oor")),
        "score_calibration_eod_rem_oor": bool(item.get("score_calibration_eod_rem_oor")),
        "score_calibration_tau_oor": bool(item.get("score_calibration_tau_oor")),
        "score_calibration_note": item.get("score_calibration_note"),
        "score_calibration_partial": item.get("score_calibration_partial"),
        "realized_t1_to_tau": item.get("realized_t1_to_tau"),
        "score_rem": item.get("score_rem"),
        "predicted_score_rem": item.get("predicted_score_rem"),
        "as_of_tau": item.get("as_of_tau") or item.get("rem_tau"),
        "y_spec_tau": item.get("y_spec_tau"),
        "features_tau": item.get("features_tau"),
        "features_tau_fill": fill,
        "formula_terms_tau": formula_terms_tau,
        "score_formula_terms_tau": formula_terms_tau,
        "score_formula_tau": score_formula_tau,
        "factor_coefficients_tau": coefs_tau or None,
        "gap_pct": item.get("gap_pct"),
        "event_prior": item.get("event_prior"),
        "dual_score_fusion": live_fusion or "blend",
        "dual_score_weights": live_w,
    }


def eod_gate_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """入簿 / 买卖 EOD 门槛用分：始终原始 ŷ_EOD（校准 g 不进闸）。"""
    _ = config
    return resolve_predicted_score_eod(item)


def decision_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """排序 / 表列 / 调仓报告主分：与 ``rank_key_for_item`` 相同（raw ŷ_trade）。"""
    return rank_key_for_item(item, config=config)


def rank_key_for_item(item: Optional[dict], *, config: Optional[dict] = None) -> Optional[float]:
    """排序键：ŷ_trade = 加权融合 ŷ_EOD_rem 与 ŷ_τ（原始分）。

    校准 g 仅写入 ``*_cal`` 供 tip/研究对照，不改变排序键。
    """
    if not isinstance(item, dict):
        return None
    b = item.get("predicted_score_blend")
    if b is None:
        b = compute_predicted_score_blend(item, config=config)
    try:
        return float(b) if b is not None else resolve_predicted_score_eod(item)
    except (TypeError, ValueError):
        return resolve_predicted_score_eod(item)
