"""多层 ŷ 驱动的 A 股底仓做 T 策略（dual_y）。

角色（PIT）：
  y_τ      — 盘中主方向（开→收 OC 拟合）；正/反 T 可分 enter（y_tau_enter_buy_then_sell / _sell_then_buy）
             定向锚优先 y_tau_oc（映射前）；剩余映射分仅供融合/对照，不定向
  y_hl     — **已下线**：不再算分/挂载；旧键可读兼容；`path_ridge` 仅研究模块残留
  y_on     — 尾盘是否强制回补
  y_trade / y_eod — **v6 已下线**：不参与选向；`load_t0_rules` 丢弃旧闸键；快照字段仅作对照

选向分数：开盘可预计算开盘 Z；**确认根（前 N 根齐窗）用前缀分钟因果重算**
ŷ_τ（及 τ 窗）后再 close-band 选向。禁止用全日/未发生分钟做开盘选向。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.t0.config import t0_dir_label

logger = logging.getLogger(__name__)

# 默认阈值（ŷ 为百分比点；可用 rules 覆盖）
DEFAULT_TRADE_ENTER = 0.01
DEFAULT_TAU_ENTER = 0.0
DEFAULT_ON_RISK = 0.01
DEFAULT_ON_ALLOW = 0.01
DEFAULT_PATH_ENTER = 0.0  # ŷ_hl 极值序 %；|ŷ|≤enter 横盘跳过；与 y_tau_enter 同尺度
DEFAULT_PATH_STRONG = 5.0  # |y_hl|>此值时须与 y_τ 同号；≤则允许异号
DEFAULT_GAP_TIER_PCT = 1.0

# dual_y 下 y_τ 符号 → 正/反 T（映射见 minute_path）
# scalp / trend: y_τ>0→正T；scalp 与 trend 同义，scalp 仅兼容
# fixed_sell_then_buy / fixed_buy_then_sell: 忽略 y_τ 符号，固定方向（仍过 |y_τ| 门槛）
Y_TAU_MAP_DEFAULT = "trend"
Y_TAU_MAP_CHOICES = ("scalp", "trend", "fixed_sell_then_buy", "fixed_buy_then_sell")
Y_TAU_MAP_LABELS = {
    "scalp": "符号定方向（兼容别名）",
    "trend": "符号定方向",
    "fixed_sell_then_buy": "固定反T",
    "fixed_buy_then_sell": "固定正T",
}

# compute | live_book | ledger
DEFAULT_Y_SCORE_SOURCE = "compute"
_MIN_HIST_BARS = 16
# 与 score_stock.fetch_daily_bars(limit=40) 对齐：周线确认按「从头每 5 根切桶」，
# 窗长不同会漂 weekly_confirm / ma_slope / technical_pattern。
_EOD_FACTOR_BAR_LIMIT = 40
# 做 T 回测：评估窗与因子窗分离；warmup 仅供 hist_prior，不进成交明细
T0_BACKTEST_SCORE_WARMUP = max(_MIN_HIST_BARS + 8, 40)
# 纸面预演 / Worker：须含「当日 + 40 根 T−1 及更早」，否则 EOD 周线切桶比回测少 1 根
T0_PAPER_DAILY_BAR_LIMIT = T0_BACKTEST_SCORE_WARMUP + 1

# 进程内轻量缓存：回测逐日重算时复用模型句柄
_MODEL_CACHE: Dict[str, Any] = {}
# 单日 τ 截面缓存：day → build_tau_pool_by_date 条目
_TAU_XS_DAY_CACHE: Dict[str, Dict[str, Any]] = {}
# 盯盘 / 做T回测共用截面宇宙（纸面持仓 ∪ 观察池 ∪ extra）
_CS_UNIVERSE_MEMO: Optional[List[str]] = None
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
    "mom3_pct",
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
        "mom3_pct",
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
        limit=10,
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
        limit=10,
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
            from core.research.tc_ridge import explain_tc_prediction, load_tc_model

            feats_r = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_r:
                feats_r = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            expl_r = explain_tc_prediction(feats_r, model_doc=load_tc_model())
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
        try:
            from core.research.t30_ridge import explain_t30_prediction, load_t30_model

            feats_t30 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_t30:
                feats_t30 = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            expl_t30 = explain_t30_prediction(feats_t30, model_doc=load_t30_model())
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
        try:
            from core.research.t45_ridge import explain_t45_prediction, load_t45_model

            feats_t45 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_t45:
                feats_t45 = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            expl_t45 = explain_t45_prediction(feats_t45, model_doc=load_t45_model())
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
        try:
            from core.research.t60_ridge import explain_t60_prediction, load_t60_model

            feats_t60 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_t60:
                feats_t60 = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            expl_t60 = explain_t60_prediction(feats_t60, model_doc=load_t60_model())
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
        try:
            from core.research.t75_ridge import explain_t75_prediction, load_t75_model

            feats_t75 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_t75:
                feats_t75 = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            expl_t75 = explain_t75_prediction(feats_t75, model_doc=load_t75_model())
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
        try:
            from core.research.t90_ridge import explain_t90_prediction, load_t90_model

            feats_t90 = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            if not feats_t90:
                feats_t90 = (
                    item.get("features_path")
                    if isinstance(item.get("features_path"), dict)
                    else {}
                )
            expl_t90 = explain_t90_prediction(feats_t90, model_doc=load_t90_model())
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
                "mom3_pct",
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
                "mom3_pct",
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
        "y_tau_oc",
        "y_trade",
        "residual",
        "r_hat",
        "y_oo",
        "y_oc",
        "y_τc",
        "y_τc_ridge",
        "y_τc_source",
        "ranking",
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
    if out.get("y_tau_oc") is None:
        ft = out.get("formula_terms_tau") or out.get("score_formula_terms_tau")
        if not isinstance(ft, dict):
            ft = score_snap.get("formula_terms_tau") or score_snap.get(
                "score_formula_terms_tau"
            )
        if isinstance(ft, dict):
            oc = _f(ft.get("y_tau_raw"))
            if oc is None:
                oc = _f(ft.get("total"))
            if oc is not None:
                out["y_tau_oc"] = oc
                out["predicted_score_tau_oc"] = oc
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
            "y_tau_oc",
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

    契约：``y_tau`` = OC 拟合（开→收），与训练 / 表列 / 定向同口径。
    剩余映射分（若有）放 ``y_tau_mapped``，不定向。
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
    # 显式写入的 y_tau：若同时有 y_tau_oc 且二者不同，视 y_tau 为 mapped
    y_tau_field = _f(item.get("y_tau"))

    y_tau_oc = _f(item.get("y_tau_oc"))
    if y_tau_oc is None:
        y_tau_oc = _f(item.get("predicted_score_tau_oc"))
    if y_tau_oc is None:
        ft = item.get("formula_terms_tau")
        if isinstance(ft, dict):
            y_tau_oc = _f(ft.get("y_tau_raw"))

    if y_tau_mapped is None and y_tau_field is not None:
        if y_tau_oc is None or abs(y_tau_field - y_tau_oc) < 1e-9:
            y_tau_mapped = y_tau_field
        else:
            y_tau_mapped = y_tau_field
    # 做T主口径：OC；无 OC 时回退 mapped / 字段
    y_tau = y_tau_oc if y_tau_oc is not None else (
        y_tau_field if y_tau_field is not None else y_tau_mapped
    )
    if y_tau_oc is None and y_tau is not None:
        y_tau_oc = y_tau

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
        for k in ("y_oo", "y_oc", "y_τc", "y_co", "ranking", "residual", "r_hat"):
            if _win.get(k) is not None:
                out[k] = _win[k]
    except Exception:  # noqa: BLE001
        logger.debug("stamp_window_scores failed", exc_info=True)
    try:
        from core.research.tc_ridge import pick_y_tc_hat, pick_y_tc_label, write_y_tc_hat, write_y_tc_label

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
        from core.research.t30_ridge import pick_y_t30_hat, write_y_t30_hat

        y_t30 = pick_y_t30_hat(item)
        if y_t30 is not None:
            write_y_t30_hat(out, y_t30)
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
        from core.research.t45_ridge import pick_y_t45_hat, write_y_t45_hat

        y_t45 = pick_y_t45_hat(item)
        if y_t45 is not None:
            write_y_t45_hat(out, y_t45)
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
        from core.research.t60_ridge import pick_y_t60_hat, write_y_t60_hat

        y_t60 = pick_y_t60_hat(item)
        if y_t60 is not None:
            write_y_t60_hat(out, y_t60)
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
        from core.research.t75_ridge import pick_y_t75_hat, write_y_t75_hat

        y_t75 = pick_y_t75_hat(item)
        if y_t75 is not None:
            write_y_t75_hat(out, y_t75)
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
        from core.research.t90_ridge import pick_y_t90_hat, write_y_t90_hat

        y_t90 = pick_y_t90_hat(item)
        if y_t90 is not None:
            write_y_t90_hat(out, y_t90)
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
    if y_tau_oc is not None:
        out["y_tau_oc"] = y_tau_oc
        out["predicted_score_tau_oc"] = y_tau_oc
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


def _y_path_missing_reason(scores: dict) -> str:
    """旧 dual_y path 闸文案（生产 close-band 不调用；库函数兜底）。"""
    return "dual_y：ŷ_hl 已下线"


def scores_from_ledger_row(row: Optional[dict]) -> Dict[str, Optional[float]]:
    if not isinstance(row, dict):
        return scores_from_item(None)
    # 账本字段名
    mapped = {
        "predicted_score_eod": row.get("yhat_eod") if row.get("yhat_eod") is not None else row.get("predicted_score_eod"),
        "predicted_score_tau": row.get("yhat_tau") if row.get("yhat_tau") is not None else row.get("predicted_score_tau"),
        "predicted_score": row.get("yhat") if row.get("yhat") is not None else row.get("predicted_score"),
        "predicted_score_on": row.get("yhat_on") if row.get("yhat_on") is not None else row.get("predicted_score_on"),
        "predicted_score_nowcast": row.get("yhat_nowcast")
        if row.get("yhat_nowcast") is not None
        else row.get("predicted_score_nowcast"),
        "predicted_score_path": row.get("yhat_path")
        if row.get("yhat_path") is not None
        else row.get("predicted_score_path"),
        "predicted_score_hl": row.get("yhat_hl")
        if row.get("yhat_hl") is not None
        else (row.get("predicted_score_hl") if row.get("predicted_score_hl") is not None else row.get("yhat_path")),
        "y_hl": row.get("y_hl") if row.get("y_hl") is not None else row.get("y_path"),
        "y_check": row.get("y_check"),
        "eod_trust": row.get("eod_trust"),
    }
    return scores_from_item(mapped)


def normalize_y_trade_enter(raw: Any) -> float:
    """|y_trade| 入场下限（收益百分点）；旧配置负值加载时取 abs。"""
    try:
        val = float(DEFAULT_TRADE_ENTER if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        val = DEFAULT_TRADE_ENTER
    return max(0.0, min(abs(val), 5.0))


def normalize_y_trade_floor(raw: Any) -> float:
    """别名：y_trade_floor → y_trade_enter。"""
    return normalize_y_trade_enter(raw)


def trade_mag_floor(cfg: dict) -> float:
    raw = cfg.get("y_trade_enter")
    if raw is None or raw == "":
        raw = cfg.get("y_trade_floor")
    return normalize_y_trade_enter(raw)


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    try:
        v = cfg.get(key)
        if v is None or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def side_tau_enter(cfg: dict, *, for_buy_then_sell: bool) -> float:
    """反T用 y_tau_enter_sell_then_buy，正T用 y_tau_enter_buy_then_sell；缺省回退 y_tau_enter。

    返回值 ≥0；0 表示关闭该侧 τ 入场闸。上限由 load_t0_rules 钳制，此处不截断以便直传 cfg。
    """
    base = _cfg_float(cfg, "y_tau_enter", DEFAULT_TAU_ENTER)
    key = "y_tau_enter_buy_then_sell" if for_buy_then_sell else "y_tau_enter_sell_then_buy"
    raw = _f(cfg.get(key))
    v = float(base if raw is None else raw)
    return max(0.0, v)


def side_hl_enter(cfg: dict, *, for_buy_then_sell: bool) -> float:
    """正/反 y_hl 入场；缺省回退 y_hl_enter（再缺则旧 y_path_enter / 对应侧 τ enter）。

    返回值 ≥0；0 表示关闭该侧 HL 入场闸。
    """
    tau_side = side_tau_enter(cfg, for_buy_then_sell=for_buy_then_sell)
    base = _f(cfg.get("y_hl_enter"))
    if base is None:
        base = _cfg_float(cfg, "y_path_enter", tau_side)
    key_new = "y_hl_enter_buy_then_sell" if for_buy_then_sell else "y_hl_enter_sell_then_buy"
    key_old = (
        "y_path_enter_buy_then_sell" if for_buy_then_sell else "y_path_enter_sell_then_buy"
    )
    raw = _f(cfg.get(key_new))
    if raw is None:
        raw = _f(cfg.get(key_old))
    v = float(base if raw is None else raw)
    return max(0.0, v)


def side_path_enter(cfg: dict, *, for_buy_then_sell: bool) -> float:
    """兼容旧名；等同 side_hl_enter。"""
    return side_hl_enter(cfg, for_buy_then_sell=for_buy_then_sell)


def enters_for_y_tau(cfg: dict, y_tau: float) -> Tuple[float, float, bool]:
    """按 y_τ 符号选门槛。Returns (tau_enter, path_enter, for_buy_then_sell)。y_τ=0 → 正T侧仅作占位。"""
    for_buy_then_sell = float(y_tau) >= 0
    return (
        side_tau_enter(cfg, for_buy_then_sell=for_buy_then_sell),
        side_path_enter(cfg, for_buy_then_sell=for_buy_then_sell),
        for_buy_then_sell,
    )


def t0_confidence_scale(scores: dict, cfg: dict) -> float:
    """v6 目标价不再随 ŷ 缩放；保留以免旧调用崩。"""
    _ = scores, cfg
    return 1.0


def scale_t0_ratio(base_ratio: float, scores: dict, cfg: dict) -> float:
    """兼容旧调用：动仓比例固定为基准，不再随 ŷ 缩放。"""
    _ = scores, cfg
    return max(0.05, min(1.0, float(base_ratio)))


def resolve_direction_y_tau(scores: Optional[dict]) -> Optional[float]:
    """做 T 定向 / |y_τ| 闸用的 ŷ：优先 OC 拟合（开→收），与训练·表列·组成合计同口径。

    ``y_tau`` / ``predicted_score_tau`` 在分钟时钟下可能是剩余映射后的值，
    与 OC 异号时会出现「表列负τ、成交却正T」；定向不得再用映射后分。
    无 OC 字段时回退 mapped ``y_tau``（旧快照）。
    """
    if not isinstance(scores, dict):
        return None
    for key in ("y_tau_oc", "predicted_score_tau_oc"):
        v = _f(scores.get(key))
        if v is not None:
            return v
    ft = scores.get("formula_terms_tau") or scores.get("score_formula_terms_tau")
    if isinstance(ft, dict):
        for key in ("y_tau_raw", "total"):
            v = _f(ft.get(key))
            if v is not None:
                return v
    for key in ("y_tau", "predicted_score_tau", "score_rem", "yhat_tau"):
        v = _f(scores.get(key))
        if v is not None:
            return v
    return None


def normalize_y_tau_map(raw: Any) -> str:
    mode = str(raw or Y_TAU_MAP_DEFAULT).strip().lower()
    aliases = {
        "follow": "trend",
        "momentum": "trend",
        "invert": "trend",
        "scalp": "trend",  # 语义修正后与 trend 等价
    }
    mode = aliases.get(mode, mode)
    if mode not in Y_TAU_MAP_CHOICES:
        mode = Y_TAU_MAP_DEFAULT
    return mode


def _tau_path_same_sign(
    y_tau: Optional[float],
    y_path: Optional[float],
    *,
    sign_eps: float = 1e-9,
) -> bool:
    """y_τ 与 y_hl 同号（均非零）。"""
    if y_tau is None or y_path is None:
        return False
    if abs(float(y_tau)) <= sign_eps or abs(float(y_path)) <= sign_eps:
        return False
    return (float(y_tau) > 0) == (float(y_path) > 0)


def _tau_path_enter_gate(
    y_tau: float,
    y_path: float,
    tau_enter: float,
    path_enter: float,
) -> Tuple[bool, Optional[str]]:
    """同号且各自过门槛：正侧 y>enter；负侧 y<-enter。"""
    if not _tau_path_same_sign(y_tau, y_path):
        return False, (
            f"dual_y：y_τ={y_tau:.3f}% 与 y_hl={y_path:.3f}% 异号跳过"
        )
    te, pe = float(tau_enter), float(path_enter)
    yt, yp = float(y_tau), float(y_path)
    if yt > 0:
        if yt <= te:
            return False, f"dual_y：y_τ={yt:.3f}%≤{te}% 未过门槛"
        if yp <= pe:
            return False, f"dual_y：y_hl={yp:.3f}%≤{pe}% 未过门槛"
        return True, None
    if yt < -te:
        if yp >= -pe:
            return False, f"dual_y：y_hl={yp:.3f}%≥-{pe}% 未过门槛"
        return True, None
    return False, f"dual_y：y_τ={yt:.3f}% 未过门槛"


def _tau_cc_for_sign_gate(
    y_tau: float,
    gap_pct: Optional[float],
) -> float:
    """把 OC 口径 y_τ 抬到昨收口径（features 对照；不再作选向闸）。"""
    from core.signal.dual_score.fusion import lift_tau_vs_prev_close

    lifted = lift_tau_vs_prev_close(float(y_tau), gap_pct)
    return float(lifted) if lifted is not None else float(y_tau)


def _strong_head_tau_sign_gate(
    y_head: float,
    y_tau: float,
    gate_pct: float,
    head_key: str,
    *,
    sign_eps: float = 1e-9,
    tau_label: str = "y_τ",
) -> Tuple[bool, Optional[str]]:
    """|y_head|>gate 时要求与对照 τ 同号（均非零）。"""
    g = float(gate_pct)
    yh, yt = float(y_head), float(y_tau)
    if abs(yh) <= g:
        return True, None
    if abs(yt) <= sign_eps:
        return False, (
            f"dual_y：|{head_key}|={abs(yh):.3f}%>{g}% 但 {tau_label}={yt:.3f}%≈0 异号跳过"
        )
    if (yh > 0) == (yt > 0):
        return True, None
    return False, (
        f"dual_y：|{head_key}|={abs(yh):.3f}%>{g}% 且 "
        f"{head_key}={yh:.3f}% 与 {tau_label}={yt:.3f}% 异号跳过"
    )


def _path_direction_sign(y_path: Optional[float], path_enter: float) -> int:
    if y_path is None:
        return 0
    thr = float(path_enter)
    if y_path > thr:
        return 1
    if y_path < -thr:
        return -1
    return 0


def _gap_tier_direction_override(
    gap_pct: Optional[float],
    tau_sign: int,
    cfg: dict,
) -> Optional[Dict[str, Any]]:
    """大缺口分档：skip_opposite 跳过与大缺口均值回归相悖的 τ 方向。"""
    mode = str(cfg.get("y_gap_tier_mode") or "skip_opposite").strip().lower()
    if mode in {"", "off", "none", "false", "0"}:
        return None
    tier = _cfg_float(cfg, "y_gap_tier_pct", DEFAULT_GAP_TIER_PCT)
    if gap_pct is None or abs(float(gap_pct)) < tier:
        return None
    g = float(gap_pct)
    tau_dir = direction_from_y_tau_sign(tau_sign, cfg)
    if mode == "revert":
        want = "sell_then_buy" if g > 0 else "buy_then_sell"
        if tau_dir == want:
            return None
        return {
            "skip": True,
            "direction": None,
            "reason": (
                f"dual_y[gap_tier/revert]：gap={g:+.2f}%≥{tier}% 期望{t0_dir_label(want)}，"
                f"τ→{t0_dir_label(tau_dir)} 跳过"
            ),
        }
    # skip_opposite：大缺口日禁止「顺势」映射（高开跳过正T）；低开+反T 恒放行
    if g >= tier and tau_dir == "buy_then_sell":
        return {
            "skip": True,
            "direction": None,
            "reason": (
                f"dual_y[gap_tier]：高开{g:+.2f}%≥{tier}% 跳过正T（τ顺势映射，追高风险）"
            ),
        }
    return None


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
        from core.research.path_panel import clear_y_hl

        clear_y_hl(item)
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
    """即时补 ŷ_r（与 ŷ_τ 同 X）；缺模型/缺前缀则不出分。仅展示，不进 ĉ / 选腿。"""
    if not isinstance(item, dict):
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
        from core.research.tc_ridge import (
            explain_tc_prediction,
            load_tc_model,
            predict_tc_from_features,
            write_y_tc_hat,
        )

        model = load_tc_model()
        if model is not None:
            y_r = predict_tc_from_features(feats, model_doc=model)
            if y_r is not None:
                write_y_tc_hat(item, float(y_r), model_doc=model)
                expl = explain_tc_prediction(feats, model_doc=model)
                if expl:
                    item["formula_terms_r"] = expl
                    item["score_formula_terms_r"] = expl
        from core.signal.yhat_windows import stamp_remaining_y_τc

        stamp_remaining_y_τc(item)
    except Exception:  # noqa: BLE001
        logger.debug("attach y_r predict failed", exc_info=True)


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


def _predict_horizon_hat(
    head: str,
    feats: dict,
    *,
    load_ridge,
    predict_ridge,
    load_tree,
    predict_tree,
) -> tuple:
    """返回 (p_up, model_doc, source)。tree 优先于 ridge（backend=tree 且有模型）。"""
    from core.research.horizon_tree import (
        HORIZON_PROB_BACKEND_TREE,
        current_horizon_prob_backend,
    )

    if current_horizon_prob_backend() == HORIZON_PROB_BACKEND_TREE:
        try:
            tree_doc = load_tree()
        except Exception:  # noqa: BLE001
            tree_doc = None
        if tree_doc is not None:
            try:
                y_hat = predict_tree(feats, model_doc=tree_doc)
            except Exception:  # noqa: BLE001
                y_hat = None
            if y_hat is not None:
                return float(y_hat), tree_doc, "tree"
    model = load_ridge()
    if model is None:
        return None, None, None
    y_hat = predict_ridge(feats, model_doc=model)
    if y_hat is None:
        return None, model, None
    return float(y_hat), model, "ridge"


def _attach_y_t30_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ30（ŷ_τ X + 序列特征）；τ⊕35 越界 / 缺模型则不出分。不进 C_τ。"""
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
        from core.research.t30_ridge import (
            explain_t30_prediction,
            load_t30_model,
            predict_t30_from_features,
            write_y_t30_hat,
        )
        from core.research.t30_tree import (
            load_t30_tree_model,
            predict_t30_tree_from_features,
        )
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
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T30_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t30",
            feats,
            load_ridge=load_t30_model,
            predict_ridge=predict_t30_from_features,
            load_tree=load_t30_tree_model,
            predict_tree=predict_t30_tree_from_features,
        )
        if y_hat is None:
            return
        write_y_t30_hat(item, float(y_hat))
        if src:
            item["y_τ30_source"] = src
            item["y_t30_source"] = src
        if src == "ridge":
            expl = explain_t30_prediction(feats, model_doc=model)
            if expl:
                item["formula_terms_t30"] = expl
                item["score_formula_terms_t30"] = expl
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t30 predict failed", exc_info=True)


