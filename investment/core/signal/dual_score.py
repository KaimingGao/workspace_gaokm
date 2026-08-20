"""双层 predicted_score：ŷ_EOD + ŷ_τ；昨收口径正交加权融合。

ŷ_EOD      预估 close[T]/close[T-1]−1（现价对昨收）
ŷ_τ        预估 close[T]/open[T]−1（独立 rem 头；买入闸仍用这一层）
ŷ_trade    = w·ŷ_EOD + w·(缺口∘ŷ_τ)  同为现价对昨收，不经过 ŷ_EOD_rem
ŷ_EOD_rem  仅派生对照（y_state / cascade / nowcast），不进 ŷ_trade
ŷ_nowcast  = 顺序 Kalman(EOD → open → 可选分钟 τ)；默认影子，不替换 predicted_score

规范见 docs/predicted-score-chain.md §2.5。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.signal.nowcast_kf import (
    DEFAULT_NOWCAST,
    adaptive_process_q,
    align_rem_yhat_to_clock,
    as_process_q,
    compound_pct,
    kalman_fusion_weights,
    merge_nowcast_cfg,
    nordhaus_from_nowcast_rows,
    normalize_tau_label,
    remaining_at_tau,
    rem_label_is_open_to_close,
    rem_obs_var,
    resolve_eod_prior_var,
    run_live_nowcast,
)

DEFAULT_DUAL_SCORE: Dict[str, Any] = {
    # 正交加权：簿排序用 ŷ_trade=blend(ŷ_EOD, 缺口∘ŷ_τ)；买入另须 ŷ_τ≥floor
    "fusion_mode": "blend",
    "tau": "open",
    # 基线门槛；全池无人过闸时见 tau_freeze_breakglass
    "min_predicted_score_tau": 0.1,
    # 候选池内无人过基线门槛时，临时降至本值；None = 0.5 × 基线（不再默认 0.0）
    "tau_freeze_breakglass": True,
    "min_predicted_score_tau_relax": None,
    "block_buy_if_tau_missing": True,
    "w_eod": 0.5,
    "w_tau": 0.5,
    # fixed | theme_boost | variance | kalman — 决策层合成权，不改两套 Ridge
    "w_mode": "fixed",
    # theme_boost：主题日把 w_τ 乘以此系数后再归一
    "theme_w_tau_boost": 1.25,
    # variance：EOD 侧残差方差代理（百分点²）；τ 侧优先用 rem OOS
    "eod_residual_var": 1.0,
    # 研究影子：ŷ_cascade = ŷ_EOD_rem + (ŷ_τ − α)；不进主排序
    "enable_cascade_shadow": True,
    # A2：刷簿时同池按 ŷ_τ 另写影子簿（不进 execution）
    "enable_tau_shadow_book": False,
    # 有本地分钟缓存时附加 ret_open_to_tau（默认关；开后仍不拉网）
    "enable_minute_tau": False,
    "minute_tau_hm": "09:45",
    # nowcast / Kalman：默认只写影子字段，不改排序键
    "nowcast": dict(DEFAULT_NOWCAST),
    # 高维 Y(τ)：校验 / 展示 / 过滤（不改 predicted_score 语义）
    "y_state": {
        "enabled": True,
        "eps_sign": 0.05,
        "disagree_warn": 0.5,
        "sigma_warn": 1.5,
        "filter_buys": True,
        "block_on": ["conflict", "missing_tau"],
        "defer_on": ["low_conf", "single_head"],
        "show_badge": True,
        "scale_weights": True,
        "trust": {
            "ok": 1.0,
            "low_conf": 0.5,
            "single_head": 0.75,
            "conflict": 0.0,
            "missing_tau": 0.0,
        },
    },
    "y_spec": {
        "formula": "close[T]/open[T]-1",
        "unit": "pct",
        "tau": "open",
        "note": "ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)；不替换 EOD predicted_score",
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

    实现见 ``nowcast_kf.remaining_at_tau``。
    """
    return remaining_at_tau(y_eod, realized_pct)


def _as_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fuse_remaining_heads(
    left: Optional[float],
    right: Optional[float],
    *,
    w_eod: float = 0.5,
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
    y_eod: Optional[float],
    y_tau: Optional[float],
    *,
    gap_pct: Optional[float] = None,
    w_eod: float = 0.5,
    w_tau: float = 0.5,
) -> Tuple[Optional[float], Optional[float], str]:
    """ŷ_trade（昨收）= w·ŷ_EOD + w·(缺口∘ŷ_τ)。返回 (cc, tau_cc, vs)。

    不经过 ŷ_EOD_rem。缺缺口且仍有 ŷ_τ 时无法抬，vs=open。
    """
    tau_cc = lift_tau_vs_prev_close(y_tau, gap_pct)
    cc = fuse_remaining_heads(y_eod, tau_cc, w_eod=w_eod, w_tau=w_tau)
    if cc is None:
        return None, tau_cc, "open"
    if y_tau is None or _as_float(gap_pct) is not None:
        return cc, tau_cc, "prev_close"
    return cc, tau_cc, "open"


def stamp_trade_prev_close(
    item: dict,
    *,
    y_eod: Optional[float],
    y_tau: Optional[float],
    w_eod: float,
    w_tau: float,
) -> Optional[float]:
    cc, tau_cc, vs = trade_blend_vs_prev_close(
        y_eod,
        y_tau,
        gap_pct=item_gap_pct(item),
        w_eod=w_eod,
        w_tau=w_tau,
    )
    item["predicted_score_blend_tau_cc"] = tau_cc
    item["predicted_score_blend_vs"] = vs
    return cc


def _fusion_weights_from_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Tuple[float, float]:
    cfg = get_dual_score_cfg(config)
    try:
        we = float(cfg.get("w_eod") if cfg.get("w_eod") is not None else 0.5)
    except (TypeError, ValueError):
        we = 0.5
    try:
        wt = float(cfg.get("w_tau") if cfg.get("w_tau") is not None else 0.5)
    except (TypeError, ValueError):
        wt = 0.5
    book_w = item.get("dual_score_weights") if isinstance(item, dict) else None
    if isinstance(book_w, dict) and book_w.get("w_eod") is not None:
        try:
            we = float(book_w.get("w_eod"))
            wt = float(
                book_w.get("w_tau") if book_w.get("w_tau") is not None else 1.0 - we
            )
        except (TypeError, ValueError):
            pass
    return we, wt


