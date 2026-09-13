"""双层 ŷ：配置解析、字段对齐、排序键与 τ 买入闸。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.signal.dual_score.fusion import (
    _as_float,
    fuse_remaining_heads_meta,
    item_gap_pct,
    lift_tau_vs_prev_close,
    normalize_fusion_mode,
    resolve_fusion_weights,
    stamp_trade_prev_close,
    unlifted_trade_blend_stale,
)
from core.signal.nowcast_kf import (
    DEFAULT_NOWCAST,
    as_process_q,
    merge_nowcast_cfg,
    resolve_eod_prior_var,
    run_live_nowcast,
)

from core.signal.minute_tau_grid import DEFAULT_MINUTE_TAU_GRID as _DEFAULT_MINUTE_TAU_GRID

DEFAULT_DUAL_SCORE: Dict[str, Any] = {
    # 正交加权：簿排序用 ŷ_trade=blend(ŷ_EOD, 缺口∘ŷ_τ)；买入另须 ŷ_τ≥floor
    "fusion_mode": "blend",
    "tau": "open",
    # 基线门槛；全池无人过闸时见 tau_freeze_breakglass
    "min_predicted_score_tau": 0.1,
    # 候选池内无人过基线门槛时，临时降至本值；None = 0.5 × 基线（不再默认 0.0）
    "tau_freeze_breakglass": True,
    "min_predicted_score_tau_relax": None,
    # breakglass 最少有效 τ 样本；过少不放宽，避免 1–2 票噪声降全局闸
    "tau_freeze_breakglass_min_n": 3,
    "block_buy_if_tau_missing": True,
    "w_eod": 0.5,
    "w_tau": 0.5,
    # fixed | theme_boost | variance | kalman — 决策层合成权，不改两套 Ridge
    "w_mode": "fixed",
    # theme_boost：主题日把 w_τ 乘以此系数后再归一
    "theme_w_tau_boost": 1.25,
    # variance：EOD 侧残差方差代理（百分点²）；τ 侧优先用 τ OOS
    "eod_residual_var": 1.0,
    # 研究影子：ŷ_cascade = ŷ_EOD_rem + (ŷ_τ − α)；不进主排序
    "enable_cascade_shadow": True,
    # A2：刷簿时同池按 ŷ_τ 另写影子簿（不进 execution）
    "enable_tau_shadow_book": False,
    # 有本地分钟缓存时附加 ret_open_to_tau（默认关；开后仍不拉网）
    "enable_minute_tau": False,
    "minute_tau_hm": "10:30",
    # 变长前缀训练时钟（共享 β）；09:30…11:00 每 5m；做 T v6 逐根 rescore；live 仍用 minute_tau_hm
    "minute_tau_grid": list(_DEFAULT_MINUTE_TAU_GRID),
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
        "note": "ŷ_oc = close[T]/open[T]−1；ranking = w·ŷ_oo + w·ŷ_oc",
    },
}


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
    try:
        raw["tau_freeze_breakglass_min_n"] = max(
            1, int(raw.get("tau_freeze_breakglass_min_n") or 3)
        )
    except (TypeError, ValueError):
        raw["tau_freeze_breakglass_min_n"] = 3
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
    hm = str(raw.get("minute_tau_hm") or "10:30").strip() or "10:30"
    if ":" not in hm and len(hm) == 4 and hm.isdigit():
        hm = f"{hm[:2]}:{hm[2:]}"
    raw["minute_tau_hm"] = hm
    grid_raw = raw.get("minute_tau_grid")
    if isinstance(grid_raw, (list, tuple)):
        grid: list = []
        for t in grid_raw:
            s = str(t or "").strip()
            if not s or s.lower() == "open":
                continue
            if ":" not in s and len(s) == 4 and s.isdigit():
                s = f"{s[:2]}:{s[2:]}"
            if s not in grid:
                grid.append(s)
        raw["minute_tau_grid"] = grid or list(_DEFAULT_MINUTE_TAU_GRID)
    else:
        raw["minute_tau_grid"] = list(_DEFAULT_MINUTE_TAU_GRID)
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


def _yhat_pct_field(item: dict, *keys: str) -> Optional[float]:
    """读取疑似 ŷ% 字段；排除 heuristic 0–100（|x|≥10 且无显式 ŷ 标尺时仍可能误伤，故硬上限 20）。"""
    for k in keys:
        v = item.get(k)
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:
            continue
        if abs(f) > 20.0:
            continue
        return f
    return None


def resolve_predicted_score_eod(item: Optional[dict]) -> Optional[float]:
    """读取 ŷ_EOD。

    优先 ``predicted_score_eod`` / ``predicted_score``。
    ``score`` 仅作启发式/遗留回退；若 ``score`` 与 ``predicted_score_blend`` 数值相同，
    视为 align 后的 ŷ_trade，不再当 EOD（避免买入闸吃到 blend）。
    OOS heuristic：禁止 0–100；若仍有组/全局 ŷ% 则用作 EOD 兼容（rank / T0）。
    """
    if not isinstance(item, dict):
        return None
    if is_heuristic_score_scale(item):
        return _yhat_pct_field(
            item,
            "score_cluster",
            "score_global",
            "predicted_score_eod",
            "predicted_score",
            "score",
        )
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
            from core.research.tau_ridge import load_tau_model

            rem_doc = load_tau_model()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            rem_doc = None
    from core.signal.dual_score.fusion import resolve_item_fusion_weights, stamp_item_fusion_weights

    we, wt, _repaired = resolve_item_fusion_weights(
        item,
        config=config,
        eod_next=eod_next,
        y_tau=y_t,
        rem_model_doc=rem_doc,
    )
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
    stamp_item_fusion_weights(
        item,
        w_eod=we,
        w_tau=wt,
        eod_next=eod_next,
        tau_available=y_t is not None,
    )
    if cc is not None:
        return cc
    # eod_next：禁止回落 τ_cc（即使外部误写入 y_τ）
    if eod_next:
        return y_eod
    if tau_cc is not None:
        return tau_cc
    return y_eod


def align_trade_score_fields(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    write_score: bool = True,
    refresh_window: bool = True,
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
    # OOS→heuristic：0–100 只进 heuristic_score；组/全局 ŷ% 写入 EOD/blend 兼容字段供 rank/T0
    if is_heuristic_score_scale(item):
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
        if yhat_f is not None and abs(yhat_f) > 20.0:
            yhat_f = None
        if yhat_f is not None:
            item["predicted_score"] = yhat_f
            item["predicted_score_eod"] = yhat_f
            item["predicted_score_blend"] = yhat_f
            item["decision_score"] = yhat_f
            if write_score:
                item["score"] = yhat_f
        else:
            item["predicted_score"] = None
            item["predicted_score_eod"] = None
            item["predicted_score_eod_rem"] = None
            item["predicted_score_blend"] = None
            if write_score:
                item["score"] = None
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

    if refresh_window:
        try:
            from core.signal.session_pit import refresh_dual_score_window

            refresh_dual_score_window(item)
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            pass

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
    stale_zero_w_tau = False
    if not eod_next and tau is not None:
        bw = item.get("dual_score_weights")
        if isinstance(bw, dict):
            try:
                stale_zero_w_tau = float(bw.get("w_tau") or 0.0) <= 1e-12
            except (TypeError, ValueError):
                stale_zero_w_tau = False
        # kalman/variance：决策层权可能已收敛到 0，但簿上仍留旧 w_τ>0 + 含 τ 的 blend
        if not stale_zero_w_tau and blend is not None and y_eod_f is not None:
            try:
                cfg_w = get_dual_score_cfg(config)
                w_mode = str(cfg_w.get("w_mode") or "fixed")
                if w_mode in ("variance", "kalman"):
                    feats = (
                        item.get("features_tau")
                        if isinstance(item.get("features_tau"), dict)
                        else None
                    )
                    _we_rt, wt_rt, _note = resolve_fusion_weights(
                        cfg_w, feats=feats, rem_model_doc=None
                    )
                    if float(wt_rt or 0.0) <= 1e-12 and abs(float(blend) - float(y_eod_f)) > 1e-6:
                        stale_zero_w_tau = True
            except Exception:  # noqa: BLE001
                logger.debug("stale_zero_w_tau runtime check failed", exc_info=True)
    if blend is None or stale or force_recompute or unlifted or stale_zero_w_tau:
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
        "min_n": int(cfg.get("tau_freeze_breakglass_min_n") or 3),
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
    min_n = int(meta["min_n"])
    if (
        bool(cfg.get("tau_freeze_breakglass", True))
        and n_valid >= min_n
        and n_pass == 0
    ):
        meta["effective_floor"] = relax
        meta["mode"] = "freeze_breakglass"
        meta["note"] = (
            f"τ 试验档：候选池无人过 ŷ_τ≥{base:g}（n={n_valid}≥{min_n}，"
            f"max={tau_max:.3f}%），临时降至 {relax:g}；"
            f"可回滚 tau_freeze_breakglass / 门槛"
        )
        return relax, meta
    if (
        bool(cfg.get("tau_freeze_breakglass", True))
        and n_valid > 0
        and n_valid < min_n
        and n_pass == 0
    ):
        meta["note"] = (
            f"τ 试验档未触发：有效 τ 仅 {n_valid} < min_n={min_n}，保持基线 {base:g}"
        )
    return base, meta


def buy_passes_tau_gate(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    floor: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """ŷ_τ 买入闸（τ 头的 OC 预估）。返回 (ok, skip_reason)。

    始终用原始 ŷ_τ。
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
            return False, "ŷ_τ 缺失（dual_score 硬闸：τ 模型未加载或未推 ŷ_τ）"
        # 非阻断但记录告警：τ 头训练后可实际不生效
        import logging
        logging.getLogger(__name__).warning(
            "buy_passes_tau_gate: ŷ_τ 缺失但 block_buy_if_tau_missing=False；"
            "τ 买入闸形同虚设，请检查 ŷ_τ 模型是否已 promote"
        )
        return True, "ŷ_τ 缺失（非阻断，但 τ 闸未生效）"
    if y_tau < floor_v:
        return (
            False,
            f"ŷ_τ={y_tau:.3f}% < min_predicted_score_tau({floor_v:g})",
        )
    return True, None


def eod_gate_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """入簿 / 买卖 EOD 门槛用分：始终原始 ŷ_EOD。"""
    _ = config
    return resolve_predicted_score_eod(item)