def _attach_y_t45_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ45（ŷ_τ X + 序列特征）；τ⊕50 越界 / 缺模型则不出分。不进 C_τ。"""
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
        from core.research.t45_ridge import (
            explain_t45_prediction,
            load_t45_model,
            predict_t45_from_features,
            write_y_t45_hat,
        )
        from core.research.t45_tree import (
            load_t45_tree_model,
            predict_t45_tree_from_features,
        )
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
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T45_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t45",
            feats,
            load_ridge=load_t45_model,
            predict_ridge=predict_t45_from_features,
            load_tree=load_t45_tree_model,
            predict_tree=predict_t45_tree_from_features,
        )
        if y_hat is None:
            return
        write_y_t45_hat(item, float(y_hat))
        if src:
            item["y_τ45_source"] = src
            item["y_t45_source"] = src
        if src == "ridge":
            expl = explain_t45_prediction(feats, model_doc=model)
            if expl:
                item["formula_terms_t45"] = expl
                item["score_formula_terms_t45"] = expl
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t45 predict failed", exc_info=True)


def _attach_y_t60_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ60（ŷ_τ X + 序列特征）；τ⊕65 越界 / 缺模型则不出分。不进 C_τ。"""
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
        from core.research.t60_ridge import (
            explain_t60_prediction,
            load_t60_model,
            predict_t60_from_features,
            write_y_t60_hat,
        )
        from core.research.t60_tree import (
            load_t60_tree_model,
            predict_t60_tree_from_features,
        )
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
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T60_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t60",
            feats,
            load_ridge=load_t60_model,
            predict_ridge=predict_t60_from_features,
            load_tree=load_t60_tree_model,
            predict_tree=predict_t60_tree_from_features,
        )
        if y_hat is None:
            return
        write_y_t60_hat(item, float(y_hat))
        if src:
            item["y_τ60_source"] = src
            item["y_t60_source"] = src
        if src == "ridge":
            expl = explain_t60_prediction(feats, model_doc=model)
            if expl:
                item["formula_terms_t60"] = expl
                item["score_formula_terms_t60"] = expl
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t60 predict failed", exc_info=True)