def unlifted_trade_blend_stale(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> bool:
    """旧簿用 ``w·ŷ_EOD + w·ŷ_τ``（未缺口抬升）写成 blend → 量纲错，须重算。"""
    if not isinstance(item, dict):
        return False
    if str(item.get("dual_score_window") or "") == "eod_next":
        return False  # eod_next 另走剥离 τ
    y_eod = resolve_predicted_score_eod(item)
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
    if y_eod is None or y_tau is None or gap is None or blend is None:
        return False
    we, wt = _fusion_weights_from_item(item, config=config)
    naive = fuse_remaining_heads(y_eod, y_tau, w_eod=we, w_tau=wt)
    tau_cc = lift_tau_vs_prev_close(y_tau, gap)
    lifted = fuse_remaining_heads(y_eod, tau_cc, w_eod=we, w_tau=wt)
    if naive is None or lifted is None:
        return False
    if abs(float(naive) - float(lifted)) < 1e-6:
        return False
    return abs(blend - float(naive)) < 1e-4 and abs(blend - float(lifted)) > 1e-4


def fuse_remaining_heads_meta(
    eod_rem: Optional[float],
    y_tau: Optional[float],
    *,
    w_eod: float = 0.5,
    w_tau: float = 0.5,
) -> Dict[str, Any]:
    """同 ``fuse_remaining_heads``，并标 ``dual_score_head``：blend | single_eod | single_tau | none。"""
    a = _as_float(eod_rem)
    b = _as_float(y_tau)
    fused = fuse_remaining_heads(eod_rem, y_tau, w_eod=w_eod, w_tau=w_tau)
    if a is None and b is None:
        head = "none"
    elif a is None:
        head = "single_tau"
    elif b is None:
        head = "single_eod"
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
    elif mode in ("variance", "kalman"):
        rem_ok = isinstance(rem_model_doc, dict) and bool(rem_model_doc)
        if not rem_ok:
            # 缺 rem 模型时勿静默当 fixed：告警 + note 降级标记
            import logging

            logging.getLogger(__name__).warning(
                "resolve_fusion_weights: w_mode=%s but rem_model missing/empty; "
                "fallback to fixed w_eod/w_tau",
                mode,
            )
            note = f"{mode}→fixed(rem_missing)"
        elif mode == "variance":
            ve, ve_src = resolve_eod_prior_var(
                cfg_var=cfg.get("eod_residual_var"),
                prior_var=(
                    (cfg.get("nowcast") or {}).get("prior_var")
                    if isinstance(cfg.get("nowcast"), dict)
                    else None
                ),
            )
            theme = feats.get("theme_day") if isinstance(feats, dict) else None
            vt = rem_obs_var(rem_model_doc, default=1.0, theme_day=theme)
            # 精度 ∝ 1/var
            we = 1.0 / ve
            wt = 1.0 / vt
            note = f"variance(ve={ve:g}/{ve_src},vt={vt:g})"
        else:
            ve, ve_src = resolve_eod_prior_var(
                cfg_var=cfg.get("eod_residual_var"),
                prior_var=(
                    (cfg.get("nowcast") or {}).get("prior_var")
                    if isinstance(cfg.get("nowcast"), dict)
                    else None
                ),
            )
            theme = feats.get("theme_day") if isinstance(feats, dict) else None
            gap = feats.get("gap_pct") if isinstance(feats, dict) else None
            vt = rem_obs_var(rem_model_doc, default=1.0, theme_day=theme)
            nc = cfg.get("nowcast") if isinstance(cfg.get("nowcast"), dict) else {}
            q, q_note = adaptive_process_q(
                nc.get("q_process"),
                theme_day=theme,
                gap_pct=gap,
                theme_q_boost=nc.get("theme_q_boost"),
                gap_q_trigger_pct=nc.get("gap_q_trigger_pct"),
                gap_q_boost=nc.get("gap_q_boost"),
            )
            we, wt, _k, note = kalman_fusion_weights(
                prior_var=ve, obs_var=vt, q_process=q
            )
            note = f"{note}|ve={ve_src}"
            if q_note and q_note not in ("base", "q=0"):
                note = f"{note}|{q_note}"
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
        "min_predicted_score_tau_relax",
        "tau_freeze_breakglass",
        "tau",
        "enable_tau_shadow_book",
        "nowcast",
        "w_mode",
        "y_state",
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
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            config = {}
    raw = dict(DEFAULT_DUAL_SCORE)
    raw["y_state"] = dict(DEFAULT_DUAL_SCORE.get("y_state") or {})
    raw["nowcast"] = dict(DEFAULT_DUAL_SCORE.get("nowcast") or {})
    patch = _dual_patch_from_config(config)
    y_spec_patch = patch.pop("y_spec", None)
    y_state_patch = patch.pop("y_state", None)
    nowcast_patch = patch.pop("nowcast", None)
    raw.update(patch)
    if isinstance(y_spec_patch, dict):
        ys = dict(DEFAULT_DUAL_SCORE.get("y_spec") or {})
        ys.update(y_spec_patch)
        raw["y_spec"] = ys
    if isinstance(y_state_patch, dict):
        ys = dict(DEFAULT_DUAL_SCORE.get("y_state") or {})
        trust_p = y_state_patch.get("trust")
        rank_p = y_state_patch.get("book_check_rank")
        ys.update({k: v for k, v in y_state_patch.items() if k not in ("trust", "book_check_rank")})
        if isinstance(ys.get("trust"), dict) and isinstance(trust_p, dict):
            merged_t = dict(ys.get("trust") or {})
            merged_t.update(trust_p)
            ys["trust"] = merged_t
        elif isinstance(trust_p, dict):
            ys["trust"] = dict(trust_p)
        if isinstance(rank_p, dict):
            ys["book_check_rank"] = dict(rank_p)
        raw["y_state"] = ys
    if isinstance(nowcast_patch, dict):
        nc = dict(DEFAULT_DUAL_SCORE.get("nowcast") or {})
        nc.update(nowcast_patch)
        raw["nowcast"] = nc
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
    try:
        if raw.get("min_predicted_score_tau_relax") is not None:
            raw["min_predicted_score_tau_relax"] = float(
                raw["min_predicted_score_tau_relax"]
            )
        else:
            raw["min_predicted_score_tau_relax"] = None
    except (TypeError, ValueError):
        raw["min_predicted_score_tau_relax"] = None
    raw["tau_freeze_breakglass"] = bool(raw.get("tau_freeze_breakglass", True))
    raw["block_buy_if_tau_missing"] = bool(raw.get("block_buy_if_tau_missing", True))
    try:
        raw["w_eod"] = float(raw["w_eod"] if raw.get("w_eod") is not None else 0.5)
    except (TypeError, ValueError):
        raw["w_eod"] = 0.5
    try:
        raw["w_tau"] = float(raw["w_tau"] if raw.get("w_tau") is not None else 0.5)
    except (TypeError, ValueError):
        raw["w_tau"] = 0.5
    raw["enable_tau_shadow_book"] = bool(raw.get("enable_tau_shadow_book", False))
    raw["enable_cascade_shadow"] = bool(raw.get("enable_cascade_shadow", True))
    raw["enable_minute_tau"] = bool(raw.get("enable_minute_tau", False))
    hm = str(raw.get("minute_tau_hm") or "09:45").strip() or "09:45"
    if ":" not in hm and len(hm) == 4 and hm.isdigit():
        hm = f"{hm[:2]}:{hm[2:]}"
    raw["minute_tau_hm"] = hm
    w_mode = str(raw.get("w_mode") or "fixed").strip().lower()
    if w_mode not in ("fixed", "theme_boost", "variance", "kalman"):
        w_mode = "fixed"
    raw["w_mode"] = w_mode
    raw["nowcast"] = merge_nowcast_cfg(raw.get("nowcast"))
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


def is_heuristic_score_scale(item: Optional[dict]) -> bool:
    """主分是否为 0–100 启发式（不可当 ŷ_EOD%）。"""
    if not isinstance(item, dict):
        return False
    if str(item.get("score_scale") or "") == "heuristic_0_100":
        return True
    return str(item.get("return_model_source") or "") == "oos_failed_heuristic"


def dual_track_score_fields(item: Optional[dict]) -> Dict[str, Any]:
    """heuristic（0–100）与 predicted/组·全局 ŷ% 双轨字段，刷簿/观察摘要共用。

    互不覆盖：失败组降级时 predicted 可空，但 ``score_cluster`` 仍保留组 ŷ 对照。
    """
    if not isinstance(item, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in (
        "heuristic_score",
        "predicted_score",
        "predicted_score_eod",
        "score_cluster",
        "score_global",
        "delta_vs_global",
        "score_scale",
        "return_model_source",
    ):
        if item.get(k) is not None:
            out[k] = item.get(k)
    return out


def resolve_predicted_score_eod(item: Optional[dict]) -> Optional[float]:
    """读取 ŷ_EOD。

    优先 ``predicted_score_eod`` / ``predicted_score``。
    ``score`` 仅作启发式/遗留回退；若 ``score`` 与 ``predicted_score_blend`` 数值相同，
    视为 align 后的 ŷ_trade，不再当 EOD（避免买入闸吃到 blend）。
    OOS 失败降级的 heuristic（0–100）不算 ŷ_EOD。
    """
    if not isinstance(item, dict):
        return None
    if is_heuristic_score_scale(item):
        return None
    for k in ("predicted_score_eod", "predicted_score"):
        v = item.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    v = item.get("score")
    if v is None:
        return None
    try:
        score_f = float(v)
    except (TypeError, ValueError):
        return None
    # 0–100 规则分不得冒充 ŷ_EOD（Top-K 无 predicted 时曾靠此漏进榜）
    try:
        from core.signal.score_display import looks_like_legacy_heuristic_score

        if looks_like_legacy_heuristic_score(score_f, item=item):
            return None
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        if abs(score_f) >= 10.0:
            return None
    blend = item.get("predicted_score_blend")
    if blend is not None:
        try:
            if abs(score_f - float(blend)) < 1e-9:
                return None
        except (TypeError, ValueError):
            pass
    return score_f


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


def resolve_predicted_score_eod_rem(item: Optional[dict]) -> Optional[float]:
    """从打分行读取 ŷ_EOD_rem（剩余/日内修正后的 EOD 预测）。"""
    if not isinstance(item, dict):
        return None
    for k in ("predicted_score_eod_rem", "score_eod_rem"):
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
    """ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)，同为现价对昨收。

    ``dual_score_window=eod_next``（收盘后）：停用 τ 侧融合，避免
    缺口∘ŷ_τ ≈ T 日已实现涨跌幅泄漏进 T+1 前瞻主排序分。
    """
    if not isinstance(item, dict):
        return None
    eod_next = str(item.get("dual_score_window") or "") == "eod_next"
    cfg = get_dual_score_cfg(config)
    y_eod = resolve_predicted_score_eod(item)
    y_t = None if eod_next else resolve_predicted_score_tau(item)
    rem_doc = None
    if str(cfg.get("w_mode") or "") in ("variance", "kalman"):
        try:
            from quant.research.rem_ridge import load_rem_model

            rem_doc = load_rem_model()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            rem_doc = None
    we, wt, _note = resolve_fusion_weights(
        cfg,
        feats=item.get("features_tau")
        if isinstance(item.get("features_tau"), dict)
        else None,
        rem_model_doc=rem_doc,
    )
    # 簿上 dual_score_weights 优先（theme/variance 已算）
    book_w = item.get("dual_score_weights")
    if isinstance(book_w, dict) and book_w.get("w_eod") is not None:
        try:
            we = float(book_w.get("w_eod"))
            wt = float(book_w.get("w_tau") if book_w.get("w_tau") is not None else 1.0 - we)
        except (TypeError, ValueError):
            pass
    tau_cc = lift_tau_vs_prev_close(y_t, item_gap_pct(item))
    meta = fuse_remaining_heads_meta(y_eod, y_t, w_eod=we, w_tau=wt)
    item["dual_score_head"] = meta.get("dual_score_head")
    item["dual_score_single_head"] = bool(meta.get("single_head"))
    cc = stamp_trade_prev_close(
        item,
        y_eod=_as_float(y_eod),
        y_tau=_as_float(y_t),
        w_eod=we,
        w_tau=wt,
    )
    if cc is not None:
        return cc
    if tau_cc is not None:
        return tau_cc
    return y_eod


def align_trade_score_fields(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    write_score: bool = True,
) -> Optional[dict]:
    """就地对齐：predicted_score_blend / decision_score = ŷ_trade。

    ``predicted_score`` / ``predicted_score_eod`` 保留 ŷ_EOD。
    重算条件：
    1) blend 缺失；
    2) 旧簿 eod_next 把 blend 写成 EOD 且 ŷ_τ 仍分叉；
    3) ``dual_score_window=eod_next``（收盘后），一律剥离 τ 侧，
       避免今日已实现收益（缺口∘ŷ_τ≈T日收盘涨幅）泄漏进 T+1 决策。
    ``write_score=False``：不改 ``score``（score_stock 主分仍为 EOD 时用）。
    """
    if not isinstance(item, dict):
        return item
    # OOS→heuristic：表列 score 不得写 0–100；有组/全局 ŷ 则写入 score，否则清空
    if is_heuristic_score_scale(item):
        item["predicted_score"] = None
        item["predicted_score_eod"] = None
        item["predicted_score_eod_rem"] = None
        item["predicted_score_blend"] = None
        hs = item.get("heuristic_score")
        if hs is None:
            raw = item.get("score")
            try:
                raw_f = float(raw) if raw is not None and raw != "" else None
            except (TypeError, ValueError):
                raw_f = None
            if raw_f is not None and abs(raw_f) > 20:
                hs = raw_f
        try:
            hs_f = float(hs) if hs is not None and hs != "" else None
        except (TypeError, ValueError):
            hs_f = None
        if hs_f is not None:
            item["heuristic_score"] = hs_f
        yhat = item.get("score_cluster")
        if yhat is None:
            yhat = item.get("score_global")
        try:
            yhat_f = float(yhat) if yhat is not None and yhat != "" else None
        except (TypeError, ValueError):
            yhat_f = None
        if write_score:
            item["score"] = yhat_f  # ŷ% 或 None；禁止 0–100
        item["score_scale"] = "heuristic_0_100"
        try:
            from core.signal.y_state import stamp_y_state

            stamp_y_state(item, config=config)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            pass
        return item
    y_eod = item.get("predicted_score_eod")
    if y_eod is None:
        y_eod = item.get("predicted_score")
    try:
        y_eod_f = float(y_eod) if y_eod is not None and y_eod != "" else None
    except (TypeError, ValueError):
        y_eod_f = None
    if y_eod_f is not None:
        item["predicted_score_eod"] = y_eod_f
        item["predicted_score"] = y_eod_f

    tau = resolve_predicted_score_tau(item)
    try:
        blend = (
            float(item["predicted_score_blend"])
            if item.get("predicted_score_blend") is not None
            and item.get("predicted_score_blend") != ""
            else None
        )
    except (TypeError, ValueError):
        blend = None
    eod_next = str(item.get("dual_score_window") or "") == "eod_next"
    stale = (
        blend is not None
        and y_eod_f is not None
        and tau is not None
        and abs(blend - y_eod_f) < 1e-9
        and abs(float(tau) - y_eod_f) > 1e-6
    )
    # eod_next：旧簿上的 blend 可能含 τ 侧今日已实现收益，一律重算剥离
    force_recompute = eod_next and blend is not None
    unlifted = (not eod_next) and unlifted_trade_blend_stale(item, config=config)
    if blend is None or stale or force_recompute or unlifted:
        recomputed = compute_predicted_score_blend(item, config=config)
        if recomputed is not None:
            blend = float(recomputed)
    if blend is None and (tau is None or eod_next) and y_eod_f is not None:
        blend = y_eod_f
    if blend is not None:
        item["predicted_score_blend"] = blend
        item["decision_score"] = blend
        if write_score:
            item["score"] = blend
    try:
        from core.signal.y_state import stamp_y_state

        stamp_y_state(item, config=config)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    align_nowcast_score_fields(item, config=config)
    return item


def align_nowcast_score_fields(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[dict]:
    """就地修旧簿：收盘后 nowcast 塌成 ŷ_EOD 且丢掉 K。对照列仍吃 ŷ_τ。"""
    if not isinstance(item, dict) or is_heuristic_score_scale(item):
        return item
    y_eod = resolve_predicted_score_eod(item)
    y_tau = resolve_predicted_score_tau(item)
    if y_eod is None or y_tau is None:
        return item
    nc = _as_float(item.get("predicted_score_nowcast"))
    k_now = _as_float(item.get("nowcast_K"))
    as_of = str(item.get("nowcast_as_of") or "").strip().lower()
    collapsed = (
        nc is None
        or (k_now is None and abs(float(nc) - float(y_eod)) < 1e-4)
        or (as_of in ("", "eod") and abs(float(nc) - float(y_eod)) < 1e-4)
    )
    if not collapsed:
        return item
    cfg = get_dual_score_cfg(config)
    nc_cfg = (
        cfg.get("nowcast") if isinstance(cfg.get("nowcast"), dict) else merge_nowcast_cfg(None)
    )
    # 升成排序键时收盘后仍不把当日 OC 融进下一期
    if bool(nc_cfg.get("use_as_rank_key")) and str(item.get("dual_score_window") or "") == "eod_next":
        return item
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    ve, _ve_src = resolve_eod_prior_var(
        cfg_var=cfg.get("eod_residual_var"),
        prior_var=nc_cfg.get("prior_var"),
        prefer_cluster=False,
    )
    as_of_tau = str(item.get("as_of_tau") or item.get("rem_tau") or cfg.get("tau") or "open")
    pack = run_live_nowcast(
        y_eod=y_eod,
        y_tau=y_tau,
        gap_pct=item_gap_pct(item),
        ret_open_to_tau=feats.get("ret_open_to_tau"),
        as_of=as_of_tau,
        taus=nc_cfg.get("taus"),
        prior_var=ve,
        q_process=as_process_q(nc_cfg.get("q_process"), 0.05),
        theme_day=feats.get("theme_day"),
        nowcast_cfg=nc_cfg,
        allow_minute=bool(cfg.get("enable_minute_tau")) or feats.get("ret_open_to_tau") is not None,
    )
    item["predicted_score_nowcast"] = pack.get("predicted_score_nowcast")
    item["nowcast_vs"] = pack.get("nowcast_vs") or "prev_close"
    item["nowcast_as_of"] = pack.get("nowcast_as_of")
    item["nowcast_K"] = pack.get("nowcast_K")
    item["nowcast_q"] = pack.get("nowcast_q")
    item["nowcast_x_prior"] = pack.get("nowcast_x_prior")
    item["nowcast_P"] = pack.get("nowcast_P")
    item["nowcast_revisions"] = pack.get("nowcast_revisions") or []
    item["nowcast_path"] = pack.get("nowcast_path")
    return item


def decision_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """排序 / 表列 / 调仓报告主分：与 ``rank_key_for_item`` 相同（raw ŷ_trade）。"""
    return rank_key_for_item(item, config=config)


def rank_key_field(*, config: Optional[dict] = None) -> str:
    """簿 / 回测上的 rank_key 字段名。默认 blend；opt-in 才是 nowcast。"""
    cfg = get_dual_score_cfg(config)
    nc = cfg.get("nowcast") if isinstance(cfg.get("nowcast"), dict) else {}
    if nc.get("use_as_rank_key"):
        return "predicted_score_nowcast"
    return "predicted_score_blend"


def rank_key_for_item(item: Optional[dict], *, config: Optional[dict] = None) -> Optional[float]:
    """排序键：默认 ŷ_trade（blend）。``nowcast.use_as_rank_key`` 才改用 Kalman 分。

    ``dual_score_window=eod_next``（收盘后）：一律停用 τ 侧，避免旧簿上
    被今日已实现收益污染的 blend 直接进入 T+1 前瞻决策。
    校准 g 仅写入 ``*_cal`` 供 tip/研究对照，不改变排序键。
    """
    if not isinstance(item, dict):
        return None
    cfg = get_dual_score_cfg(config)
    nc = cfg.get("nowcast") if isinstance(cfg.get("nowcast"), dict) else {}
    if nc.get("use_as_rank_key"):
        n = item.get("predicted_score_nowcast")
        if n is not None:
            try:
                return float(n)
            except (TypeError, ValueError):
                pass
    eod_next = str(item.get("dual_score_window") or "") == "eod_next"
    y_eod = resolve_predicted_score_eod(item)
    tau = None if eod_next else resolve_predicted_score_tau(item)
    stored = item.get("predicted_score_blend")
    try:
        stored_f = float(stored) if stored is not None and stored != "" else None
    except (TypeError, ValueError):
        stored_f = None
    # eod_next：旧簿 stored_blend 可能含 τ 侧泄漏，一律重算（重算会剥离 τ）
    if eod_next:
        stored_f = None
    stale = (
        stored_f is not None
        and y_eod is not None
        and tau is not None
        and abs(stored_f - float(y_eod)) < 1e-9
        and abs(float(tau) - float(y_eod)) > 1e-6
    )
    unlifted = (not eod_next) and unlifted_trade_blend_stale(item, config=config)
    if stored_f is None or stale or unlifted:
        b = compute_predicted_score_blend(item, config=config)
        if b is not None:
            try:
                return float(b)
            except (TypeError, ValueError):
                pass
    if stored_f is not None:
        return stored_f
    return resolve_predicted_score_eod(item)

def resolve_tau_buy_floor_for_pool(
    items: Optional[Sequence[dict]],
    *,
    config: Optional[dict] = None,
) -> Tuple[float, Dict[str, Any]]:
    """为买入候选池解析有效 ŷ_τ 门槛。

    默认用 ``min_predicted_score_tau``。若开启 ``tau_freeze_breakglass`` 且池内
    **有有效 ŷ_τ 却无人过基线**，则临时降至 ``min_predicted_score_tau_relax``
    （缺省为基线的一半，不再默认 0.0），避免整簿买腿被冻死（仍要求有 τ；
    缺失仍走 block_buy_if_tau_missing）。
    """
    cfg = get_dual_score_cfg(config)
    base = float(cfg.get("min_predicted_score_tau") or 0.0)
    relax_raw = cfg.get("min_predicted_score_tau_relax")
    if relax_raw is None or relax_raw == "":
        relax = 0.5 * base
    else:
        try:
            relax = float(relax_raw)
        except (TypeError, ValueError):
            relax = 0.5 * base
    relax = max(0.0, min(float(relax), float(base)))
    meta: Dict[str, Any] = {
        "base_floor": base,
        "relax_floor": relax,
        "effective_floor": base,
        "mode": "strict",
        "breakglass": bool(cfg.get("tau_freeze_breakglass", True)),
        "n_tau_valid": 0,
        "n_pass_base": 0,
        "tau_max": None,
        "note": None,
    }
    n_valid = 0
    n_pass = 0
    tau_max: Optional[float] = None
    for it in items or []:
        if not isinstance(it, dict):
            continue
        y = resolve_predicted_score_tau(it)
        if y is None:
            continue
        try:
            yv = float(y)
        except (TypeError, ValueError):
            continue
        n_valid += 1
        if tau_max is None or yv > tau_max:
            tau_max = yv
        if yv >= base:
            n_pass += 1
    meta["n_tau_valid"] = n_valid
    meta["n_pass_base"] = n_pass
    if tau_max is not None:
        meta["tau_max"] = round(tau_max, 6)
    if (
        bool(cfg.get("tau_freeze_breakglass", True))
        and n_valid > 0
        and n_pass == 0
    ):
        meta["effective_floor"] = relax
        meta["mode"] = "freeze_breakglass"
        meta["note"] = (
            f"τ 试验档：候选池无人过 ŷ_τ≥{base:g}（max={tau_max:.3f}%），"
            f"临时降至 {relax:g}；可回滚 tau_freeze_breakglass / 门槛"
        )
        return relax, meta
    return base, meta


def buy_passes_tau_gate(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    floor: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """ŷ_τ 买入闸（rem 头的 OC 预估）。返回 (ok, skip_reason)。

    始终用原始 ŷ_τ（方案 A：校准 g 只做 tip/研究对照，不进买卖闸）。
    收盘后 ``eod_next``：不吃当日 ŷ_τ（已实现 OC，再闸会污染下一期决策）；EOD 门槛另走。
    ``floor`` 可覆盖配置门槛（买入池冻结降级时传入有效楼）。
    """
    cfg = get_dual_score_cfg(config)
    if str((item or {}).get("dual_score_window") or "") == "eod_next":
        return True, None
    y_tau = resolve_predicted_score_tau(item)
    if floor is None:
        floor_v = float(cfg["min_predicted_score_tau"])
    else:
        try:
            floor_v = float(floor)
        except (TypeError, ValueError):
            floor_v = float(cfg["min_predicted_score_tau"])
    if y_tau is None:
        if cfg.get("block_buy_if_tau_missing"):
            return False, "ŷ_τ 缺失（dual_score 硬闸：rem 模型未加载或未推 ŷ_τ）"
        # 非阻断但记录告警：τ 头训练后可实际不生效
        import logging
        logging.getLogger(__name__).warning(
            "buy_passes_tau_gate: ŷ_τ 缺失但 block_buy_if_tau_missing=False；"
            "τ 买入闸形同虚设，请检查 rem 模型是否已 promote"
        )
        return True, "ŷ_τ 缺失（非阻断，但 τ 闸未生效）"
    if y_tau < floor_v:
        return (
            False,
            f"ŷ_τ={y_tau:.3f}% < min_predicted_score_tau({floor_v:g})",
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
    fuse_intraday: bool = True,
) -> Dict[str, Any]:
    """写入双层契约字段（不改 predicted_score / score 主值）。

    ``rem_yhat`` 就是独立训出的 ŷ_τ（open→close），不依赖 ŷ_EOD。
    ŷ_trade = w·ŷ_EOD + w·(缺口∘ŷ_τ)；ŷ_EOD_rem 只作派生对照，不进融合。
    ``residual_delta`` 已废弃，忽略。
    ``fuse_intraday=False``（收盘后 eod_next）：τ 买入闸不吃当日 ŷ_τ；
    主排序分 ŷ_trade 停用 τ 侧（缺口∘ŷ_τ ≈ 今日已实现涨跌幅，会污染 T+1 前瞻决策）；
    nowcast / cascade 对照列仍吃 ŷ_τ，避免塌成 ŷ_EOD。
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
    fuse = bool(fuse_intraday)
    eod_rem = y_eod if not fuse else eod_remaining_at_tau(y_eod, realized)
    clock = normalize_tau_label(as_of) if fuse else "eod"
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
    # 收盘后 eod_next：τ 侧=今日已实现收益，不得参与主排序分融合
    y_tau_for_trade = y_tau if fuse else None
    head_meta = fuse_remaining_heads_meta(
        y_eod, y_tau_for_trade, w_eod=we, w_tau=wt
    )
    if not fuse:
        w_note = f"{w_note}|eod_next(tau_stripped)"

    nc = cfg.get("nowcast") if isinstance(cfg.get("nowcast"), dict) else merge_nowcast_cfg(None)
    ve, ve_src = resolve_eod_prior_var(
        cfg_var=cfg.get("eod_residual_var"),
        prior_var=nc.get("prior_var"),
    )
    allow_minute = bool(cfg.get("enable_minute_tau")) or ret_ot is not None
    # 对照列始终吃 ŷ_τ；仅当 nowcast 被升成排序键且已收盘，才不把当日 OC 融进下一期
    nc_rank = bool(nc.get("use_as_rank_key")) and not fuse
    nowcast_pack = run_live_nowcast(
        y_eod=y_eod,
        y_tau=None if nc_rank else y_tau_raw,
        gap_pct=None if nc_rank else gap_pct,
        ret_open_to_tau=None if nc_rank else ret_ot,
        as_of="eod" if nc_rank else str(as_of or tau),
        taus=nc.get("taus"),
        prior_var=ve,
        rem_model_doc=rem_model_doc,
        q_process=as_process_q(nc.get("q_process"), 0.05),
        theme_day=None if nc_rank else feat_snap.get("theme_day"),
        nowcast_cfg=nc,
        allow_minute=False if nc_rank else allow_minute,
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
    signal_item["dual_score_window"] = "intraday" if fuse else "eod_next"
    # eod_next 不传 y_tau：避免主排序分融合今日已实现收益（缺口∘ŷ_τ≈T日收盘涨幅）
    trade = stamp_trade_prev_close(
        signal_item,
        y_eod=_as_float(y_eod),
        y_tau=_as_float(y_tau_for_trade),
        w_eod=we,
        w_tau=wt,
    )
    if event_prior is not None:
        signal_item["event_prior"] = event_prior
    formula_terms_tau = None
    if feats_merged:
        try:
            from quant.research.rem_ridge import explain_rem_prediction

            formula_terms_tau = explain_rem_prediction(
                feats_merged, model_doc=rem_model_doc
            )
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            formula_terms_tau = None
    if not isinstance(formula_terms_tau, dict):
        formula_terms_tau = {}
    else:
        formula_terms_tau = dict(formula_terms_tau)
    formula_terms_tau["eod_remaining"] = eod_rem
    formula_terms_tau["y_eod"] = y_eod
    formula_terms_tau["y_tau"] = y_tau
    formula_terms_tau["y_tau_raw"] = y_tau_raw
    formula_terms_tau["y_tau_cc"] = signal_item.get("predicted_score_blend_tau_cc")
    formula_terms_tau["trade"] = trade
    formula_terms_tau["cascade"] = cascade
    formula_terms_tau["nowcast"] = nowcast_pack.get("predicted_score_nowcast")
    formula_terms_tau["nowcast_K"] = nowcast_pack.get("nowcast_K")
    formula_terms_tau["nowcast_q"] = nowcast_pack.get("nowcast_q")
    formula_terms_tau["nowcast_path"] = nowcast_pack.get("nowcast_path")
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
    signal_item["predicted_score_nowcast"] = nowcast_pack.get("predicted_score_nowcast")
    signal_item["nowcast_vs"] = nowcast_pack.get("nowcast_vs") or "prev_close"
    signal_item["nowcast_as_of"] = nowcast_pack.get("nowcast_as_of")
    signal_item["nowcast_revisions"] = nowcast_pack.get("nowcast_revisions") or []
    signal_item["nowcast_K"] = nowcast_pack.get("nowcast_K")
    signal_item["nowcast_P"] = nowcast_pack.get("nowcast_P")
    signal_item["nowcast_x_prior"] = nowcast_pack.get("nowcast_x_prior")
    signal_item["nowcast_q"] = nowcast_pack.get("nowcast_q")
    signal_item["nowcast_q_note"] = nowcast_pack.get("nowcast_q_note")
    signal_item["nowcast_path"] = nowcast_pack.get("nowcast_path")
    signal_item["dual_score_weights"] = {
        "w_eod": round(we, 6),
        "w_tau": round(0.0 if not fuse else wt, 6),
        "w_mode": cfg.get("w_mode") or "fixed",
        "w_note": w_note,
        "mode": "blend",
        "tau_available": y_tau is not None,
        "tau_in_trade": bool(fuse and y_tau_for_trade is not None),
        "nowcast_K": nowcast_pack.get("nowcast_K"),
        "nowcast_q": nowcast_pack.get("nowcast_q"),
        "eod_prior_var": round(ve, 6),
        "eod_prior_var_src": ve_src,
        "window": "intraday" if fuse else "eod_next",
    }
    # heuristic 轨：ŷ_EOD 为空；表列 score 只用组/全局 ŷ%，0–100 只留 heuristic_score
    if is_heuristic_score_scale(signal_item):
        signal_item["predicted_score"] = None
        signal_item["predicted_score_eod"] = None
        signal_item["predicted_score_eod_rem"] = None
        signal_item["predicted_score_blend"] = None
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
            signal_item["score"] = (
                float(yhat) if yhat is not None and yhat != "" else None
            )
        except (TypeError, ValueError):
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
) -> Dict[str, Any]:
    """历史回测 / 无实时行情时：用 PIT 日线 quote·bars 挂 ŷ_τ + blend。

    缺口 = open[T]/close[T−1]（与 live ``gap_pct_from_quote_bars`` 同口径）。
    不拉同伴行情；``sector_gap_breadth`` 可由调用方截面预计算后传入。
    无 rem 模型时仍写契约字段（ŷ_τ=None，ŷ_trade 退回 ŷ_EOD）。
    ``fuse_intraday=False`` / 簿上 ``dual_score_window=eod_next``：
    - τ 买入闸不吃当日 ŷ_τ；
    - 主排序分 ŷ_trade 停用 τ 侧（缺口∘ŷ_τ ≈ T日已实现涨跌幅，不得污染 T+1 前瞻决策）；
    - nowcast / cascade 对照列仍吃 ŷ_τ（研究对照，不改排序键）。
    """
    if not isinstance(signal_item, dict):
        return signal_item
    if rem_model_doc is None:
        try:
            from quant.research.rem_ridge import load_rem_model

            rem_model_doc = load_rem_model()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            rem_model_doc = None
    q = quote if isinstance(quote, dict) else signal_item.get("_bt_quote")
    b = bars if bars is not None else signal_item.get("_bt_bars")
    cfg = get_dual_score_cfg(config)
    gap_v = None
    try:
        from core.event_prior import gap_pct_from_quote_bars, get_event_prior_cfg

        gap_v = gap_pct_from_quote_bars(q, b)
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
        from core.research.rem_theme import resolve_theme_day

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
    # 只传 rem 头 Z 特征；勿塞全日线 sub_scores（训练未用，易误导）
    rem_yhat = None
    try:
        from quant.research.rem_ridge import predict_rem_from_features

        rem_yhat = predict_rem_from_features(feats, model_doc=rem_model_doc)
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
        as_of_tau=str(cfg.get("tau") or "open"),
        rem_model_doc=rem_model_doc,
        fuse_intraday=(
            bool(fuse_intraday)
            if fuse_intraday is not None
            else str(signal_item.get("dual_score_window") or "") != "eod_next"
        ),
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


def build_nowcast_shadow_book(
    eligible_rows: Sequence[Dict[str, Any]],
    *,
    max_names: int,
    eod_book: Optional[Sequence[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """同 EOD 入池集合按 ŷ_nowcast 降序截断。不改主簿、不驱动买入。"""
    max_n = max(1, int(max_names or 1))
    rows: List[Dict[str, Any]] = []
    missing = 0
    for r in eligible_rows or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        y_n = row.get("predicted_score_nowcast")
        if y_n is None:
            missing += 1
            key = float("-inf")
        else:
            try:
                key = float(y_n)
            except (TypeError, ValueError):
                missing += 1
                key = float("-inf")
        row["_nowcast_rank_key"] = key
        rows.append(row)
    rows.sort(
        key=lambda x: float(x.get("_nowcast_rank_key") or float("-inf")), reverse=True
    )
    book: List[Dict[str, Any]] = []
    for i, r in enumerate(rows[:max_n]):
        r.pop("_nowcast_rank_key", None)
        r["rank"] = i + 1
        r["rank_key"] = "predicted_score_nowcast"
        book.append(r)
    for r in rows[max_n:]:
        r.pop("_nowcast_rank_key", None)
    compare = compare_book_overlap(eod_book or [], book)
    nordhaus = nordhaus_from_nowcast_rows(rows)
    meta = {
        "mode": "nowcast_shadow",
        "rank_key": "predicted_score_nowcast",
        "max_names": max_n,
        "eligible_count": len(rows),
        "missing_nowcast_count": missing,
        "nordhaus_revision_slope": nordhaus,
        "note": "N3 nowcast 影子簿：Kalman 昨收口径重排；不驱动 execution / 纸面买入",
        "vs_eod_book": compare,
    }
    return book, meta


def nowcast_shadow_alerts(
    shadow_book: Sequence[Dict[str, Any]],
    shadow_meta: Optional[Dict[str, Any]] = None,
    *,
    decision_book: Optional[Sequence[Dict[str, Any]]] = None,
    max_top_divergent: int = 5,
) -> List[Dict[str, Any]]:
    """nowcast 影子闭环告警：比较影子簿(ŷ_nowcast)与决策簿(ŷ_trade/EOD)，对显著背离发告警。

    闭环用途：影子预测 → 对照实际决策 → 告警，供 strategy_monitor / 证据层消费。
    不改决策、不驱动买入；仅产告警列表。
    """
    alerts: List[Dict[str, Any]] = []
    meta = shadow_meta if isinstance(shadow_meta, dict) else {}
    compare = meta.get("vs_eod_book")
    if not isinstance(compare, dict) and decision_book is not None:
        compare = compare_book_overlap(decision_book, shadow_book)
    if not isinstance(compare, dict):
        return alerts

    jaccard = compare.get("jaccard")
    spearman = compare.get("spearman_shared")

    # 1) 重叠过低 — nowcast 与决策簿显著背离
    if jaccard is not None:
        if jaccard < 0.3:
            sev = "critical" if jaccard < 0.15 else "warning"
            alerts.append({
                "code": "nowcast_shadow_low_overlap",
                "severity": sev,
                "metric": "jaccard",
                "value": jaccard,
                "threshold": 0.3,
                "message": (
                    f"nowcast 影子簿与决策簿重叠 Jaccard={jaccard:.2f}，"
                    "intraday 信号与 EOD 决策显著背离"
                ),
            })

    # 2) 秩倒挂 — spearman 低/负
    if spearman is not None:
        if spearman < 0.3:
            sev = "critical" if spearman < 0.0 else "warning"
            alerts.append({
                "code": "nowcast_shadow_rank_inversion",
                "severity": sev,
                "metric": "spearman_shared",
                "value": spearman,
                "threshold": 0.3,
                "message": (
                    f"nowcast 与决策簿共享票秩 Spearman={spearman:.2f}，排序倒挂"
                ),
            })

    # 3) Nordhaus 修正斜率陡 — 预测快速修正，不确定性高
    nord = meta.get("nordhaus_revision_slope")
    if nord is not None:
        try:
            nord_f = float(nord)
            if abs(nord_f) > 0.5:
                alerts.append({
                    "code": "nowcast_revision_volatile",
                    "severity": "warning",
                    "metric": "nordhaus_revision_slope",
                    "value": nord_f,
                    "threshold": 0.5,
                    "message": (
                        f"nowcast Nordhaus 修正斜率 {nord_f:+.3f}，"
                        "预测快速修正、不确定性高"
                    ),
                })
        except (TypeError, ValueError):
            pass

    # 4) nowcast 覆盖不足 — missing 比例高，影子簿不可信
    missing = meta.get("missing_nowcast_count")
    eligible = meta.get("eligible_count")
    if missing is not None and eligible:
        try:
            miss_ratio = float(missing) / float(eligible)
            if miss_ratio > 0.3:
                alerts.append({
                    "code": "nowcast_coverage_low",
                    "severity": "warning",
                    "metric": "missing_ratio",
                    "value": round(miss_ratio, 3),
                    "threshold": 0.3,
                    "message": (
                        f"nowcast 缺失率 {miss_ratio:.1%}（{missing}/{eligible}），"
                        "影子簿不可信"
                    ),
                })
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # 5) 高秩 nowcast 票缺席决策簿 — 背离信号点名
    only_tau = set(compare.get("only_tau") or [])
    if only_tau:
        shadow_top = [
            str(r.get("stock_code") or "").strip()
            for r in (shadow_book or [])[:max_top_divergent]
            if isinstance(r, dict)
        ]
        divergent = [c for c in shadow_top if c and c in only_tau]
        if divergent:
            alerts.append({
                "code": "nowcast_high_rank_absent",
                "severity": "info",
                "metric": "top_rank_absent",
                "value": divergent,
                "message": (
                    f"nowcast 高秩票 {','.join(divergent[:5])} 缺席决策簿，"
                    "intraday 与 EOD 信号背离"
                ),
            })
    return alerts


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
            from core.signal.cluster_live import (
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
    y_eod = expl.get("y_eod")
    trade = expl.get("trade")
    y_tau = expl.get("y_tau")
    y_tau_cc = expl.get("y_tau_cc")
    if y_tau is None:
        y_tau = expl.get("total")
    if y_eod is not None and trade is not None and y_tau is not None:
        try:
            tau_bit = y_tau_cc if y_tau_cc is not None else y_tau
            return (
                f"ŷ_trade = w·ŷ_EOD({float(y_eod):+.3f}) + "
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
    for k in ("sector_gap_breadth", "theme_day", "gap_atr", "gap_vs_sector", "ret_open_to_tau"):
        v = item.get(k)
        if v is not None and v != "":
            feats.setdefault(k, v)

    try:
        from quant.research.rem_ridge import REM_Z_FEATURES

        z_keys = set(REM_Z_FEATURES) | {"open_gap"}
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    if feats:
        try:
            from quant.research.rem_ridge import explain_rem_prediction

            expl = explain_rem_prediction(feats)
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
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        return {}


def dual_score_book_fields(item: Optional[dict]) -> Dict[str, Any]:
    """簿/观察行透传字段。

    ``dual_score_fusion`` / ``dual_score_weights`` 一律用当前配置，
    避免簿内旧戳（如 f1 / 旧 w_*）误导 tip。
    旧簿无 ``formula_terms_tau`` 时现场补全组成表。
    返回前对齐 ŷ_trade（修 eod_next 塌成 EOD 的旧 blend），再挂校准 g。
    """
    if not isinstance(item, dict):
        return {}
    work = dict(item)
    try:
        align_trade_score_fields(work, write_score=False)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    try:
        from core.signal.y_state import stamp_y_state

        stamp_y_state(work)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        pass
    try:
        cfg = get_dual_score_cfg()
        live_fusion = cfg.get("fusion_mode")
        book_w = work.get("dual_score_weights")
        win = work.get("dual_score_window")
        if isinstance(book_w, dict) and book_w.get("w_eod") is not None:
            live_w = {
                "w_eod": book_w.get("w_eod"),
                "w_tau": book_w.get("w_tau"),
                "w_mode": book_w.get("w_mode") or cfg.get("w_mode") or "fixed",
                "w_note": book_w.get("w_note"),
                "mode": "blend",
                "tau_available": book_w.get("tau_available"),
                "window": book_w.get("window") or win,
            }
        else:
            live_w = {
                "w_eod": cfg.get("w_eod"),
                "w_tau": cfg.get("w_tau"),
                "w_mode": cfg.get("w_mode") or "fixed",
                "mode": "blend",
                "window": win,
            }
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        live_fusion = "blend"
        live_w = {"w_eod": 0.5, "w_tau": 0.5, "w_mode": "fixed", "mode": "blend"}
    formula_terms_tau = ensure_formula_terms_tau(work)
    score_formula_tau = work.get("score_formula_tau") or format_tau_formula_string(
        formula_terms_tau
    )
    coefs_tau = work.get("factor_coefficients_tau")
    if not isinstance(coefs_tau, dict) or not coefs_tau:
        coefs_tau = rem_factor_coefficients_public()
    fill = work.get("features_tau_fill")
    if not isinstance(fill, dict):
        fill = features_tau_fill_diag(work.get("features_tau"))
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
            # 在已对齐的 ŷ_trade 上挂 g，避免 tip 校准列仍对塌缩 EOD
            attach_calibrated_scores(work, model_doc=live, force=True)
        cal_applied = bool(work.get("score_calibration_applied"))
        cal_enabled = bool(work.get("score_calibration_enabled", cal_enabled))
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in dual_score.py", exc_info=True)
        cal_applied = bool(work.get("score_calibration_applied"))
        cal_enabled = bool(work.get("score_calibration_enabled"))
    # 权重优先簿内已算（含 theme/variance）；缺则用当前配置
    return {
        "predicted_score_eod": work.get("predicted_score_eod", work.get("predicted_score")),
        "predicted_score_eod_rem": work.get("predicted_score_eod_rem"),
        "predicted_score_tau": work.get("predicted_score_tau", work.get("score_rem")),
        "predicted_score_tau_delta": work.get("predicted_score_tau_delta"),
        "predicted_score_tau_cascade": work.get("predicted_score_tau_cascade"),
        "predicted_score_blend": work.get("predicted_score_blend"),
        "predicted_score_blend_tau_cc": work.get("predicted_score_blend_tau_cc"),
        "predicted_score_blend_vs": work.get("predicted_score_blend_vs"),
        "decision_score": work.get("decision_score"),
        "predicted_score_nowcast": work.get("predicted_score_nowcast"),
        "nowcast_vs": work.get("nowcast_vs"),
        "nowcast_as_of": work.get("nowcast_as_of"),
        "nowcast_K": work.get("nowcast_K"),
        "nowcast_x_prior": work.get("nowcast_x_prior"),
        "nowcast_q": work.get("nowcast_q"),
        "nowcast_revisions": work.get("nowcast_revisions"),
        "predicted_score_cal": work.get("predicted_score_cal"),
        "predicted_score_eod_rem_cal": work.get("predicted_score_eod_rem_cal"),
        "predicted_score_tau_cal": work.get("predicted_score_tau_cal"),
        "predicted_score_blend_cal": work.get("predicted_score_blend_cal"),
        "score_calibration_applied": cal_applied,
        "score_calibration_enabled": cal_enabled,
        "score_calibration_eod_oor": bool(work.get("score_calibration_eod_oor")),
        "score_calibration_eod_rem_oor": bool(work.get("score_calibration_eod_rem_oor")),
        "score_calibration_tau_oor": bool(work.get("score_calibration_tau_oor")),
        "score_calibration_note": work.get("score_calibration_note"),
        "score_calibration_partial": work.get("score_calibration_partial"),
        "realized_t1_to_tau": work.get("realized_t1_to_tau"),
        "score_rem": work.get("score_rem"),
        "predicted_score_rem": work.get("predicted_score_rem"),
        "as_of_tau": work.get("as_of_tau") or work.get("rem_tau"),
        "y_spec_tau": work.get("y_spec_tau"),
        "features_tau": work.get("features_tau"),
        "features_tau_fill": fill,
        "formula_terms_tau": formula_terms_tau,
        "score_formula_terms_tau": formula_terms_tau,
        "score_formula_tau": score_formula_tau,
        "factor_coefficients_tau": coefs_tau or None,
        "gap_pct": work.get("gap_pct"),
        "event_prior": work.get("event_prior"),
        "dual_score_fusion": live_fusion or "blend",
        "dual_score_weights": live_w,
        "dual_score_window": work.get("dual_score_window"),
        "dual_score_head": work.get("dual_score_head"),
        "dual_score_single_head": bool(work.get("dual_score_single_head")),
        "nowcast_P": work.get("nowcast_P"),
        "y_mu": work.get("y_mu"),
        "y_sigma": work.get("y_sigma"),
        "y_disagree": work.get("y_disagree"),
        "y_check": work.get("y_check"),
        "y_sign_conflict": bool(work.get("y_sign_conflict")),
        "eod_trust": work.get("eod_trust"),
        "y_tau_to_close": work.get("y_tau_to_close"),
        "y_tau_to_close_src": work.get("y_tau_to_close_src"),
        "y_state": work.get("y_state"),
    }


def eod_gate_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """入簿 / 买卖 EOD 门槛用分：始终原始 ŷ_EOD（校准 g 不进闸）。"""
    _ = config
    return resolve_predicted_score_eod(item)
