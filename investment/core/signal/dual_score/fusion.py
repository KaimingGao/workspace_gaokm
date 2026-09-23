"""双层 ŷ：融合算术（缺口抬升、加权、cascade）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

from core.signal.yhat_geom import (
    compound_pct,
    rem_obs_var,
    remaining_at_tau,
    resolve_oo_prior_var,
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


def oo_remaining_at_tau(
    y_oo: Optional[float],
    realized_pct: Optional[float],
) -> Optional[float]:
    """把 ŷ_oo 严格映成与 ŷ_τ 同一目标：T 收相对 T 开。

    实现见 ``yhat_geom.remaining_at_tau``。
    """
    return remaining_at_tau(y_oo, realized_pct)


# 遗留别名
eod_remaining_at_tau = oo_remaining_at_tau


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cfg_w_oo(cfg: Dict[str, Any]) -> float:
    """读盘：w_oo 优先，旧键 w_eod 回退。"""
    raw = cfg.get("w_oo")
    if raw is None:
        raw = cfg.get("w_eod")
    try:
        return float(raw if raw is not None else 0.5)
    except (TypeError, ValueError):
        return 0.5


def fuse_remaining_heads(
    left: Optional[float],
    right: Optional[float],
    *,
    w_oo: float = 0.5,
    w_tau: float = 0.5,
) -> Optional[float]:
    """加权融合两个头。缺一侧用另一侧。量纲由调用方对齐（ŷ_trade 用昨收口径）。

    两套模型独立训练、互不依赖；只在决策时用权重合成。
    单头降级时调用方应读 ``fuse_remaining_heads_meta`` 或检查返回旁路标记。
    """
    a = _as_float(left)
    b = _as_float(right)
    if a is None and b is None:
        return None
    if a is None:
        return round(b, 6)  # type: ignore[arg-type]
    if b is None:
        return round(a, 6)
    try:
        we = float(w_oo)
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


def item_gap_pct(item: Optional[dict]) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    g = _as_float(item.get("gap_pct"))
    if g is not None:
        return g
    feats = item.get("features_tau")
    if isinstance(feats, dict):
        return _as_float(feats.get("gap_pct"))
    return None


def lift_tau_vs_prev_close(
    y_tau: Optional[float],
    gap_pct: Optional[float],
) -> Optional[float]:
    """ŷ_τ（开盘后）按缺口映到现价对昨收。无缺口则原样返回。"""
    t = _as_float(y_tau)
    if t is None:
        return None
    g = _as_float(gap_pct)
    if g is None:
        return t
    return compound_pct(g, t)


def trade_blend_vs_prev_close(
    y_oo: Optional[float],
    y_tau: Optional[float],
    *,
    gap_pct: Optional[float] = None,
    w_oo: float = 0.5,
    w_tau: float = 0.5,
) -> Tuple[Optional[float], Optional[float], str]:
    """ŷ_trade（昨收）= w·ŷ_oo + w·(缺口∘ŷ_τ)。返回 (cc, tau_cc, vs)。

    不经过 ŷ_oo_rem。缺缺口且仍有 ŷ_τ 时无法抬，vs=open。
    """
    tau_cc = lift_tau_vs_prev_close(y_tau, gap_pct)
    cc = fuse_remaining_heads(y_oo, tau_cc, w_oo=w_oo, w_tau=w_tau)
    if cc is None:
        return None, tau_cc, "open"
    if y_tau is None or _as_float(gap_pct) is not None:
        return cc, tau_cc, "prev_close"
    return cc, tau_cc, "open"


def stamp_trade_prev_close(
    item: dict,
    *,
    y_oo: Optional[float],
    y_tau: Optional[float],
    w_oo: float,
    w_tau: float,
) -> Optional[float]:
    cc, tau_cc, vs = trade_blend_vs_prev_close(
        y_oo,
        y_tau,
        gap_pct=item_gap_pct(item),
        w_oo=w_oo,
        w_tau=w_tau,
    )
    item["predicted_score_blend_tau_cc"] = tau_cc
    item["predicted_score_blend_vs"] = vs
    return cc


def _fusion_weights_from_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    eod_next: Optional[bool] = None,
    y_tau: Optional[float] = None,
    rem_model_doc: Optional[dict] = None,
) -> Tuple[float, float]:
    return resolve_item_fusion_weights(
        item,
        config=config,
        eod_next=eod_next,
        y_tau=y_tau,
        rem_model_doc=rem_model_doc,
    )[:2]


def resolve_item_fusion_weights(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    eod_next: Optional[bool] = None,
    y_tau: Optional[float] = None,
    rem_model_doc: Optional[dict] = None,
) -> Tuple[float, float, bool]:
    """解析融合权：簿优先；盘中若残留 eod_next 的 w_τ=0 且 ŷ_τ 仍可用则恢复 cfg。

    返回 (w_oo, w_tau, repaired_stale_eod_next_weights)。
    """
    from core.signal.dual_score.resolve import (
        get_dual_score_cfg,
        resolve_fusion_weights,
        resolve_predicted_score_tau,
    )

    cfg = get_dual_score_cfg(config)
    if eod_next is None:
        eod_next = (
            str((item or {}).get("dual_score_window") or "") == "eod_next"
            if isinstance(item, dict)
            else False
        )
    tau_v = y_tau
    if tau_v is None and isinstance(item, dict) and not eod_next:
        tau_v = resolve_predicted_score_tau(item)
        try:
            tau_v = float(tau_v) if tau_v is not None and tau_v != "" else None
        except (TypeError, ValueError):
            tau_v = None

    feats = (
        item.get("features_tau")
        if isinstance(item, dict) and isinstance(item.get("features_tau"), dict)
        else None
    )
    we, wt, _note = resolve_fusion_weights(
        cfg, feats=feats, rem_model_doc=rem_model_doc
    )
    repaired = False
    book_w = item.get("dual_score_weights") if isinstance(item, dict) else None
    if isinstance(book_w, dict):
        try:
            we_raw = book_w.get("w_oo")
            if we_raw is None:
                we_raw = book_w.get("w_eod")
            if we_raw is not None:
                we = float(we_raw)
                wt_raw = book_w.get("w_tau")
                wt = float(wt_raw if wt_raw is not None else 1.0 - we)
        except (TypeError, ValueError):
            pass
    if eod_next:
        wt = 0.0
    elif tau_v is not None and wt <= 1e-12:
        # 旧簿 eod_next stamp：w_τ=0 / tau_in_trade=false，窗口已回 intraday 须恢复双头权
        we, wt_cfg, _ = resolve_fusion_weights(
            cfg, feats=feats, rem_model_doc=rem_model_doc
        )
        wt = wt_cfg
        repaired = True
    return we, wt, repaired


def stamp_item_fusion_weights(
    item: Optional[dict],
    *,
    w_oo: float,
    w_tau: float,
    eod_next: bool,
    tau_available: bool,
) -> None:
    """写回 ``dual_score_weights``，与 attach_dual_score_pit / tip 展示一致。"""
    if not isinstance(item, dict):
        return
    prev = item.get("dual_score_weights")
    base = dict(prev) if isinstance(prev, dict) else {}
    item["dual_score_weights"] = {
        **base,
        "w_oo": round(float(w_oo), 6),
        "w_tau": round(0.0 if eod_next else float(w_tau), 6),
        "tau_in_trade": bool(not eod_next and tau_available),
        "window": "eod_next" if eod_next else "intraday",
    }


def unlifted_trade_blend_stale(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> bool:
    """旧簿用 ``w·ŷ_oo + w·ŷ_τ``（未缺口抬升）写成 blend → 量纲错，须重算。"""
    if not isinstance(item, dict):
        return False
    if str(item.get("dual_score_window") or "") == "eod_next":
        return False  # eod_next 另走剥离 τ
    from core.signal.dual_score.resolve import (
        resolve_predicted_score_oo,
        resolve_predicted_score_tau,
    )

    y_oo = resolve_predicted_score_oo(item)
    y_tau = resolve_predicted_score_tau(item)
    gap = item_gap_pct(item)
    try:
        blend = (
            float(item["predicted_score_blend"])
            if item.get("predicted_score_blend") is not None
            and item.get("predicted_score_blend") != ""
            else None
        )
    except (TypeError, ValueError):
        blend = None
    if y_oo is None or y_tau is None or gap is None or blend is None:
        return False
    we, wt = _fusion_weights_from_item(item, config=config)
    naive = fuse_remaining_heads(y_oo, y_tau, w_oo=we, w_tau=wt)
    tau_cc = lift_tau_vs_prev_close(y_tau, gap)
    lifted = fuse_remaining_heads(y_oo, tau_cc, w_oo=we, w_tau=wt)
    if naive is None or lifted is None:
        return False
    if abs(float(naive) - float(lifted)) < 1e-6:
        return False
    return abs(blend - float(naive)) < 1e-4 and abs(blend - float(lifted)) > 1e-4


def fuse_remaining_heads_meta(
    oo_rem: Optional[float],
    y_tau: Optional[float],
    *,
    w_oo: float = 0.5,
    w_tau: float = 0.5,
) -> Dict[str, Any]:
    """同 ``fuse_remaining_heads``，并标 ``dual_score_head``：blend | single_oo | single_tau | none。"""
    a = _as_float(oo_rem)
    b = _as_float(y_tau)
    fused = fuse_remaining_heads(oo_rem, y_tau, w_oo=w_oo, w_tau=w_tau)
    if a is None and b is None:
        head = "none"
    elif a is None:
        head = "single_tau"
    elif b is None:
        head = "single_oo"
    else:
        head = "blend"
    return {
        "predicted_score_blend": fused,
        "dual_score_head": head,
        "single_head": head.startswith("single"),
    }


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
    """决策层合成权。返回 (w_oo, w_tau, mode_note)。"""
    we = _cfg_w_oo(cfg)
    try:
        wt = float(cfg.get("w_tau") if cfg.get("w_tau") is not None else 0.5)
    except (TypeError, ValueError):
        wt = 0.5
    mode = str(cfg.get("w_mode") or "fixed").strip().lower()
    if mode == "kalman":
        mode = "fixed"
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
        rem_ok = isinstance(rem_model_doc, dict) and bool(rem_model_doc)
        if not rem_ok:
            logger.warning(
                "resolve_fusion_weights: w_mode=variance but tau_model missing/empty; "
                "fallback to fixed w_oo/w_tau"
            )
            note = "variance→fixed(tau_missing)"
        else:
            ve, ve_src = resolve_oo_prior_var(
                cfg_var=cfg.get("eod_residual_var"),
            )
            theme = feats.get("theme_day") if isinstance(feats, dict) else None
            vt = rem_obs_var(rem_model_doc, default=1.0, theme_day=theme)
            we = 1.0 / ve
            wt = 1.0 / vt
            note = f"variance(ve={ve:g}/{ve_src},vt={vt:g})"
    else:
        mode = "fixed"
        note = "fixed"
    s = we + wt
    if abs(s) < 1e-12:
        return 0.5, 0.5, note
    return we / s, wt / s, note


def cascade_tau_shadow(
    oo_rem: Optional[float],
    y_tau: Optional[float],
    *,
    rem_intercept: Optional[float] = None,
) -> Optional[float]:
    """研究影子：ŷ_cascade = ŷ_oo_rem + (ŷ_τ − α)。α 缺省按 0。"""
    a = _as_float(oo_rem)
    b = _as_float(y_tau)
    if a is None or b is None:
        return None
    try:
        alpha = 0.0 if rem_intercept is None else float(rem_intercept)
    except (TypeError, ValueError):
        alpha = 0.0
    return round(a + (b - alpha), 6)