def _attach_y_t75_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ75（ŷ_τ X + 序列特征）；τ⊕80 越界 / 缺模型则不出分。不进 C_τ。"""
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
        from core.research.t75_ridge import (
            explain_t75_prediction,
            load_t75_model,
            predict_t75_from_features,
            write_y_t75_hat,
        )
        from core.research.t75_tree import (
            load_t75_tree_model,
            predict_t75_tree_from_features,
        )
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
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T75_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t75",
            feats,
            load_ridge=load_t75_model,
            predict_ridge=predict_t75_from_features,
            load_tree=load_t75_tree_model,
            predict_tree=predict_t75_tree_from_features,
        )
        if y_hat is None:
            return
        write_y_t75_hat(item, float(y_hat))
        if src:
            item["y_τ75_source"] = src
            item["y_t75_source"] = src
        if src == "ridge":
            expl = explain_t75_prediction(feats, model_doc=model)
            if expl:
                item["formula_terms_t75"] = expl
                item["score_formula_terms_t75"] = expl
    except Exception:  # noqa: BLE001
        logger.debug("attach y_t75 predict failed", exc_info=True)


def _attach_y_t90_to_item(
    item: dict,
    *,
    allow_open_z: bool = True,
    hist_bars: Optional[Sequence[dict]] = None,
) -> None:
    """即时补 ŷ_τ90（ŷ_τ X + 序列特征）；τ⊕95 越界 / 缺模型则不出分。不进 C_τ。"""
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
        from core.research.t90_ridge import (
            explain_t90_prediction,
            load_t90_model,
            predict_t90_from_features,
            write_y_t90_hat,
        )
        from core.research.t90_tree import (
            load_t90_tree_model,
            predict_t90_tree_from_features,
        )
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
        ft = item.get("features_tau")
        if isinstance(ft, dict):
            for k in list(T90_SEQ_FEATURES) + ["tau_lag1", "tau_ma5"]:
                if feats.get(k) is not None:
                    ft[k] = feats[k]
        y_hat, model, src = _predict_horizon_hat(
            "t90",
            feats,
            load_ridge=load_t90_model,
            predict_ridge=predict_t90_from_features,
            load_tree=load_t90_tree_model,
            predict_tree=predict_t90_tree_from_features,
        )
        if y_hat is None:
            return
        write_y_t90_hat(item, float(y_hat))
        if src:
            item["y_τ90_source"] = src
            item["y_t90_source"] = src
        if src == "ridge":
            expl = explain_t90_prediction(feats, model_doc=model)
            if expl:
                item["formula_terms_t90"] = expl
                item["score_formula_terms_t90"] = expl
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
    """前缀小包注入后按 features_tau 重算 ŷ_oc 组成。

    ``resolve_scores`` 常已写上盘中 as_of / 开盘→τ，但注入还会改截面
    （同业缺口广度、HL 位置、近15m、板块中位）。不能因组成表已有
    ``ret_open_to_tau`` 就跳过，否则调仓与做 T 同钟 ŷ_oc 会差一截小因子。
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
    try:
        from core.research.tau_ridge import explain_tau_prediction, predict_tau_from_features

        yhat = predict_tau_from_features(feats)
        expl = explain_tau_prediction(feats)
    except Exception:  # noqa: BLE001
        logger.debug("refresh tau oc from prefix pack failed", exc_info=True)
        return
    if yhat is not None:
        yv = float(yhat)
        item["y_oc"] = yv
        item["predicted_score_oc"] = yv
        item["y_tau_oc"] = yv
        item["predicted_score_tau_oc"] = yv
        item["predicted_score_tau"] = yv
        item["predicted_score_rem"] = yv
        item["score_rem"] = yv
        item["y_tau"] = yv
    if isinstance(expl, dict):
        prev = expl_now if isinstance(expl_now, dict) else {}
        out = dict(prev)
        out.update(expl)
        if yhat is not None:
            out["y_tau"] = float(yhat)
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
    from core.research.path_panel import attach_path_minute_feats
    from core.signal.minute_tau_feats import (
        apply_sector_ret_cs,
        apply_sector_ret_last_30m_cs,
        apply_sector_ret_last_45m_cs,
        apply_sector_ret_last_60m_cs,
        apply_sector_ret_last_75m_cs,
        apply_sector_ret_last_90m_cs,
        extract_horizon_seq_packs,
        resolve_sector_ret_last_30m,
        resolve_sector_ret_last_45m,
        resolve_sector_ret_last_60m,
        resolve_sector_ret_last_75m,
        resolve_sector_ret_last_90m,
        resolve_sector_ret_to_tau,
    )

    out = attach_path_minute_feats(
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


def predict_path_from_prefix_minutes(
    score_snap: Optional[dict],
    minute_prefix: Sequence[dict],
    *,
    day_bar: Optional[dict] = None,
    hist_bars: Optional[Sequence[dict]] = None,
) -> Optional[float]:
    """用开盘 Z + **已到达前缀**分钟小包即时估 ŷ_hl（确认根因果，无全日前视）。"""
    if not isinstance(score_snap, dict):
        return None
    bars = [b for b in (minute_prefix or []) if isinstance(b, dict)]
    if len(bars) < 1:
        return None
    try:
        from core.research.path_panel import path_features_from_open_row
        from core.research.path_ridge import load_path_model, predict_path_from_features
    except Exception:  # noqa: BLE001
        logger.debug("predict_path_from_prefix imports failed", exc_info=True)
        return None
    model = load_path_model()
    if model is None:
        return None
    feats = _open_z_feats_from_snap(score_snap)
    day = day_bar if isinstance(day_bar, dict) else {}
    trade_day = str(day.get("date") or (bars[0] or {}).get("date") or "")[:10]
    open_px = _f(day.get("open")) or _f(feats.get("open")) or _f((bars[0] or {}).get("open"))
    prev_c = _f(day.get("prev_close")) or _f(feats.get("prev_close"))
    tau_hm = prefix_tau_hm_from_bars(bars)
    try:
        feats = _attach_prefix_minute_sector_feats(
            feats,
            minute_bars=bars,
            trade_date=trade_day,
            open_px=open_px,
            prev_close=prev_c,
            tau_hm=tau_hm,
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach_path_minute_feats in prefix path failed", exc_info=True)
        return None
    if feats.get("ret_open_to_tau") is None:
        return None
    hist = [b for b in (hist_bars or []) if isinstance(b, dict)]
    prev = hist[-1] if hist else None
    path_feats = path_features_from_open_row(feats, hist=hist, prev_bar=prev)
    try:
        from core.research.path_panel import attach_path_lag_features

        code = ""
        if isinstance(score_snap, dict):
            code = str(score_snap.get("stock_code") or score_snap.get("code") or "").strip()
        path_feats = attach_path_lag_features(
            path_feats,
            hist_bars=hist,
            asof_date=trade_day,
            stock_code=code,
        )
    except Exception:  # noqa: BLE001
        logger.debug("attach path lag in prefix path failed", exc_info=True)
    try:
        return predict_path_from_features(path_feats, model_doc=model)
    except Exception:  # noqa: BLE001
        logger.debug("predict_path_from_prefix failed", exc_info=True)
        return None


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
        from core.research.tau_ridge import load_tau_model, predict_tau_from_features
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
        from core.research.path_panel import clear_y_hl

        out = dict(fallback)
        out["_minute_data_missing"] = True
        out["_score_source"] = "prefix_minute_missing"
        clear_y_hl(out)
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
    cache_key = (
        raw,
        dkey,
        str(hm or ""),
        len(prefix),
        1 if fuse_intraday else 0,
        1 if include_tau_horizons else 0,
        id(open_snap) if isinstance(open_snap, dict) else 0,
        id(tau_pool_day) if isinstance(tau_pool_day, dict) else 0,
    )
    cached = _PREFIX_RESCORE_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    sret = None
    sc: Dict[str, Any] = {}
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

    if scores_have_any(sc):
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
    # 禁止 silently 用开盘 Z 冒充盘中 path
    out["_minute_prefix"] = prefix
    _attach_y_path_to_item(
        out,
        hist_bars=hist_bars,
        allow_open_z=False,
        include_tau_horizons=include_tau_horizons,
    )
    out.pop("_minute_prefix", None)
    out["_score_source"] = src
    out["_score_prefix_hm"] = hm
    out["_score_prefix_bars"] = len(prefix)
    out.pop("_minute_data_missing", None)
    if len(_PREFIX_RESCORE_CACHE) >= _PREFIX_RESCORE_CACHE_MAX:
        _PREFIX_RESCORE_CACHE.clear()
    _PREFIX_RESCORE_CACHE[cache_key] = out
    return copy.deepcopy(out)


def attach_portrait_y_path(
    day: Optional[dict],
    score_snap: Optional[dict],
    *,
    minute_bars: Optional[Sequence[dict]] = None,
    day_bar: Optional[dict] = None,
    hist_bars: Optional[Sequence[dict]] = None,
    tau_hm: str = "10:30",
    prefix_bars: Optional[int] = None,
) -> Dict[str, Any]:
    """兼容入口：转 ``attach_portrait_dual_scores``。"""
    return attach_portrait_dual_scores(
        day,
        score_snap,
        minute_bars=minute_bars,
        day_bar=day_bar,
        hist_bars=hist_bars,
        tau_hm=tau_hm,
        prefix_bars=prefix_bars,
    )


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
        y_tau_p = _f(snap.get("y_tau_oc"))
        if y_tau_p is None:
            y_tau_p = _f(snap.get("y_tau"))
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


def direction_from_y_tau_sign(tau_sign: int, cfg: dict) -> str:
    """由 y_τ 符号（±1）与 y_tau_map 解析 sell_then_buy / buy_then_sell。

    y_τ>0 → buy_then_sell（正T）；y_τ<0 → sell_then_buy（反T）。
    scalp 与 trend 同义，scalp 仅兼容。
    """
    mode = normalize_y_tau_map(cfg.get("y_tau_map"))
    if mode == "fixed_sell_then_buy":
        return "sell_then_buy"
    if mode == "fixed_buy_then_sell":
        return "buy_then_sell"
    # scalp / trend：符号定方向
    return "buy_then_sell" if tau_sign > 0 else "sell_then_buy"


def resolve_dual_y_direction(
    *,
    scores: dict,
    cfg: dict,
    cash: float,
    shares: float,
) -> Dict[str, Any]:
    """dual_y 库函数选向（v6 生产走 close-band，不调用本函数）。

    1. 有 y_τ（方向锚）
    2. |y_τ|≥侧向 y_tau_enter（path 链写死关闭）
    3. 可选 gap_tier 跳过
    4. 正 T 须有现金+仓
    通过后 y_τ（OC）映射正/反 T。y_trade / y_eod / nowcast 不参与。
    """
    tau_enter = _cfg_float(cfg, "y_tau_enter", DEFAULT_TAU_ENTER)
    # 主仓 τ 冻结降级回注：与 buy 腿 effective floor 对齐
    eff_enter = _f(cfg.get("y_tau_enter_effective"))
    if eff_enter is not None and eff_enter < tau_enter:
        tau_enter = max(0.0, float(eff_enter))
    # 旧键 y_tau_enter_strong：若更高则并入入场闸（双闸已合并）
    strong_legacy = _f(cfg.get("y_tau_enter_strong"))
    if strong_legacy is not None and strong_legacy > tau_enter:
        tau_enter = strong_legacy

    tau_enter_sell_then_buy = side_tau_enter(
        {**cfg, "y_tau_enter": tau_enter}, for_buy_then_sell=False
    )
    tau_enter_buy_then_sell = side_tau_enter(
        {**cfg, "y_tau_enter": tau_enter}, for_buy_then_sell=True
    )
    path_enter = _cfg_float(cfg, "y_hl_enter", _cfg_float(cfg, "y_path_enter", tau_enter))
    path_enter_sell_then_buy = side_path_enter(
        {**cfg, "y_hl_enter": path_enter, "y_tau_enter": tau_enter},
        for_buy_then_sell=False,
    )
    path_enter_buy_then_sell = side_path_enter(
        {**cfg, "y_hl_enter": path_enter, "y_tau_enter": tau_enter},
        for_buy_then_sell=True,
    )

    tau_map = normalize_y_tau_map(cfg.get("y_tau_map"))

    y_eod = _f(scores.get("y_eod"))
    y_tau = resolve_direction_y_tau(scores)
    # mapped：显式字段或昨收口径 τ_cc；禁止用 predicted 静默覆盖（脏簿应交由上游归一）
    y_tau_mapped = _f(scores.get("y_tau_mapped"))
    if y_tau_mapped is None:
        y_tau_mapped = _f(scores.get("predicted_score_tau_cc"))
    if y_tau_mapped is None:
        y_tau_mapped = _f(scores.get("predicted_score_blend_tau_cc"))
    y_trade = _f(scores.get("y_trade"))
    residual = None
    try:
        from core.signal.yhat_windows import t0_residual_pct

        residual = t0_residual_pct(scores)
    except Exception:  # noqa: BLE001
        residual = None
    from core.signal.yhat_windows import pick_y_co

    y_path = None  # ŷ_hl 已下线
    y_co = pick_y_co(scores)
    y_check = scores.get("y_check")
    gap_pct = _f(scores.get("gap_pct"))
    if gap_pct is None and isinstance(scores.get("features_tau"), dict):
        gap_pct = _f(scores["features_tau"].get("gap_pct"))
    y_tau_cc = (
        _tau_cc_for_sign_gate(float(y_tau), gap_pct) if y_tau is not None else None
    )

    use_path = False
    path_required = False

    features = {
        "y_eod": y_eod,
        "y_tau": y_tau,
        "y_tau_oc": y_tau,
        "y_tau_cc": y_tau_cc,
        "y_tau_mapped": y_tau_mapped,
        "dual_score_window": scores.get("dual_score_window"),
        "y_trade": y_trade,
        "residual": residual if residual is not None else y_trade,
        "y_co": y_co,
        "y_hl": y_path,
        "y_check": y_check,
        "gap_pct": gap_pct,
        "y_tau_enter": tau_enter,
        "y_tau_enter_sell_then_buy": tau_enter_sell_then_buy,
        "y_tau_enter_buy_then_sell": tau_enter_buy_then_sell,
        "y_hl_enter": path_enter,
        "y_hl_enter_sell_then_buy": path_enter_sell_then_buy,
        "y_hl_enter_buy_then_sell": path_enter_buy_then_sell,
    }

    if y_tau is None:
        return {
            "direction": None,
            "skip": True,
            "direction_score": None,
            "direction_reason": "dual_y：缺 y_τ，无法定盘中方向",
            "features": features,
            "signal_skip": True,
        }

    side_tau, side_path, for_buy_then_sell = enters_for_y_tau(
        {
            **cfg,
            "y_tau_enter": tau_enter,
            "y_tau_enter_sell_then_buy": tau_enter_sell_then_buy,
            "y_tau_enter_buy_then_sell": tau_enter_buy_then_sell,
            "y_hl_enter": path_enter,
            "y_hl_enter_sell_then_buy": path_enter_sell_then_buy,
            "y_hl_enter_buy_then_sell": path_enter_buy_then_sell,
        },
        y_tau,
    )
    side_tag = "正T" if for_buy_then_sell else "反T"

    if use_path and y_path is not None:
        enter_ok, enter_reason = _tau_path_enter_gate(
            y_tau, y_path, side_tau, side_path
        )
        if not enter_ok:
            return {
                "direction": None,
                "skip": True,
                "direction_score": y_tau,
                "direction_reason": enter_reason or f"dual_y：y_τ/y_path 未过{side_tag}门槛",
                "features": features,
                "signal_skip": True,
            }
    elif y_tau > 0:
        if y_tau <= side_tau:
            return {
                "direction": None,
                "skip": True,
                "direction_score": y_tau,
                "direction_reason": (
                    f"dual_y：y_τ={y_tau:.3f}%≤{side_tau}%（{side_tag}入场）跳过"
                ),
                "features": features,
                "signal_skip": True,
            }
    elif y_tau < 0:
        if y_tau >= -side_tau:
            return {
                "direction": None,
                "skip": True,
                "direction_score": y_tau,
                "direction_reason": (
                    f"dual_y：y_τ={y_tau:.3f}%≥-{side_tau}%（{side_tag}入场）跳过"
                ),
                "features": features,
                "signal_skip": True,
            }
    else:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": "dual_y：y_τ=0 横盘跳过",
            "features": features,
            "signal_skip": True,
        }

    if use_path and y_path is None and path_required:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": _y_path_missing_reason(scores),
            "features": features,
            "signal_skip": True,
        }

    main = 1 if y_tau > 0 else -1
    direction = direction_from_y_tau_sign(main, cfg)

    gap_block = _gap_tier_direction_override(gap_pct, main, cfg)
    if gap_block and gap_block.get("skip"):
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": str(gap_block.get("reason") or "大缺口分档跳过"),
            "features": features,
            "signal_skip": True,
        }

    if direction == "buy_then_sell" and not (cash > 0 and shares > 0):
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": (
                f"dual_y[{tau_map}]：y_τ={y_tau:.3f}%→{t0_dir_label(direction)} 但缺现金/仓"
            ),
            "features": features,
            "signal_skip": True,
        }

    path_note = ""
    if use_path and y_path is not None and _tau_path_same_sign(y_tau, y_path):
        path_note = (
            f"；y_hl={y_path:.3f}%同号过闸"
            f"({side_tag} τ>{side_tau:.3f}%,hl>{side_path:.3f}%)"
        )

    return {
        "direction": direction,
        "skip": False,
        "direction_score": y_tau,
        "direction_reason": (
            f"dual_y[{tau_map}]：y_τ={y_tau:.3f}%→{t0_dir_label(direction)}"
            + path_note
        ),
        "features": {**features, "y_tau_map": tau_map},
        "signal_skip": False,
    }


