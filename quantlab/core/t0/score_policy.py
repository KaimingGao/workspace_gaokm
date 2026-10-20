"""多层 ŷ 驱动的 A 股底仓做 T 策略（dual_y）。

角色（PIT）：
  y_τc     — 盘中主方向；定向锚用 y_τc，时钟对齐值在 y_tau
  y_hl     — **已下线**：不再算分/挂载；旧行仍可读 y_hl / y_path
  y_on     — 快照对照；隔夜闸 y_on_allow / y_on_risk 已下线
  y_trade / y_eod — **v6 已下线**：不参与选向；`load_t0_rules` 丢弃旧闸键；快照字段仅作对照

选向分数：开盘可预计算开盘 Z；**确认根（前 N 根齐窗）用前缀分钟因果重算**
ŷ_τ（及 τ 窗）后再 close-band 选向。禁止用全日/未发生分钟做开盘选向。
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW
from core.signal.factors.alpha_tc import ALPHA_TC_PANEL_WINDOW

logger = logging.getLogger(__name__)

# compute | live_book | ledger
DEFAULT_Y_SCORE_SOURCE = "compute"
_MIN_HIST_BARS = 16
# 与 score_stock 实盘取数对齐（alpha_oo / alpha_tc 面板窗取较大者）。周线仍从头切桶。
_EOD_FACTOR_BAR_LIMIT = max(int(ALPHA_OO_PANEL_WINDOW), int(ALPHA_TC_PANEL_WINDOW))
# 做 T 回测：评估窗与因子窗分离；warmup 仅供 hist_prior，不进成交明细
T0_BACKTEST_SCORE_WARMUP = max(_MIN_HIST_BARS + 8, _EOD_FACTOR_BAR_LIMIT)
# 纸面预演 / Worker：须含「当日 + EOD 窗」，否则比回测少 1 根、raw 包缺特征
T0_PAPER_DAILY_BAR_LIMIT = T0_BACKTEST_SCORE_WARMUP + 1

# 进程内轻量缓存：回测逐日重算时复用模型句柄
_MODEL_CACHE: Dict[str, Any] = {}
# 单日 τ 截面缓存：day → build_tau_pool_by_date 条目
_TAU_XS_DAY_CACHE: Dict[str, Dict[str, Any]] = {}
# 盯盘 / 做T回测共用截面宇宙（纸面持仓 ∪ 观察池 ∪ extra）
_CS_UNIVERSE_MEMO: Optional[List[str]] = None
# 做 T 截面 z：请求打开后，ŷ 用观察池当天特征，而不是单票把 z 打成 0。
_POOL_Z_REQUEST: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "t0_pool_z_request", default=False
)
_POOL_Z_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar(
    "t0_pool_z_depth", default=0
)
# 同伴日线复用（同进程、同码表；全样本截面 z 跨日冷启动时免重复拉 K）
_PEER_BARS_MEMO: Dict[str, Dict[str, List[dict]]] = {}
_PEER_BARS_MEMO_MAX = 4
_ITEM_SINK: contextvars.ContextVar[Optional[List[dict]]] = contextvars.ContextVar(
    "t0_pool_item_sink", default=None
)
# date → 观察池原始特征行（不含持仓外的票）
_POOL_FEAT_CACHE: Dict[str, List[dict]] = {}
# date|HH:MM → 该钟 tc_HHMM L1 同伴行（含 raw_ath_*；Cube miss 时做截面 z）
_TAU_LAKE_PEER_CACHE: Dict[str, List[dict]] = {}
# 同一交易日多根 5m 前缀重算时，EOD 因子窗不变；缓存避免每根重跑 score_window
_EOD_ITEM_CACHE: Dict[tuple, dict] = {}
_EOD_ITEM_CACHE_MAX = 256
# 本地估值/财务：按代码缓存，避免逐根 5m 打盘
_FUND_CACHE: Dict[str, Any] = {}
# 同票同日前缀 ŷ：扫描 + 多轮引擎各走一遍，回测可复用
_PREFIX_RESCORE_CACHE: Dict[tuple, dict] = {}
_PREFIX_RESCORE_CACHE_MAX = 4096


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _is_imputed_formula_term(term: Any) -> bool:
    if not isinstance(term, dict):
        return False
    return "缺特征" in str(term.get("note") or "")


def _slim_formula_terms(
    expl: Any,
    *,
    limit: int = 10,
    pin_keys: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """压缩分项拆解，避免成交日 scores / data-score-detail 过大截断。

    ``pin_keys``：对照关心的因子（如非流动性）即使贡献小也保留；
    在 ``limit`` 内用 pin 替换末位，**不追加**以免属性过长截断坏 JSON。
    缺特征（z=0 / 贡献 0）不占表；合计仍是 Ridge 原值。
    """
    if not isinstance(expl, dict):
        return None
    terms_in = list(expl.get("terms") or [])
    n_missing = sum(1 for t in terms_in if _is_imputed_formula_term(t))
    terms: List[Dict[str, Any]] = []
    for t in terms_in:
        if not isinstance(t, dict) or _is_imputed_formula_term(t):
            continue
        key = str(t.get("key") or t.get("factor") or "").strip()
        if not key:
            continue
        row: Dict[str, Any] = {
            "key": key,
            "label": t.get("label") or key,
            "beta": _f(t.get("beta")),
            "z": _f(t.get("z")),
            "contrib": _f(t.get("contrib")),
        }
        if t.get("gated"):
            row["gated"] = True
        note = t.get("note")
        if note:
            row["note"] = str(note)[:40]
        terms.append(row)
    terms.sort(key=lambda x: -abs(float(x.get("contrib") or 0.0)))
    keep_n = max(1, int(limit))
    pin = {str(k).strip() for k in (pin_keys or []) if str(k).strip()}
    if pin and terms:
        pin_terms = [t for t in terms if str(t.get("key")) in pin]
        non_pin = [t for t in terms if str(t.get("key")) not in pin]
        budget = max(0, keep_n - len(pin_terms))
        kept = list(non_pin[:budget]) + pin_terms
        kept.sort(key=lambda x: -abs(float(x.get("contrib") or 0.0)))
    else:
        kept = list(terms[:keep_n]) if terms else []
    out: Dict[str, Any] = {
        "intercept": _f(expl.get("intercept")),
        "total": _f(expl.get("total")),
        "terms": kept,
    }
    if n_missing:
        out["missing_n"] = int(n_missing)
    if expl.get("missing_n") is not None and out.get("missing_n") is None:
        out["missing_n"] = expl.get("missing_n")
    if expl.get("missing_keys") is not None:
        out["missing_keys"] = expl.get("missing_keys")
    if expl.get("head") is not None:
        out["head"] = expl.get("head")
    if expl.get("model_role") is not None:
        out["model_role"] = expl.get("model_role")
    for meta_k in (
        "y_eod",
        "y_tau",
        "y_tau_raw",
        "trade",
        "cascade",
        "nowcast",
        "eod_remaining",
        "rem_oc",
    ):
        if expl.get(meta_k) is not None:
            out[meta_k] = expl.get(meta_k)
    return out


# tip 对照常看、β 往往偏小，slim 时在 limit 内优先保留
_TIP_PIN_FACTORS = (
    "amihud",
    "liquidity",
    "money_flow",
    "volume_price",
    "relative_strength",
    "size",
)
_TAU_TIP_PIN_FACTORS = (
    "gap_pct",
    "theme_day",
    "gap_atr",
    "sector_gap_breadth",
    "gap_vs_sector",
    "yclose_loc",
    "tau_lag1",
    "tau_ma5",
    "ret_open_to_tau",
    "tau_elapsed_min",
    "loc_hl",
    "ret_last_15m",
    "sector_ret_to_tau",
)


def _slim_factor_coefs(coefs: Any, *, limit: int = 14) -> Optional[Dict[str, float]]:
    if not isinstance(coefs, dict) or not coefs:
        return None
    pairs: List[Tuple[str, float]] = []
    for k, v in coefs.items():
        if str(k) == "intercept":
            continue
        fv = _f(v)
        if fv is None:
            continue
        pairs.append((str(k), float(fv)))
    if not pairs:
        return None
    pairs.sort(key=lambda kv: (-abs(kv[1]), kv[0]))
    return {k: v for k, v in pairs[: max(1, int(limit))]}


def _slim_features_tau(feats: Any, *, limit: int = 24) -> Optional[Dict[str, Any]]:
    if not isinstance(feats, dict) or not feats:
        return None
    # 先保住 τ Z 键，避免 dict 截断丢掉 gap_pct / breadth / 分钟小包
    from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS, MINUTE_TAU_SHAPE_KEYS
    from core.research.tau_panel import (
        T30_SEQ_FEATURES,
        T45_SEQ_FEATURES,
        T60_SEQ_FEATURES,
        T75_SEQ_FEATURES,
        T90_SEQ_FEATURES,
    )

    pin = (
        "gap_pct",
        "open_gap",
        "sector_gap_breadth",
        "theme_day",
        "gap_atr",
        "gap_vs_sector",
        "yclose_loc",
        "tau_lag1",
        "tau_ma5",
    ) + MINUTE_TAU_ALL_KEYS + MINUTE_TAU_SHAPE_KEYS + T30_SEQ_FEATURES + T45_SEQ_FEATURES + T60_SEQ_FEATURES + T75_SEQ_FEATURES + T90_SEQ_FEATURES
    out: Dict[str, Any] = {}
    lim = max(len(pin) + 4, int(limit))

    def _put(key: str, v: Any) -> None:
        if key in out or len(out) >= lim:
            return
        if isinstance(v, bool):
            out[key] = v
            return
        fv = _f(v)
        if fv is not None:
            out[key] = fv

    for k in pin:
        if k in feats:
            _put(k, feats.get(k))
    for k, v in feats.items():
        _put(str(k), v)
    return out or None


def tau_pool_day_score_kwargs(
    pool_day: Optional[dict],
    code: str,
) -> Dict[str, Any]:
    """从 ``build_tau_pool_by_date`` 单日条目抽出 resolve_scores 截面参数。

    开盘缺口截面；开→τ 的 ``sector_ret_to_tau`` 由确认根前缀重算时按末根钟解析。
    """
    pool = pool_day if isinstance(pool_day, dict) else {}
    ref = pool.get("ref_by_code") if isinstance(pool.get("ref_by_code"), dict) else {}
    key = str(code or "").strip()
    return {
        "pool_gaps": pool.get("pool_gaps"),
        "sector_gap_breadth": pool.get("sector_gap_breadth"),
        "sector_gap_median": ref.get(key) if key else None,
    }


def tip_fields_from_item(item: Optional[dict]) -> Dict[str, Any]:
    """从 signal_item / 簿行抽出 tip 用因子字段（与数据中心对照）。"""
    if not isinstance(item, dict):
        return {}
    out: Dict[str, Any] = {}
    eod_terms = _slim_formula_terms(
        item.get("score_formula_terms") or item.get("formula_terms"),
        limit=24,
        pin_keys=_TIP_PIN_FACTORS,
    )
    if eod_terms and (eod_terms.get("terms") or eod_terms.get("intercept") is not None):
        out["score_formula_terms"] = eod_terms
    coefs = _slim_factor_coefs(item.get("factor_coefficients"))
    if coefs:
        out["factor_coefficients"] = coefs

    tau_terms = _slim_formula_terms(
        item.get("formula_terms_tau") or item.get("score_formula_terms_tau"),
        limit=24,
        pin_keys=_TAU_TIP_PIN_FACTORS,
    )
    if tau_terms and (tau_terms.get("terms") or tau_terms.get("total") is not None):
        out["formula_terms_tau"] = tau_terms
        out["score_formula_terms_tau"] = tau_terms
    coefs_tau = _slim_factor_coefs(item.get("factor_coefficients_tau"))
    if not coefs_tau and (tau_terms or item.get("predicted_score_tau") is not None):
        try:
            from core.signal.dual_score import rem_factor_coefficients_public

            coefs_tau = _slim_factor_coefs(rem_factor_coefficients_public())
        except Exception:  # noqa: BLE001
            logger.debug("rem_factor_coefficients_public failed", exc_info=True)
            coefs_tau = None
    if coefs_tau:
        out["factor_coefficients_tau"] = coefs_tau

    co_terms = _slim_formula_terms(
        item.get("formula_terms_co")
        or item.get("score_formula_terms_co")
        or item.get("formula_terms_on")
        or item.get("score_formula_terms_on"),
        limit=24,
    )
    if co_terms and (co_terms.get("terms") or co_terms.get("total") is not None):
        out["formula_terms_co"] = co_terms
        out["score_formula_terms_co"] = co_terms

    r_terms = _slim_formula_terms(
        item.get("formula_terms_r") or item.get("score_formula_terms_r"),
        limit=12,
    )
    if not r_terms or not (r_terms.get("terms") or r_terms.get("total") is not None):
        try:
            from core.research.tc_ridge import explain_tau_prediction, load_tau_model

            feats_r = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_r:
                feats_r = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            from core.signal.dual_score.tau import tau_explain_feats_row

            expl_r = explain_tau_prediction(
                tau_explain_feats_row(item, feats_r),
                model_doc=load_tau_model(),
            )
            r_terms = _slim_formula_terms(expl_r, limit=12)
        except Exception:  # noqa: BLE001
            logger.debug("r tip explain fallback failed", exc_info=True)
            r_terms = None
    if r_terms and (r_terms.get("terms") or r_terms.get("total") is not None):
        out["formula_terms_r"] = r_terms
        out["score_formula_terms_r"] = r_terms

    t30_terms = _slim_formula_terms(
        item.get("formula_terms_t30") or item.get("score_formula_terms_t30"),
        limit=12,
    )
    if not t30_terms or not (t30_terms.get("terms") or t30_terms.get("total") is not None):
        if str(item.get("y_τ30_source") or item.get("y_t30_source") or "") == "tree":
            t30_terms = None
        else:
            try:
                from core.research.horizon_ridge import explain_ridge_prediction, load_ridge_model

                feats_t30 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
                if not feats_t30:
                    feats_t30 = (
                        item.get("features_path")
                        if isinstance(item.get("features_path"), dict)
                        else {}
                    )
                expl_t30 = explain_ridge_prediction("t30", feats_t30, model_doc=load_ridge_model("t30"))
                t30_terms = _slim_formula_terms(expl_t30, limit=12)
            except Exception:  # noqa: BLE001
                logger.debug("t30 tip explain fallback failed", exc_info=True)
                t30_terms = None
    if t30_terms and (t30_terms.get("terms") or t30_terms.get("total") is not None):
        out["formula_terms_t30"] = t30_terms
        out["score_formula_terms_t30"] = t30_terms

    t45_terms = _slim_formula_terms(
        item.get("formula_terms_t45") or item.get("score_formula_terms_t45"),
        limit=12,
    )
    if not t45_terms or not (t45_terms.get("terms") or t45_terms.get("total") is not None):
        if str(item.get("y_τ45_source") or item.get("y_t45_source") or "") == "tree":
            t45_terms = None
        else:
            try:
                from core.research.horizon_ridge import explain_ridge_prediction, load_ridge_model

                feats_t45 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
                if not feats_t45:
                    feats_t45 = (
                        item.get("features_path")
                        if isinstance(item.get("features_path"), dict)
                        else {}
                    )
                expl_t45 = explain_ridge_prediction("t45", feats_t45, model_doc=load_ridge_model("t45"))
                t45_terms = _slim_formula_terms(expl_t45, limit=12)
            except Exception:  # noqa: BLE001
                logger.debug("t45 tip explain fallback failed", exc_info=True)
                t45_terms = None
    if t45_terms and (t45_terms.get("terms") or t45_terms.get("total") is not None):
        out["formula_terms_t45"] = t45_terms
        out["score_formula_terms_t45"] = t45_terms

    t60_terms = _slim_formula_terms(
        item.get("formula_terms_t60") or item.get("score_formula_terms_t60"),
        limit=12,
    )
    if not t60_terms or not (t60_terms.get("terms") or t60_terms.get("total") is not None):
        if str(item.get("y_τ60_source") or item.get("y_t60_source") or "") == "tree":
            t60_terms = None
        else:
            try:
                from core.research.horizon_ridge import explain_ridge_prediction, load_ridge_model

                feats_t60 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
                if not feats_t60:
                    feats_t60 = (
                        item.get("features_path")
                        if isinstance(item.get("features_path"), dict)
                        else {}
                    )
                expl_t60 = explain_ridge_prediction("t60", feats_t60, model_doc=load_ridge_model("t60"))
                t60_terms = _slim_formula_terms(expl_t60, limit=12)
            except Exception:  # noqa: BLE001
                logger.debug("t60 tip explain fallback failed", exc_info=True)
                t60_terms = None
    if t60_terms and (t60_terms.get("terms") or t60_terms.get("total") is not None):
        out["formula_terms_t60"] = t60_terms
        out["score_formula_terms_t60"] = t60_terms

    t75_terms = _slim_formula_terms(
        item.get("formula_terms_t75") or item.get("score_formula_terms_t75"),
        limit=12,
    )
    if not t75_terms or not (t75_terms.get("terms") or t75_terms.get("total") is not None):
        if str(item.get("y_τ75_source") or item.get("y_t75_source") or "") == "tree":
            t75_terms = None
        else:
            try:
                from core.research.horizon_ridge import explain_ridge_prediction, load_ridge_model

                feats_t75 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
                if not feats_t75:
                    feats_t75 = (
                        item.get("features_path")
                        if isinstance(item.get("features_path"), dict)
                        else {}
                    )
                expl_t75 = explain_ridge_prediction("t75", feats_t75, model_doc=load_ridge_model("t75"))
                t75_terms = _slim_formula_terms(expl_t75, limit=12)
            except Exception:  # noqa: BLE001
                logger.debug("t75 tip explain fallback failed", exc_info=True)
                t75_terms = None
    if t75_terms and (t75_terms.get("terms") or t75_terms.get("total") is not None):
        out["formula_terms_t75"] = t75_terms
        out["score_formula_terms_t75"] = t75_terms

    t90_terms = _slim_formula_terms(
        item.get("formula_terms_t90") or item.get("score_formula_terms_t90"),
        limit=12,
    )
    if not t90_terms or not (t90_terms.get("terms") or t90_terms.get("total") is not None):
        if str(item.get("y_τ90_source") or item.get("y_t90_source") or "") == "tree":
            t90_terms = None
        else:
            try:
                from core.research.horizon_ridge import explain_ridge_prediction, load_ridge_model

                feats_t90 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
                if not feats_t90:
                    feats_t90 = (
                        item.get("features_path")
                        if isinstance(item.get("features_path"), dict)
                        else {}
                    )
                expl_t90 = explain_ridge_prediction("t90", feats_t90, model_doc=load_ridge_model("t90"))
                t90_terms = _slim_formula_terms(expl_t90, limit=12)
            except Exception:  # noqa: BLE001
                logger.debug("t90 tip explain fallback failed", exc_info=True)
                t90_terms = None
    if t90_terms and (t90_terms.get("terms") or t90_terms.get("total") is not None):
        out["formula_terms_t90"] = t90_terms
        out["score_formula_terms_t90"] = t90_terms

    feats_path = item.get("features_path")
    if isinstance(feats_path, dict) and feats_path:
        slim_path = {
            str(k): v
            for k, v in feats_path.items()
            if v is not None and str(k) in {
                "gap_pct",
                "sector_gap_breadth",
                "theme_day",
                "gap_atr",
                "gap_vs_sector",
                "yclose_loc",
                "path_lag1",
                "path_ma5",
                "t_hi_frac",
                "t_lo_frac",
                "t_hi_minus_lo",
                "room_to_high",
                "room_to_low",
                "mom_accel_5_15",
                "mom_accel_5_30",
                "vol_down_up",
                "range_efficiency",
                "vp_confirm",
                "vol_up_share",
                "pullback_x_vol",
            }
        }
        if slim_path:
            out["features_path"] = slim_path
    elif isinstance(item.get("features_tau"), dict):
        # 无 path 特征时退回 τ 开盘 Z，便于 tip 重算组成
        slim_path = {
            str(k): v
            for k, v in item["features_tau"].items()
            if v is not None and str(k) in {
                "gap_pct",
                "sector_gap_breadth",
                "theme_day",
                "gap_atr",
                "gap_vs_sector",
                "yclose_loc",
                "path_lag1",
                "path_ma5",
                "tau_lag1",
                "tau_ma5",
            }
        }
        if slim_path:
            out["features_path"] = slim_path

    feats_tau = _slim_features_tau(item.get("features_tau"))
    if feats_tau:
        out["features_tau"] = feats_tau

    try:
        from core.signal.dual_score.co import features_co_snapshot

        feats_co = features_co_snapshot(
            item.get("features_co")
            if isinstance(item.get("features_co"), dict)
            else item.get("features_on")
        )
        if feats_co:
            out["features_co"] = feats_co
    except Exception:  # noqa: BLE001
        logger.debug("features_co tip slim failed", exc_info=True)

    for k in (
        "gap_pct",
        "as_of_tau",
        "rem_tau",
        "dual_score_fusion",
        "dual_score_weights",
        "dual_score_window",
        "dual_score_head",
        "predicted_score_eod_rem",
        "return_model_source",
        "cluster_label",
        "weight_source",
        "y_spec_tau",
        "y_spec_τc",
        "y_spec_r",
        "y_spec_τ30",
        "y_spec_t30",
        "y_τc_ridge",
        "y_τc_source",
        "nowcast_K",
        "nowcast_vs",
        "nowcast_as_of",
        "predicted_score_eod",
        "predicted_score_tau",
        "predicted_score_nowcast",
    ):
        v = item.get(k)
        if v is None or v == "" or v == {}:
            continue
        out[k] = v
    return out


def pack_day_scores(score_snap: Optional[dict]) -> Optional[Dict[str, Any]]:
    """成交日持久化：ŷ 标量 + tip 因子字段。"""
    if not isinstance(score_snap, dict) or not scores_have_any(score_snap):
        return None
    out: Dict[str, Any] = {}
    for k in (
        "y_eod",
        "y_tau",
        "y_trade",
        "residual",
        "r_hat",
        "y_oo",
        "y_τc",
        "y_τc_ridge",
        "y_τc_source",
        "y_oo_fuse",
        "y_on",
        "y_on_path",
        "y_co",
        "y_nowcast",
        "predicted_score_r",
        "y_r_hat",
        "y_r",
        "r_realized",
        "y_r_realized",
        "y_τ30",
        "y_t30",
        "y_t30_hat",
        "predicted_score_t30",
        "y_t30_realized",
        "t30_realized",
        "y_τ45",
        "y_t45",
        "y_t45_hat",
        "predicted_score_t45",
        "y_t45_realized",
        "t45_realized",
        "y_τ60",
        "y_t60",
        "y_t60_hat",
        "predicted_score_t60",
        "y_t60_realized",
        "t60_realized",
        "y_τ75",
        "y_t75",
        "y_t75_hat",
        "predicted_score_t75",
        "y_t75_realized",
        "t75_realized",
        "y_τ90",
        "y_t90",
        "y_t90_hat",
        "predicted_score_t90",
        "y_t90_realized",
        "t90_realized",
        "y_check",
        "eod_trust",
        "y_tau_portrait_oc",
        "gap_pct",
    ):
        if k in score_snap and score_snap.get(k) is not None:
            out[k] = score_snap.get(k)
    for k, v in tip_fields_from_item(score_snap).items():
        out[k] = v
    src = score_snap.get("_score_source")
    if src:
        out["_score_source"] = src
    try:
        from core.signal.yhat_windows import pick_y_co

        yco = pick_y_co(out) or pick_y_co(score_snap)
        if yco is not None:
            out["y_co"] = yco
            out.pop("y_on", None)
    except Exception:  # noqa: BLE001
        logger.debug("pack_day_scores canonical y_co failed", exc_info=True)
    return out or None


def attach_day_scores(
    day: Optional[dict],
    score_snap: Optional[dict] = None,
    *,
    features: Optional[dict] = None,
) -> Dict[str, Any]:
    """把 dual_y ŷ 写回日结果（成交/跳过共用），供成交明细 y_* 列读取。"""
    out: Dict[str, Any] = dict(day or {})
    feats: Dict[str, Any] = {}
    if isinstance(out.get("direction_features"), dict):
        feats.update(out["direction_features"])
    if isinstance(features, dict):
        feats.update({k: v for k, v in features.items() if v is not None or k not in feats})
    snap = score_snap if isinstance(score_snap, dict) else None
    if scores_have_any(snap):
        for k in (
            "y_eod",
            "y_tau",
            "y_trade",
            "y_on",
            "y_on_path",
            "y_co",
            "y_nowcast",
            "predicted_score_r",
            "y_r_hat",
            "y_r",
            "r_realized",
            "y_r_realized",
            "y_check",
            "eod_trust",
            "gap_pct",
        ):
            if feats.get(k) is None and snap.get(k) is not None:
                feats[k] = snap.get(k)
        packed = pack_day_scores(snap)
        if packed:
            out["scores"] = packed
    elif scores_have_any(feats):
        packed = pack_day_scores(feats)
        if packed:
            out["scores"] = packed
    if feats:
        out["direction_features"] = feats
    return attach_eod_tau_realized(out)


def attach_eod_tau_realized(
    day: Optional[dict],
    *,
    open_px: Optional[float] = None,
    close_px: Optional[float] = None,
    prev_close: Optional[float] = None,
) -> Dict[str, Any]:
    """写入 y_eod / y_τ 对照真实收益（与训练标签同口径）。

    - ``eod_realized``: close[T]/close[T−1]−1（%）
    - ``tau_realized``: close[T]/open[T]−1（%）
    """
    out: Dict[str, Any] = dict(day or {})
    o = _f(open_px if open_px is not None else out.get("open"))
    c = _f(close_px if close_px is not None else out.get("close"))
    pc = _f(prev_close if prev_close is not None else out.get("prev_close"))
    if isinstance(out.get("bar"), dict):
        bar = out["bar"]
        if o is None:
            o = _f(bar.get("open"))
        if c is None:
            c = _f(bar.get("close"))
        if pc is None:
            pc = _f(bar.get("prev_close"))

    tau_r: Optional[float] = None
    eod_r: Optional[float] = None
    if o is not None and c is not None and float(o) > 0:
        tau_r = round((float(c) / float(o) - 1.0) * 100.0, 4)
    if pc is not None and c is not None and float(pc) > 0:
        eod_r = round((float(c) / float(pc) - 1.0) * 100.0, 4)

    if o is not None:
        out["open"] = float(o)
    if c is not None:
        out["close"] = float(c)
    if pc is not None:
        out["prev_close"] = float(pc)

    scores = dict(out.get("scores") or {}) if isinstance(out.get("scores"), dict) else {}
    feats = (
        dict(out.get("direction_features") or {})
        if isinstance(out.get("direction_features"), dict)
        else {}
    )
    if tau_r is not None:
        out["tau_realized"] = tau_r
        scores["tau_realized"] = tau_r
        feats["tau_realized"] = tau_r
    if eod_r is not None:
        out["eod_realized"] = eod_r
        scores["eod_realized"] = eod_r
        feats["eod_realized"] = eod_r
    if scores:
        out["scores"] = scores
    if feats:
        out["direction_features"] = feats
    return out



def scores_from_item(item: Optional[dict]) -> Dict[str, Optional[float]]:
    """从 signal_item / 账本行 / insights 抽出做T用分数。

    契约：分数里的 ``y_tau`` 取自 ``y_τc``（price(τ)→close，含时钟对齐）。
    缺 ``y_τc`` 时才读旧行 ``y_tau`` / ``score_rem``。
    """
    if not isinstance(item, dict):
        return {
            "y_eod": None,
            "y_tau": None,
            "y_trade": None,
            "y_co": None,
            "y_nowcast": None,
            "y_check": None,
            "eod_trust": None,
        }
    y_eod = _f(item.get("y_oo"))
    if y_eod is None:
        y_eod = _f(item.get("predicted_score_oo"))
    if y_eod is None:
        y_eod = _f(item.get("y_eod"))
    if y_eod is None:
        y_eod = _f(item.get("predicted_score_eod"))
    if y_eod is None:
        y_eod = _f(item.get("yhat_eod"))

    # 可能已是剩余映射（predicted_score_tau / 旧 y_tau）
    y_tau_mapped = _f(item.get("y_tau_mapped"))
    if y_tau_mapped is None:
        y_tau_mapped = _f(item.get("predicted_score_tau"))
    if y_tau_mapped is None:
        y_tau_mapped = _f(item.get("score_rem"))
    if y_tau_mapped is None:
        y_tau_mapped = _f(item.get("yhat_tau"))
    from core.signal.yhat_windows import pick_y_τc

    y_from_tc = _f(item.get("y_τc"))
    if y_from_tc is None:
        y_from_tc = _f(item.get("predicted_score_τc"))
    y_tau_field = _f(item.get("y_tau"))
    if y_tau_mapped is None and y_tau_field is not None:
        y_tau_mapped = y_tau_field
    if y_from_tc is not None:
        y_tau = y_from_tc
    elif y_tau_field is not None:
        y_tau = y_tau_field
    elif y_tau_mapped is not None:
        y_tau = y_tau_mapped
    else:
        y_tau = pick_y_τc(item)

    # 契约：predicted_score / predicted_score_eod = ŷ_oo；ŷ_trade = blend / decision_score。
    # 旧实现先读 predicted_score，双头下会把 y_trade 塌成 y_eod（成交明细两列相同）。
    y_trade = _f(item.get("y_trade"))
    if y_trade is None:
        y_trade = _f(item.get("predicted_score_blend"))
    if y_trade is None:
        y_trade = _f(item.get("decision_score"))
    if y_trade is None:
        y_trade = _f(item.get("yhat"))
    if y_trade is None:
        # 旧单头：无 blend / EOD 分叉时 predicted_score 即 trade
        y_eod_probe = _f(item.get("predicted_score_eod"))
        y_ps = _f(item.get("predicted_score"))
        if y_eod_probe is None or y_ps is None or abs(y_ps - y_eod_probe) > 1e-9:
            y_trade = y_ps
    # 禁止 heuristic 0–100 冒充 trade
    if y_trade is not None and abs(y_trade) > 20.0:
        y_trade = None

    from core.signal.yhat_windows import pick_y_co

    y_co = pick_y_co(item)

    y_on_path = _f(item.get("y_on_path"))
    if y_on_path is None:
        y_on_path = _f(item.get("predicted_score_on_path"))

    y_nowcast = _f(item.get("y_nowcast"))
    if y_nowcast is None:
        y_nowcast = _f(item.get("predicted_score_nowcast"))

    y_check = item.get("y_check")
    if y_check is not None:
        y_check = str(y_check)
    eod_trust = _f(item.get("eod_trust"))

    out: Dict[str, Any] = {
        "y_eod": y_eod,
        "y_tau": y_tau,
        "y_trade": y_trade,
        "y_co": y_co,
        "y_nowcast": y_nowcast,
        "y_check": y_check,
        "eod_trust": eod_trust,
    }
    try:
        from core.signal.yhat_windows import stamp_window_scores

        _win = stamp_window_scores(item)
        for k in ("y_oo", "y_τc", "y_co", "y_oo_fuse", "residual", "r_hat"):
            if _win.get(k) is not None:
                out[k] = _win[k]
    except Exception:  # noqa: BLE001
        logger.debug("stamp_window_scores failed", exc_info=True)
    try:
        from core.signal.yhat_windows import pick_y_tc_hat, pick_y_tc_label, write_y_tc_hat, write_y_tc_label

        y_r_hat = pick_y_tc_hat(item)
        if y_r_hat is not None:
            src_τc = item.get("y_τc")
            if src_τc is None:
                src_τc = item.get("predicted_score_τc")
            if src_τc is None:
                write_y_tc_hat(out, y_r_hat, model_doc=item)
            else:
                out["predicted_score_r"] = float(y_r_hat)
                out["y_r_hat"] = float(y_r_hat)
                out["y_r"] = float(y_r_hat)
        y_r_lab = pick_y_tc_label(item)
        if y_r_lab is not None:
            write_y_tc_label(out, y_r_lab)
    except Exception:  # noqa: BLE001
        y_r_hat = _f(item.get("predicted_score_r"))
        if y_r_hat is None:
            y_r_hat = _f(item.get("y_r_hat"))
        if y_r_hat is None:
            y_r_hat = _f(item.get("y_r"))
        if y_r_hat is not None:
            out["predicted_score_r"] = y_r_hat
            out["y_r_hat"] = y_r_hat
            out["y_r"] = y_r_hat
    try:
        from core.research.horizon_ridge import pick_y_hat, write_y_hat

        y_t30 = pick_y_hat("t30", item)
        if y_t30 is not None:
            write_y_hat("t30", out, y_t30)
    except Exception:  # noqa: BLE001
        y_t30 = _f(item.get("predicted_score_t30"))
        if y_t30 is None:
            y_t30 = _f(item.get("y_t30_hat"))
        if y_t30 is None:
            y_t30 = _f(item.get("y_τ30"))
        if y_t30 is None:
            y_t30 = _f(item.get("y_t30"))
        if y_t30 is not None:
            out["predicted_score_t30"] = y_t30
            out["y_t30_hat"] = y_t30
            out["y_t30"] = y_t30
            out["y_τ30"] = y_t30
    try:
        from core.research.horizon_ridge import pick_y_hat, write_y_hat

        y_t45 = pick_y_hat("t45", item)
        if y_t45 is not None:
            write_y_hat("t45", out, y_t45)
    except Exception:  # noqa: BLE001
        y_t45 = _f(item.get("predicted_score_t45"))
        if y_t45 is None:
            y_t45 = _f(item.get("y_t45_hat"))
        if y_t45 is None:
            y_t45 = _f(item.get("y_τ45"))
        if y_t45 is None:
            y_t45 = _f(item.get("y_t45"))
        if y_t45 is not None:
            out["predicted_score_t45"] = y_t45
            out["y_t45_hat"] = y_t45
            out["y_t45"] = y_t45
            out["y_τ45"] = y_t45
    try:
        from core.research.horizon_ridge import pick_y_hat, write_y_hat

        y_t60 = pick_y_hat("t60", item)
        if y_t60 is not None:
            write_y_hat("t60", out, y_t60)
    except Exception:  # noqa: BLE001
        y_t60 = _f(item.get("predicted_score_t60"))
        if y_t60 is None:
            y_t60 = _f(item.get("y_t60_hat"))
        if y_t60 is None:
            y_t60 = _f(item.get("y_τ60"))
        if y_t60 is None:
            y_t60 = _f(item.get("y_t60"))
        if y_t60 is not None:
            out["predicted_score_t60"] = y_t60
            out["y_t60_hat"] = y_t60
            out["y_t60"] = y_t60
            out["y_τ60"] = y_t60
    try:
        from core.research.horizon_ridge import pick_y_hat, write_y_hat

        y_t75 = pick_y_hat("t75", item)
        if y_t75 is not None:
            write_y_hat("t75", out, y_t75)
    except Exception:  # noqa: BLE001
        y_t75 = _f(item.get("predicted_score_t75"))
        if y_t75 is None:
            y_t75 = _f(item.get("y_t75_hat"))
        if y_t75 is None:
            y_t75 = _f(item.get("y_τ75"))
        if y_t75 is None:
            y_t75 = _f(item.get("y_t75"))
        if y_t75 is not None:
            out["predicted_score_t75"] = y_t75
            out["y_t75_hat"] = y_t75
            out["y_t75"] = y_t75
            out["y_τ75"] = y_t75
    try:
        from core.research.horizon_ridge import pick_y_hat, write_y_hat

        y_t90 = pick_y_hat("t90", item)
        if y_t90 is not None:
            write_y_hat("t90", out, y_t90)
    except Exception:  # noqa: BLE001
        y_t90 = _f(item.get("predicted_score_t90"))
        if y_t90 is None:
            y_t90 = _f(item.get("y_t90_hat"))
        if y_t90 is None:
            y_t90 = _f(item.get("y_τ90"))
        if y_t90 is None:
            y_t90 = _f(item.get("y_t90"))
        if y_t90 is not None:
            out["predicted_score_t90"] = y_t90
            out["y_t90_hat"] = y_t90
            out["y_t90"] = y_t90
            out["y_τ90"] = y_t90
    if y_tau_mapped is not None:
        out["y_tau_mapped"] = y_tau_mapped
    if y_on_path is not None:
        out["y_on_path"] = y_on_path
        out["predicted_score_on_path"] = y_on_path

    gap = _f(item.get("gap_pct"))
    if gap is None and isinstance(item.get("features_tau"), dict):
        gap = _f(item["features_tau"].get("gap_pct"))
    if gap is not None:
        out["gap_pct"] = gap
    for k in (
        "nowcast_K",
        "nowcast_vs",
        "nowcast_as_of",
        "predicted_score_eod",
        "predicted_score_tau",
        "predicted_score_nowcast",
        "dual_score_weights",
        "dual_score_window",
        "as_of_tau",
        "rem_tau",
    ):
        v = item.get(k)
        if v is not None and v != "" and v != {}:
            out[k] = v
    out.update(tip_fields_from_item(item))
    try:
        from core.signal.yhat_windows import stamp_remaining_y_τc

        stamp_remaining_y_τc(out)
    except Exception:  # noqa: BLE001
        logger.debug("stamp remaining y_τc in scores_from_item failed", exc_info=True)
    try:
        from core.research.holdout import current_scoring_model_role

        out["_score_model_role"] = current_scoring_model_role()
    except Exception:  # noqa: BLE001
        logger.debug("stamp score model role failed", exc_info=True)
    return out


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    try:
        v = cfg.get(key)
        if v is None or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def resolve_direction_y_tau(scores: Optional[dict]) -> Optional[float]:
    """做 T 定向用的 ŷ_τc（price(τ)→close，含时钟对齐）。"""
    if not isinstance(scores, dict):
        return None
    for key in ("y_τc", "predicted_score_τc"):
        v = _f(scores.get(key))
        if v is not None:
            return v
    return None


def _tau_path_same_sign(
    y_tau: Optional[float],
    y_path: Optional[float],
    *,
    sign_eps: float = 1e-9,
) -> bool:
    """y_τ 与 y_hl 同号（均非零）。旧成交对照用。"""
    if y_tau is None or y_path is None:
        return False
    if abs(float(y_tau)) <= sign_eps or abs(float(y_path)) <= sign_eps:
        return False
    return (float(y_tau) > 0) == (float(y_path) > 0)


def _minute_pack_present(feats: Optional[dict]) -> bool:
    """features_tau 是否已含分钟小包（至少 ret_open_to_tau）。"""
    if not isinstance(feats, dict):
        return False
    return feats.get("ret_open_to_tau") is not None


def _attach_y_path_to_item(
    item: dict,
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    allow_open_z: bool = True,
    include_tau_horizons: bool = True,
) -> None:
    """挂载辅头 ŷ_r / τ30–90（ŷ_hl 已下线，不再预测）。

    函数名保留兼容调用方；``allow_open_z`` 仍约束盘中不得用纯开盘 Z 冒充盘中辅头。
    """
    if not isinstance(item, dict):
        return
    try:
        for k in (
            "y_hl",
            "predicted_score_hl",
            "y_path",
            "predicted_score_path",
        ):
            item.pop(k, None)
        item.pop("y_hl_status", None)
        item.pop("y_path_status", None)
        item.pop("y_hl_error", None)
        item.pop("y_path_error", None)
        item.pop("formula_terms_path", None)
        item.pop("score_formula_terms_path", None)
        item.pop("y_hl_portrait", None)
        item.pop("y_path_portrait", None)
    except Exception:  # noqa: BLE001
        logger.debug("clear retired y_hl fields failed", exc_info=True)
    try:
        _attach_y_r_to_item(item, allow_open_z=allow_open_z)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_r failed", exc_info=True)
    if not include_tau_horizons:
        return
    try:
        _attach_y_t30_to_item(item, allow_open_z=allow_open_z, hist_bars=hist_bars)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t30 failed", exc_info=True)
    try:
        _attach_y_t45_to_item(item, allow_open_z=allow_open_z, hist_bars=hist_bars)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t45 failed", exc_info=True)
    try:
        _attach_y_t60_to_item(item, allow_open_z=allow_open_z, hist_bars=hist_bars)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t60 failed", exc_info=True)
    try:
        _attach_y_t75_to_item(item, allow_open_z=allow_open_z, hist_bars=hist_bars)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t75 failed", exc_info=True)
    try:
        _attach_y_t90_to_item(item, allow_open_z=allow_open_z, hist_bars=hist_bars)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t90 failed", exc_info=True)


def _attach_y_r_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
) -> None:
    """ŷ_τ 已并进 y_τc，不再用 tc 模型另打一列盖掉它。这里只补 remaining。"""
    _ = allow_open_z
    if not isinstance(item, dict):
        return
    try:
        from core.signal.yhat_windows import stamp_remaining_y_τc

        stamp_remaining_y_τc(item)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_r remaining failed", exc_info=True)


def _tau_clock_from_item(item: dict) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for k in ("_score_prefix_hm", "minute_tau_hm", "tau", "tau_hm"):
        raw = str(item.get(k) or "").strip()
        if len(raw) >= 4 and raw[:2].isdigit():
            if ":" in raw:
                return raw[:5]
            digits = raw.replace(":", "")[:4]
            if len(digits) == 4 and digits.isdigit():
                return f"{digits[:2]}:{digits[2:]}"
    return None


def _minute_prefix_from_item(item: dict) -> List[dict]:
    if not isinstance(item, dict):
        return []
    for k in ("_minute_prefix", "minute_prefix", "minute_bars"):
        raw = item.get(k)
        if isinstance(raw, list) and raw:
            return [b for b in raw if isinstance(b, dict)]
    return []


def _open_px_from_item(item: dict) -> Optional[float]:
    if not isinstance(item, dict):
        return None
    day = item.get("day_bar") if isinstance(item.get("day_bar"), dict) else {}
    v = _f(day.get("open"))
    if v is not None:
        return v
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    return _f(feats.get("open")) or _f(item.get("open"))


def _cube_covers_horizon_names(cube: Any, names: Sequence[str]) -> bool:
    """入模列必须都在立方里。缺一列就不把这份立方当截面命中。"""
    if cube is None:
        return False
    cube_names = {str(n) for n in (getattr(cube, "names", None) or []) if str(n or "").strip()}
    need = [str(n).strip() for n in (names or []) if str(n or "").strip()]
    if len(cube_names) < 3 or not need:
        return False
    try:
        from core.signal.factors.z_th import to_legacy_key
    except Exception:  # noqa: BLE001
        to_legacy_key = lambda k: k  # type: ignore[assignment, misc]
    for raw in need:
        bare = str(to_legacy_key(raw) or raw)
        if raw not in cube_names and bare not in cube_names:
            return False
    return True


def _finalize_cs_row(row: dict, names: Sequence[str], covered: Sequence[str]) -> Optional[dict]:
    """没进截面的 raw_ath_* 记 0。稀疏的非 ath 列保持原值，避免各钟被压成同一个 z。"""
    if not isinstance(row, dict):
        return None
    out = dict(row)
    have = {str(n) for n in covered or []}
    for raw in names or []:
        key = str(raw or "").strip()
        if not key or key in have or not key.startswith("raw_ath_"):
            continue
        out[key] = 0.0
    return out


def _strip_peer_open_minute_placeholders(row: dict) -> None:
    """观察池 open 行常带 ret_open_to_tau=0；进截面会把各钟差异压成常数（√2 / 0）。

    池缓存是开盘快照，不是同钟分钟截面；leave-one-in 时一律剥分钟键，只留日线/开盘列。
    """
    if not isinstance(row, dict):
        return
    try:
        from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS, MINUTE_TAU_SHAPE_KEYS
    except Exception:  # noqa: BLE001
        MINUTE_TAU_ALL_KEYS = ()  # type: ignore[assignment]
        MINUTE_TAU_SHAPE_KEYS = ()  # type: ignore[assignment]
    keys = set(MINUTE_TAU_ALL_KEYS) | set(MINUTE_TAU_SHAPE_KEYS)
    keys.update(
        {
            "ret_open_to_tau",
            "ret_prev_to_tau",
            "ret_vs_sector",
            "sector_ret_to_tau",
            "loc_hl",
            "range_pct",
            "yclose_loc",
        }
    )
    for bare in list(keys):
        for k in (bare, f"z_τh_{bare}", f"z_τc_{bare}", f"z_th_{bare}"):
            if k in row:
                row.pop(k, None)


def _finite_feat_count(rows: Sequence[dict], name: str) -> int:
    import math

    key = str(name or "").strip()
    if not key:
        return 0
    n = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        v = row.get(key)
        if v is None or v == "":
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            n += 1
    return n


def _impute_unzscored_raw_ath(
    row: dict,
    names: Sequence[str],
    covered: Sequence[str],
) -> dict:
    """没进截面的 ``raw_ath_*`` 记 0（当天均值）。

    价位比≈1、剩余分钟≈235 若原样进截面模型，logit 会被打到个位数概率。
    稀疏的裸分钟键（如 ``ret_open_to_tau``）仍保留，避免各钟被压成同一个 z。
    """
    if not isinstance(row, dict):
        return row
    have = {str(n) for n in covered or []}
    for raw in names or []:
        key = str(raw or "")
        if not key.startswith("raw_ath_") or key in have:
            continue
        if key in row:
            row[key] = 0.0
    return row


def _tau_clock_peer_rows(date: str, tau_hm: str) -> List[dict]:
    """该钟 ``tc_HHMM`` L1 行。与开盘池缓存不同，这里有同钟 ``raw_ath_*``。"""
    dkey = str(date or "")[:10]
    hm = str(tau_hm or "").strip()[:5]
    if len(dkey) < 10 or not hm or hm in ("09:30", "0930", "open"):
        return []
    ck = f"{dkey}|{hm}"
    cached = _TAU_LAKE_PEER_CACHE.get(ck)
    if cached is not None:
        return cached
    codes = _watching_codes_for_z()
    if len(codes) < 2:
        _TAU_LAKE_PEER_CACHE[ck] = []
        return []
    rows: List[dict] = []
    try:
        from core.research.feature_lake import enabled as lake_on, load_day_rows
        from core.research.lake_bridge import family_for_tc
        from core.research.lake_replay import assemble_item_from_lake_rows
    except Exception:  # noqa: BLE001
        logger.debug("tau lake peer import failed", exc_info=True)
        _TAU_LAKE_PEER_CACHE[ck] = []
        return []
    if not lake_on():
        _TAU_LAKE_PEER_CACHE[ck] = []
        return []
    try:
        tc_m = load_day_rows(family_for_tc(tau_hm=hm), list(codes), dkey) or {}
    except Exception:  # noqa: BLE001
        logger.debug("tau lake peer load failed day=%s hm=%s", dkey, hm, exc_info=True)
        tc_m = {}
    for code in codes:
        c = str(code or "").strip()
        tc = tc_m.get(c) if isinstance(tc_m, dict) else None
        if not isinstance(tc, dict) or not tc:
            continue
        item = assemble_item_from_lake_rows(c, dkey, tc=tc)
        if item is None:
            continue
        rows.append(_capture_feature_row(item, c))
    _TAU_LAKE_PEER_CACHE[ck] = rows
    return rows


def _cs_z_horizon_feats(
    feats: dict,
    *,
    head: str,
    trade_date: str,
    stock_code: str = "",
    tau_hm: str = "",
) -> dict:
    """截面模型：用观察池同伴 μ/σ 把 raw 映成截面 z（对齐拟合）。非截面原样返回。

    ``tau_hm``：当前前缀钟。Cube 只读该钟；miss 不退回 open。
    再试同钟 ``tc_HHMM`` L1 同伴（含 ``raw_ath_*``）；仍无分布的 ``raw_ath_*`` 记 0。
    """
    if not isinstance(feats, dict) or not feats:
        return feats
    dkey = str(trade_date or "")[:10]
    if len(dkey) < 10:
        return feats
    try:
        from core.research.feature_standardize import (
            cross_section_zscore_dicts,
            cs_group_key,
            is_cross_section_zscore,
        )
        from core.research.horizon_ridge import load_ridge_model
        from core.signal.cross_section_rescore import _active_names
        from core.signal.factors.z_th import stamp_z_th_aliases
    except Exception:  # noqa: BLE001
        logger.debug("horizon cs-z import failed", exc_info=True)
        return feats
    try:
        doc = load_ridge_model(head)
    except Exception:  # noqa: BLE001
        doc = None
    if not is_cross_section_zscore(doc):
        return feats
    rm = doc.get("return_model") if isinstance(doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names and isinstance(doc, dict):
        names = _active_names(doc)
    if not names:
        return feats
    peers = list(_POOL_FEAT_CACHE.get(dkey) or [])
    code = str(stock_code or "").strip()
    hm = str(tau_hm or "").strip()[:5]
    tau_only = bool(hm) and hm not in ("09:30", "0930", "open")
    # H4：按钟 Cube 优先；τ 覆盖失败不静默 open，改同钟 L1 / leave-one-in
    try:
        from core.research.day_cube import apply_cube_z_to_dicts, enabled, load_day_cube

        watching_codes = _watching_codes_for_z()
        if enabled() and len(watching_codes) >= 2:
            clocks: List[str] = []
            if tau_only:
                clocks.append(hm)
                safe = "".join(ch for ch in hm if ch.isalnum() or ch in "_-")
                if safe and safe != hm:
                    clocks.append(safe)
            else:
                clocks.append("open")
            seen_c = set()
            for clk in clocks:
                if not clk or clk in seen_c:
                    continue
                seen_c.add(clk)
                cube = load_day_cube(dkey, watching_codes, clock=clk)
                if _cube_covers_horizon_names(cube, names):
                    src = dict(feats)
                    stamp_z_th_aliases(src)
                    z_all = apply_cube_z_to_dicts([src], [code], cube)
                    if z_all:
                        logger.debug(
                            "horizon cs-z cube hit head=%s date=%s clock=%s",
                            head,
                            dkey,
                            clk,
                        )
                        return _finalize_cs_row(z_all[0], names, names)
    except Exception:  # noqa: BLE001
        logger.debug("horizon cs-z day_cube failed", exc_info=True)
    # 开盘池没有同钟 raw_ath_*。Cube miss 时用 tc_HHMM L1 做 μ/σ。
    if tau_only:
        lake_peers = _tau_clock_peer_rows(dkey, hm)
        if len(lake_peers) >= 2:
            peers = lake_peers
    if len(peers) < 2:
        logger.debug(
            "horizon cs-z abstain head=%s date=%s clock=%s reason=peers",
            head,
            dkey,
            hm,
        )
        return None
    # 池内 leave-one-in：剥开盘分钟占位；稀疏裸分钟键保留；raw_ath_* 无分布则记 0
    in_pool = bool(code) and any(
        str(p.get("stock_code") or "").strip() == code for p in peers if isinstance(p, dict)
    )
    if in_pool:
        rows: List[dict] = []
        slot = 0
        for peer in peers:
            if not isinstance(peer, dict):
                continue
            if str(peer.get("stock_code") or "").strip() == code:
                slot = len(rows)
                copied = dict(feats)
            else:
                copied = dict(peer.get("features_tau") or {})
                _strip_peer_open_minute_placeholders(copied)
            stamp_z_th_aliases(copied)
            rows.append(copied)
        if len(rows) >= 2:
            cs_names = [n for n in names if _finite_feat_count(rows, str(n)) >= 2]
            if cs_names:
                # 同钟一组（开盘同伴的分钟列已剥，稀疏裸键保留）
                gkey = cs_group_key(dkey, hm)
                groups = [gkey] * len(rows)
                z_rows = cross_section_zscore_dicts(rows, cs_names, groups)
                row = z_rows[slot] if slot < len(z_rows) else dict(feats)
                return _finalize_cs_row(row, names, cs_names)
            return _finalize_cs_row(
                rows[slot] if slot < len(rows) else dict(feats),
                names,
                (),
            )
    # 池外：μ/σ 仅观察池，不把持仓并进截面
    peer_feats = []
    for p in peers:
        if not isinstance(p, dict):
            continue
        ft = dict(p.get("features_tau") or {})
        _strip_peer_open_minute_placeholders(ft)
        peer_feats.append(ft)
    return _finalize_cs_row(
        _z_row_against_peers(
            feats,
            peer_feats,
            names,
            stamp=stamp_z_th_aliases,
        ),
        names,
        names,
    )


def _predict_horizon_hat(
    head: str,
    feats: dict,
    *,
    load_ridge,
    predict_ridge,
    predict_tree,
    trade_date: str = "",
    stock_code: str = "",
    tau_hm: str = "",
) -> tuple:
    """返回 (p_up, model_doc, source)。

    正式路径：读启用槽包内 ``backend``（AUC 择优落盘）。
    遗留：``horizon_prob_backend=tree`` 强制读启用套 ``backends.tree``（或包顶 ``backend=tree``）。
    截面模型时先按观察池日截面 z 再预测。
    """
    from core.research.horizon_ridge import _doc_backend
    from core.research.horizon_tree import (
        HORIZON_PROB_BACKEND_TREE,
        current_horizon_prob_backend,
    )

    pred_feats = _cs_z_horizon_feats(
        feats,
        head=head,
        trade_date=trade_date,
        stock_code=stock_code,
        tau_hm=tau_hm,
    )
    if pred_feats is None:
        logger.debug(
            "horizon predict abstain head=%s date=%s clock=%s reason=cs_z_missing",
            head,
            str(trade_date or "")[:10],
            str(tau_hm or "")[:5],
        )
        return None, None, "cs_z_missing"

    if current_horizon_prob_backend() == HORIZON_PROB_BACKEND_TREE:
        try:
            pack = load_ridge()
        except Exception:  # noqa: BLE001
            pack = None
        if isinstance(pack, dict):
            if _doc_backend(pack) == "tree":
                try:
                    y_hat = predict_ridge(pred_feats, model_doc=pack)
                except Exception:  # noqa: BLE001
                    y_hat = None
                if y_hat is not None:
                    return float(y_hat), pack, "tree"
            be = pack.get("backends") if isinstance(pack.get("backends"), dict) else {}
            tree_pack = be.get("tree") if isinstance(be, dict) else None
            tree_rm = None
            if isinstance(tree_pack, dict):
                tree_rm = tree_pack.get("return_model") or tree_pack.get(
                    "return_model_research"
                )
            if isinstance(tree_rm, dict):
                shadow = {
                    "backend": "tree",
                    "return_model": tree_rm,
                    "backends": be,
                }
                try:
                    y_hat = predict_tree(pred_feats, model_doc=shadow)
                except Exception:  # noqa: BLE001
                    y_hat = None
                if y_hat is not None:
                    return float(y_hat), shadow, "tree"

    model = load_ridge()
    if model is None:
        return None, None, None
    src = _doc_backend(model)
    y_hat = predict_ridge(pred_feats, model_doc=model)
    if y_hat is None:
        return None, model, None
    return float(y_hat), model, src if src in ("tree", "ridge") else "ridge"


def _stamp_horizon_formula_terms(
    item: dict,
    feats: Optional[dict],
    *,
    src: Optional[str],
    model: Any,
    y_hat: Optional[float],
    keys: Sequence[str],
    explain_ridge: Any = None,
) -> None:
    """Tree 用 pred_contrib 组成；Ridge 用线性拆解。合计对齐表列分数。"""
    expl = None
    if str(src or "").strip().lower() == "tree":
        try:
            from core.research.horizon_tree import tree_tip_formula

            expl = tree_tip_formula(feats, model, y_hat=y_hat)
        except Exception:  # noqa: BLE001
            logger.debug("tree horizon formula stamp failed", exc_info=True)
            expl = None
    elif explain_ridge is not None:
        try:
            expl = explain_ridge(feats, model_doc=model)
        except Exception:  # noqa: BLE001
            logger.debug("ridge horizon formula stamp failed", exc_info=True)
            expl = None
    if isinstance(expl, dict):
        for k in keys:
            item[k] = expl


def _inject_raw_ath_live(
    feats: dict,
    item: dict,
    head: str,
    *,
    tau_hm: Optional[str],
    hist_bars: Optional[Sequence[dict]] = None,
) -> dict:
    """live / 回测：写入 raw_ath_*（GAP/YK + 升格 seq/lag）。"""
    out = feats if isinstance(feats, dict) else {}
    try:
        from core.signal.factors.alpha_th import inject_raw_ath

        day_bar = item.get("day_bar") if isinstance(item.get("day_bar"), dict) else {}
        hist = list(hist_bars) if hist_bars else None
        if not hist:
            hist = item.get("hist_bars") if isinstance(item.get("hist_bars"), list) else None
        if not hist:
            hist = item.get("bars") if isinstance(item.get("bars"), list) else None
        open_t = _open_px_from_item(item)
        prev_close = None
        try:
            if day_bar.get("prev_close") is not None:
                prev_close = float(day_bar.get("prev_close"))
        except (TypeError, ValueError):
            prev_close = None
        px_tau = None
        try:
            if out.get("price_tau") is not None:
                px_tau = float(out.get("price_tau"))
        except (TypeError, ValueError):
            px_tau = None
        if px_tau is None:
            try:
                if item.get("price_tau") is not None:
                    px_tau = float(item.get("price_tau"))
            except (TypeError, ValueError):
                px_tau = None
        if px_tau is None:
            px_tau = open_t
        inject_raw_ath(
            out,
            hist,
            open_t=open_t,
            prev_close=prev_close,
            price_tau=px_tau,
            minute_bars=_minute_prefix_from_item(item) or None,
            tau_hm=tau_hm,
            feats=out,
            head=head,
        )
        from core.signal.factors.z_th import stamp_z_th_aliases

        stamp_z_th_aliases(out)
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k, v in out.items():
                if (
                    str(k).startswith("raw_ath_") or str(k).startswith("z_τh_")
                ) and v is not None:
                    ft[k] = v
    except Exception:  # noqa: BLE001
        logger.debug("inject raw_ath_%s failed", head, exc_info=True)
    return out


def _attach_y_t30_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ30（ŷ_τ X + 序列特征）；决策钟不在 09:30…11:00 / 缺模型则不出分。只进 ŷ_τw 选票。"""
    if not isinstance(item, dict):
        return
    from core.signal.minute_tau_grid import tau_clock_allows_t30

    hm = _tau_clock_from_item(item)
    if hm and not tau_clock_allows_t30(hm):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if not feats:
        feats = item.get("features_path") if isinstance(item.get("features_path"), dict) else {}
    if not feats:
        return
    open_z_only = not _minute_pack_present(feats)
    if open_z_only and not allow_open_z:
        return
    try:
        from core.research.horizon_ridge import (
            explain_ridge_prediction,
            load_ridge_model,
            predict_ridge_from_features,
            write_y_hat,
        )
        from core.research.horizon_tree import predict_tree_from_features
        from core.research.tau_panel import (
            T30_SEQ_FEATURES,
            attach_t30_lag_features,
            attach_tau_lag_features,
        )
        from core.signal.minute_tau_feats import (
            T30_SEQ_TRAIL_KEYS,
            attach_sector_ret_last_30m_cs_if_missing,
            extract_t30_seq_pack,
        )

        feats = dict(feats)
        asof = str(
            item.get("date") or item.get("as_of") or item.get("trade_date") or ""
        )[:10]
        if len(asof) < 10 and isinstance(item.get("day_bar"), dict):
            asof = str(item["day_bar"].get("date") or "")[:10]
        if hm and any(feats.get(k) is None for k in T30_SEQ_TRAIL_KEYS):
            seq = extract_t30_seq_pack(
                _minute_prefix_from_item(item),
                trade_date=asof,
                tau_hm=hm,
                open_px=_open_px_from_item(item),
            )
            for k, v in seq.items():
                if v is not None and feats.get(k) is None:
                    feats[k] = v
        if hm:
            feats = attach_tau_lag_features(
                feats, hist_bars=hist_bars or [], asof_date=asof
            )
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if hm and code:
            feats = attach_t30_lag_features(
                feats,
                hist_bars=hist_bars or [],
                asof_date=asof,
                tau_hm=hm,
                stock_code=code,
            )
        if hm:
            feats = attach_sector_ret_last_30m_cs_if_missing(
                feats, trade_date=asof, tau_hm=hm
            )
        feats = _inject_raw_ath_live(
            feats, item, "t30", tau_hm=hm, hist_bars=hist_bars
        )
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T30_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t30",
            feats,
            load_ridge=lambda: load_ridge_model("t30"),
            predict_ridge=lambda feats, model_doc=None: predict_ridge_from_features(
                "t30", feats, model_doc=model_doc
            ),
            predict_tree=lambda feats, model_doc=None: predict_tree_from_features(
                "t30", feats, model_doc=model_doc
            ),
            trade_date=asof,
            stock_code=code,
            tau_hm=hm or "",
        )
        if y_hat is None:
            from core.research.horizon_ridge import clear_y_hat

            clear_y_hat("t30", item)
            return
        write_y_hat("t30", item, float(y_hat))
        if src:
            item["y_τ30_source"] = src
            item["y_t30_source"] = src
        tip_feats = _cs_z_horizon_feats(
            feats, head="t30", trade_date=asof, stock_code=code, tau_hm=hm or ""
        )
        _stamp_horizon_formula_terms(
            item,
            tip_feats,
            src=src,
            model=model,
            y_hat=float(y_hat),
            keys=("formula_terms_t30", "score_formula_terms_t30"),
            explain_ridge=lambda feats, model_doc=None: explain_ridge_prediction(
                "t30", feats, model_doc=model_doc
            ),
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t30 predict failed", exc_info=True)


def _attach_y_t45_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ45（ŷ_τ X + 序列特征）；决策钟不在 09:30…11:00 / 缺模型则不出分。只进 ŷ_τw 选票。"""
    if not isinstance(item, dict):
        return
    from core.signal.minute_tau_grid import tau_clock_allows_t45

    hm = _tau_clock_from_item(item)
    if hm and not tau_clock_allows_t45(hm):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if not feats:
        feats = item.get("features_path") if isinstance(item.get("features_path"), dict) else {}
    if not feats:
        return
    open_z_only = not _minute_pack_present(feats)
    if open_z_only and not allow_open_z:
        return
    try:
        from core.research.horizon_ridge import (
            explain_ridge_prediction,
            load_ridge_model,
            predict_ridge_from_features,
            write_y_hat,
        )
        from core.research.horizon_tree import predict_tree_from_features
        from core.research.tau_panel import (
            T45_SEQ_FEATURES,
            attach_t45_lag_features,
            attach_tau_lag_features,
        )
        from core.signal.minute_tau_feats import (
            T45_SEQ_TRAIL_KEYS,
            attach_sector_ret_last_45m_cs_if_missing,
            extract_t45_seq_pack,
        )

        feats = dict(feats)
        asof = str(
            item.get("date") or item.get("as_of") or item.get("trade_date") or ""
        )[:10]
        if len(asof) < 10 and isinstance(item.get("day_bar"), dict):
            asof = str(item["day_bar"].get("date") or "")[:10]
        if hm and any(feats.get(k) is None for k in T45_SEQ_TRAIL_KEYS):
            seq = extract_t45_seq_pack(
                _minute_prefix_from_item(item),
                trade_date=asof,
                tau_hm=hm,
                open_px=_open_px_from_item(item),
            )
            for k, v in seq.items():
                if v is not None and feats.get(k) is None:
                    feats[k] = v
        if hm:
            feats = attach_tau_lag_features(
                feats, hist_bars=hist_bars or [], asof_date=asof
            )
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if hm and code:
            feats = attach_t45_lag_features(
                feats,
                hist_bars=hist_bars or [],
                asof_date=asof,
                tau_hm=hm,
                stock_code=code,
            )
        if hm:
            feats = attach_sector_ret_last_45m_cs_if_missing(
                feats, trade_date=asof, tau_hm=hm
            )
        feats = _inject_raw_ath_live(
            feats, item, "t45", tau_hm=hm, hist_bars=hist_bars
        )
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T45_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t45",
            feats,
            load_ridge=lambda: load_ridge_model("t45"),
            predict_ridge=lambda feats, model_doc=None: predict_ridge_from_features(
                "t45", feats, model_doc=model_doc
            ),
            predict_tree=lambda feats, model_doc=None: predict_tree_from_features(
                "t45", feats, model_doc=model_doc
            ),
            trade_date=asof,
            stock_code=code,
            tau_hm=hm or "",
        )
        if y_hat is None:
            from core.research.horizon_ridge import clear_y_hat

            clear_y_hat("t45", item)
            return
        write_y_hat("t45", item, float(y_hat))
        if src:
            item["y_τ45_source"] = src
            item["y_t45_source"] = src
        tip_feats = _cs_z_horizon_feats(
            feats, head="t45", trade_date=asof, stock_code=code, tau_hm=hm or ""
        )
        _stamp_horizon_formula_terms(
            item,
            tip_feats,
            src=src,
            model=model,
            y_hat=float(y_hat),
            keys=("formula_terms_t45", "score_formula_terms_t45"),
            explain_ridge=lambda feats, model_doc=None: explain_ridge_prediction(
                "t45", feats, model_doc=model_doc
            ),
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t45 predict failed", exc_info=True)


def _attach_y_t60_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ60（ŷ_τ X + 序列特征）；决策钟不在 09:30…11:00 / 缺模型则不出分。只进 ŷ_τw 选票。"""
    if not isinstance(item, dict):
        return
    from core.signal.minute_tau_grid import tau_clock_allows_t60

    hm = _tau_clock_from_item(item)
    if hm and not tau_clock_allows_t60(hm):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if not feats:
        feats = item.get("features_path") if isinstance(item.get("features_path"), dict) else {}
    if not feats:
        return
    open_z_only = not _minute_pack_present(feats)
    if open_z_only and not allow_open_z:
        return
    try:
        from core.research.horizon_ridge import (
            explain_ridge_prediction,
            load_ridge_model,
            predict_ridge_from_features,
            write_y_hat,
        )
        from core.research.horizon_tree import predict_tree_from_features
        from core.research.tau_panel import (
            T60_SEQ_FEATURES,
            attach_t60_lag_features,
            attach_tau_lag_features,
        )
        from core.signal.minute_tau_feats import (
            T60_SEQ_TRAIL_KEYS,
            attach_sector_ret_last_60m_cs_if_missing,
            extract_t60_seq_pack,
        )

        feats = dict(feats)
        asof = str(
            item.get("date") or item.get("as_of") or item.get("trade_date") or ""
        )[:10]
        if len(asof) < 10 and isinstance(item.get("day_bar"), dict):
            asof = str(item["day_bar"].get("date") or "")[:10]
        if hm and any(feats.get(k) is None for k in T60_SEQ_TRAIL_KEYS):
            seq = extract_t60_seq_pack(
                _minute_prefix_from_item(item),
                trade_date=asof,
                tau_hm=hm,
                open_px=_open_px_from_item(item),
            )
            for k, v in seq.items():
                if v is not None and feats.get(k) is None:
                    feats[k] = v
        if hm:
            feats = attach_tau_lag_features(
                feats, hist_bars=hist_bars or [], asof_date=asof
            )
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if hm and code:
            feats = attach_t60_lag_features(
                feats,
                hist_bars=hist_bars or [],
                asof_date=asof,
                tau_hm=hm,
                stock_code=code,
            )
        if hm:
            feats = attach_sector_ret_last_60m_cs_if_missing(
                feats, trade_date=asof, tau_hm=hm
            )
        feats = _inject_raw_ath_live(
            feats, item, "t60", tau_hm=hm, hist_bars=hist_bars
        )
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T60_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t60",
            feats,
            load_ridge=lambda: load_ridge_model("t60"),
            predict_ridge=lambda feats, model_doc=None: predict_ridge_from_features(
                "t60", feats, model_doc=model_doc
            ),
            predict_tree=lambda feats, model_doc=None: predict_tree_from_features(
                "t60", feats, model_doc=model_doc
            ),
            trade_date=asof,
            stock_code=code,
            tau_hm=hm or "",
        )
        if y_hat is None:
            from core.research.horizon_ridge import clear_y_hat

            clear_y_hat("t60", item)
            return
        write_y_hat("t60", item, float(y_hat))
        if src:
            item["y_τ60_source"] = src
            item["y_t60_source"] = src
        tip_feats = _cs_z_horizon_feats(
            feats, head="t60", trade_date=asof, stock_code=code, tau_hm=hm or ""
        )
        _stamp_horizon_formula_terms(
            item,
            tip_feats,
            src=src,
            model=model,
            y_hat=float(y_hat),
            keys=("formula_terms_t60", "score_formula_terms_t60"),
            explain_ridge=lambda feats, model_doc=None: explain_ridge_prediction(
                "t60", feats, model_doc=model_doc
            ),
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t60 predict failed", exc_info=True)


def _attach_y_t75_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ75（ŷ_τ X + 序列特征）；决策钟不在 09:30…11:00 / 缺模型则不出分。只进 ŷ_τw 选票。"""
    if not isinstance(item, dict):
        return
    from core.signal.minute_tau_grid import tau_clock_allows_t75

    hm = _tau_clock_from_item(item)
    if hm and not tau_clock_allows_t75(hm):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if not feats:
        feats = item.get("features_path") if isinstance(item.get("features_path"), dict) else {}
    if not feats:
        return
    open_z_only = not _minute_pack_present(feats)
    if open_z_only and not allow_open_z:
        return
    try:
        from core.research.horizon_ridge import (
            explain_ridge_prediction,
            load_ridge_model,
            predict_ridge_from_features,
            write_y_hat,
        )
        from core.research.horizon_tree import predict_tree_from_features
        from core.research.tau_panel import (
            T75_SEQ_FEATURES,
            attach_t75_lag_features,
            attach_tau_lag_features,
        )
        from core.signal.minute_tau_feats import (
            T75_SEQ_TRAIL_KEYS,
            attach_sector_ret_last_75m_cs_if_missing,
            extract_t75_seq_pack,
        )

        feats = dict(feats)
        asof = str(
            item.get("date") or item.get("as_of") or item.get("trade_date") or ""
        )[:10]
        if len(asof) < 10 and isinstance(item.get("day_bar"), dict):
            asof = str(item["day_bar"].get("date") or "")[:10]
        if hm and any(feats.get(k) is None for k in T75_SEQ_TRAIL_KEYS):
            seq = extract_t75_seq_pack(
                _minute_prefix_from_item(item),
                trade_date=asof,
                tau_hm=hm,
                open_px=_open_px_from_item(item),
            )
            for k, v in seq.items():
                if v is not None and feats.get(k) is None:
                    feats[k] = v
        if hm:
            feats = attach_tau_lag_features(
                feats, hist_bars=hist_bars or [], asof_date=asof
            )
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if hm and code:
            feats = attach_t75_lag_features(
                feats,
                hist_bars=hist_bars or [],
                asof_date=asof,
                tau_hm=hm,
                stock_code=code,
            )
        if hm:
            feats = attach_sector_ret_last_75m_cs_if_missing(
                feats, trade_date=asof, tau_hm=hm
            )
        feats = _inject_raw_ath_live(
            feats, item, "t75", tau_hm=hm, hist_bars=hist_bars
        )
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T75_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t75",
            feats,
            load_ridge=lambda: load_ridge_model("t75"),
            predict_ridge=lambda feats, model_doc=None: predict_ridge_from_features(
                "t75", feats, model_doc=model_doc
            ),
            predict_tree=lambda feats, model_doc=None: predict_tree_from_features(
                "t75", feats, model_doc=model_doc
            ),
            trade_date=asof,
            stock_code=code,
            tau_hm=hm or "",
        )
        if y_hat is None:
            from core.research.horizon_ridge import clear_y_hat

            clear_y_hat("t75", item)
            return
        write_y_hat("t75", item, float(y_hat))
        if src:
            item["y_τ75_source"] = src
            item["y_t75_source"] = src
        tip_feats = _cs_z_horizon_feats(
            feats, head="t75", trade_date=asof, stock_code=code, tau_hm=hm or ""
        )
        _stamp_horizon_formula_terms(
            item,
            tip_feats,
            src=src,
            model=model,
            y_hat=float(y_hat),
            keys=("formula_terms_t75", "score_formula_terms_t75"),
            explain_ridge=lambda feats, model_doc=None: explain_ridge_prediction(
                "t75", feats, model_doc=model_doc
            ),
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t75 predict failed", exc_info=True)


def _attach_y_t90_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ90（ŷ_τ X + 序列特征）；决策钟不在 09:30…11:00 / 缺模型则不出分。只进 ŷ_τw 选票。"""
    if not isinstance(item, dict):
        return
    from core.signal.minute_tau_grid import tau_clock_allows_t90

    hm = _tau_clock_from_item(item)
    if hm and not tau_clock_allows_t90(hm):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if not feats:
        feats = item.get("features_path") if isinstance(item.get("features_path"), dict) else {}
    if not feats:
        return
    open_z_only = not _minute_pack_present(feats)
    if open_z_only and not allow_open_z:
        return
    try:
        from core.research.horizon_ridge import (
            explain_ridge_prediction,
            load_ridge_model,
            predict_ridge_from_features,
            write_y_hat,
        )
        from core.research.horizon_tree import predict_tree_from_features
        from core.research.tau_panel import (
            T90_SEQ_FEATURES,
            attach_t90_lag_features,
            attach_tau_lag_features,
        )
        from core.signal.minute_tau_feats import (
            T90_SEQ_TRAIL_KEYS,
            attach_sector_ret_last_90m_cs_if_missing,
            extract_t90_seq_pack,
        )

        feats = dict(feats)
        asof = str(
            item.get("date") or item.get("as_of") or item.get("trade_date") or ""
        )[:10]
        if len(asof) < 10 and isinstance(item.get("day_bar"), dict):
            asof = str(item["day_bar"].get("date") or "")[:10]
        if hm and any(feats.get(k) is None for k in T90_SEQ_TRAIL_KEYS):
            seq = extract_t90_seq_pack(
                _minute_prefix_from_item(item),
                trade_date=asof,
                tau_hm=hm,
                open_px=_open_px_from_item(item),
            )
            for k, v in seq.items():
                if v is not None and feats.get(k) is None:
                    feats[k] = v
        if hm:
            feats = attach_tau_lag_features(
                feats, hist_bars=hist_bars or [], asof_date=asof
            )
        code = str(item.get("stock_code") or item.get("code") or "").strip()
        if hm and code:
            feats = attach_t90_lag_features(
                feats,
                hist_bars=hist_bars or [],
                asof_date=asof,
                tau_hm=hm,
                stock_code=code,
            )
        if hm:
            feats = attach_sector_ret_last_90m_cs_if_missing(
                feats, trade_date=asof, tau_hm=hm
            )
        feats = _inject_raw_ath_live(
            feats, item, "t90", tau_hm=hm, hist_bars=hist_bars
        )
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T90_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t90",
            feats,
            load_ridge=lambda: load_ridge_model("t90"),
            predict_ridge=lambda feats, model_doc=None: predict_ridge_from_features(
                "t90", feats, model_doc=model_doc
            ),
            predict_tree=lambda feats, model_doc=None: predict_tree_from_features(
                "t90", feats, model_doc=model_doc
            ),
            trade_date=asof,
            stock_code=code,
            tau_hm=hm or "",
        )
        if y_hat is None:
            from core.research.horizon_ridge import clear_y_hat

            clear_y_hat("t90", item)
            return
        write_y_hat("t90", item, float(y_hat))
        if src:
            item["y_τ90_source"] = src
            item["y_t90_source"] = src
        tip_feats = _cs_z_horizon_feats(
            feats, head="t90", trade_date=asof, stock_code=code, tau_hm=hm or ""
        )
        _stamp_horizon_formula_terms(
            item,
            tip_feats,
            src=src,
            model=model,
            y_hat=float(y_hat),
            keys=("formula_terms_t90", "score_formula_terms_t90"),
            explain_ridge=lambda feats, model_doc=None: explain_ridge_prediction(
                "t90", feats, model_doc=model_doc
            ),
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t90 predict failed", exc_info=True)


def _inject_minute_pack_from_prefix(
    item: dict,
    *,
    minute_prefix: Sequence[dict],
    day_bar: Optional[dict],
    hm: str,
) -> None:
    """把前缀分钟小包并入 features_tau（按该根钟覆盖；禁收盘 leftover）。"""
    if not isinstance(item, dict):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    day = ""
    if isinstance(day_bar, dict):
        day = str(day_bar.get("date") or day_bar.get("trade_date") or "")[:10]
    if len(day) < 10 and minute_prefix:
        b0 = minute_prefix[0] if isinstance(minute_prefix[0], dict) else {}
        day = str(b0.get("datetime") or b0.get("date") or "")[:10]
    if len(day) < 10:
        return
    try:
        from core.signal.minute_tau_feats import (
            T30_SEQ_CS_KEYS,
            T30_SEQ_PACK_KEYS,
            T45_SEQ_CS_KEYS,
            T45_SEQ_PACK_KEYS,
            T60_SEQ_CS_KEYS,
            T60_SEQ_PACK_KEYS,
            T75_SEQ_CS_KEYS,
            T75_SEQ_PACK_KEYS,
            T90_SEQ_CS_KEYS,
            T90_SEQ_PACK_KEYS,
            attach_ret_vs_sector,
            apply_sector_ret_cs,
            apply_sector_ret_last_30m_cs,
            apply_sector_ret_last_45m_cs,
            apply_sector_ret_last_60m_cs,
            apply_sector_ret_last_75m_cs,
            apply_sector_ret_last_90m_cs,
            clear_minute_tau_pack_keys,
            extract_minute_tau_pack,
            extract_horizon_seq_packs,
            resolve_sector_ret_last_30m,
            resolve_sector_ret_last_45m,
            resolve_sector_ret_last_60m,
            resolve_sector_ret_last_75m,
            resolve_sector_ret_last_90m,
            resolve_sector_ret_to_tau,
        )

        open_px = None
        prev_close = None
        if isinstance(day_bar, dict):
            open_px = _f(day_bar.get("open"))
            prev_close = _f(day_bar.get("prev_close"))
        clock = str(hm or "10:00")[:5]
        pack = extract_minute_tau_pack(
            minute_prefix,
            trade_date=day,
            tau_hm=clock,
            open_px=open_px,
            prev_close=prev_close,
        )
        # 路径小包按该钟覆盖。开→τ 截面也必须按该钟重写：开盘锚常带着
        # 10:00 中位，09:35 再 attach_ret_vs_sector 会把板块 z 打到 +4。
        merged = clear_minute_tau_pack_keys(dict(feats), include_cs=True)
        for k in (
            T30_SEQ_PACK_KEYS + T30_SEQ_CS_KEYS
            + T45_SEQ_PACK_KEYS + T45_SEQ_CS_KEYS
            + T60_SEQ_PACK_KEYS + T60_SEQ_CS_KEYS
            + T75_SEQ_PACK_KEYS + T75_SEQ_CS_KEYS
            + T90_SEQ_PACK_KEYS + T90_SEQ_CS_KEYS
        ):
            merged.pop(k, None)
        if pack:
            for k, v in pack.items():
                if v is not None:
                    merged[k] = v
        seq_all = extract_horizon_seq_packs(
            minute_prefix,
            trade_date=day,
            tau_hm=clock,
            open_px=open_px,
        )
        for k, v in seq_all.items():
            if v is not None:
                merged[k] = v
        peer = current_t0_cs_universe_codes() or None
        try:
            sret = resolve_sector_ret_to_tau(day, clock, codes=peer)
            merged = apply_sector_ret_cs(merged, sret, overwrite=True)
        except Exception:  # noqa: BLE001
            logger.debug("inject prefix sector_ret_to_tau overwrite failed", exc_info=True)
            attach_ret_vs_sector(merged)
        try:
            merged = apply_sector_ret_last_30m_cs(
                merged, resolve_sector_ret_last_30m(day, clock, codes=peer), overwrite=True
            )
            merged = apply_sector_ret_last_45m_cs(
                merged, resolve_sector_ret_last_45m(day, clock, codes=peer), overwrite=True
            )
            merged = apply_sector_ret_last_60m_cs(
                merged, resolve_sector_ret_last_60m(day, clock, codes=peer), overwrite=True
            )
            merged = apply_sector_ret_last_75m_cs(
                merged, resolve_sector_ret_last_75m(day, clock, codes=peer), overwrite=True
            )
            merged = apply_sector_ret_last_90m_cs(
                merged, resolve_sector_ret_last_90m(day, clock, codes=peer), overwrite=True
            )
        except Exception:  # noqa: BLE001
            logger.debug("inject prefix horizon CS overwrite failed", exc_info=True)
        if merged.get("gap_pct") is None:
            try:
                from core.signal.session_pit import recover_gap_pct_from_minute_pack

                recovered = recover_gap_pct_from_minute_pack(merged)
            except Exception:  # noqa: BLE001
                logger.debug("inject recover gap from pack failed", exc_info=True)
                recovered = None
            if recovered is not None:
                merged["gap_pct"] = recovered
        item["features_tau"] = merged
        if item.get("gap_pct") is None and merged.get("gap_pct") is not None:
            item["gap_pct"] = merged.get("gap_pct")
    except Exception:  # noqa: BLE001
        logger.debug("inject minute pack from prefix failed", exc_info=True)


def _refresh_tau_oc_from_feats(
    item: dict,
    *,
    hm: str,
    trade_date: str,
    force: bool = True,
) -> None:
    """前缀小包注入后按 features_tau 重算 ŷ_τc 组成。

    ``resolve_scores`` 常已写上盘中 as_of / 开盘→τ，但注入还会改截面
    （同业缺口广度、HL 位置、近15m、板块中位）。不能因组成表已有
    ``ret_open_to_tau`` 就跳过，否则调仓与做 T 同钟 ŷ_τc 会差一截小因子。
    """
    if not isinstance(item, dict):
        return
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else None
    if not isinstance(feats, dict) or feats.get("ret_open_to_tau") is None:
        return
    day = str(trade_date or item.get("date") or "")[:10]
    clock = str(hm or "").strip()[:5]
    as_of_now = str(item.get("as_of_tau") or item.get("rem_tau") or "").strip()
    stale_open = not as_of_now or as_of_now == "open"
    if len(day) >= 10 and clock and (stale_open or force):
        as_of = f"{day}T{clock}:00+08:00"
        item["as_of_tau"] = as_of
        item["rem_tau"] = as_of
    expl_now = item.get("formula_terms_tau")
    src = "ridge"
    try:
        from core.research.tc_ridge import explain_tau_prediction, predict_tau_from_features

        z_feats = _tau_feature_row_for_refresh(item, day) or feats
        yhat = predict_tau_from_features(z_feats)
        expl = explain_tau_prediction(z_feats)
    except Exception:  # noqa: BLE001
        logger.debug("refresh tau oc from prefix pack failed", exc_info=True)
        return
    if yhat is not None:
        yv = float(yhat)
        from core.signal.yhat_windows import write_y_τc

        from core.signal.yhat_geom import align_rem_yhat_to_clock

        spec = item.get("y_spec_tau") if isinstance(item.get("y_spec_tau"), dict) else {}
        formula = str((spec or {}).get("formula") or "")
        doc = {"y_spec": spec} if formula else {"horizon_mode": "tau_to_close"}
        aligned = align_rem_yhat_to_clock(
            yv,
            rem_model_doc=doc,
            ret_open_to_tau=feats.get("ret_open_to_tau"),
            clock=clock,
        )
        stored = float(aligned) if aligned is not None else yv
        write_y_τc(item, stored)
        item["y_τc_source"] = "ridge"
        item["y_τc_ridge"] = stored
        item.pop("y_τc_tree", None)
        item["y_tau_raw"] = yv
        item.pop("y_tau", None)
        item.pop("predicted_score_tau", None)
        item["predicted_score_rem"] = stored
        item["score_rem"] = stored
    if isinstance(expl, dict):
        prev = expl_now if isinstance(expl_now, dict) else {}
        out = dict(prev)
        out.update(expl)
        if yhat is not None:
            out["y_τc"] = stored
            out["y_tau"] = stored
            out["y_tau_raw"] = float(yhat)
        item["formula_terms_tau"] = out
        item["score_formula_terms_tau"] = out


def prefix_tau_hm_from_bars(
    minute_prefix: Sequence[dict],
    *,
    default: str = "10:00",
) -> str:
    """前缀末根 HH:MM（因果 τ 钟）。"""
    bars = [b for b in (minute_prefix or []) if isinstance(b, dict)]
    if not bars:
        return str(default or "10:00")[:5]
    last_ts = str((bars[-1] or {}).get("datetime") or (bars[-1] or {}).get("date") or "")
    if " " in last_ts:
        return last_ts.split(" ", 1)[1][:5] or str(default or "10:00")[:5]
    if "T" in last_ts:
        return last_ts.split("T", 1)[1][:5] or str(default or "10:00")[:5]
    return str(default or "10:00")[:5]


def _open_z_feats_from_snap(score_snap: Optional[dict]) -> Dict[str, Any]:
    """从开盘快照抽出开盘 Z（去掉分钟键，避免脏值挡住前缀小包）。"""
    from core.signal.minute_tau_feats import MINUTE_TAU_ALL_KEYS, MINUTE_TAU_SHAPE_KEYS

    feats: Dict[str, Any] = {}
    if not isinstance(score_snap, dict):
        return feats
    ft = score_snap.get("features_tau")
    if isinstance(ft, dict):
        skip = set(MINUTE_TAU_ALL_KEYS) | set(MINUTE_TAU_SHAPE_KEYS)
        for k, v in ft.items():
            if k in skip:
                continue
            if v is not None:
                feats[k] = v
    if feats.get("gap_pct") is None and score_snap.get("gap_pct") is not None:
        feats["gap_pct"] = score_snap.get("gap_pct")
    return feats


def _attach_prefix_minute_sector_feats(
    feats: Dict[str, Any],
    *,
    minute_bars: Sequence[dict],
    trade_date: str,
    open_px: Optional[float],
    prev_close: Optional[float],
    tau_hm: str,
) -> Dict[str, Any]:
    """前缀分钟小包（强制覆盖）+ 开→τ 截面（与训练同口径）。"""
    from core.signal.minute_tau_feats import (
        apply_sector_ret_cs,
        apply_sector_ret_last_30m_cs,
        apply_sector_ret_last_45m_cs,
        apply_sector_ret_last_60m_cs,
        apply_sector_ret_last_75m_cs,
        apply_sector_ret_last_90m_cs,
        attach_minute_prefix_feats,
        extract_horizon_seq_packs,
        resolve_sector_ret_last_30m,
        resolve_sector_ret_last_45m,
        resolve_sector_ret_last_60m,
        resolve_sector_ret_last_75m,
        resolve_sector_ret_last_90m,
        resolve_sector_ret_to_tau,
    )

    out = attach_minute_prefix_feats(
        feats,
        minute_bars=minute_bars,
        trade_date=trade_date,
        open_px=open_px,
        prev_close=prev_close,
        tau_hm=tau_hm,
        overwrite=True,
    )
    peer = current_t0_cs_universe_codes() or None
    day = str(trade_date or "")[:10]
    clock = str(tau_hm or "10:00")
    try:
        sret = resolve_sector_ret_to_tau(day, clock, codes=peer)
        out = apply_sector_ret_cs(out, sret, overwrite=True)
    except Exception:  # noqa: BLE001
        logger.debug("prefix sector_ret_to_tau attach failed", exc_info=True)
    try:
        seq_all = extract_horizon_seq_packs(
            minute_bars,
            trade_date=day,
            tau_hm=clock,
            open_px=open_px,
        )
        for k, v in seq_all.items():
            if v is not None:
                out[k] = v
    except Exception:  # noqa: BLE001
        logger.debug("prefix horizon seq attach failed", exc_info=True)
    try:
        out = apply_sector_ret_last_30m_cs(
            out, resolve_sector_ret_last_30m(day, clock, codes=peer), overwrite=True
        )
        out = apply_sector_ret_last_45m_cs(
            out, resolve_sector_ret_last_45m(day, clock, codes=peer), overwrite=True
        )
        out = apply_sector_ret_last_60m_cs(
            out, resolve_sector_ret_last_60m(day, clock, codes=peer), overwrite=True
        )
        out = apply_sector_ret_last_75m_cs(
            out, resolve_sector_ret_last_75m(day, clock, codes=peer), overwrite=True
        )
        out = apply_sector_ret_last_90m_cs(
            out, resolve_sector_ret_last_90m(day, clock, codes=peer), overwrite=True
        )
    except Exception:  # noqa: BLE001
        logger.debug("prefix horizon CS overwrite failed", exc_info=True)
    return out


def predict_tau_oc_from_prefix_minutes(
    score_snap: Optional[dict],
    minute_prefix: Sequence[dict],
    *,
    day_bar: Optional[dict] = None,
    tau_hm: Optional[str] = None,
    hist_bars: Optional[Sequence[dict]] = None,
) -> Optional[float]:
    """用开盘 Z + 前缀分钟小包估 ŷ_τ OC 头（画像/确认根；对齐前 N 根）。"""
    if not isinstance(score_snap, dict):
        return None
    bars = [b for b in (minute_prefix or []) if isinstance(b, dict)]
    if len(bars) < 1:
        return None
    try:
        from core.research.tc_ridge import load_tau_model, predict_tau_from_features
    except Exception:  # noqa: BLE001
        logger.debug("predict_tau_oc_from_prefix imports failed", exc_info=True)
        return None
    model = load_tau_model()
    if model is None:
        return None
    feats = _open_z_feats_from_snap(score_snap)
    day = day_bar if isinstance(day_bar, dict) else {}
    trade_day = str(day.get("date") or (bars[0] or {}).get("date") or "")[:10]
    open_px = _f(day.get("open")) or _f(feats.get("open")) or _f((bars[0] or {}).get("open"))
    prev_c = _f(day.get("prev_close")) or _f(feats.get("prev_close"))
    hm = str(tau_hm or "").strip()[:5] or prefix_tau_hm_from_bars(bars)
    try:
        feats = _attach_prefix_minute_sector_feats(
            feats,
            minute_bars=bars,
            trade_date=trade_day,
            open_px=open_px,
            prev_close=prev_c,
            tau_hm=hm,
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach minute feats for portrait tau failed", exc_info=True)
        return None
    if feats.get("ret_open_to_tau") is None:
        return None
    try:
        from core.research.tau_panel import attach_tau_lag_features

        hist = [b for b in (hist_bars or []) if isinstance(b, dict)]
        feats = attach_tau_lag_features(feats, hist_bars=hist, asof_date=trade_day)
    except Exception:  # noqa: BLE001
        logger.debug("attach tau lag in prefix tau failed", exc_info=True)
    try:
        # 模型标签=open→close；此处取 raw OC 头，不做剩余窗映射
        return predict_tau_from_features(feats, model_doc=model)
    except Exception:  # noqa: BLE001
        logger.debug("predict_tau_oc_from_prefix failed", exc_info=True)
        return None


def _minute_bars_until_hm(
    minute_bars: Sequence[dict],
    tau_hm: str = "10:30",
) -> List[dict]:
    """截到 ≤τ 的分钟（兼容旧画像钟；新口径优先 ``_minute_bars_first_n``）。"""
    hm = str(tau_hm or "10:30").strip()[:5] or "10:30"
    try:
        th, tm = int(hm[:2]), int(hm[3:5])
    except (TypeError, ValueError):
        th, tm = 10, 30
    out: List[dict] = []
    for b in minute_bars or []:
        if not isinstance(b, dict):
            continue
        ts = str(b.get("datetime") or b.get("date") or "")
        part = ts
        if "T" in ts:
            part = ts.split("T", 1)[1]
        elif " " in ts:
            part = ts.split(" ", 1)[1]
        try:
            hh = int(part[0:2])
            mm = int(part[3:5])
        except (TypeError, ValueError):
            continue
        if (hh, mm) <= (th, tm):
            out.append(b)
    return out


def _minute_bars_first_n(
    minute_bars: Sequence[dict],
    n: int,
) -> List[dict]:
    """当日开盘起前 N 根（做 T 前缀契约）。"""
    bars = [b for b in (minute_bars or []) if isinstance(b, dict)]
    bars.sort(key=lambda x: str(x.get("datetime") or x.get("date") or ""))
    nn = max(2, int(n or 2))
    return bars[:nn]


def rescore_scores_at_fixed_prefix(
    *,
    stock_code: str,
    minute_prefix: Sequence[dict],
    day_bar: Optional[dict],
    hist_bars: Optional[Sequence[dict]] = None,
    tau_pool_day: Optional[dict] = None,
    fuse_intraday: bool = True,
    open_snap: Optional[dict] = None,
    include_tau_horizons: bool = True,
) -> Dict[str, Any]:
    """用已发生分钟前缀重算 dual_y（因果；≥1 根即可，含 09:35 首根）。

    非 09:30：**必须有分钟根**；空前缀 → ``minute_data_missing``。
    有分钟根但打分失败时回退 open_snap，并注入分钟小包、禁止 open_z path。
    """
    raw = str(stock_code or "").strip()
    prefix = [b for b in (minute_prefix or []) if isinstance(b, dict)]
    fallback = dict(open_snap or {}) if isinstance(open_snap, dict) else {}

    def _missing(reason: str = "minute_data_missing") -> Dict[str, Any]:
        out = dict(fallback)
        out["_minute_data_missing"] = True
        out["_score_source"] = "prefix_minute_missing"
        for k in (
            "y_hl",
            "predicted_score_hl",
            "y_path",
            "predicted_score_path",
        ):
            out.pop(k, None)
        out.pop("predicted_score_complexity", None)
        out.pop("y_complexity_hat", None)
        out.pop("predicted_score_cx", None)
        out.pop("y_cx_hat", None)
        out.pop("predicted_score_tpd", None)
        out.pop("y_tpd_hat", None)
        out.pop("predicted_score_r", None)
        out.pop("y_r_hat", None)
        out.pop("y_r", None)
        out.pop("predicted_score_t30", None)
        out.pop("y_t30_hat", None)
        out.pop("y_t30", None)
        out.pop("y_τ30", None)
        out.pop("predicted_score_t45", None)
        out.pop("y_t45_hat", None)
        out.pop("y_t45", None)
        out.pop("y_τ45", None)
        out.pop("predicted_score_t60", None)
        out.pop("y_t60_hat", None)
        out.pop("y_t60", None)
        out.pop("y_τ60", None)
        out.pop("predicted_score_t75", None)
        out.pop("y_t75_hat", None)
        out.pop("y_t75", None)
        out.pop("y_τ75", None)
        out.pop("predicted_score_t90", None)
        out.pop("y_t90_hat", None)
        out.pop("y_t90", None)
        out.pop("y_τ90", None)
        return out

    if not raw or not isinstance(day_bar, dict):
        return fallback
    if len(prefix) < 1:
        # 非开盘信息集传空前缀 = 分钟数据缺失
        return _missing()
    hm = prefix_tau_hm_from_bars(prefix)
    dkey = str((day_bar or {}).get("date") or "")[:10]
    # 稳定键：扫槽 / 开轮 / 画像共用同一进程缓存；勿用 id(open_snap)（拷贝即 miss）
    _snap_tok = 0
    if isinstance(open_snap, dict) and scores_have_any(open_snap):
        _parts = []
        for _k in ("y_tau", "y_tau_oc", "y_oo", "y_r", "y_tc", "predicted_score"):
            _v = open_snap.get(_k)
            if _v is not None:
                _parts.append(f"{_k}:{_v}")
        _snap_tok = hash(tuple(_parts)) if _parts else 1
    _pool_tok = 0
    if isinstance(tau_pool_day, dict):
        try:
            _pool_tok = len(tau_pool_day)
        except Exception:  # noqa: BLE001
            _pool_tok = 1
    cache_key = (
        raw,
        dkey,
        str(hm or ""),
        len(prefix),
        1 if fuse_intraday else 0,
        1 if include_tau_horizons else 0,
        _snap_tok,
        _pool_tok,
    )
    cached = _PREFIX_RESCORE_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    # 轻量路径：开盘 snap 已有分 → 跳过整票 resolve，只注入分钟包 + 重算辅头
    light = bool(fallback) and scores_have_any(fallback)
    sc: Dict[str, Any] = {}
    if not light:
        sret = None
        try:
            sc = resolve_scores_for_code(
                raw,
                hist_bars=hist_bars,
                day_bar=day_bar,
                source="compute",
                fuse_intraday=bool(fuse_intraday),
                allow_fallback=False,
                minute_bars=prefix,
                sector_ret_to_tau=sret,
                use_minute_tau=True,
                minute_tau_hm=hm,
                **tau_pool_day_score_kwargs(tau_pool_day, raw),
            )
        except Exception:  # noqa: BLE001
            logger.debug("rescore_scores_at_fixed_prefix failed", exc_info=True)
            sc = {}

    if light:
        out = copy.deepcopy(fallback)
        src = "prefix_light"
    elif scores_have_any(sc):
        out = dict(sc)
        src = "prefix_causal"
    else:
        # 有分钟根但未能出分：回退开盘锚，仍属「有分钟数据」
        out = dict(fallback)
        src = "prefix_open_fallback"

    _inject_minute_pack_from_prefix(
        out, minute_prefix=prefix, day_bar=day_bar, hm=hm or "10:30"
    )
    if not out.get("stock_code"):
        out["stock_code"] = raw
    dkey = str((day_bar or {}).get("date") or "")[:10]
    if dkey and not out.get("date"):
        out["date"] = dkey
    try:
        from core.research.tau_panel import attach_tau_lag_features

        ft = out.get("features_tau") if isinstance(out.get("features_tau"), dict) else {}
        if isinstance(ft, dict) and ft.get("tau_lag1") is None and hist_bars and dkey:
            out["features_tau"] = attach_tau_lag_features(
                ft, hist_bars=hist_bars, asof_date=dkey
            )
    except Exception:  # noqa: BLE001
        logger.debug("prefix tau lag attach failed", exc_info=True)
    _refresh_tau_oc_from_feats(
        out, hm=str(hm or "")[:5], trade_date=dkey, force=True
    )
    # 禁止 silently 用开盘 Z 冒充盘中 path；时钟须在 attach 前写入（辅头 CS-z / 分钟小包读此前缀）
    out["_minute_prefix"] = prefix
    out["_score_prefix_hm"] = hm
    out["_score_prefix_bars"] = len(prefix)
    _attach_y_path_to_item(
        out,
        hist_bars=hist_bars,
        allow_open_z=False,
        include_tau_horizons=include_tau_horizons,
    )
    out.pop("_minute_prefix", None)
    out["_score_source"] = src
    out.pop("_minute_data_missing", None)
    if len(_PREFIX_RESCORE_CACHE) >= _PREFIX_RESCORE_CACHE_MAX:
        _PREFIX_RESCORE_CACHE.clear()
    _PREFIX_RESCORE_CACHE[cache_key] = out
    return copy.deepcopy(out)


def attach_portrait_dual_scores(
    day: Optional[dict],
    score_snap: Optional[dict],
    *,
    minute_bars: Optional[Sequence[dict]] = None,
    day_bar: Optional[dict] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    tau_hm: str = "10:30",
    prefix_bars: Optional[int] = None,
) -> Dict[str, Any]:
    """日结果补画像用 ŷ_τ_oc（**前 N 根**因果分钟；ŷ_hl 已下线）。

    不再默认截到 10:30；N 取 ``prefix_bars`` / 日结果 / ``T0_LAST_LEG1_PREFIX_BARS``。
    """
    out: Dict[str, Any] = dict(day or {})
    snap = dict(score_snap or {})
    if isinstance(out.get("scores"), dict):
        for k, v in out["scores"].items():
            if k not in snap or snap.get(k) is None:
                snap[k] = v
    day_ref = day_bar if isinstance(day_bar, dict) else out

    n_pref: Optional[int] = None
    if prefix_bars is not None:
        try:
            n_pref = int(prefix_bars)
        except (TypeError, ValueError):
            n_pref = None
    if n_pref is None:
        for src in (out, snap):
            if not isinstance(src, dict):
                continue
            for key in ("_score_prefix_bars", "prefix_bars"):
                if src.get(key) is not None:
                    try:
                        n_pref = int(src.get(key))
                        break
                    except (TypeError, ValueError):
                        pass
            if n_pref is not None:
                break
    if n_pref is None:
        try:
            from core.t0.config import T0_LAST_LEG1_PREFIX_BARS

            n_pref = int(T0_LAST_LEG1_PREFIX_BARS)
        except Exception:  # noqa: BLE001
            n_pref = 18
    n_pref = max(1, min(int(n_pref or 6), 48))
    prefix = _minute_bars_first_n(minute_bars or [], n_pref)
    # 兼容：显式 tau_hm 且未给 N 时仍可按钟截（旧调用）
    if not prefix and tau_hm:
        prefix = _minute_bars_until_hm(minute_bars or [], tau_hm=tau_hm)
    hm = prefix_tau_hm_from_bars(prefix, default=str(tau_hm or "10:00")[:5])

    y_tau_p = _f(out.get("y_tau_portrait_oc"))
    if y_tau_p is None and isinstance(out.get("scores"), dict):
        y_tau_p = _f(out["scores"].get("y_tau_portrait_oc"))
    # 确认根已因果重算：直接用作画像（与选向同信息集）
    if y_tau_p is None and str(snap.get("_score_source") or "") == "prefix_causal":
        y_tau_p = _f(snap.get("y_τc"))
        if y_tau_p is None:
            y_tau_p = _f(snap.get("predicted_score_τc"))
    if y_tau_p is None:
        y_tau_p = predict_tau_oc_from_prefix_minutes(
            snap, prefix, day_bar=day_ref, tau_hm=hm, hist_bars=hist_bars
        )

    sc = dict(out.get("scores") or {}) if isinstance(out.get("scores"), dict) else {}
    feats = (
        dict(out.get("direction_features") or {})
        if isinstance(out.get("direction_features"), dict)
        else {}
    )
    if y_tau_p is not None:
        y_tau_f = round(float(y_tau_p), 4)
        out["y_tau_portrait_oc"] = y_tau_f
        sc["y_tau_portrait_oc"] = y_tau_f
        feats["y_tau_portrait_oc"] = y_tau_f
    # ŷ_hl 已下线：清画像残留，不再预测
    out.pop("y_hl_portrait", None)
    out.pop("y_path_portrait", None)
    sc.pop("y_hl_portrait", None)
    sc.pop("y_path_portrait", None)
    feats.pop("y_hl_portrait", None)
    feats.pop("y_path_portrait", None)
    feats.pop("y_hl", None)
    out["portrait_prefix_bars"] = n_pref
    out["portrait_prefix_hm"] = hm
    sc["portrait_prefix_bars"] = n_pref
    sc["portrait_prefix_hm"] = hm
    if sc:
        out["scores"] = sc
    if feats:
        out["direction_features"] = feats
    return out


def scores_have_any(scores: Optional[dict]) -> bool:
    if not isinstance(scores, dict):
        return False
    return any(
        _f(scores.get(k)) is not None
        for k in ("y_eod", "y_tau", "y_trade", "y_co", "y_on")
    )


def open_decision_quote(
    day_bar: dict,
    prev_bar: Optional[dict] = None,
    *,
    code: str = "",
) -> Dict[str, Any]:
    """开盘决策报价：只用 open[T] / close[T−1]，不把收盘价当现价。"""
    o = _f((day_bar or {}).get("open"))
    pc = _f((day_bar or {}).get("prev_close"))
    if pc is None and isinstance(prev_bar, dict):
        pc = _f(prev_bar.get("close"))
    change = None
    if o is not None and pc is not None and pc > 0:
        change = round((float(o) / float(pc) - 1.0) * 100.0, 4)
    return {
        "success": True,
        "stock_code": str(code or "").strip() or None,
        "date": str((day_bar or {}).get("date") or "")[:10] or None,
        "trade_date": str((day_bar or {}).get("date") or "")[:10] or None,
        "open": o,
        "open_raw": o,
        "prev_close": pc,
        "price_raw": o,
        "change_raw": change,
    }


def _enrich_quote_path_price(
    quote: dict,
    day_bar: Optional[dict],
    *,
    code: str = "",
) -> Dict[str, Any]:
    """为 ŷ_co 补路径现价（开→收 / 收→收）；不改 open / prev_close / change_raw。

    开盘决策报价常把 ``price_raw=open``，会导致 ret_oc≈0、ŷ_co 与簿相反号。
    优先用当日已走出路径的 close；当日且日线仍平开时再拉现价对齐刷簿。
    """
    if not isinstance(quote, dict):
        return quote
    out = dict(quote)
    open_px = _f(out.get("open") if out.get("open") is not None else out.get("open_raw"))
    cur_px = _f(
        out.get("price_raw")
        if out.get("price_raw") is not None
        else (out.get("close") if out.get("close") is not None else out.get("price"))
    )
    # 已有异于开盘的路径价（盘中 live quote）→ 保留
    if (
        cur_px is not None
        and cur_px > 0
        and open_px is not None
        and abs(float(cur_px) - float(open_px)) > 1e-9
    ):
        return out

    px: Optional[float] = None
    day = day_bar if isinstance(day_bar, dict) else None
    if day is not None:
        c = _f(day.get("close"))
        o = _f(day.get("open"))
        hi = _f(day.get("high"))
        lo = _f(day.get("low"))
        if c is not None and c > 0:
            moved = False
            if hi is not None and lo is not None and abs(float(hi) - float(lo)) > 1e-9:
                moved = True
            if o is not None and abs(float(c) - float(o)) > 1e-9:
                moved = True
            if moved:
                px = float(c)

    if px is None:
        day_key = str(out.get("date") or out.get("trade_date") or "")[:10]
        if len(day_key) < 10 and day is not None:
            day_key = str(day.get("date") or "")[:10]
        today = ""
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            today = ""
        if today and (not day_key or day_key == today):
            raw = str(code or out.get("stock_code") or "").strip()
            if raw:
                try:
                    from core.data.facade import get_quote

                    live = get_quote(raw) or {}
                    if isinstance(live, dict):
                        lp = _f(
                            live.get("price_raw")
                            if live.get("price_raw") is not None
                            else (
                                live.get("price")
                                if live.get("price") is not None
                                else live.get("close")
                            )
                        )
                        if lp is not None and lp > 0:
                            px = float(lp)
                except Exception:  # noqa: BLE001
                    logger.debug("path price live quote failed for %s", raw, exc_info=True)

    if px is not None and px > 0:
        out["price_raw"] = px
        out["close"] = px
        out["price"] = px
    return out


def _scoring_models() -> Tuple[Any, Dict[str, Any], Any]:
    """(tau_doc, cluster_models_by_code, global_return_model)。"""
    from core.research.holdout import current_scoring_model_role

    role = current_scoring_model_role()
    if _MODEL_CACHE.get("ok") and _MODEL_CACHE.get("role") == role:
        return (
            _MODEL_CACHE.get("tau") or _MODEL_CACHE.get("rem"),
            _MODEL_CACHE.get("cluster") or {},
            _MODEL_CACHE.get("global_rm"),
        )
    tau = None
    try:
        from core.research.tc_ridge import load_tau_model

        tau = load_tau_model()
    except Exception:  # noqa: BLE001
        logger.debug("load tau model failed", exc_info=True)
    # 分组 return_model 已退役
    cluster: Dict[str, Any] = {}
    global_rm = None
    try:
        from core.research.oo_ridge import load_scoring_return_model

        # 与调仓回测同口径：研究套上下文读 research，否则 active
        global_rm, _meta = load_scoring_return_model()
    except Exception:  # noqa: BLE001
        logger.debug("load global return model failed", exc_info=True)
    _MODEL_CACHE.clear()
    _MODEL_CACHE.update(
        {
            "ok": True,
            "tau": tau,
            "cluster": cluster,
            "global_rm": global_rm,
            "role": role,
        }
    )
    return tau, cluster, global_rm


def _pool_z_disabled() -> bool:
    raw = str(os.environ.get("QUANTLAB_POOL_Z", "1") or "1").strip().lower()
    return raw in {"0", "false", "no", "off"}


def _pool_z_active() -> bool:
    return (not _pool_z_disabled()) and bool(_POOL_Z_REQUEST.get()) and int(_POOL_Z_DEPTH.get()) == 0


@contextlib.contextmanager
def watching_pool_z_scope() -> Iterator[None]:
    """做 T / 纸面打分时，把截面 z 对齐当天观察池（全样本，与拟合同口径）。"""
    if _pool_z_disabled():
        yield
        return
    token = _POOL_Z_REQUEST.set(True)
    try:
        yield
    finally:
        _POOL_Z_REQUEST.reset(token)


def _watching_codes_for_z() -> List[str]:
    """观察池全名单。持仓若不在池内，不进 μ/σ。"""
    try:
        from core.watching.store import WATCHING_MAX_SIZE, read_watching

        doc = read_watching() or {}
        raw_list = doc.get("watchlist") if isinstance(doc, dict) else None
        out: List[str] = []
        seen = set()
        for raw in raw_list or []:
            if isinstance(raw, dict):
                code = str(raw.get("stock_code") or raw.get("code") or "").strip()
            else:
                code = str(raw or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out[: max(1, int(WATCHING_MAX_SIZE))]
    except Exception:  # noqa: BLE001
        logger.debug("watching codes for pool z failed", exc_info=True)
        return []


def _split_bars_on_date(
    bars: Sequence[dict], date: str
) -> Tuple[List[dict], Optional[dict]]:
    dkey = str(date or "")[:10]
    hist: List[dict] = []
    day = None
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        cur = str(bar.get("date") or "")[:10]
        if len(cur) < 10:
            continue
        if cur < dkey:
            hist.append(bar)
        elif cur == dkey:
            day = bar
            break
    return hist, day


def _capture_feature_row(item: dict, code: str) -> dict:
    tau = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    co = item.get("features_co") if isinstance(item.get("features_co"), dict) else {}
    return {
        "stock_code": code,
        "sub_scores": dict(item.get("sub_scores") or {}),
        "features_tau": dict(tau),
        "features_co": dict(co),
        "gap_pct": item.get("gap_pct"),
        "as_of_tau": item.get("as_of_tau"),
        "dual_score_window": item.get("dual_score_window"),
    }


def seed_watching_pool_feature_rows(date: str, items: Sequence[dict]) -> bool:
    """把已经打好的观察池特征放进当日缓存，供前缀 ŷ_τc 用同一套 μ/σ。"""
    dkey = str(date or "")[:10]
    if len(dkey) < 10:
        return False
    rows: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code:
            continue
        has_oo = isinstance(it.get("sub_scores"), dict) and bool(it.get("sub_scores"))
        has_tau = isinstance(it.get("features_tau"), dict) and bool(it.get("features_tau"))
        if not has_oo and not has_tau:
            continue
        rows.append(_capture_feature_row(it, code))
    if len(rows) < 2:
        return False
    _POOL_FEAT_CACHE[dkey] = rows
    return True


@contextlib.contextmanager
def temporary_pool_feature_rows(date: str, items: Sequence[dict]) -> Iterator[None]:
    """前缀重算期间借用这批特征做 μ/σ，结束时还原缓存。"""
    dkey = str(date or "")[:10]
    prev = _POOL_FEAT_CACHE.get(dkey)
    seeded = seed_watching_pool_feature_rows(dkey, items)
    try:
        yield
    finally:
        if not seeded:
            return
        if prev is None:
            _POOL_FEAT_CACHE.pop(dkey, None)
        else:
            _POOL_FEAT_CACHE[dkey] = prev


def _peer_rows_from_feature_lake(date: str, codes: Sequence[str]) -> List[dict]:
    """优先读 L1 Feature Lake（与拟合写穿同一 raw），缺 oo 行的票跳过。"""
    dkey = str(date or "")[:10]
    if len(dkey) < 10 or not codes:
        return []
    try:
        from core.research.feature_lake import enabled as lake_on, load_day_rows
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC
        from core.research.lake_replay import assemble_item_from_lake_rows
    except Exception:  # noqa: BLE001
        return []
    if not lake_on():
        return []
    try:
        oo_m = load_day_rows(FAMILY_OO, list(codes), dkey) or {}
        tc_m = load_day_rows(FAMILY_TC, list(codes), dkey) or {}
        co_m = load_day_rows(FAMILY_CO, list(codes), dkey) or {}
    except Exception:  # noqa: BLE001
        logger.debug("peer lake load_day_rows failed day=%s", dkey, exc_info=True)
        return []
    rows: List[dict] = []
    for code in codes:
        c = str(code or "").strip()
        if not c:
            continue
        oo = oo_m.get(c)
        if not oo:
            continue
        item = assemble_item_from_lake_rows(
            c, dkey, oo=oo, tc=tc_m.get(c), co=co_m.get(c)
        )
        if item is None:
            continue
        rows.append(_capture_feature_row(item, c))
    return rows


def _peer_feature_rows(date: str) -> List[dict]:
    """观察池当日特征行（供截面 μ/σ）。结果按日缓存。

    优先 Feature Lake L1（与拟合同 raw）；miss 再并行 ``compute_scores_from_bars``
    （``include_tau_horizons=False``，只抽主头特征进 μ/σ）。
    """
    dkey = str(date or "")[:10]
    if len(dkey) < 10:
        return []
    cached = _POOL_FEAT_CACHE.get(dkey)
    if cached is not None:
        return cached
    codes = _watching_codes_for_z()
    if len(codes) < 2:
        _POOL_FEAT_CACHE[dkey] = []
        return []
    rows: List[dict] = []
    depth = _POOL_Z_DEPTH.set(int(_POOL_Z_DEPTH.get()) + 1)
    req = _POOL_Z_REQUEST.set(False)
    t0 = time.perf_counter()
    bar_limit = max(80, int(_EOD_FACTOR_BAR_LIMIT) + 8)
    try:
        lake_rows = _peer_rows_from_feature_lake(dkey, codes)
        by_code = {
            str(r.get("stock_code") or "").strip(): r
            for r in lake_rows
            if isinstance(r, dict) and str(r.get("stock_code") or "").strip()
        }
        miss = [c for c in codes if c not in by_code]
        filled_n = 0
        if miss:
            try:
                from core.research.materialize.consumer import consumer_read_only
                from core.research.materialize.flags import require_day_cube

                if require_day_cube() or consumer_read_only():
                    logger.warning(
                        "peer feature bars fill blocked by REQUIRE_CUBE date=%s miss=%s",
                        dkey,
                        len(miss),
                    )
                    miss = []
            except Exception:  # noqa: BLE001
                pass
        if miss:
            need = list(miss)
            memo_key = f"{bar_limit}|{'|'.join(codes)}"
            bars_map = _PEER_BARS_MEMO.get(memo_key)
            if bars_map is None:
                bars_map = load_bars_by_code_for_tau_pool(need, limit=bar_limit)
                # 仅全量 miss（无 lake）时写入全码表 memo
                if not by_code:
                    if len(_PEER_BARS_MEMO) >= int(_PEER_BARS_MEMO_MAX):
                        _PEER_BARS_MEMO.clear()
                    _PEER_BARS_MEMO[memo_key] = bars_map

            def _one(code: str) -> Optional[dict]:
                bars = bars_map.get(code) or []
                hist, day = _split_bars_on_date(bars, dkey)
                if day is None or len(hist) < _MIN_HIST_BARS:
                    return None
                private: List[dict] = []
                sink_tok = _ITEM_SINK.set(private)
                try:
                    compute_scores_from_bars(
                        code,
                        hist,
                        day_bar=day,
                        fuse_intraday=False,
                        use_minute_tau=False,
                        include_tau_horizons=False,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("peer feature row failed for %s", code, exc_info=True)
                    return None
                finally:
                    _ITEM_SINK.reset(sink_tok)
                return private[0] if private else None

            workers = max(1, min(8, len(need)))
            filled: List[dict] = []
            if workers == 1:
                for code in need:
                    row = _one(code)
                    if row:
                        filled.append(row)
            else:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                from core.research.materialize.consumer import (
                    bind_materialize_writer,
                    context_initargs,
                )

                with ThreadPoolExecutor(
                    max_workers=workers,
                    initializer=bind_materialize_writer,
                    initargs=context_initargs(),
                ) as pool:
                    futs = [pool.submit(_one, c) for c in need]
                    for fut in as_completed(futs):
                        try:
                            row = fut.result()
                        except Exception:  # noqa: BLE001
                            row = None
                        if row:
                            filled.append(row)
            for row in filled:
                c = str(row.get("stock_code") or "").strip()
                if c:
                    by_code[c] = _capture_feature_row(row, c)
            filled_n = len(filled)
            src = "lake+bars" if lake_rows else "bars"
        else:
            workers = 0
            src = "lake"
        rows = [by_code[c] for c in codes if c in by_code]
        logger.info(
            "watching pool feature rows date=%s codes=%s rows=%s source=%s lake=%s bars=%s workers=%s elapsed=%.1fs",
            dkey,
            len(codes),
            len(rows),
            src,
            len(lake_rows),
            filled_n,
            workers,
            time.perf_counter() - t0,
        )
    except Exception:  # noqa: BLE001
        logger.debug("watching pool feature rows failed", exc_info=True)
        rows.clear()
    finally:
        _POOL_Z_REQUEST.reset(req)
        _POOL_Z_DEPTH.reset(depth)
    _POOL_FEAT_CACHE[dkey] = rows
    return rows


def _peer_rows_for_cs_z(
    peers: Sequence[dict],
    *,
    field: str,
    stock_code: str = "",
    self_feats: Optional[dict] = None,
) -> List[dict]:
    """全样本同伴特征行；若 ``stock_code`` 在池内则用 ``self_feats`` 置换该行（leave-one-in）。"""
    code = str(stock_code or "").strip()
    self_row = dict(self_feats or {}) if isinstance(self_feats, dict) else {}
    out: List[dict] = []
    for peer in peers or []:
        if not isinstance(peer, dict):
            continue
        pc = str(peer.get("stock_code") or "").strip()
        if code and pc == code and self_row:
            out.append(dict(self_row))
        else:
            feat = peer.get(field) if field else None
            out.append(dict(feat) if isinstance(feat, dict) else {})
    return out


def _z_row_against_peers(
    raw: dict,
    peers: Sequence[dict],
    names: Sequence[str],
    *,
    stamp,
) -> dict:
    """用同伴行的总体 μ/σ 标准化 ``raw``。同伴不足 2 个有限值时，该列记 0。"""
    import math

    import numpy as np

    keys = [str(n) for n in names if str(n or "").strip()]
    peer_rows = []
    for row in peers:
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        try:
            stamp(copied)
        except Exception:  # noqa: BLE001
            logger.debug("stamp peer feature row failed", exc_info=True)
        peer_rows.append(copied)
    src = dict(raw or {})
    try:
        stamp(src)
    except Exception:  # noqa: BLE001
        logger.debug("stamp target feature row failed", exc_info=True)
    out = dict(src)
    if len(peer_rows) < 2 or not keys:
        return out
    for name in keys:
        vals = []
        for row in peer_rows:
            v = row.get(name)
            if v is None or v == "":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                vals.append(fv)
        cur = src.get(name)
        try:
            x = float(cur) if cur not in (None, "") else float("nan")
        except (TypeError, ValueError):
            x = float("nan")
        def _write_z(zval: float) -> None:
            out[name] = zval
            # 与 cross_section_zscore_dicts 一致：z 同步到裸名，防 stamp 冲掉
            for p in ("z_τh_", "z_τc_", "z_co_", "z_oo_"):
                if name.startswith(p):
                    bare = name[len(p) :]
                    if bare:
                        out[bare] = zval
                    return
            for p in ("z_τh_", "z_τc_", "z_co_", "z_oo_"):
                canon = f"{p}{name}"
                if canon in out:
                    out[canon] = zval

        if len(vals) < 2:
            if math.isfinite(x):
                _write_z(0.0)
            continue
        arr = np.asarray(vals, dtype=np.float64)
        mu = float(arr.mean())
        var = float(np.mean((arr - mu) ** 2))
        if (not math.isfinite(var)) or var <= 1e-12:
            if math.isfinite(x):
                _write_z(0.0)
            continue
        sd = math.sqrt(var)
        if math.isfinite(x):
            _write_z(0.0 if sd < 1e-12 else (x - mu) / sd)
    return out


def _predict_oo_from_row(item: dict, z_row: dict) -> None:
    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.oo_ridge import load_scoring_return_model
    except Exception:  # noqa: BLE001
        logger.debug("pool z oo model import failed", exc_info=True)
        return
    model, _meta = load_scoring_return_model()
    if model is None or not is_cross_section_zscore(model):
        return
    try:
        yhat = model.predict(z_row)
    except Exception:  # noqa: BLE001
        logger.debug("pool z oo predict failed", exc_info=True)
        return
    if yhat is None:
        return
    yv = float(yhat)
    item["predicted_score"] = yv
    item["predicted_score_oo"] = yv
    item["y_oo"] = yv
    try:
        expl = model.explain_prediction(z_row)
    except Exception:  # noqa: BLE001
        expl = None
    if expl:
        item["score_formula_terms"] = expl
        item["formula_terms"] = expl


def _predict_tau_from_row(item: dict, z_row: dict, raw_feats: dict) -> None:
    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.tc_ridge import load_tau_model, predict_tau_from_features
        from core.signal.dual_score.tau import apply_tau_score_fields
    except Exception:  # noqa: BLE001
        logger.debug("pool z tau import failed", exc_info=True)
        return
    ridge_doc = load_tau_model()
    if not is_cross_section_zscore(ridge_doc):
        return
    try:
        yhat = predict_tau_from_features(z_row, model_doc=ridge_doc)
    except Exception:  # noqa: BLE001
        logger.debug("pool z tau predict failed", exc_info=True)
        return
    fuse = str(item.get("dual_score_window") or "") != "eod_next"
    apply_tau_score_fields(
        item,
        rem_yhat=yhat,
        gap_pct=item.get("gap_pct"),
        feats=raw_feats,
        explain_feats=z_row,
        as_of_tau=item.get("as_of_tau"),
        rem_model_doc=ridge_doc,
        fuse_intraday=fuse,
        y_source="ridge",
    )


def _light_item_from_peer_row(row: dict, date: str) -> dict:
    """同伴特征行 → 轻量 signal_item（供 DayCube / rescore）。"""
    code = str(row.get("stock_code") or "").strip()
    dkey = str(date or "")[:10]
    co = row.get("features_co") if isinstance(row.get("features_co"), dict) else {}
    out: Dict[str, Any] = {
        "stock_code": code,
        "as_of": dkey,
        "date": dkey,
        "trade_date": dkey,
        "sub_scores": dict(row.get("sub_scores") or {}),
        "features_tau": dict(row.get("features_tau") or {}),
        "features_co": dict(co),
        "gap_pct": row.get("gap_pct"),
        "as_of_tau": row.get("as_of_tau"),
        "dual_score_window": row.get("dual_score_window"),
        "success": True,
    }
    if co:
        out["features_on"] = dict(co)
    return out


def _align_item_to_watching_pool(item: dict, date: str) -> None:
    """用观察池当天全样本 μ/σ 重算本票 ŷ（与拟合 / 调仓 batch 同路径）。

    DayCube 命中：只对本票 ``rescore_fitted_cross_section``（μ/σ 信 cube，不重建同伴）。
    miss：抽全池同伴 raw → ensure cube → rescore；池内 leave-one-in，池外持仓不进 μ/σ。
    """
    if not isinstance(item, dict):
        return
    dkey = str(date or "")[:10]
    if len(dkey) < 10:
        return
    code = str(item.get("stock_code") or "").strip()
    allow = {code} if code else None
    watching_codes = _watching_codes_for_z()
    # 快路径：已有 DayCube → 单票 z+ŷ，跳过整池同伴重建
    try:
        from core.research.day_cube import enabled as cube_on, load_day_cube
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        if cube_on() and len(watching_codes) >= 2:
            cube = load_day_cube(dkey, watching_codes, clock="open")
            if cube is not None:
                rescore_fitted_cross_section(
                    [item], score_codes=allow, day_cube=cube
                )
                return
    except Exception:  # noqa: BLE001
        logger.debug("pool z cube-hit rescore failed", exc_info=True)

    peers = _peer_feature_rows(dkey)
    if len(peers) < 2:
        return
    peer_items: List[dict] = []
    in_pool = False
    for p in peers:
        if not isinstance(p, dict):
            continue
        pc = str(p.get("stock_code") or "").strip()
        if code and pc == code:
            in_pool = True
            peer_items.append(item)
        else:
            peer_items.append(_light_item_from_peer_row(p, dkey))
    if len(peer_items) < 2:
        return
    cube_src = (
        peer_items
        if in_pool
        else [_light_item_from_peer_row(p, dkey) for p in peers if isinstance(p, dict)]
    )
    score_items = peer_items if in_pool else list(peer_items) + [item]
    cube = None
    try:
        from core.research.batch_score import ensure_day_cube_for_items
        from core.research.day_cube import enabled as cube_on

        cube = ensure_day_cube_for_items(
            cube_src,
            date=dkey,
            clock="open",
            persist=bool(cube_on()),
            universe_codes=watching_codes or None,
        )
    except Exception:  # noqa: BLE001
        logger.debug("pool z day_cube failed", exc_info=True)
        cube = None
    try:
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        if cube is not None:
            rescore_fitted_cross_section(
                score_items, score_codes=allow, day_cube=cube
            )
            return
        if in_pool:
            # 无 cube：整批 leave-one-in 现算 μ/σ（与 rescore 同函数）
            rescore_fitted_cross_section(score_items, score_codes=allow)
            return
    except Exception:  # noqa: BLE001
        logger.debug("pool z rescore failed", exc_info=True)
    # 池外无 cube / rescore 失败：μ/σ 仅观察池，不把持仓并进截面
    _align_item_to_watching_pool_legacy(item, peers)


def _align_item_to_watching_pool_legacy(item: dict, peers: Sequence[dict]) -> None:
    """旧手写 z+predict（仅回退）。"""
    code = str(item.get("stock_code") or "").strip()
    try:
        from core.research.oo_ridge import load_scoring_return_model
        from core.research.tc_ridge import load_tau_model
        from core.signal.cross_section_rescore import _active_names
        from core.signal.factors.z_oo import stamp_z_oo_aliases
        from core.signal.factors.z_tc import stamp_z_tc_aliases
    except Exception:  # noqa: BLE001
        logger.debug("pool z legacy import failed", exc_info=True)
        return
    model, _meta = load_scoring_return_model()
    oo_names = [str(k) for k in (getattr(model, "coefficients", None) or {})]
    if oo_names:
        oo_raw = dict(item.get("sub_scores") or {})
        z_oo = _z_row_against_peers(
            oo_raw,
            _peer_rows_for_cs_z(
                peers, field="sub_scores", stock_code=code, self_feats=oo_raw
            ),
            oo_names,
            stamp=stamp_z_oo_aliases,
        )
        _predict_oo_from_row(item, z_oo)
    ridge_doc = load_tau_model()
    rm = ridge_doc.get("return_model") if isinstance(ridge_doc, dict) else {}
    tau_names = _active_names(rm if isinstance(rm, dict) else {})
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    if tau_names and feats:
        z_tau = _z_row_against_peers(
            feats,
            _peer_rows_for_cs_z(
                peers, field="features_tau", stock_code=code, self_feats=feats
            ),
            tau_names,
            stamp=stamp_z_tc_aliases,
        )
        _predict_tau_from_row(item, z_tau, feats)


def _tau_feature_row_for_refresh(item: dict, trade_date: str) -> Optional[dict]:
    """前缀重算时用已缓存的观察池 μ/σ，避免单票 raw×β 盖掉截面分。"""
    dkey = str(trade_date or "")[:10]
    peers = _POOL_FEAT_CACHE.get(dkey) or []
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else None
    if not isinstance(feats, dict) or len(peers) < 2:
        return None
    try:
        from core.research.feature_standardize import cross_section_zscore_dicts
        from core.research.tc_ridge import load_tau_model
        from core.signal.cross_section_rescore import _active_names
        from core.signal.factors.z_tc import stamp_z_tc_aliases
    except Exception:  # noqa: BLE001
        logger.debug("prefix pool z import failed", exc_info=True)
        return None
    ridge_doc = load_tau_model()
    rm = ridge_doc.get("return_model") if isinstance(ridge_doc, dict) else {}
    names = _active_names(rm if isinstance(rm, dict) else {})
    if not names:
        return None
    code = str(item.get("stock_code") or "").strip()
    try:
        from core.research.day_cube import apply_cube_z_to_dicts, enabled, load_day_cube

        watching_codes = [
            str(p.get("stock_code") or "").strip()
            for p in peers
            if isinstance(p, dict) and str(p.get("stock_code") or "").strip()
        ]
        if enabled() and len(watching_codes) >= 2:
            cube = load_day_cube(dkey, watching_codes, clock="open")
            if cube is not None:
                src = dict(feats)
                stamp_z_tc_aliases(src)
                z_all = apply_cube_z_to_dicts([src], [code], cube)
                if z_all:
                    return z_all[0]
    except Exception:  # noqa: BLE001
        logger.debug("prefix pool z day_cube failed", exc_info=True)
    in_pool = any(str(p.get("stock_code") or "") == code for p in peers)
    if in_pool:
        rows = []
        slot = 0
        for peer in peers:
            if str(peer.get("stock_code") or "") == code:
                slot = len(rows)
                copied = dict(feats)
            else:
                copied = dict(peer.get("features_tau") or {})
            stamp_z_tc_aliases(copied)
            rows.append(copied)
        z_rows = cross_section_zscore_dicts(rows, names)
        return z_rows[slot] if slot < len(z_rows) else None
    return _z_row_against_peers(
        feats,
        [p.get("features_tau") or {} for p in peers],
        names,
        stamp=stamp_z_tc_aliases,
    )


def clear_score_model_cache() -> None:
    """测试 / 热更模型后清空缓存。"""
    global _CS_UNIVERSE_MEMO
    _MODEL_CACHE.clear()
    _TAU_XS_DAY_CACHE.clear()
    _EOD_ITEM_CACHE.clear()
    _FUND_CACHE.clear()
    _PREFIX_RESCORE_CACHE.clear()
    _POOL_FEAT_CACHE.clear()
    _TAU_LAKE_PEER_CACHE.clear()
    _CS_UNIVERSE_MEMO = None


def warmup_watching_day_cubes(
    codes: Sequence[str],
    dates: Sequence[str],
    *,
    progress_cb: Optional[Any] = None,
    cancel_cb: Optional[Any] = None,
    min_lake_frac: float = 0.5,
    prefetch_l1: bool = True,
) -> Dict[str, int]:
    """做 T 回测开跑：prefetch L1 + 按日期块 ensure DayCube（命中则跳过）。

    Lake 当日 oo 覆盖不足 ``min_lake_frac`` 则不预建该日（留给冷路径 bars）。
    ``QUANTLAB_MATERIALIZE_REQUIRE_CUBE=1`` 时 miss 不 ensure（禁止冷建），只计 hit。
    ``prefetch_l1=False``：独立物化 L2（刚写完 L1）跳过整池 panel 预热，避免长时间 current=0。
    返回 ``prefetch_codes`` / ``cubes_hit`` / ``cubes_built`` / ``days``。
    """
    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    day_list = sorted({str(d)[:10] for d in (dates or []) if len(str(d or "")[:10]) >= 10})
    out = {
        "prefetch_codes": 0,
        "cubes_hit": 0,
        "cubes_built": 0,
        "days": len(day_list),
    }
    if len(code_list) < 2 or not day_list:
        return out

    def _cancelled() -> bool:
        if not cancel_cb:
            return False
        try:
            return bool(cancel_cb())
        except Exception:  # noqa: BLE001
            return False

    def _emit(msg: str, cur: int = 0, tot: int = 1) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(int(cur), max(1, int(tot)), msg)
        except Exception:  # noqa: BLE001
            logger.debug("warmup day_cube progress failed", exc_info=True)

    if prefetch_l1:
        try:
            from core.research.lake_replay import prefetch_l1_coverage

            n_codes = max(1, len(code_list))

            def _pref_pcb(done: int, total: int, message: str = "") -> None:
                _emit(message or f"预热 Feature Lake {done}/{total}", done, total)

            _emit("预热 Feature Lake…", 0, n_codes)
            stats = (
                prefetch_l1_coverage(code_list, progress_cb=_pref_pcb, cancel_cb=cancel_cb)
                or {}
            )
            out["prefetch_codes"] = int(max(stats.values()) if stats else 0)
        except Exception:  # noqa: BLE001
            logger.debug("warmup l1 prefetch failed", exc_info=True)

    try:
        from core.research.day_cube import enabled as cube_on, day_cube_status
        from core.research.feature_lake import enabled as lake_on
        from core.research.lake_bridge import FAMILY_CO, FAMILY_OO, FAMILY_TC
    except Exception:  # noqa: BLE001
        logger.debug("warmup day_cube imports failed", exc_info=True)
        return out
    if not cube_on() or not lake_on():
        return out

    try:
        from core.research.materialize.consumer import consumer_read_only
        from core.research.materialize.cube_batch import (
            CUBE_FILL_CHUNK_DAYS,
            build_day_cubes_from_lake,
            day_chunks,
        )
        from core.research.materialize.flags import require_day_cube
    except Exception:  # noqa: BLE001
        logger.debug("warmup cube batch imports failed", exc_info=True)
        return out

    n_days = len(day_list)
    try:
        frac = float(min_lake_frac if min_lake_frac not in (None, "") else 0.5)
    except (TypeError, ValueError):
        frac = 0.5
    if not (frac > 0):
        frac = 0.5
    need = max(2, int(len(code_list) * frac + 0.999))
    day_pos = {d: i + 1 for i, d in enumerate(day_list)}

    def _seed(day: str, items: List[dict]) -> None:
        seed_watching_pool_feature_rows(day, items)

    load_failed = 0
    for chunk in day_chunks(day_list, CUBE_FILL_CHUNK_DAYS):
        if _cancelled():
            break
        pending: List[str] = []
        for day in chunk:
            try:
                status = day_cube_status(day, code_list, clock="open")
            except Exception:  # noqa: BLE001
                load_failed += 1
                if load_failed == 1:
                    logger.warning(
                        "warmup day_cube status failed day=%s", day, exc_info=True
                    )
                continue
            if status == "ok":
                out["cubes_hit"] += 1
                continue
            try:
                blocked = require_day_cube() or consumer_read_only()
            except Exception:  # noqa: BLE001
                blocked = False
            if blocked:
                logger.warning(
                    "warmup day_cube miss under REQUIRE_CUBE day=%s (skip ensure)",
                    day,
                )
                continue
            pending.append(day)
        if pending and not _cancelled():
            filled = build_day_cubes_from_lake(
                code_list,
                pending,
                family_oo=FAMILY_OO,
                family_tc=FAMILY_TC,
                family_co=FAMILY_CO,
                clock="open",
                universe_codes=code_list,
                min_items=need,
                require_oo_item=True,
                on_items=_seed,
                progress_cb=lambda cur, tot, msg="": _emit(msg, cur, tot),
                cancel_cb=cancel_cb,
                progress_total=n_days,
                progress_index=day_pos,
                label="预热 DayCube",
                swallow_ensure_errors=True,
                replace_existing=True,
            )
            out["cubes_built"] += int((filled or {}).get("cubes_built") or 0)
        if _cancelled():
            break
        last = day_pos[chunk[-1]]
        _emit(f"预热 DayCube {last}/{n_days}…", last, n_days)
    logger.info(
        "warmup watching day_cubes codes=%s days=%s hit=%s built=%s prefetch=%s",
        len(code_list),
        out["days"],
        out["cubes_hit"],
        out["cubes_built"],
        out["prefetch_codes"],
    )
    return out


def seed_tau_cross_section_day(day_key: str, pool_day: Optional[dict]) -> None:
    """把当日开盘缺口截面写入缓存，避免单票兜底退化成空池。"""
    dkey = str(day_key or "")[:10]
    if len(dkey) < 10 or not isinstance(pool_day, dict) or not pool_day:
        return
    if pool_day.get("sector_gap_breadth") is None and not pool_day.get("pool_gaps"):
        return
    _TAU_XS_DAY_CACHE[dkey] = dict(pool_day)


def _try_hydrate_tau_xs_from_day_cube(day_key: str, codes: Sequence[Any]) -> bool:
    """优先从 DayCube slim 灌 τ 截面缓存（命中则跳过重扫 bars）。"""
    dkey = str(day_key or "")[:10]
    if len(dkey) < 10:
        return False
    if dkey in _TAU_XS_DAY_CACHE:
        return True
    try:
        from core.research.day_cube import load_tau_pool_day

        pool = load_tau_pool_day(dkey, [str(c) for c in (codes or [])], clock="open")
        if isinstance(pool, dict) and (
            pool.get("sector_gap_breadth") is not None or pool.get("pool_gaps")
        ):
            _TAU_XS_DAY_CACHE[dkey] = dict(pool)
            return True
    except Exception:  # noqa: BLE001
        logger.debug("day_cube tau hydrate failed day=%s", dkey, exc_info=True)
    return False


def seed_tau_cross_section_pool(tau_pool_by_date: Optional[dict]) -> None:
    for dkey, pool_day in (tau_pool_by_date or {}).items():
        seed_tau_cross_section_day(str(dkey), pool_day)


def t0_cs_universe_cap() -> int:
    """与拟合观察池上限同一截面宽度。"""
    try:
        from core.watching.store import WATCHING_MAX_SIZE

        return max(8, int(WATCHING_MAX_SIZE))
    except Exception:  # noqa: BLE001
        return 200


def t0_cs_universe_codes(
    *extras: Any,
    holdings: Optional[Sequence[Any]] = None,
    paper: Optional[dict] = None,
    cap: Optional[int] = None,
) -> List[str]:
    """做 T / 持仓表 / 观察池 / 拟合同截面：纸面持仓 ∪ 观察池 ∪ extra。"""
    n = max(1, int(cap if cap is not None else t0_cs_universe_cap()))
    out: List[str] = []
    seen = set()

    def _add(raw: Any) -> None:
        c = str(raw or "").strip()
        if not c or c in seen:
            return
        seen.add(c)
        out.append(c)

    def _walk(raw: Any) -> None:
        if raw is None:
            return
        if isinstance(raw, dict):
            _add(raw.get("stock_code") or raw.get("code"))
            return
        if isinstance(raw, (list, tuple, set)):
            for x in raw:
                _walk(x)
            return
        _add(raw)

    src_holdings = holdings
    if isinstance(paper, dict):
        _walk(paper.get("holdings") or [])
    _walk(src_holdings)
    try:
        from core.paths import WATCHING_PATH
        import json
        import os

        if os.path.isfile(WATCHING_PATH):
            with open(WATCHING_PATH, encoding="utf-8") as f:
                doc = json.load(f) or {}
            wl = doc.get("watchlist") if isinstance(doc, dict) else None
            _walk(wl)
    except Exception:  # noqa: BLE001
        logger.debug("t0 cs universe watching merge failed", exc_info=True)
    for extra in extras:
        _walk(extra)
    return out[:n]


def set_t0_cs_universe_codes(codes: Optional[Sequence[str]]) -> List[str]:
    """写入缺口截面宇宙，并同步分钟同伴码。传入列表保持顺序，不重并观察池。"""
    global _CS_UNIVERSE_MEMO
    uni: List[str] = []
    seen = set()
    for raw in codes or []:
        c = str(raw or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        uni.append(c)
    if not uni:
        uni = t0_cs_universe_codes()
    _CS_UNIVERSE_MEMO = uni or None
    try:
        from core.signal.minute_tau_feats import set_peer_codes_for_sector_ret

        set_peer_codes_for_sector_ret(uni)
    except Exception:  # noqa: BLE001
        logger.debug("set_peer_codes_for_sector_ret failed", exc_info=True)
    return uni


def current_t0_cs_universe_codes() -> List[str]:
    return list(_CS_UNIVERSE_MEMO or [])


def _tau_cross_section_kwargs(code: str, day_key: str) -> Dict[str, Any]:
    """单票缺截面时：当日缓存 / 观察池∪持仓 → pool_gaps / breadth / sector_gap_median。

    空 memo 时用 ``t0_cs_universe_codes``（与做 T 回测同宇宙），避免退化成单票。
    空池不写入日缓存：离线仓无当日 K 时，后续观察池 ∪ 持仓还能补上。
    """
    raw = str(code or "").strip()
    dkey = str(day_key or "")[:10]
    if not raw or len(dkey) < 10:
        return {}
    pool = _TAU_XS_DAY_CACHE.get(dkey)
    if not isinstance(pool, dict) or not pool:
        try:
            codes = list(_CS_UNIVERSE_MEMO or [])
            if not codes:
                codes = t0_cs_universe_codes(raw)
            elif raw not in codes:
                codes = list(codes) + [raw]
            bars_map = load_bars_by_code_for_tau_pool(codes, limit=40)
            by_day = build_tau_pool_by_date(bars_map)
            pool = by_day.get(dkey) if isinstance(by_day.get(dkey), dict) else {}
            if pool:
                _TAU_XS_DAY_CACHE[dkey] = pool
        except Exception:  # noqa: BLE001
            logger.debug("tau cross-section fallback failed for %s %s", raw, dkey, exc_info=True)
            pool = {}
    if not pool:
        return {}
    ref = pool.get("ref_by_code") if isinstance(pool.get("ref_by_code"), dict) else {}
    return {
        "pool_gaps": pool.get("pool_gaps"),
        "sector_gap_breadth": pool.get("sector_gap_breadth"),
        "sector_gap_median": ref.get(raw),
    }


def _t0_index_bars_for_score(
    code: str,
    *,
    quote: Optional[dict],
    hist: Sequence[dict],
) -> Optional[List[dict]]:
    """与 score_stock 同源：基准指数日线，并裁到个股 hist 末日（开盘决策 T−1）。

    只读进程缓存，不打东财/新浪指数接口——远端挂死会占 ``ak_lock``，
    把单票做 T 回测拖到前端 300s abort（UI 一直停在「xxx·5m」）。
    """
    try:
        from core.signal.live_features import peek_cached_index_bars
        from core.signal.session_pit import prepare_eod_bars
        from core.ports.market import resolve_market_code

        mkt = str(resolve_market_code(str(code) or "") or "CN")
        pack = peek_cached_index_bars(market=mkt, allow_live_origin=True) or {}
        bars = list(pack.get("bars") or [])
        if not bars:
            return None
        idx_eod, _ = prepare_eod_bars(bars, quote)
        asof_hist = ""
        if hist:
            asof_hist = str((hist[-1] or {}).get("date") or "")[:10]
        if asof_hist:
            idx_eod = [
                b
                for b in idx_eod
                if str((b or {}).get("date") or "")[:10] <= asof_hist
            ]
        return idx_eod or None
    except Exception:  # noqa: BLE001
        logger.debug("t0 index bars resolve failed", exc_info=True)
        return None


def _t0_local_fundamentals(code: str) -> Optional[dict]:
    """与刷簿 skip_fundamentals 路径一致：本地估值/财务快照，避免财务因子假中性。"""
    fundamentals = None
    raw = str(code or "").strip()
    if not raw:
        return None
    if raw in _FUND_CACHE:
        hit = _FUND_CACHE.get(raw)
        return hit if isinstance(hit, dict) and hit else None
    try:
        from core.valuation_em import merge_cached_valuation

        fundamentals = merge_cached_valuation(raw, None)
    except Exception:  # noqa: BLE001
        logger.debug("t0 valuation merge failed", exc_info=True)
    try:
        from core.fundamentals_pit import merge_local_fundamentals_snapshot

        fundamentals = merge_local_fundamentals_snapshot(raw, fundamentals) or fundamentals
    except Exception:  # noqa: BLE001
        logger.debug("t0 fundamentals snapshot merge failed", exc_info=True)
    out = fundamentals if isinstance(fundamentals, dict) and fundamentals else None
    _FUND_CACHE[raw] = out
    return out


def _eod_item_cache_key(
    raw: str,
    hist: Sequence[dict],
    day: Optional[dict],
    quote: Optional[dict],
    *,
    pool_gaps: Optional[Sequence[float]],
    sector_gap_breadth: Optional[float],
    sector_gap_median: Optional[float],
    horizon_days: int,
    fund_fp: tuple,
    idx_n: int,
) -> tuple:
    hist_end = str((hist[-1] or {}).get("date") or "")[:10] if hist else ""
    day_d = str((day or {}).get("date") or "")[:10]
    q = quote if isinstance(quote, dict) else {}
    try:
        open_px = round(
            float(q.get("price") if q.get("price") is not None else (day or {}).get("open") or 0),
            6,
        )
    except (TypeError, ValueError):
        open_px = 0.0
    try:
        med = round(float(sector_gap_median), 8) if sector_gap_median is not None else None
    except (TypeError, ValueError):
        med = None
    try:
        br = round(float(sector_gap_breadth), 8) if sector_gap_breadth is not None else None
    except (TypeError, ValueError):
        br = None
    gaps = tuple(
        round(float(g), 6)
        for g in (pool_gaps or [])
        if g is not None
    )
    return (
        str(raw),
        hist_end,
        len(hist),
        day_d,
        open_px,
        med,
        br,
        gaps,
        int(horizon_days or 1),
        tuple(fund_fp or ()),
        int(idx_n),
    )


def _fund_cache_fp(fund: Optional[dict]) -> tuple:
    if not isinstance(fund, dict) or not fund:
        return ()
    out = []
    for k in ("market_cap", "pe_ttm", "pb", "ps_ttm", "roe"):
        if k not in fund:
            continue
        try:
            out.append((k, round(float(fund.get(k)), 6)))
        except (TypeError, ValueError):
            out.append((k, str(fund.get(k))[:32]))
    return tuple(out)


def _clone_eod_item(item: dict) -> dict:
    try:
        return copy.deepcopy(item)
    except Exception:  # noqa: BLE001
        return dict(item)


def compute_scores_from_bars(
    code: str,
    hist_bars: Sequence[dict],
    *,
    day_bar: Optional[dict] = None,
    quote: Optional[dict] = None,
    rem_model_doc: Any = None,
    return_models_by_code: Optional[Dict[str, Any]] = None,
    default_return_model: Any = None,
    fuse_intraday: bool = True,
    min_history: int = _MIN_HIST_BARS,
    horizon_days: int = 1,
    pool_gaps: Optional[Sequence[float]] = None,
    sector_gap_breadth: Optional[float] = None,
    index_bars: Optional[Sequence[dict]] = None,
    fundamentals: Optional[dict] = None,
    sector_gap_median: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    sector_ret_to_tau: Optional[float] = None,
    use_minute_tau: Optional[bool] = None,
    minute_tau_hm: Optional[str] = None,
    include_tau_horizons: bool = True,
) -> Dict[str, Optional[float]]:
    """开盘决策信息集即时算 dual_y 分数（不读账本/簿）。

    ``hist_bars``：不含当日的日线（因子截止 T−1）。
    ``day_bar`` / ``quote``：提供 open[T]（缺口）；勿用收盘价冒充开盘决策现价。
    ``pool_gaps`` / ``sector_gap_breadth``：截面缺口（批量算分时传入，增强 ŷ_τ）。
    ``sector_gap_median``：同行/截面参照缺口（``gap_vs_sector = gap − median``）。
    ``index_bars`` / ``fundamentals``：可选；缺省时拉本地指数+估值，对齐数据中心相对强弱等。
    ``sector_ret_to_tau``：开→τ 池中位（训练 panel 同口径）；缺省由 dual_score 从分钟仓聚合。
    ``minute_bars``：可选当日 5m；``enable_minute_tau`` 时写入 ≤τ 小包（否则可读缓存）。
    ``minute_tau_hm``：因果 τ 钟（固定前缀末根）；有分钟输入时截面/小包对齐此时钟。
    ``use_minute_tau=False``：做 T 开盘预计算强制开盘 Z（禁分钟前缀 / 开→τ 截面）。
    ``include_tau_horizons=False``：只算主头特征（建观察池截面 μ/σ 用，跳过 τ30–90）。
    """
    raw = str(code or "").strip()
    hist = [b for b in (hist_bars or []) if isinstance(b, dict)]
    if not raw or len(hist) < max(8, int(min_history or _MIN_HIST_BARS)):
        return scores_from_item(None)

    day = day_bar if isinstance(day_bar, dict) else None
    q = quote if isinstance(quote, dict) else None
    if q is None and day is not None:
        q = open_decision_quote(day, hist[-1] if hist else None, code=raw)
    if q is None:
        return scores_from_item(None)

    # 单票缺截面时用观察池 ∪ 持仓补 gap_vs_sector / breadth（与盯盘同池）
    if (
        sector_gap_median is None
        or sector_gap_breadth is None
        or not pool_gaps
    ):
        day_key = str((day or {}).get("date") or (q or {}).get("date") or "")[:10]
        if len(day_key) >= 10:
            xs = _tau_cross_section_kwargs(raw, day_key)
            if sector_gap_median is None and xs.get("sector_gap_median") is not None:
                sector_gap_median = xs.get("sector_gap_median")
            if sector_gap_breadth is None and xs.get("sector_gap_breadth") is not None:
                sector_gap_breadth = xs.get("sector_gap_breadth")
            if not pool_gaps and xs.get("pool_gaps"):
                pool_gaps = xs.get("pool_gaps")

    try:
        from core.signal.cross_section_batch import score_window_as_item
        from core.signal.dual_score import attach_dual_score_pit
        from core.signal.return_score import apply_predicted_scores_by_model
        from core.signal.y_state import build_y_state
    except Exception:  # noqa: BLE001
        logger.debug("compute_scores_from_bars imports failed", exc_info=True)
        return scores_from_item(None)

    rem, cluster, global_rm = _scoring_models()
    if rem_model_doc is not None:
        rem = rem_model_doc
    models = return_models_by_code if return_models_by_code is not None else cluster
    default_rm = default_return_model if default_return_model is not None else global_rm

    idx = [b for b in (index_bars or []) if isinstance(b, dict)]
    if not idx:
        idx = _t0_index_bars_for_score(raw, quote=q, hist=hist) or []
    fund = fundamentals if isinstance(fundamentals, dict) else None
    if not fund:
        fund = _t0_local_fundamentals(raw)

    # 与 score_stock 同源：ŷ β 键即使启发式权≈0 / regime 禁用也必须算 sub_scores
    required_factor_keys: List[str] = []
    try:
        eod_model_pre = None
        if isinstance(models, dict):
            eod_model_pre = models.get(raw)
        if eod_model_pre is None:
            eod_model_pre = default_rm
        for m in (eod_model_pre, default_rm):
            if m is None:
                continue
            for k in (getattr(m, "coefficients", None) or {}).keys():
                kk = str(k).strip()
                if kk and kk not in required_factor_keys:
                    required_factor_keys.append(kk)
    except Exception:  # noqa: BLE001
        logger.debug("t0 required_factor_keys resolve failed", exc_info=True)
        required_factor_keys = []

    try:
        # EOD 因子窗与刷簿 score_stock 对齐（含 alpha_oo）；τ/ATR 仍用更长 hist
        eod_hist = hist[-int(_EOD_FACTOR_BAR_LIMIT) :]
        eod_key = _eod_item_cache_key(
            raw,
            hist,
            day,
            q,
            pool_gaps=pool_gaps,
            sector_gap_breadth=sector_gap_breadth,
            sector_gap_median=sector_gap_median,
            horizon_days=max(1, int(horizon_days or 1)),
            fund_fp=_fund_cache_fp(fund),
            idx_n=len(idx),
        )
        cached = _EOD_ITEM_CACHE.get(eod_key)
        if isinstance(cached, dict) and cached:
            item = _clone_eod_item(cached)
        else:
            item = score_window_as_item(
                raw,
                list(eod_hist),
                horizon_days=max(1, int(horizon_days or 1)),
                quote=q,
                index_bars=idx or None,
                fundamentals=fund,
                required_factor_keys=required_factor_keys or None,
            )
            if not item:
                return scores_from_item(None)
            scored = apply_predicted_scores_by_model(
                [item],
                models or {},
                default_model=default_rm,
            )
            item = scored[0] if scored else item
            if item.get("predicted_score") is not None and item.get("predicted_score_eod") is None:
                item["predicted_score_eod"] = item.get("predicted_score")
            gaps = [float(g) for g in (pool_gaps or []) if g is not None]
            if gaps:
                item["_pool_gaps"] = gaps
            if sector_gap_breadth is not None:
                try:
                    item["sector_gap_breadth"] = float(sector_gap_breadth)
                except (TypeError, ValueError):
                    pass
            if sector_gap_median is not None:
                try:
                    item["_sector_gap_median"] = float(sector_gap_median)
                except (TypeError, ValueError):
                    pass
            if len(_EOD_ITEM_CACHE) >= int(_EOD_ITEM_CACHE_MAX):
                _EOD_ITEM_CACHE.clear()
            _EOD_ITEM_CACHE[eod_key] = _clone_eod_item(item)
        if item.get("predicted_score") is not None and item.get("predicted_score_eod") is None:
            item["predicted_score_eod"] = item.get("predicted_score")
        allow_minute = True if use_minute_tau is None else bool(use_minute_tau)
        if allow_minute and sector_ret_to_tau is not None:
            try:
                item["_sector_ret_to_tau"] = float(sector_ret_to_tau)
            except (TypeError, ValueError):
                pass
        causal_hm = str(minute_tau_hm or "").strip()[:5] or None
        if allow_minute and causal_hm:
            item["_minute_tau_hm"] = causal_hm
        try:
            from core.research.tau_panel import GAP_ATR_WINDOW
        except Exception:  # noqa: BLE001
            GAP_ATR_WINDOW = 14
        # 开盘 PIT：τ / EOD / 决策用开盘 quote（price=open）；勿把当日 close 喂进 ŷ_co
        # （否则表列 y_on≈当日 OC，与 τ实假相关）。
        dual_bars = list(hist[-max(int(GAP_ATR_WINDOW) + 6, 20) :])
        # 当日 bar 仅供 asof 对齐；hist_bars_pit 会剥掉 asof 日 K，不吃 T 振幅
        if day is not None:
            dual_bars = dual_bars + [day]
        pit_day = str((day or {}).get("date") or (q or {}).get("date") or "")[:10]
        attach_dual_score_pit(
            item,
            quote=q,
            bars=dual_bars,
            rem_model_doc=rem,
            sector_gap_breadth=item.get("sector_gap_breadth"),
            fuse_intraday=bool(fuse_intraday),
            sector_gap_median=item.get("_sector_gap_median"),
            minute_bars=minute_bars if allow_minute else None,
            sector_ret_to_tau=item.get("_sector_ret_to_tau") if allow_minute else None,
            use_minute_tau=False if not allow_minute else use_minute_tau,
            minute_tau_hm=causal_hm if allow_minute else None,
            trade_day=pit_day if len(pit_day) >= 10 else None,
        )
        # 复盘对照：路径价 ŷ_co（可含当日 close）写入旁路字段，不覆盖决策 y_on
        try:
            from core.signal.dual_score.co import attach_co_score_pit

            q_path = _enrich_quote_path_price(dict(q or {}), day, code=raw)
            open_on = item.get("predicted_score_on")
            open_feats = (
                dict(item.get("features_co") or {})
                if isinstance(item.get("features_co"), dict)
                else {}
            )
            open_terms = item.get("formula_terms_co")
            open_src = item.get("y_co_source")
            attach_co_score_pit(
                item,
                quote=q_path,
                bars=dual_bars,
                gap_pct=item.get("gap_pct"),
                sector_gap_breadth=item.get("sector_gap_breadth"),
                theme_day=(item.get("features_tau") or {}).get("theme_day")
                if isinstance(item.get("features_tau"), dict)
                else None,
            )
            path_on = item.get("predicted_score_on")
            path_feats = item.get("features_co")
            # 恢复开盘决策口径
            item["predicted_score_on"] = open_on
            if open_on is not None:
                item["y_co"] = open_on
                item["predicted_score_co"] = open_on
            if open_src:
                item["y_co_source"] = open_src
            if open_feats:
                item["features_co"] = open_feats
            if isinstance(open_terms, dict):
                item["formula_terms_co"] = open_terms
                item["score_formula_terms_co"] = open_terms
                item["formula_terms_on"] = open_terms
                item["score_formula_terms_on"] = open_terms
            if path_on is not None:
                item["predicted_score_on_path"] = path_on
                item["y_on_path"] = path_on
            if isinstance(path_feats, dict) and path_feats:
                item["features_co_path"] = dict(path_feats)
        except Exception:  # noqa: BLE001
            logger.debug("attach path-enriched y_on failed", exc_info=True)
        # 即时算路径未走 score_stock，补 EOD 组成表供 tip 对照因子
        try:
            eod_model = None
            if isinstance(models, dict):
                eod_model = models.get(raw)
            if eod_model is None:
                eod_model = default_rm
            if eod_model is not None:
                if not item.get("score_formula_terms"):
                    expl = eod_model.explain_prediction(item.get("sub_scores") or {})
                    if expl:
                        item["score_formula_terms"] = expl
                if not item.get("factor_coefficients"):
                    coefs = dict(getattr(eod_model, "coefficients", None) or {})
                    coefs.pop("intercept", None)
                    if coefs:
                        item["factor_coefficients"] = {
                            str(k): float(v)
                            for k, v in coefs.items()
                            if _f(v) is not None
                        }
                if not item.get("return_model_source"):
                    item["return_model_source"] = "t0_backtest_compute"
        except Exception:  # noqa: BLE001
            logger.debug("attach EOD formula terms failed", exc_info=True)
        try:
            st = build_y_state(item)
            if st.get("check") is not None:
                item["y_check"] = st.get("check")
            if st.get("eod_trust") is not None:
                item["eod_trust"] = st.get("eod_trust")
        except Exception:  # noqa: BLE001
            logger.debug("build_y_state in compute failed", exc_info=True)
        if allow_minute and minute_bars:
            prefix = [b for b in minute_bars if isinstance(b, dict)]
            item["_minute_prefix"] = prefix
            hm = causal_hm or prefix_tau_hm_from_bars(prefix)
            _inject_minute_pack_from_prefix(
                item, minute_prefix=prefix, day_bar=day, hm=hm or "10:30"
            )
            try:
                from core.research.tau_panel import attach_tau_lag_features

                ft = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
                if isinstance(ft, dict) and ft.get("tau_lag1") is None and hist and pit_day:
                    item["features_tau"] = attach_tau_lag_features(
                        ft, hist_bars=hist, asof_date=pit_day
                    )
            except Exception:  # noqa: BLE001
                logger.debug("compute tau lag attach failed", exc_info=True)
            _refresh_tau_oc_from_feats(
                item, hm=str(hm or "")[:5], trade_date=pit_day, force=True
            )
        _attach_y_path_to_item(
            item, hist_bars=hist, include_tau_horizons=bool(include_tau_horizons)
        )
        item.pop("_minute_prefix", None)
        try:
            from core.signal.dual_score import align_trade_score_fields

            # 与持仓/观察表同源；勿 refresh_window（历史日须保留 fuse_intraday 窗）
            align_trade_score_fields(item, write_score=False, refresh_window=False)
        except Exception:  # noqa: BLE001
            logger.debug("align_trade_score_fields in compute failed", exc_info=True)
        sink = _ITEM_SINK.get()
        if isinstance(sink, list):
            sink.append(_capture_feature_row(item, raw))
        if _pool_z_active():
            _align_item_to_watching_pool(item, pit_day)
        out = scores_from_item(item)
        out["_score_source"] = "compute"
        return out
    except Exception:  # noqa: BLE001
        logger.debug("compute_scores_from_bars failed for %s", raw, exc_info=True)
        return scores_from_item(None)


def _open_gap_pct(day_bar: Optional[dict], prev_bar: Optional[dict]) -> Optional[float]:
    if not isinstance(day_bar, dict):
        return None
    o = _f(day_bar.get("open"))
    pc = _f(day_bar.get("prev_close"))
    if pc is None and isinstance(prev_bar, dict):
        pc = _f(prev_bar.get("close"))
    if o is None or pc is None or pc <= 0:
        return None
    return (float(o) / float(pc) - 1.0) * 100.0


def build_tau_pool_by_date(
    bars_by_code: Optional[Dict[str, Sequence[dict]]],
) -> Dict[str, Dict[str, Any]]:
    """按日聚合开盘缺口 → 截面字段（对齐刷簿）。

    返回 ``{YYYY-MM-DD: {
        pool_gaps, sector_gap_breadth, gaps_by_code, ref_by_code
    }}``。
    ``sector_gap_breadth`` 即令为 0.0 也会写入（与缺特征不同）。
    ``ref_by_code``：同行/截面参照缺口（供 gap_vs_sector）。
    """
    from collections import defaultdict

    gaps_by_date_code: Dict[str, Dict[str, float]] = defaultdict(dict)
    for code, bars in (bars_by_code or {}).items():
        key = str(code or "").strip()
        if not key:
            continue
        seq = [b for b in (bars or []) if isinstance(b, dict)]
        for i in range(1, len(seq)):
            day = seq[i]
            prev = seq[i - 1]
            dkey = str(day.get("date") or "")[:10]
            if len(dkey) < 10:
                continue
            g = _open_gap_pct(day, prev)
            if g is None:
                continue
            gaps_by_date_code[dkey][key] = float(g)

    trigger = 2.0
    try:
        from core.market.priors.event import get_event_prior_cfg

        trigger = float(get_event_prior_cfg().get("gap_trigger_pct") or 2.0)
    except Exception:  # noqa: BLE001
        logger.debug("tau pool trigger cfg failed", exc_info=True)

    sm: Dict[str, str] = {}
    try:
        from core.portfolio_optimize import load_sector_map

        sm = load_sector_map() or {}
    except Exception:  # noqa: BLE001
        logger.debug("tau pool sector map failed", exc_info=True)
        sm = {}

    try:
        from core.research.tau_panel import sector_gap_reference_by_code
    except Exception:  # noqa: BLE001
        logger.debug("sector_gap_reference_by_code import failed", exc_info=True)
        sector_gap_reference_by_code = None  # type: ignore

    out: Dict[str, Dict[str, Any]] = {}
    for dkey, by_code in gaps_by_date_code.items():
        if not by_code:
            continue
        gaps = list(by_code.values())
        # 与 tau_panel / event_prior.sector_gap_breadth 同构：仅计正缺口 ≥ trigger
        hit = sum(1 for g in gaps if float(g) >= trigger)
        ref_by_code: Dict[str, Optional[float]] = {}
        if callable(sector_gap_reference_by_code):
            try:
                ref_by_code = sector_gap_reference_by_code(by_code, sector_map=sm) or {}
            except Exception:  # noqa: BLE001
                logger.debug("sector_gap_reference_by_code failed", exc_info=True)
                ref_by_code = {}
        out[dkey] = {
            "pool_gaps": gaps,
            "sector_gap_breadth": float(hit) / float(len(gaps)),
            "gaps_by_code": dict(by_code),
            "ref_by_code": dict(ref_by_code),
        }
    return out


def load_bars_by_code_for_tau_pool(
    codes: Sequence[str],
    *,
    limit: int = 80,
) -> Dict[str, List[dict]]:
    """批量拉日线（走缓存）供 τ 截面；失败票跳过。"""
    out: Dict[str, List[dict]] = {}
    try:
        from core.data.facade import bars_and_source
    except Exception:  # noqa: BLE001
        logger.debug("bars_and_source import failed", exc_info=True)
        return out
    seen = set()
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        try:
            bars, _src = bars_and_source(
                code,
                limit=max(20, int(limit or 80)),
                offline_ok=True,
                offline_only=True,
            )
        except Exception:  # noqa: BLE001
            logger.debug("tau pool bars load failed for %s", code, exc_info=True)
            continue
        seq = [b for b in (bars or []) if isinstance(b, dict)]
        if len(seq) >= 2:
            out[code] = seq
    return out


def _bars_map_from_holding_bars(
    hist_bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    day_bars_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, List[dict]]:
    """已 hydrate 持仓日线 → ``{code: bars}``（含当日 bar）。"""
    codes: List[str] = []
    for src in (hist_bars_by_code or {}, day_bars_by_code or {}):
        for k in src.keys():
            c = str(k or "").strip()
            if c and c not in codes:
                codes.append(c)
    bars_map: Dict[str, List[dict]] = {}
    for code in codes:
        hist = list((hist_bars_by_code or {}).get(code) or [])
        seq = [b for b in hist if isinstance(b, dict)]
        day = (day_bars_by_code or {}).get(code)
        if isinstance(day, dict):
            dkey = str(day.get("date") or "")[:10]
            last = str((seq[-1].get("date") if seq else "") or "")[:10]
            if dkey and dkey != last:
                seq = seq + [day]
        if len(seq) >= 2:
            bars_map[code] = seq
    return bars_map


def build_tau_pool_from_holding_bars(
    hist_bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    day_bars_by_code: Optional[Dict[str, dict]] = None,
) -> Dict[str, Dict[str, Any]]:
    """仅用已 hydrate 持仓日线建 τ 截面（窄池；对照 / 单测）。"""
    return build_tau_pool_by_date(
        _bars_map_from_holding_bars(hist_bars_by_code, day_bars_by_code)
    )


def build_tau_pool_watching_holdings(
    hist_bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    day_bars_by_code: Optional[Dict[str, dict]] = None,
    *,
    holdings: Optional[Sequence[Any]] = None,
    paper: Optional[dict] = None,
    extra_codes: Any = (),
    limit: int = 40,
) -> Dict[str, Dict[str, Any]]:
    """观察池 ∪ 持仓日线建开盘缺口截面，与做 T 回测同口径。

    已 hydrate 的持仓日线覆盖本地仓（会话对齐）；其余观察池票走 ``load_bars_by_code_for_tau_pool``。
    """
    extras: Sequence[Any]
    if extra_codes is None:
        extras = ()
    elif isinstance(extra_codes, (list, tuple, set)):
        extras = list(extra_codes)
    else:
        extras = (extra_codes,)
    codes = t0_cs_universe_codes(*extras, holdings=holdings, paper=paper)
    # 当日已有 DayCube slim 时直接灌缓存，避免 live 反复扫 bars 建截面
    try:
        from datetime import date as _date

        today = _date.today().isoformat()
        if _try_hydrate_tau_xs_from_day_cube(today, codes):
            cached = _TAU_XS_DAY_CACHE.get(today)
            if isinstance(cached, dict) and cached:
                return {today: dict(cached)}
    except Exception:  # noqa: BLE001
        logger.debug("tau pool day_cube short-circuit failed", exc_info=True)
    bars_map = load_bars_by_code_for_tau_pool(codes, limit=limit)
    overlay = _bars_map_from_holding_bars(hist_bars_by_code, day_bars_by_code)
    if overlay:
        bars_map.update(overlay)
    if not bars_map:
        return {}
    pool = build_tau_pool_by_date(bars_map)
    return pool


def compute_scores_map_from_bars(
    specs: Sequence[Dict[str, Any]],
    *,
    fuse_intraday: bool = True,
    use_minute_tau: Optional[bool] = False,
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量开盘算分：共享模型缓存，并用截面 open 缺口作 pool_gaps。

    每个 spec: ``{code, hist_bars, day_bar?}``。
    默认 ``use_minute_tau=False``（做 T / 纸面 hydrate 开盘信息集，禁偷读 ≤10:30 缓存）。
    """
    out: Dict[str, Dict[str, Optional[float]]] = {}
    rows: List[Tuple[str, Sequence[dict], Optional[dict], Optional[float]]] = []
    gaps_by_code: Dict[str, float] = {}
    gaps: List[float] = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        code = str(spec.get("code") or spec.get("stock_code") or "").strip()
        hist = [b for b in (spec.get("hist_bars") or []) if isinstance(b, dict)]
        day = spec.get("day_bar") if isinstance(spec.get("day_bar"), dict) else None
        if day is None and len(hist) >= _MIN_HIST_BARS + 1:
            day = hist[-1]
            hist = hist[:-1]
        if not code or len(hist) < _MIN_HIST_BARS:
            continue
        g = _open_gap_pct(day, hist[-1] if hist else None)
        rows.append((code, hist, day, g))
        if g is not None:
            gaps.append(float(g))
            gaps_by_code[code] = float(g)

    breadth = None
    if gaps:
        try:
            from core.market.priors.event import get_event_prior_cfg

            trigger = float(get_event_prior_cfg().get("gap_trigger_pct") or 2.0)
            # 仅正缺口（与刷簿 / 训练面板一致）
            hit = sum(1 for g in gaps if g >= trigger)
            breadth = hit / float(len(gaps))
        except Exception:  # noqa: BLE001
            logger.debug("pool breadth failed", exc_info=True)
            breadth = None

    ref_by_code: Dict[str, Optional[float]] = {}
    if gaps_by_code:
        try:
            from core.portfolio_optimize import load_sector_map
            from core.research.tau_panel import sector_gap_reference_by_code

            sm = load_sector_map() or {}
            ref_by_code = sector_gap_reference_by_code(gaps_by_code, sector_map=sm) or {}
        except Exception:  # noqa: BLE001
            logger.debug("batch sector gap ref failed", exc_info=True)
            ref_by_code = {}

    # 预热模型，避免逐票重复 IO
    _scoring_models()
    for code, hist, day, _g in rows:
        sc = compute_scores_from_bars(
            code,
            hist,
            day_bar=day,
            fuse_intraday=fuse_intraday,
            pool_gaps=gaps or None,
            sector_gap_breadth=breadth,
            sector_gap_median=ref_by_code.get(code),
            use_minute_tau=use_minute_tau,
        )
        if scores_have_any(sc):
            out[code] = sc
    return out


def resolve_y_score_source(cfg: Optional[dict] = None) -> str:
    """分数来源只剩即时算。旧 ledger / live_book / book 读入后按 compute。"""
    raw = str((cfg or {}).get("y_score_source") or DEFAULT_Y_SCORE_SOURCE).strip().lower()
    if raw in {
        "compute",
        "pit",
        "live",
        "realtime",
        "on_the_fly",
        "book",
        "cluster",
        "cluster_book",
        "live_book",
        "ledger",
        "score_ledger",
        "freeze",
        "",
    }:
        return "compute"
    return DEFAULT_Y_SCORE_SOURCE


def resolve_fuse_intraday(cfg: Optional[dict] = None) -> bool:
    """回测/即时算是否融合盘中 ŷ_τ。

    ``dual_score_window`` / ``y_score_window`` = ``eod_next``（或 eod/close）时
    不得 fuse。live 持仓/观察池不走这里，用 ``rolled_to_next``。
    """
    window = str(
        (cfg or {}).get("dual_score_window")
        or (cfg or {}).get("y_score_window")
        or "intraday_historical"
    ).strip().lower()
    return window not in {"eod_next", "eod", "close"}


def resolve_score_as_of(
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    day_bar: Optional[dict] = None,
) -> str:
    """决策日：优先 hist 末日（T−1）。"""
    for b in reversed(list(hist_bars or [])):
        d = str((b or {}).get("date") or "")[:10]
        if d:
            return d
    return str((day_bar or {}).get("date") or "")[:10]


def resolve_scores_for_code(
    code: str,
    *,
    hist_bars: Optional[Sequence[dict]] = None,
    day_bar: Optional[dict] = None,
    quote: Optional[dict] = None,
    as_of: Optional[str] = None,
    source: str = DEFAULT_Y_SCORE_SOURCE,
    fuse_intraday: bool = True,
    allow_fallback: bool = True,
    pool_gaps: Optional[Sequence[float]] = None,
    sector_gap_breadth: Optional[float] = None,
    sector_gap_median: Optional[float] = None,
    minute_bars: Optional[Sequence[dict]] = None,
    sector_ret_to_tau: Optional[float] = None,
    use_minute_tau: Optional[bool] = None,
    minute_tau_hm: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    """按即时算解析 dual_y 分数。旧 ledger / live_book 与 compute 同一条路径。

    ``use_minute_tau=False``：做 T 开盘预计算强制开盘信息集。
    ``minute_tau_hm``：确认根因果重算时传入前缀末根钟。
    """
    raw = str(code or "").strip()
    if not raw:
        return scores_from_item(None)
    _ = as_of, source, allow_fallback
    hist = list(hist_bars or [])
    day = day_bar if isinstance(day_bar, dict) else None
    if day is None and hist:
        if len(hist) >= _MIN_HIST_BARS + 1:
            day = hist[-1]
            hist = hist[:-1]
    return compute_scores_from_bars(
        raw,
        hist,
        day_bar=day,
        quote=quote,
        fuse_intraday=fuse_intraday,
        pool_gaps=pool_gaps,
        sector_gap_breadth=sector_gap_breadth,
        sector_gap_median=sector_gap_median,
        minute_bars=minute_bars,
        sector_ret_to_tau=sector_ret_to_tau,
        use_minute_tau=use_minute_tau,
        minute_tau_hm=minute_tau_hm,
    )


def load_scores_map_for_codes(
    codes: Sequence[str],
    *,
    as_of: Optional[str] = None,
    prefer_live_book: bool = False,
    source: Optional[str] = None,
    bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    hist_bars_by_code: Optional[Dict[str, Sequence[dict]]] = None,
    day_bars_by_code: Optional[Dict[str, dict]] = None,
    allow_fallback: bool = True,
    fuse_intraday: Optional[bool] = None,
    rules: Optional[dict] = None,
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量取 dual_y 分数。

    默认 ``source=compute``：用日线批量即时算（截面缺口共享）。
    ``prefer_live_book`` 已忽略（分池簿取分已去掉）。
    ``fuse_intraday`` 缺省时由 ``rules`` / ``dual_score_window`` 解析（eod_next → False）。
    """
    out: Dict[str, Dict[str, Optional[float]]] = {}
    want = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not want:
        return out

    fuse = (
        bool(fuse_intraday)
        if fuse_intraday is not None
        else resolve_fuse_intraday(rules)
    )

    _ = prefer_live_book
    if source is None:
        src = DEFAULT_Y_SCORE_SOURCE
    else:
        src = resolve_y_score_source({"y_score_source": source})

    if src == "compute" and (hist_bars_by_code or bars_by_code or day_bars_by_code):
        specs: List[Dict[str, Any]] = []
        for code in want:
            hist = None
            if hist_bars_by_code and code in hist_bars_by_code:
                hist = list(hist_bars_by_code.get(code) or [])
            elif bars_by_code and code in bars_by_code:
                hist = list(bars_by_code.get(code) or [])
            day = None
            if day_bars_by_code and code in day_bars_by_code:
                day = day_bars_by_code.get(code)
            specs.append({"code": code, "hist_bars": hist or [], "day_bar": day})
        with watching_pool_z_scope():
            computed = compute_scores_map_from_bars(
                specs, fuse_intraday=fuse, use_minute_tau=False
            )
        out.update(computed)
        _ = allow_fallback
        return out

    with watching_pool_z_scope():
        for code in want:
            hist = None
            if hist_bars_by_code and code in hist_bars_by_code:
                hist = hist_bars_by_code.get(code)
            elif bars_by_code and code in bars_by_code:
                hist = bars_by_code.get(code)
            day = None
            if day_bars_by_code and code in day_bars_by_code:
                day = day_bars_by_code.get(code)
            sc = resolve_scores_for_code(
                code,
                hist_bars=hist,
                day_bar=day,
                as_of=as_of,
                source=src,
                fuse_intraday=fuse,
                allow_fallback=allow_fallback,
            )
            if scores_have_any(sc):
                out[code] = sc
    return out
