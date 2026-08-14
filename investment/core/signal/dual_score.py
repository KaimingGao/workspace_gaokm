"""双层 predicted_score：ŷ_EOD + ŷ_τ；现网仅融合分（blend 排序 + τ 买入闸）。

规范见 docs/predicted-score-chain.md §2.5 · docs/tau-contract-and-partition-upgrade.md §9。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_DUAL_SCORE: Dict[str, Any] = {
    # 现网仅 blend：簿排序用 predicted_score_blend；买入另须 ŷ_τ≥floor；主字段仍为 EOD
    "fusion_mode": "f2",
    "tau": "open",
    "min_predicted_score_tau": 0.0,
    "block_buy_if_tau_missing": False,
    "w_eod": 0.5,
    "w_tau": 0.5,
    # A2：刷簿时同池按 ŷ_τ 另写影子簿（不进 execution）
    "enable_tau_shadow_book": True,
    # 有本地分钟缓存时附加 ret_open_to_tau（默认关；开后仍不拉网）
    "enable_minute_tau": False,
    "minute_tau_hm": "09:45",
    "y_spec": {
        "formula": "close[T]/open[T]-1",
        "unit": "pct",
        "tau": "open",
        "note": "当日剩余收益头；不替换 EOD predicted_score",
    },
}

_TAU_FEATURE_KEYS = (
    "gap_pct",
    "open_gap",
    "sector_gap_breadth",
    "theme_day",
    "ret_open_to_tau",
)


def normalize_fusion_mode(raw: Any) -> str:
    """产品仅保留融合分（blend）；f0/f1 入参一律归一到 f2。"""
    _ = raw
    return "f2"


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
    raw["enable_minute_tau"] = bool(raw.get("enable_minute_tau", False))
    hm = str(raw.get("minute_tau_hm") or "09:45").strip() or "09:45"
    if ":" not in hm and len(hm) == 4 and hm.isdigit():
        hm = f"{hm[:2]}:{hm[2:]}"
    raw["minute_tau_hm"] = hm
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
    """F2：s = w_eod·ŷ_EOD + w_τ·ŷ_τ；缺 τ 则退回 EOD。不对账单一 y。"""
    cfg = get_dual_score_cfg(config)
    y_e = resolve_predicted_score_eod(item)
    y_t = resolve_predicted_score_tau(item)
    if y_e is None and y_t is None:
        return None
    if y_t is None:
        return y_e
    if y_e is None:
        return y_t
    w_e = float(cfg.get("w_eod") if cfg.get("w_eod") is not None else 0.5)
    w_t = float(cfg.get("w_tau") if cfg.get("w_tau") is not None else 0.5)
    denom = w_e + w_t
    if abs(denom) < 1e-12:
        return y_e
    return round((w_e * y_e + w_t * y_t) / denom, 6)


def buy_passes_tau_gate(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Tuple[bool, Optional[str]]:
    """ŷ_τ 买入闸（融合分模式下始终启用）。返回 (ok, skip_reason)。"""
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
) -> Dict[str, Any]:
    """写入双层契约字段（不改 predicted_score / score 主值）。"""
    cfg = get_dual_score_cfg(config)
    tau = str(cfg.get("tau") or "open")
    y_spec = dict(cfg.get("y_spec") or DEFAULT_DUAL_SCORE["y_spec"])
    y_spec["tau"] = tau
    if tau == "open":
        y_spec.setdefault("formula", "close[T]/open[T]-1")
    if isinstance(y_spec_override, dict):
        y_spec.update(y_spec_override)
    as_of = as_of_tau if as_of_tau is not None else tau

    signal_item["predicted_score_eod"] = signal_item.get("predicted_score")
    signal_item["predicted_score_tau"] = rem_yhat
    signal_item["predicted_score_rem"] = rem_yhat
    signal_item["score_rem"] = rem_yhat
    signal_item["as_of_tau"] = as_of
    signal_item["y_spec_tau"] = y_spec
    signal_item["features_tau"] = features_tau_snapshot(feats)
    signal_item["gap_pct"] = gap_pct
    signal_item["rem_tau"] = as_of
    signal_item["rem_y_spec"] = y_spec.get("formula")
    signal_item["dual_score_fusion"] = cfg["fusion_mode"]
    if event_prior is not None:
        signal_item["event_prior"] = event_prior
    formula_terms_tau = None
    if feats:
        try:
            from quant.research.rem_ridge import explain_rem_prediction

            formula_terms_tau = explain_rem_prediction(feats)
        except Exception:
            formula_terms_tau = None
    signal_item["formula_terms_tau"] = formula_terms_tau
    signal_item["score_formula_terms_tau"] = formula_terms_tau
    signal_item["score_formula_tau"] = format_tau_formula_string(formula_terms_tau)
    blend = compute_predicted_score_blend(signal_item, config=config)
    signal_item["predicted_score_blend"] = blend
    signal_item["dual_score_weights"] = {
        "w_eod": cfg.get("w_eod"),
        "w_tau": cfg.get("w_tau"),
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
    无 rem 模型时仍写契约字段（ŷ_τ=None，blend 退回 EOD）。
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
    theme = 1.0 if (gap_v is not None and float(gap_v) >= trigger) else 0.0
    breadth = sector_gap_breadth
    if breadth is None and signal_item.get("sector_gap_breadth") is not None:
        try:
            breadth = float(signal_item.get("sector_gap_breadth"))
        except (TypeError, ValueError):
            breadth = None
    feats: Dict[str, Any] = {
        "gap_pct": gap_v,
        "open_gap": gap_v,
        "sector_gap_breadth": breadth,
        "theme_day": theme,
    }
    for k, v in (signal_item.get("sub_scores") or {}).items():
        if k in feats:
            continue
        try:
            feats[k] = float(v) if v is not None else None
        except (TypeError, ValueError):
            feats[k] = None
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
    """保证 tip 有 ŷ_τ 组成：已有则用；否则用 sub_scores / EOD 反推 + rem 拆解。"""
    if not isinstance(item, dict):
        return None
    existing = item.get("formula_terms_tau") or item.get("score_formula_terms_tau")
    if (
        isinstance(existing, dict)
        and isinstance(existing.get("terms"), list)
        and existing["terms"]
    ):
        return existing

    feats: Dict[str, Any] = {}
    feats.update(recover_sub_scores_for_tau(item))
    ft = item.get("features_tau")
    if isinstance(ft, dict):
        for k, v in ft.items():
            if v is not None and v != "":
                feats[k] = v
    gap = item.get("gap_pct")
    if gap is not None and gap != "":
        feats.setdefault("gap_pct", gap)
        feats.setdefault("open_gap", gap)
    if not feats:
        return existing if isinstance(existing, dict) else None
    try:
        from quant.research.rem_ridge import explain_rem_prediction

        expl = explain_rem_prediction(feats)
    except Exception:
        expl = None
    return expl if expl is not None else (existing if isinstance(existing, dict) else None)


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
        live_w = {
            "w_eod": cfg.get("w_eod"),
            "w_tau": cfg.get("w_tau"),
        }
    except Exception:
        live_fusion = "f2"
        live_w = {"w_eod": 0.5, "w_tau": 0.5}
    formula_terms_tau = ensure_formula_terms_tau(item)
    score_formula_tau = item.get("score_formula_tau") or format_tau_formula_string(
        formula_terms_tau
    )
    coefs_tau = item.get("factor_coefficients_tau")
    if not isinstance(coefs_tau, dict) or not coefs_tau:
        coefs_tau = rem_factor_coefficients_public()
    # 权重与 fusion 一样用当前配置，避免簿内旧 w_* 误导 tip
    return {
        "predicted_score_eod": item.get("predicted_score_eod", item.get("predicted_score")),
        "predicted_score_tau": item.get("predicted_score_tau", item.get("score_rem")),
        "predicted_score_blend": item.get("predicted_score_blend"),
        "score_rem": item.get("score_rem"),
        "predicted_score_rem": item.get("predicted_score_rem"),
        "as_of_tau": item.get("as_of_tau") or item.get("rem_tau"),
        "y_spec_tau": item.get("y_spec_tau"),
        "features_tau": item.get("features_tau"),
        "formula_terms_tau": formula_terms_tau,
        "score_formula_terms_tau": formula_terms_tau,
        "score_formula_tau": score_formula_tau,
        "factor_coefficients_tau": coefs_tau or None,
        "gap_pct": item.get("gap_pct"),
        "event_prior": item.get("event_prior"),
        "dual_score_fusion": live_fusion or "f2",
        "dual_score_weights": live_w,
    }


def rank_key_for_item(item: Optional[dict], *, config: Optional[dict] = None) -> Optional[float]:
    """排序键：融合分 blend（缺则 EOD）。买入门槛仍看 EOD+τ 闸。"""
    b = None
    if isinstance(item, dict):
        b = item.get("predicted_score_blend")
        if b is None:
            b = compute_predicted_score_blend(item, config=config)
    try:
        return float(b) if b is not None else resolve_predicted_score_eod(item)
    except (TypeError, ValueError):
        return resolve_predicted_score_eod(item)