def resolve_cover_policy(
    *,
    scores: dict,
    direction: Optional[str],
    cfg: dict,
) -> Dict[str, Any]:
    """尾盘回补策略。

    - **反T**：未触达买回则收盘强买（表单「当日回补」已下线，生产常开）。
      强买按**账户余额**（开盘现金+当日累计）判断是否买得起；不够则
      ``abandon_cover_cash``。不再要求卖出净得自给自足。
    - **正T**：未卖回旧仓则收盘强制卖。
    """
    if direction == "sell_then_buy":
        if bool(cfg.get("must_cover_same_day")):
            return {
                "must_cover": True,
                "reason": "反T表单强制当日回补（买回旧仓）",
                "allow_overnight": False,
            }
        return {
            "must_cover": False,
            "reason": "反T未触达买回则放弃回补（减仓落袋）",
            "allow_overnight": True,
        }

    if bool(cfg.get("must_cover_same_day")):
        return {
            "must_cover": True,
            "reason": "表单强制当日回补",
            "allow_overnight": False,
        }

    from core.signal.yhat_windows import pick_y_co

    y_on = pick_y_co(scores)
    y_trade = _f(scores.get("y_trade"))
    trade_floor = trade_mag_floor(cfg)
    on_risk = _cfg_float(cfg, "y_on_risk", DEFAULT_ON_RISK)
    on_allow = _cfg_float(cfg, "y_on_allow", DEFAULT_ON_ALLOW)

    if y_trade is not None and abs(y_trade) < trade_floor:
        return {
            "must_cover": True,
            "reason": f"|y_trade|={abs(y_trade):.3f}%<{trade_floor}%强制回补",
            "allow_overnight": False,
        }

    if y_on is None:
        return {
            "must_cover": True,
            "reason": "缺 y_on，默认强制回补",
            "allow_overnight": False,
        }

    if abs(y_on) < on_allow:
        # 中等隔夜预期：仍强制回补（稳健）
        if abs(y_on) >= on_risk:
            return {
                "must_cover": True,
                "reason": f"|y_on|={abs(y_on):.3f}≥risk{on_risk}强制回补",
                "allow_overnight": False,
            }
        return {
            "must_cover": True,
            "reason": f"|y_on|={abs(y_on):.3f}%<allow{on_allow}%默认回补",
            "allow_overnight": False,
        }

    # |y_on| 很大：仅正T在 y_on 强烈看涨时允许隔夜多头敞口
    if direction == "buy_then_sell" and y_on >= on_allow:
        return {
            "must_cover": False,
            "reason": f"y_on={y_on:.3f}%支持正T隔夜多头",
            "allow_overnight": True,
        }

    return {
        "must_cover": True,
        "reason": f"y_on={y_on:.3f}%与敞口方向不一致，强制回补",
        "allow_overnight": False,
    }


