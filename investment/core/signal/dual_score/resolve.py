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

DEFAULT_DUAL_SCORE: Dict[str, Any] = {
    # 正交加权：簿排序用 ŷ_trade=blend(ŷ_oo, 缺口∘ŷ_τ)；买入另须 ŷ_τ≥floor
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
    "w_oo": 0.5,
    "w_eod": 0.5,
    "w_tau": 0.5,
    # fixed | theme_boost | variance — 决策层合成权，不改两套 Ridge
    "w_mode": "fixed",
    # theme_boost：主题日把 w_τ 乘以此系数后再归一
    "theme_w_tau_boost": 1.25,
    # variance：EOD 侧残差方差代理（百分点²）；τ 侧优先用 τ OOS
    "eod_residual_var": 1.0,
    # 研究影子：ŷ_cascade = ŷ_oo_rem + (ŷ_τ − α)；不进主排序
    "enable_cascade_shadow": True,
    # A2：刷簿时同池按 ŷ_τ 另写影子簿（不进 execution）
    "enable_tau_shadow_book": False,
    # 有本地分钟缓存时附加 ret_open_to_tau（默认关；开后仍不拉网）
    "enable_minute_tau": False,
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
        "w_oo",
        "w_eod",
        "w_tau",
        "fusion_mode",
        "min_predicted_score_tau",
        "min_predicted_score_tau_relax",
        "tau_freeze_breakglass",
        "tau",
        "enable_tau_shadow_book",
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
    patch = _dual_patch_from_config(config)
    y_spec_patch = patch.pop("y_spec", None)
    y_state_patch = patch.pop("y_state", None)
    patch.pop("nowcast", None)
    patch_has_w_oo = "w_oo" in patch
    patch_has_w_eod = "w_eod" in patch
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
    if patch_has_w_oo:
        w_oo_raw = raw.get("w_oo")
    elif patch_has_w_eod:
        w_oo_raw = raw.get("w_eod")
    else:
        w_oo_raw = raw.get("w_oo")
        if w_oo_raw is None:
            w_oo_raw = raw.get("w_eod")
    try:
        w_oo = float(w_oo_raw if w_oo_raw is not None else 0.5)
    except (TypeError, ValueError):
        w_oo = 0.5
    raw["w_oo"] = w_oo
    raw["w_eod"] = w_oo
    try:
        raw["w_tau"] = float(raw["w_tau"] if raw.get("w_tau") is not None else 0.5)
    except (TypeError, ValueError):
        raw["w_tau"] = 0.5
    raw["enable_tau_shadow_book"] = bool(raw.get("enable_tau_shadow_book", False))
    raw["enable_cascade_shadow"] = bool(raw.get("enable_cascade_shadow", True))
    raw["enable_minute_tau"] = bool(raw.get("enable_minute_tau", False))
    raw.pop("minute_tau_hm", None)
    raw.pop("minute_tau_grid", None)
    raw.pop("nowcast", None)
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


def is_heuristic_score_scale(item: Optional[dict]) -> bool:
    """主分是否为 0–100 启发式（不可当 ŷ_oo%）。"""
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
        "predicted_score_oo",
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


def resolve_predicted_score_oo(item: Optional[dict]) -> Optional[float]:
    """读取 ŷ_oo。

    优先 ``y_oo`` / ``predicted_score_oo`` / ``predicted_score_eod`` / ``predicted_score``。
    ``score`` 仅作启发式/遗留回退；若 ``score`` 与 ``predicted_score_blend`` 数值相同，
    视为 align 后的 ŷ_trade，不再当 ŷ_oo（避免买入闸吃到 blend）。
    OOS heuristic：禁止 0–100；若仍有组/全局 ŷ% 则用作 ŷ_oo 兼容（rank / T0）。
    """
    if not isinstance(item, dict):
        return None
    if is_heuristic_score_scale(item):
        return _yhat_pct_field(
            item,
            "score_cluster",
            "score_global",
            "y_oo",
            "predicted_score_oo",
            "predicted_score_eod",
            "predicted_score",
            "score",
        )
    for k in ("y_oo", "predicted_score_oo", "predicted_score_eod", "predicted_score"):
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
    # 0–100 规则分不得冒充 ŷ_oo（Top-K 无 predicted 时曾靠此漏进榜）
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


resolve_predicted_score_eod = resolve_predicted_score_oo  # 遗留别名，统一用 resolve_predicted_score_oo


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


def resolve_predicted_score_oo_rem(item: Optional[dict]) -> Optional[float]:
    """从打分行读取 ŷ_oo_rem（剩余/日内修正后的 oo 预测）。"""
    if not isinstance(item, dict):
        return None
    for k in (
        "predicted_score_oo_rem",
        "predicted_score_eod_rem",
        "score_eod_rem",
    ):
        v = item.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


# 遗留别名
resolve_predicted_score_eod_rem = resolve_predicted_score_oo_rem


def compute_predicted_score_blend(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """ŷ_trade = w·ŷ_oo + w·(缺口∘ŷ_τ)，同为现价对昨收。

    ``dual_score_window=eod_next``：ŷ_oo 已换期，停用 τ 侧融合。
    收盘后同一周期窗口仍是 ``intraday``，ŷ_oc 继续进 ranking。
    """
    if not isinstance(item, dict):
        return None
    eod_next = str(item.get("dual_score_window") or "") == "eod_next"
    cfg = get_dual_score_cfg(config)
    y_oo = resolve_predicted_score_oo(item)
    y_t = None if eod_next else resolve_predicted_score_tau(item)
    rem_doc = None
    if str(cfg.get("w_mode") or "") == "variance":
        try:
            from core.research.tau_ridge import load_tau_model

            rem_doc = load_tau_model()
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in dual_score.py", exc_info=True)
            rem_doc = None
    from core.signal.dual_score.fusion import resolve_item_fusion_weights, stamp_item_fusion_weights
    from core.signal.yhat_windows import tau_model_is_open_to_close

    # τc 模型判断：从 item 的 y_spec_tau / rem_y_spec 推断；variance 模式用加载的模型 doc
    if rem_doc is None:
        rem_doc = {"y_spec": item.get("y_spec_tau")} if isinstance(item.get("y_spec_tau"), dict) else None
    we, wt, _repaired = resolve_item_fusion_weights(
        item,
        config=config,
        eod_next=eod_next,
        y_tau=y_t,
        rem_model_doc=rem_doc,
    )
    tau_cc = lift_tau_vs_prev_close(
        y_t,
        item_gap_pct(item),
        ret_open_to_tau=item.get("ret_open_to_tau"),
        rem_model_doc=rem_doc,
    )
    meta = fuse_remaining_heads_meta(y_oo, y_t, w_oo=we, w_tau=wt)
    item["dual_score_head"] = meta.get("dual_score_head")
    item["dual_score_single_head"] = bool(meta.get("single_head"))
    cc = stamp_trade_prev_close(
        item,
        y_oo=_as_float(y_oo),
        y_tau=_as_float(y_t),
        w_oo=we,
        w_tau=wt,
        rem_model_doc=rem_doc,
    )
    stamp_item_fusion_weights(
        item,
        w_oo=we,
        w_tau=wt,
        eod_next=eod_next,
        tau_available=y_t is not None,
    )
    if cc is not None:
        return cc
    # eod_next：禁止回落 τ_cc（即使外部误写入 y_τ）
    if eod_next:
        return y_oo
    if tau_cc is not None:
        return tau_cc
    return y_oo


def align_trade_score_fields(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
    write_score: bool = True,
    refresh_window: bool = True,
) -> Optional[dict]:
    """就地对齐：predicted_score_blend / decision_score = ŷ_trade。

    ``predicted_score`` / ``predicted_score_eod`` 保留 ŷ_oo。
    重算条件：
    1) blend 缺失；
    2) 旧簿 eod_next 把 blend 写成 EOD 且 ŷ_τ 仍分叉；
    3) ``dual_score_window=eod_next``（已换期），剥离 τ 侧。
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
    y_oo = item.get("predicted_score_eod")
    if y_oo is None:
        y_oo = item.get("predicted_score")
    try:
        y_oo_f = float(y_oo) if y_oo is not None and y_oo != "" else None
    except (TypeError, ValueError):
        y_oo_f = None
    if y_oo_f is not None:
        item["predicted_score_eod"] = y_oo_f
        item["predicted_score"] = y_oo_f

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
        and y_oo_f is not None
        and tau is not None
        and abs(blend - y_oo_f) < 1e-9
        and abs(float(tau) - y_oo_f) > 1e-6
    )
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
        # variance：决策层权可能已收敛到 0，但簿上仍留旧 w_τ>0 + 含 τ 的 blend
        if not stale_zero_w_tau and blend is not None and y_oo_f is not None:
            try:
                cfg_w = get_dual_score_cfg(config)
                w_mode = str(cfg_w.get("w_mode") or "fixed")
                if w_mode == "variance":
                    feats = (
                        item.get("features_tau")
                        if isinstance(item.get("features_tau"), dict)
                        else None
                    )
                    _we_rt, wt_rt, _note = resolve_fusion_weights(
                        cfg_w, feats=feats, rem_model_doc=None
                    )
                    if float(wt_rt or 0.0) <= 1e-12 and abs(float(blend) - float(y_oo_f)) > 1e-6:
                        stale_zero_w_tau = True
            except Exception:  # noqa: BLE001
                logger.debug("stale_zero_w_tau runtime check failed", exc_info=True)
    if blend is None or stale or force_recompute or unlifted or stale_zero_w_tau:
        recomputed = compute_predicted_score_blend(item, config=config)
        if recomputed is not None:
            blend = float(recomputed)
    if blend is None and (tau is None or eod_next) and y_oo_f is not None:
        blend = y_oo_f
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
    return item


def decision_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """排序 / 表列 / 调仓报告主分：与 ``rank_key_for_item`` 相同（ranking，回退 ŷ_trade）。"""
    return rank_key_for_item(item, config=config)


def rank_key_field(*, config: Optional[dict] = None) -> str:
    """簿 / 回测上的 rank_key 字段名。默认 blend。"""
    _ = config
    return "predicted_score_blend"


def rank_key_for_item(item: Optional[dict], *, config: Optional[dict] = None) -> Optional[float]:
    """排序键：ranking（τ→open[T+1] 基准）。

    盘中：ranking = fuse(ŷ_oo_rem, oc_with_co(ŷ_τc, ŷ_co))；
    eod_next（收盘后）：τ 侧剥离，只用 ŷ_oo，避免旧簿上被今日已实现收益
    污染的分直接进入 T+1 前瞻决策。
    ranking 算不出时回退 ŷ_trade（predicted_score_blend），再回退 ŷ_oo。
    """
    if not isinstance(item, dict):
        return None
    cfg = get_dual_score_cfg(config)
    eod_next = str(item.get("dual_score_window") or "") == "eod_next"

    if eod_next:
        # 收盘后：τ 侧剥离，只用 ŷ_oo
        return resolve_predicted_score_oo(item)

    # 盘中：优先用 ranking（τ→open[T+1] 基准，现算）
    try:
        from core.paper.rebalance.rank_lots import ranking_pct_of

        rk = ranking_pct_of(item, cfg)
        if rk is not None:
            return float(rk)
    except Exception:  # noqa: BLE001
        logger.debug("rank_key ranking_pct_of failed", exc_info=True)

    # 回退：ŷ_trade（predicted_score_blend，昨收基准）
    stored = item.get("predicted_score_blend")
    try:
        stored_f = float(stored) if stored is not None and stored != "" else None
    except (TypeError, ValueError):
        stored_f = None
    if stored_f is not None:
        return stored_f
    try:
        b = compute_predicted_score_blend(item, config=config)
        if b is not None:
            return float(b)
    except Exception:  # noqa: BLE001
        logger.debug("rank_key blend fallback failed", exc_info=True)

    # 最终回退：ŷ_oo
    return resolve_predicted_score_oo(item)


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


def oo_gate_score_for_item(
    item: Optional[dict],
    *,
    config: Optional[dict] = None,
) -> Optional[float]:
    """入簿 / 买卖 oo 门槛用分：始终原始 ŷ_oo。"""
    _ = config
    return resolve_predicted_score_oo(item)


# 遗留别名
eod_gate_score_for_item = oo_gate_score_for_item