def load_scores_for_code_date(code: str, as_of: str) -> Dict[str, Optional[float]]:
    """从 score_ledger 取某日某票分数；没有则空（仅兜底）。"""
    try:
        from core.score_ledger import load_ledger

        led = load_ledger(as_of)
        rows = led.get("rows") or []
        key = str(code or "").strip()
        for r in rows:
            if not isinstance(r, dict):
                continue
            rc = str(r.get("stock_code") or r.get("code") or "").strip()
            if rc == key:
                return scores_from_ledger_row(r)
    except Exception:  # noqa: BLE001
        logger.debug("load_scores_for_code_date failed", exc_info=True)
    return scores_from_item(None)


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
        from core.research.tau_ridge import load_tau_model

        tau = load_tau_model()
    except Exception:  # noqa: BLE001
        logger.debug("load tau model failed", exc_info=True)
    # 分组 return_model 已退役
    cluster: Dict[str, Any] = {}
    global_rm = None
    try:
        from core.signal.return_score_store import load_return_model

        global_rm, _meta = load_return_model(prefer_active=True)
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


def clear_score_model_cache() -> None:
    """测试 / 热更模型后清空缓存。"""
    global _CS_UNIVERSE_MEMO
    _MODEL_CACHE.clear()
    _TAU_XS_DAY_CACHE.clear()
    _EOD_ITEM_CACHE.clear()
    _FUND_CACHE.clear()
    _PREFIX_RESCORE_CACHE.clear()
    _CS_UNIVERSE_MEMO = None


def seed_tau_cross_section_day(day_key: str, pool_day: Optional[dict]) -> None:
    """把当日开盘缺口截面写入缓存，避免单票兜底退化成空池。"""
    dkey = str(day_key or "")[:10]
    if len(dkey) < 10 or not isinstance(pool_day, dict) or not pool_day:
        return
    if pool_day.get("sector_gap_breadth") is None and not pool_day.get("pool_gaps"):
        return
    _TAU_XS_DAY_CACHE[dkey] = dict(pool_day)


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
        # EOD 因子窗与刷簿 score_stock(limit=40) 对齐；τ/ATR 仍用更长 hist
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
            if open_feats:
                item["features_co"] = open_feats
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
        _attach_y_path_to_item(item, hist_bars=hist)
        item.pop("_minute_prefix", None)
        try:
            from core.signal.dual_score import align_trade_score_fields

            # 与持仓/观察表同源；勿 refresh_window（历史日须保留 fuse_intraday 窗）
            align_trade_score_fields(item, write_score=False, refresh_window=False)
        except Exception:  # noqa: BLE001
            logger.debug("align_trade_score_fields in compute failed", exc_info=True)
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
        from core.event_prior import get_event_prior_cfg

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


def active_book_codes_for_tau_pool(*, cap: int = 120) -> List[str]:
    """分池簿已停用：不再提供刷簿宇宙。"""
    _ = cap
    return []


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
    bars_map = load_bars_by_code_for_tau_pool(codes, limit=limit)
    overlay = _bars_map_from_holding_bars(hist_bars_by_code, day_bars_by_code)
    if overlay:
        bars_map.update(overlay)
    if not bars_map:
        return {}
    return build_tau_pool_by_date(bars_map)


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
            from core.event_prior import get_event_prior_cfg

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


def compute_scores_live(
    code: str,
    *,
    bars: Optional[Sequence[dict]] = None,
    quote: Optional[dict] = None,
    fuse_intraday: bool = True,
) -> Dict[str, Optional[float]]:
    """纸面/盘中：优先用已有日线 PIT 算；否则 ``score_stock``；再否则空。"""
    raw = str(code or "").strip()
    if not raw:
        return scores_from_item(None)

    hist_list = [b for b in (bars or []) if isinstance(b, dict)]
    if len(hist_list) >= _MIN_HIST_BARS + 1:
        day = hist_list[-1]
        hist = hist_list[:-1]
        q = quote if isinstance(quote, dict) else open_decision_quote(day, hist[-1], code=raw)
        sc = compute_scores_from_bars(
            raw,
            hist,
            day_bar=day,
            quote=q,
            fuse_intraday=fuse_intraday,
        )
        if scores_have_any(sc):
            return sc

    try:
        from core.signal.score_stock import score_stock

        item = score_stock(
            raw,
            quote=quote if isinstance(quote, dict) else None,
            skip_sentiment=True,
            skip_fundamentals=True,
            quote_timeout=8.0,
        )
        if isinstance(item, dict) and item.get("success") is not False:
            out = scores_from_item(item)
            if scores_have_any(out):
                out["_score_source"] = "score_stock"
                return out
    except Exception:  # noqa: BLE001
        logger.debug("compute_scores_live score_stock failed", exc_info=True)
    return scores_from_item(None)


def _scores_from_live_book(code: str) -> Dict[str, Optional[float]]:
    """分池簿已停用：不再从簿取分。"""
    _ = code
    return scores_from_item(None)


def resolve_y_score_source(cfg: Optional[dict] = None) -> str:
    raw = str((cfg or {}).get("y_score_source") or DEFAULT_Y_SCORE_SOURCE).strip().lower()
    if raw in {"book", "cluster", "cluster_book"}:
        return "live_book"
    if raw in {"ledger", "score_ledger", "freeze"}:
        return "ledger"
    if raw in {"compute", "pit", "live", "realtime", "on_the_fly"}:
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
    """分数账本 as_of：优先 hist 末日（T−1），避免单日兜底误用当日 ledger。"""
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
    """按 ``source`` 解析 dual_y 分数；默认即时算。

    ``compute`` 失败时仅回退 live 簿（不读冻结账本，避免半日污染快照）。
    ``use_minute_tau=False``：做 T 开盘预计算强制开盘信息集。
    ``minute_tau_hm``：确认根因果重算时传入前缀末根钟。
    """
    raw = str(code or "").strip()
    if not raw:
        return scores_from_item(None)
    src = resolve_y_score_source({"y_score_source": source})

    if src == "compute":
        hist = list(hist_bars or [])
        day = day_bar if isinstance(day_bar, dict) else None
        if day is None and hist:
            if len(hist) >= _MIN_HIST_BARS + 1:
                day = hist[-1]
                hist = hist[:-1]
        sc = compute_scores_from_bars(
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
        if scores_have_any(sc):
            return sc
        if not allow_fallback:
            return sc
        return _scores_from_live_book(raw)

    if src == "live_book":
        sc = _scores_from_live_book(raw)
        if scores_have_any(sc) or not allow_fallback:
            return sc
        return resolve_scores_for_code(
            raw,
            hist_bars=hist_bars,
            day_bar=day_bar,
            quote=quote,
            as_of=as_of,
            source="compute",
            fuse_intraday=fuse_intraday,
            allow_fallback=False,
            pool_gaps=pool_gaps,
            sector_gap_breadth=sector_gap_breadth,
            sector_gap_median=sector_gap_median,
            minute_bars=minute_bars,
            sector_ret_to_tau=sector_ret_to_tau,
            use_minute_tau=use_minute_tau,
            minute_tau_hm=minute_tau_hm,
        )

    # ledger（显式对照 / 旧路径）
    day_key = str(as_of or (day_bar or {}).get("date") or "")[:10]
    sc = load_scores_for_code_date(raw, day_key) if day_key else scores_from_item(None)
    if scores_have_any(sc):
        sc = dict(sc)
        sc["_score_source"] = "ledger"
        return sc
    if allow_fallback:
        return resolve_scores_for_code(
            raw,
            hist_bars=hist_bars,
            day_bar=day_bar,
            quote=quote,
            as_of=as_of,
            source="compute",
            fuse_intraday=fuse_intraday,
            allow_fallback=False,
            pool_gaps=pool_gaps,
            sector_gap_breadth=sector_gap_breadth,
            sector_gap_median=sector_gap_median,
            minute_bars=minute_bars,
            sector_ret_to_tau=sector_ret_to_tau,
            use_minute_tau=use_minute_tau,
            minute_tau_hm=minute_tau_hm,
        )
    return sc


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
    ``prefer_live_book=True`` 仅兼容旧调用（等价 source=live_book）。
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

    if source is None:
        src = "live_book" if prefer_live_book else DEFAULT_Y_SCORE_SOURCE
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
        computed = compute_scores_map_from_bars(
            specs, fuse_intraday=fuse, use_minute_tau=False
        )
        out.update(computed)
        if not allow_fallback:
            return out
        for code in want:
            if code in out:
                continue
            sc = _scores_from_live_book(code)
            if scores_have_any(sc):
                out[code] = sc
        return out

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
