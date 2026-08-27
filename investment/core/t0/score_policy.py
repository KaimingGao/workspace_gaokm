"""多层 ŷ 驱动的 A 股底仓做 T 策略（dual_y）。

角色（PIT）：
  y_trade  — |ŷ_trade| 下限（预期日波动幅度）/ 额度主缩放
  y_eod    — T−1 冻结先验（与 y_τ 同向时略放大额度）
  y_τ      — 盘中主方向（开→收）；|y_τ| 超 enter 门槛后连续放大额度
  y_on     — 尾盘是否强制回补
  y_nowcast— 对照 nc（Kalman 昨收）；|nc| 够强且与 y_τ 异号可跳过（OC 开比 nowcast oc）
  y_path   — 分钟极值时间序 signed range%；|ŷ_path|>enter 才入场，且须与 τ 方向一致

选向分数默认**即时算**（开盘决策信息集：昨收因子 + 今开缺口），
不依赖 score_ledger / 分池簿冻结快照；账本与簿仅作可选兜底。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 默认阈值（ŷ 为百分比点；可用 rules 覆盖）
DEFAULT_TRADE_FLOOR = 0.02
DEFAULT_EOD_PRIOR = 0.01
DEFAULT_TAU_ENTER = 0.02
DEFAULT_ON_RISK = 0.01
DEFAULT_ON_ALLOW = 0.01
DEFAULT_RATIO_BOOST_CAP = 2.0
DEFAULT_RATIO_CUT = 0.60
DEFAULT_TAU_BOOST_CAP = 1.15
DEFAULT_EOD_ALIGN_BOOST = 1.10
DEFAULT_RATIO_TAU_SOFT_BAND = 0.20
DEFAULT_TAU_NOWCAST_SIGN_EPS = 0.05
DEFAULT_NOWCAST_ENTER = 1.0  # |nowcast|≥此值且与 y_τ 异号才拦
DEFAULT_PATH_ENTER = 0.02  # ŷ_path 极值序 %；|ŷ|≤enter 横盘跳过；与 y_tau_enter 同尺度
DEFAULT_GAP_TIER_PCT = 1.5
DEFAULT_PATH_ABANDON_BARS = 6

# dual_y 下 y_τ 符号 → 正/反 T 映射（回测对照）
# scalp / trend: y_τ>0→反T（趋势跟随：OC 向上，日内更可能 low→high，先买后卖）
#   scalp 与 trend 语义一致，保留 scalp 仅作向后兼容别名
# fixed_long / fixed_reverse: 忽略 y_τ 符号，固定方向（仍过 |y_τ| 门槛）
Y_TAU_MAP_DEFAULT = "trend"
Y_TAU_MAP_CHOICES = ("scalp", "trend", "fixed_long", "fixed_reverse")
Y_TAU_MAP_LABELS = {
    "scalp": "高抛低吸（y_τ>0→反T；与 trend 等价，保留兼容）",
    "trend": "趋势跟随（y_τ>0→反T）",
    "fixed_long": "固定正T",
    "fixed_reverse": "固定反T",
}

# compute | live_book | ledger
DEFAULT_Y_SCORE_SOURCE = "compute"
_MIN_HIST_BARS = 16
# 与 score_stock.fetch_daily_bars(limit=40) 对齐：周线确认按「从头每 5 根切桶」，
# 窗长不同会漂 weekly_confirm / ma_slope / technical_pattern。
_EOD_FACTOR_BAR_LIMIT = 40
# 做 T 回测：评估窗与因子窗分离；warmup 仅供 hist_prior，不进成交明细
T0_BACKTEST_SCORE_WARMUP = max(_MIN_HIST_BARS + 8, 40)

# 进程内轻量缓存：回测逐日重算时复用模型句柄
_MODEL_CACHE: Dict[str, Any] = {}
# 单日 τ 截面缓存：day → build_tau_pool_by_date 条目
_TAU_XS_DAY_CACHE: Dict[str, Dict[str, Any]] = {}


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


def _slim_formula_terms(
    expl: Any,
    *,
    limit: int = 10,
    pin_keys: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """压缩分项拆解，避免成交日 scores / data-score-detail 过大截断。

    ``pin_keys``：对照关心的因子（如非流动性）即使贡献小也保留；
    在 ``limit`` 内用 pin 替换末位，**不追加**以免属性过长截断坏 JSON。
    """
    if not isinstance(expl, dict):
        return None
    terms_in = list(expl.get("terms") or [])
    terms: List[Dict[str, Any]] = []
    for t in terms_in:
        if not isinstance(t, dict):
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
    if expl.get("head") is not None:
        out["head"] = expl.get("head")
    for meta_k in (
        "y_eod",
        "y_tau",
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


def _slim_features_tau(feats: Any, *, limit: int = 16) -> Optional[Dict[str, Any]]:
    if not isinstance(feats, dict) or not feats:
        return None
    # 先保住 τ Z 键，避免 dict 截断丢掉 gap_pct / breadth
    pin = (
        "gap_pct",
        "open_gap",
        "sector_gap_breadth",
        "theme_day",
        "gap_atr",
        "gap_vs_sector",
        "yclose_loc",
        "mom3_pct",
        "ret_open_to_tau",
    )
    out: Dict[str, Any] = {}
    lim = max(1, int(limit))

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
    """从 ``build_tau_pool_by_date`` 单日条目抽出 resolve_scores 截面参数。"""
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
        limit=12,
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

    on_terms = _slim_formula_terms(
        item.get("formula_terms_on") or item.get("score_formula_terms_on"),
        limit=10,
    )
    if on_terms and (on_terms.get("terms") or on_terms.get("total") is not None):
        out["formula_terms_on"] = on_terms
        out["score_formula_terms_on"] = on_terms

    path_terms = _slim_formula_terms(
        item.get("formula_terms_path") or item.get("score_formula_terms_path"),
        limit=12,
    )
    if not path_terms or not (path_terms.get("terms") or path_terms.get("total") is not None):
        # 旧快照缺组成时：用 features_path / features_tau 现场补拆解
        try:
            from core.research.path_ridge import explain_path_prediction, load_path_model

            feats = item.get("features_path")
            if not isinstance(feats, dict) or not feats:
                feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
            expl = explain_path_prediction(feats, model_doc=load_path_model())
            path_terms = _slim_formula_terms(expl, limit=12)
        except Exception:  # noqa: BLE001
            logger.debug("path tip explain fallback failed", exc_info=True)
            path_terms = None
    if path_terms and (path_terms.get("terms") or path_terms.get("total") is not None):
        out["formula_terms_path"] = path_terms
        out["score_formula_terms_path"] = path_terms

    try:
        from core.research.path_ridge import path_tip_model_snapshot

        tip_m = path_tip_model_snapshot()
        if tip_m:
            out["path_tip_model"] = tip_m
    except Exception:  # noqa: BLE001
        logger.debug("path_tip_model snapshot failed", exc_info=True)

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
            }
        }
        if slim_path:
            out["features_path"] = slim_path

    feats_tau = _slim_features_tau(item.get("features_tau"))
    if feats_tau:
        out["features_tau"] = feats_tau

    try:
        from core.signal.dual_score.on import features_on_snapshot

        feats_on = features_on_snapshot(item.get("features_on"))
        if feats_on:
            out["features_on"] = feats_on
    except Exception:  # noqa: BLE001
        logger.debug("features_on tip slim failed", exc_info=True)

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
        "y_on",
        "y_on_path",
        "y_nowcast",
        "y_path",
        "y_check",
        "eod_trust",
        "y_nc",
        "y_nc_oc",
        "gap_pct",
    ):
        if k in score_snap and score_snap.get(k) is not None:
            out[k] = score_snap.get(k)
    for k, v in tip_fields_from_item(score_snap).items():
        out[k] = v
    src = score_snap.get("_score_source")
    if src:
        out["_score_source"] = src
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
            "y_nowcast",
            "y_path",
            "y_check",
            "eod_trust",
            "y_nc",
            "y_nc_oc",
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
    """从 signal_item / 账本行 / insights 抽出做T用分数。"""
    if not isinstance(item, dict):
        return {
            "y_eod": None,
            "y_tau": None,
            "y_trade": None,
            "y_on": None,
            "y_nowcast": None,
            "y_check": None,
            "eod_trust": None,
        }
    y_eod = _f(item.get("y_eod"))
    if y_eod is None:
        y_eod = _f(item.get("predicted_score_eod"))
    if y_eod is None:
        y_eod = _f(item.get("yhat_eod"))

    y_tau = _f(item.get("y_tau"))
    if y_tau is None:
        y_tau = _f(item.get("predicted_score_tau"))
    if y_tau is None:
        y_tau = _f(item.get("score_rem"))
    if y_tau is None:
        y_tau = _f(item.get("yhat_tau"))

    # 契约：predicted_score / predicted_score_eod = ŷ_EOD；ŷ_trade = blend / decision_score。
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

    y_on = _f(item.get("y_on"))
    if y_on is None:
        y_on = _f(item.get("predicted_score_on"))

    y_on_path = _f(item.get("y_on_path"))
    if y_on_path is None:
        y_on_path = _f(item.get("predicted_score_on_path"))

    y_nowcast = _f(item.get("y_nowcast"))
    if y_nowcast is None:
        y_nowcast = _f(item.get("predicted_score_nowcast"))

    y_path = _f(item.get("y_path"))
    if y_path is None:
        y_path = _f(item.get("predicted_score_path"))

    y_check = item.get("y_check")
    if y_check is not None:
        y_check = str(y_check)
    eod_trust = _f(item.get("eod_trust"))

    out: Dict[str, Any] = {
        "y_eod": y_eod,
        "y_tau": y_tau,
        "y_trade": y_trade,
        "y_on": y_on,
        "y_nowcast": y_nowcast,
        "y_path": y_path,
        "y_check": y_check,
        "eod_trust": eod_trust,
    }
    if y_on_path is not None:
        out["y_on_path"] = y_on_path
        out["predicted_score_on_path"] = y_on_path

    gap = _f(item.get("gap_pct"))
    if gap is None and isinstance(item.get("features_tau"), dict):
        gap = _f(item["features_tau"].get("gap_pct"))
    if gap is not None:
        out["gap_pct"] = gap
    status = item.get("y_path_status")
    if status:
        out["y_path_status"] = str(status)
    if item.get("y_path_error"):
        out["y_path_error"] = str(item.get("y_path_error"))
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
    nc_cc = _nowcast_cc_pct(out)
    if nc_cc is not None:
        out["y_nc"] = nc_cc
        gap_for_oc = _f(out.get("gap_pct"))
        if gap_for_oc is None and isinstance(out.get("features_tau"), dict):
            gap_for_oc = _f(out["features_tau"].get("gap_pct"))
        if gap_for_oc is not None:
            nc_oc = _nowcast_oc_pct(nc_cc, gap_for_oc)
            if nc_oc is not None:
                out["y_nc_oc"] = nc_oc
    return out


def _y_path_missing_reason(scores: dict) -> str:
    status = str(scores.get("y_path_status") or "")
    detail = {
        "no_model": "path_ridge 模型未 promote（/quant → ŷ_path 拟合/启用）",
        "feature_missing": "path 开盘特征不足",
        "predict_none": "path 模型无法出分",
        "error": scores.get("y_path_error") or "path 预测异常",
    }.get(status)
    if detail:
        return f"dual_y：缺 y_path（{detail}）"
    return "dual_y：缺 y_path（path_ridge 未加载或未算分）"


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
        "y_check": row.get("y_check"),
        "eod_trust": row.get("eod_trust"),
    }
    return scores_from_item(mapped)


def normalize_y_trade_floor(raw: Any) -> float:
    """|y_trade| 下限（收益百分点）；旧配置负值加载时取 abs。"""
    try:
        val = float(DEFAULT_TRADE_FLOOR if raw is None or raw == "" else raw)
    except (TypeError, ValueError):
        val = DEFAULT_TRADE_FLOOR
    return max(0.0, min(abs(val), 5.0))


def trade_mag_floor(cfg: dict) -> float:
    return normalize_y_trade_floor(cfg.get("y_trade_floor"))


def _cfg_float(cfg: dict, key: str, default: float) -> float:
    try:
        v = cfg.get(key)
        if v is None or v == "":
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _eod_prior_sign(y_eod: Optional[float], eod_prior: float) -> int:
    if y_eod is None:
        return 0
    if y_eod >= eod_prior:
        return 1
    if y_eod <= -eod_prior:
        return -1
    return 0


def _tau_direction_sign(y_tau: Optional[float], tau_enter: float) -> int:
    if y_tau is None:
        return 0
    if y_tau >= tau_enter:
        return 1
    if y_tau <= -tau_enter:
        return -1
    return 0


def t0_confidence_scale(scores: dict, cfg: dict) -> float:
    """ŷ 信心 → 目标价倍数：1=按配置触发；弱压低、强抬高。

    沿用 ``y_ratio_*``：各路取最弱一档（min），映射到
    ``[y_ratio_cut, y_ratio_boost_cap]``（默认 0.6～2.0）；eod 同向略抬 strength。
    """
    if not isinstance(scores, dict):
        return 1.0

    floor = trade_mag_floor(cfg)
    cut = _cfg_float(cfg, "y_ratio_cut", DEFAULT_RATIO_CUT)
    cut = max(0.2, min(float(cut), 1.0))
    cap = _cfg_float(cfg, "y_ratio_boost_cap", DEFAULT_RATIO_BOOST_CAP)
    cap = max(1.0, min(float(cap), 2.0))
    if cap < cut:
        cap = cut
    tau_enter = _cfg_float(cfg, "y_tau_enter", DEFAULT_TAU_ENTER)
    eod_prior = _cfg_float(cfg, "y_eod_prior", DEFAULT_EOD_PRIOR)
    eod_align_boost = _cfg_float(cfg, "y_ratio_eod_align_boost", DEFAULT_EOD_ALIGN_BOOST)
    soft_band = _cfg_float(cfg, "y_ratio_tau_soft_band", DEFAULT_RATIO_TAU_SOFT_BAND)

    strengths: List[float] = []

    y_trade = _f(scores.get("y_trade"))
    if y_trade is not None:
        mag = abs(y_trade)
        if mag < floor:
            strengths.append(0.0)
        else:
            span = max(0.5, floor + 1.0)
            strengths.append(min(1.0, max(0.0, (mag - floor) / span)))

    y_tau = _f(scores.get("y_tau"))
    if y_tau is not None and abs(y_tau) >= tau_enter:
        if soft_band > 0 and abs(y_tau) < tau_enter + soft_band:
            strengths.append(0.0)
        else:
            span_t = max(0.5, tau_enter + 0.5)
            strengths.append(min(1.0, max(0.0, (abs(y_tau) - tau_enter) / span_t)))

    if not strengths:
        return 1.0

    strength = min(strengths)
    y_eod = _f(scores.get("y_eod"))
    if y_tau is not None and y_eod is not None:
        prior = _eod_prior_sign(y_eod, eod_prior)
        main = _tau_direction_sign(y_tau, tau_enter)
        if prior != 0 and main != 0 and prior == main:
            strength = min(1.0, strength * eod_align_boost)

    return max(cut, min(cap, cut + (cap - cut) * strength))


def scale_t0_triggers(
    sell_pct: float,
    buy_pct: float,
    scores: dict,
    cfg: dict,
) -> Dict[str, float]:
    """按 ŷ 信心缩放卖/买触发 %（目标价距离）；可高于基准（强信号）。"""
    sell = float(sell_pct)
    buy = float(buy_pct)
    scale = t0_confidence_scale(scores, cfg) if isinstance(scores, dict) else 1.0
    return {
        "sell_trigger_pct": round(max(0.1, min(sell * scale, 20.0)), 4),
        "buy_trigger_pct": round(max(0.1, min(buy * scale, 20.0)), 4),
        "trigger_scale": round(scale, 4),
        "sell_trigger_pct_base": round(sell, 4),
        "buy_trigger_pct_base": round(buy, 4),
    }


def scale_t0_ratio(base_ratio: float, scores: dict, cfg: dict) -> float:
    """兼容旧调用：动仓比例固定为基准，不再随 ŷ 缩放。"""
    _ = scores, cfg
    return max(0.05, min(1.0, float(base_ratio)))


def normalize_y_tau_map(raw: Any) -> str:
    mode = str(raw or Y_TAU_MAP_DEFAULT).strip().lower()
    aliases = {
        "follow": "trend",
        "momentum": "trend",
        "invert": "trend",
        "scalp": "trend",  # 语义修正后与 trend 等价
        "long": "fixed_long",
        "always_long": "fixed_long",
        "reverse": "fixed_reverse",
        "always_reverse": "fixed_reverse",
    }
    mode = aliases.get(mode, mode)
    if mode not in Y_TAU_MAP_CHOICES:
        mode = Y_TAU_MAP_DEFAULT
    return mode


def _path_direction_sign(y_path: Optional[float], path_enter: float) -> int:
    if y_path is None:
        return 0
    thr = float(path_enter)
    if y_path > thr:
        return 1
    if y_path < -thr:
        return -1
    return 0


def _nowcast_oc_pct(y_nowcast_cc: Optional[float], gap_pct: Optional[float]) -> Optional[float]:
    """nowcast oc：nc（昨收）→ open→close（与 y_τ 同窗口）。"""
    if y_nowcast_cc is None or gap_pct is None:
        return None
    try:
        g = float(gap_pct) / 100.0
        cc = float(y_nowcast_cc) / 100.0
    except (TypeError, ValueError):
        return None
    denom = 1.0 + g
    if abs(denom) < 1e-9:
        return None
    oc = ((1.0 + cc) / denom - 1.0) * 100.0
    return round(oc, 4)


def _nowcast_cc_pct(scores: dict) -> Optional[float]:
    """nowcast = nc：Kalman 对照昨收 (1−K)·ŷ_EOD + K·(缺口∘ŷ_τ)。"""
    if not isinstance(scores, dict):
        return None
    item: Dict[str, Any] = {
        "predicted_score_eod": scores.get("predicted_score_eod") or scores.get("y_eod"),
        "predicted_score_tau": scores.get("predicted_score_tau") or scores.get("y_tau"),
        "predicted_score_nowcast": scores.get("predicted_score_nowcast") or scores.get("y_nowcast"),
        "gap_pct": scores.get("gap_pct"),
        "features_tau": scores.get("features_tau"),
        "nowcast_K": scores.get("nowcast_K"),
        "nowcast_vs": scores.get("nowcast_vs"),
        "nowcast_as_of": scores.get("nowcast_as_of"),
        "nowcast_q": scores.get("nowcast_q"),
        "dual_score_weights": scores.get("dual_score_weights"),
        "as_of_tau": scores.get("as_of_tau") or scores.get("rem_tau"),
        "dual_score_window": scores.get("dual_score_window"),
    }
    if isinstance(item.get("features_tau"), dict) and item.get("gap_pct") is None:
        ft = item["features_tau"]
        if ft.get("gap_pct") is not None:
            item["gap_pct"] = ft.get("gap_pct")
    try:
        from core.signal.dual_score.resolve import align_nowcast_score_fields

        align_nowcast_score_fields(item)
    except Exception:  # noqa: BLE001
        logger.debug("_nowcast_cc_pct align_nowcast failed", exc_info=True)

    eod = _f(item.get("predicted_score_eod"))
    tau = _f(item.get("predicted_score_tau"))
    gap = _f(item.get("gap_pct"))
    feats = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}

    if eod is not None and tau is not None:
        try:
            from core.signal.nowcast_kf import as_process_q, run_live_nowcast

            as_of = str(item.get("as_of_tau") or item.get("rem_tau") or "open")
            q_raw = item.get("nowcast_q")
            pack = run_live_nowcast(
                y_eod=eod,
                y_tau=tau,
                gap_pct=gap,
                ret_open_to_tau=feats.get("ret_open_to_tau"),
                as_of=as_of,
                q_process=as_process_q(q_raw, 0.05) if q_raw is not None else 0.05,
                theme_day=feats.get("theme_day"),
                allow_minute=feats.get("ret_open_to_tau") is not None,
            )
            nc = _f(pack.get("predicted_score_nowcast"))
            if nc is not None:
                return round(float(nc), 4)
        except Exception:  # noqa: BLE001
            logger.debug("_nowcast_cc_pct run_live_nowcast failed", exc_info=True)

    from core.signal.dual_score.fusion import lift_tau_vs_prev_close
    from core.signal.nowcast_kf import compound_pct

    vs = str(item.get("nowcast_vs") or scores.get("nowcast_vs") or "").strip().lower()
    raw = _f(item.get("predicted_score_nowcast"))
    if raw is None:
        raw = _f(scores.get("y_nowcast"))
    if raw is None:
        raw = _f(scores.get("predicted_score_nowcast"))
    tau_cc = lift_tau_vs_prev_close(tau, gap)
    k = _f(item.get("nowcast_K"))
    if k is None and isinstance(item.get("dual_score_weights"), dict):
        k = _f(item["dual_score_weights"].get("nowcast_K"))
    if k is None:
        k = 1.05 / 2.05
    if eod is not None and tau_cc is not None and k is not None and 0 <= k <= 1:
        return round((1.0 - k) * eod + k * tau_cc, 4)
    if raw is not None and gap is not None and vs not in ("prev_close", "eod_next"):
        lifted = compound_pct(gap, raw)
        if lifted is not None:
            return round(float(lifted), 4)
    if raw is not None and vs == "prev_close":
        return raw
    return raw


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
        want = "long_t" if g > 0 else "reverse_t"
        if tau_dir == want:
            return None
        return {
            "skip": True,
            "direction": None,
            "reason": (
                f"dual_y[gap_tier/revert]：gap={g:+.2f}%≥{tier}% 期望{want}，"
                f"τ→{tau_dir} 跳过"
            ),
        }
    # skip_opposite：大缺口日禁止「顺势」映射
    if g >= tier and tau_dir == "reverse_t":
        return {
            "skip": True,
            "direction": None,
            "reason": (
                f"dual_y[gap_tier]：高开{g:+.2f}%≥{tier}% 跳过反T（τ顺势映射）"
            ),
        }
    if g <= -tier and tau_dir == "long_t":
        return {
            "skip": True,
            "direction": None,
            "reason": (
                f"dual_y[gap_tier]：低开{g:+.2f}%≤-{tier}% 跳过正T（τ顺势映射）"
            ),
        }
    return None


def _attach_y_path_to_item(item: dict, *, hist_bars: Optional[Sequence[dict]] = None) -> None:
    """即时算分后补 ŷ_path（开盘特征 → path_ridge）。"""
    if not isinstance(item, dict):
        return
    feats_tau = item.get("features_tau") if isinstance(item.get("features_tau"), dict) else {}
    row: Dict[str, Any] = dict(feats_tau)
    if row.get("gap_pct") is None:
        row["gap_pct"] = item.get("gap_pct")
    hist = [b for b in (hist_bars or []) if isinstance(b, dict)]
    try:
        from core.research.path_panel import path_features_from_open_row
        from core.research.path_ridge import (
            explain_path_prediction,
            load_path_model,
            predict_path_from_features,
        )

        model = load_path_model()
        if model is None:
            if _f(item.get("y_path")) is None:
                item["y_path_status"] = "no_model"
            return
        prev = hist[-1] if hist else None
        path_feats = path_features_from_open_row(row, hist=hist, prev_bar=prev)
        existing = _f(item.get("y_path"))
        if existing is not None:
            item["y_path_status"] = "ok"
            if not isinstance(item.get("features_path"), dict):
                ft = dict(feats_tau)
                for k, v in path_feats.items():
                    if v is not None:
                        ft[k] = v
                if ft:
                    item["features_path"] = ft
            if item.get("formula_terms_path") is None:
                expl = explain_path_prediction(
                    item.get("features_path") or path_feats, model_doc=model
                )
                if expl:
                    item["formula_terms_path"] = expl
                    item["score_formula_terms_path"] = expl
            return
        if not any(v is not None for v in path_feats.values()):
            item["y_path_status"] = "feature_missing"
            return
        y_path = predict_path_from_features(path_feats, model_doc=model)
        if y_path is not None:
            item["y_path"] = y_path
            item["predicted_score_path"] = y_path
            item["y_path_status"] = "ok"
            ft = dict(feats_tau)
            for k, v in path_feats.items():
                if v is not None:
                    ft[k] = v
            item["features_path"] = ft
            expl = explain_path_prediction(path_feats, model_doc=model)
            if expl:
                item["formula_terms_path"] = expl
                item["score_formula_terms_path"] = expl
        else:
            item["y_path_status"] = "predict_none"
    except Exception as exc:  # noqa: BLE001
        item["y_path_status"] = "error"
        item["y_path_error"] = str(exc)[:120]
        logger.debug("attach y_path failed", exc_info=True)


def direction_from_y_tau_sign(tau_sign: int, cfg: dict) -> str:
    """由 y_τ 符号（±1）与 y_tau_map 解析 long_t / reverse_t。

    y_τ>0（OC 向上，日内更可能 low→high）→ reverse_t（先买后卖）
    y_τ<0（OC 向下，日内更可能 high→low）→ long_t（先卖后买）
    scalp 与 trend 语义一致，保留 scalp 仅作向后兼容别名。
    """
    mode = normalize_y_tau_map(cfg.get("y_tau_map"))
    if mode == "fixed_long":
        return "long_t"
    if mode == "fixed_reverse":
        return "reverse_t"
    # scalp / trend：趋势跟随
    return "reverse_t" if tau_sign > 0 else "long_t"


def resolve_dual_y_direction(
    *,
    scores: dict,
    cfg: dict,
    cash: float,
    shares: float,
) -> Dict[str, Any]:
    """dual_y 选向：|y_trade|下限 → τ/path 双门槛且同向 → 可选 τ↔nowcast 异号跳过。

    做 T 准入（y_use_path 开且 ŷ_path 可得时）：
      |y_τ|≥y_tau_enter 定正/反 T；
      |y_path|>y_path_enter 且与 τ 同向（正T↔y_path>0，反T↔y_path<0）；
      任一侧未过门槛或异号则跳过。
    y_path 缺失时默认不拦（y_path_required 开则拦）。
    y_eod 仅目标价同向回升，不参与准入。
    """
    from core.t0.config import coerce_cfg_bool

    trade_floor = trade_mag_floor(cfg)
    eod_prior = _cfg_float(cfg, "y_eod_prior", DEFAULT_EOD_PRIOR)
    tau_enter = _cfg_float(cfg, "y_tau_enter", DEFAULT_TAU_ENTER)
    # 旧键 y_tau_enter_strong：若更高则并入入场闸（双闸已合并）
    strong_legacy = _f(cfg.get("y_tau_enter_strong"))
    if strong_legacy is not None and strong_legacy > tau_enter:
        tau_enter = strong_legacy

    # 新键优先；旧 y_block_trade_tau_sign 仅作迁移别名；皆缺则默认开
    if "y_block_tau_nowcast_sign" in cfg:
        block_tau_nc = bool(cfg.get("y_block_tau_nowcast_sign"))
    elif "y_block_trade_tau_sign" in cfg:
        block_tau_nc = bool(cfg.get("y_block_trade_tau_sign"))
    else:
        block_tau_nc = True
    sign_eps = _cfg_float(
        cfg,
        "y_tau_nowcast_sign_eps",
        _cfg_float(cfg, "y_trade_tau_sign_eps", DEFAULT_TAU_NOWCAST_SIGN_EPS),
    )
    nc_enter = _cfg_float(cfg, "y_nowcast_enter", DEFAULT_NOWCAST_ENTER)
    nc_enter = max(sign_eps, min(float(nc_enter), 10.0))
    tau_map = normalize_y_tau_map(cfg.get("y_tau_map"))
    map_tag = Y_TAU_MAP_LABELS.get(tau_map, tau_map)

    y_eod = _f(scores.get("y_eod"))
    y_tau = _f(scores.get("y_tau"))
    y_trade = _f(scores.get("y_trade"))
    y_nowcast = _f(scores.get("y_nowcast"))
    y_path = _f(scores.get("y_path"))
    y_check = scores.get("y_check")
    gap_pct = _f(scores.get("gap_pct"))
    if gap_pct is None and isinstance(scores.get("features_tau"), dict):
        gap_pct = _f(scores["features_tau"].get("gap_pct"))

    use_path = bool(cfg.get("y_use_path", True))
    path_enter = _cfg_float(cfg, "y_path_enter", tau_enter)
    path_required = bool(cfg.get("y_path_required", False))
    use_nowcast_oc = coerce_cfg_bool(cfg.get("y_nowcast_oc_gate"), False)

    features = {
        "y_eod": y_eod,
        "y_tau": y_tau,
        "y_trade": y_trade,
        "y_on": _f(scores.get("y_on")),
        "y_nowcast": y_nowcast,
        "y_path": y_path,
        "y_check": y_check,
        "gap_pct": gap_pct,
        "y_tau_enter": tau_enter,
        "y_nowcast_enter": nc_enter,
    }

    if y_trade is None and y_tau is None and y_eod is None:
        return {
            "direction": None,
            "skip": True,
            "direction_score": None,
            "direction_reason": "dual_y：缺 y_eod/y_τ/y_trade（即时算分失败）",
            "features": features,
            "signal_skip": True,
        }

    if y_trade is not None and abs(y_trade) < trade_floor:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau if y_tau is not None else y_trade,
            "direction_reason": (
                f"dual_y：|y_trade|={abs(y_trade):.3f}%<{trade_floor}% 预期幅度不足"
            ),
            "features": features,
            "signal_skip": True,
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

    if abs(y_tau) < tau_enter:
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": f"dual_y：|y_τ|={abs(y_tau):.3f}%<{tau_enter}% 横盘跳过",
            "features": features,
            "signal_skip": True,
        }

    # 可选：y_τ 与 nowcast 异号则跳过（仅 |nowcast| 够强时；OC 开时用 y_nc_oc）
    nc_cc = _nowcast_cc_pct(scores) if isinstance(scores, dict) else None
    if nc_cc is not None:
        features["y_nc"] = nc_cc
    nc_compare = nc_cc
    nc_label = "y_nc"
    if nc_cc is not None and gap_pct is not None:
        nc_oc = _nowcast_oc_pct(nc_cc, gap_pct)
        if nc_oc is not None:
            features["y_nc_oc"] = nc_oc
            if use_nowcast_oc:
                nc_compare = nc_oc
                nc_label = "y_nc_oc"
    features["y_nowcast_oc_gate"] = use_nowcast_oc
    features["nowcast_compare_label"] = nc_label
    if (
        block_tau_nc
        and nc_compare is not None
        and abs(y_tau) >= sign_eps
        and abs(nc_compare) >= nc_enter
        and (y_tau > 0) != (nc_compare > 0)
    ):
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": (
                f"dual_y：y_τ={y_tau:.3f}% 与 {nc_label}={nc_compare:.3f}% 异号跳过"
                f"（|{nc_label}|≥{nc_enter}%）"
            ),
            "features": features,
            "signal_skip": True,
        }

    # y_eod 仅标注 / 目标价同向回升（t0_confidence_scale）
    prior = 0
    if y_eod is not None:
        if y_eod >= eod_prior:
            prior = 1
        elif y_eod <= -eod_prior:
            prior = -1

    main = 1 if y_tau >= tau_enter else -1
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

    path_sign = _path_direction_sign(y_path, path_enter)
    if use_path:
        if y_path is None and path_required:
            return {
                "direction": None,
                "skip": True,
                "direction_score": y_tau,
                "direction_reason": _y_path_missing_reason(scores),
                "features": features,
                "signal_skip": True,
            }
        # path 入场闸：|ŷ_path|≤enter 横盘跳过；>enter 且与 τ 冲突才否决
        if y_path is not None and path_sign == 0:
            return {
                "direction": None,
                "skip": True,
                "direction_score": y_tau,
                "direction_reason": (
                    f"dual_y：|y_path|={abs(y_path):.3f}%≤{path_enter}% 横盘跳过"
                ),
                "features": features,
                "signal_skip": True,
            }
        if y_path is not None and path_sign != 0:
            path_wants_long = path_sign > 0
            path_wants_reverse = path_sign < 0
            if direction == "long_t" and not path_wants_long:
                return {
                    "direction": None,
                    "skip": True,
                    "direction_score": y_tau,
                    "direction_reason": (
                        f"dual_y：y_τ→{direction} 但 y_path={y_path:.3f}% 先高后低（不一致）"
                    ),
                    "features": features,
                    "signal_skip": True,
                }
            if direction == "reverse_t" and not path_wants_reverse:
                return {
                    "direction": None,
                    "skip": True,
                    "direction_score": y_tau,
                    "direction_reason": (
                        f"dual_y：y_τ→{direction} 但 y_path={y_path:.3f}% 先低后高（不一致）"
                    ),
                    "features": features,
                    "signal_skip": True,
                }

    if direction == "reverse_t" and not (cash > 0 and shares > 0):
        return {
            "direction": None,
            "skip": True,
            "direction_score": y_tau,
            "direction_reason": (
                f"dual_y[{tau_map}]：y_τ={y_tau:.3f}%→{direction} 但缺现金/仓"
            ),
            "features": features,
            "signal_skip": True,
        }

    nc_note = ""
    if y_nowcast is not None and (y_nowcast * y_tau) > 0 and abs(y_nowcast) >= abs(y_tau):
        nc_note = f"；nowcast={y_nowcast:.3f}%同向增强(影子)"
        features["nowcast_align"] = True
    else:
        features["nowcast_align"] = False

    path_note = ""
    if y_path is not None and path_sign != 0:
        path_note = f"；y_path={y_path:.1f}一致"

    return {
        "direction": direction,
        "skip": False,
        "direction_score": y_tau,
        "direction_reason": (
            f"dual_y[{tau_map}]：y_τ={y_tau:.3f}%→{direction}（{map_tag}）"
            + (f"；y_eod先验={prior:+d}" if prior else "")
            + (f"；y_trade={y_trade:.3f}%" if y_trade is not None else "")
            + path_note
            + nc_note
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
    """尾盘回补：默认强制；仅当 y_on 强烈支持隔夜敞口时放行。

    long_t 默认强制当日回补：卖空后若买不回会留下隔夜空头敞口，
    风险不对称，故不论 y_on 一律回补（仅 reverse_t 在 y_on 强烈看涨时放行）。
    """
    if bool(cfg.get("must_cover_same_day")):
        return {
            "must_cover": True,
            "reason": "表单强制当日回补",
            "allow_overnight": False,
        }

    # long_t 一律当日回补，防未回补空头敞口（风险不对称）
    if direction == "long_t":
        return {
            "must_cover": True,
            "reason": "long_t 默认强制回补（防未回补空头敞口）",
            "allow_overnight": False,
        }

    y_on = _f(scores.get("y_on"))
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

    # |y_on| 很大：仅反T在 y_on 强烈看涨时允许隔夜多头敞口
    # （long_t 已在函数开头一律回补，不在此放行）
    if direction == "reverse_t" and y_on >= on_allow:
        return {
            "must_cover": False,
            "reason": f"y_on={y_on:.3f}%支持反T隔夜多头",
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
        for k in ("y_eod", "y_tau", "y_trade", "y_on", "y_path")
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
    """为 ŷ_ON 补路径现价（开→收 / 收→收）；不改 open / prev_close / change_raw。

    开盘决策报价常把 ``price_raw=open``，会导致 ret_oc≈0、ŷ_ON 与簿相反号。
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
    if _MODEL_CACHE.get("ok"):
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
    cluster: Dict[str, Any] = {}
    try:
        from core.signal.cluster.live import (
            filter_primary_cluster_models_by_code,
            load_cluster_return_models_by_code,
        )

        cluster = filter_primary_cluster_models_by_code(
            load_cluster_return_models_by_code() or {}
        ) or {}
    except Exception:  # noqa: BLE001
        logger.debug("load cluster return models failed", exc_info=True)
    global_rm = None
    try:
        from core.signal.return_score_store import load_return_model

        global_rm, _meta = load_return_model(prefer_active=True)
    except Exception:  # noqa: BLE001
        logger.debug("load global return model failed", exc_info=True)
    _MODEL_CACHE.clear()
    _MODEL_CACHE.update(
        {"ok": True, "tau": tau, "cluster": cluster, "global_rm": global_rm}
    )
    return tau, cluster, global_rm


def clear_score_model_cache() -> None:
    """测试 / 热更模型后清空缓存。"""
    _MODEL_CACHE.clear()
    _TAU_XS_DAY_CACHE.clear()


def _tau_cross_section_kwargs(code: str, day_key: str) -> Dict[str, Any]:
    """单票缺截面时：活跃簿宇宙当日缺口 → pool_gaps / breadth / sector_gap_median。"""
    raw = str(code or "").strip()
    dkey = str(day_key or "")[:10]
    if not raw or len(dkey) < 10:
        return {}
    pool = _TAU_XS_DAY_CACHE.get(dkey)
    if not isinstance(pool, dict):
        try:
            codes = active_book_codes_for_tau_pool(cap=120)
            if raw not in codes:
                codes = list(codes) + [raw]
            bars_map = load_bars_by_code_for_tau_pool(codes, limit=40)
            by_day = build_tau_pool_by_date(bars_map)
            pool = by_day.get(dkey) if isinstance(by_day.get(dkey), dict) else {}
            _TAU_XS_DAY_CACHE[dkey] = pool or {}
        except Exception:  # noqa: BLE001
            logger.debug("tau cross-section fallback failed for %s %s", raw, dkey, exc_info=True)
            pool = {}
            _TAU_XS_DAY_CACHE[dkey] = {}
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
    """与 score_stock 同源：基准指数日线，并裁到个股 hist 末日（开盘决策 T−1）。"""
    try:
        from core.signal.live_features import fetch_live_index_bars
        from core.signal.session_pit import prepare_eod_bars
        from core.ports.market import resolve_market_code

        mkt = str(resolve_market_code(str(code) or "") or "CN")
        # 与刷簿相对强弱同口径：多取几根，避免短窗/偶发空包把 RS 打成中性
        pack = fetch_live_index_bars(market=mkt, limit=120) or {}
        bars = list(pack.get("bars") or [])
        if not bars:
            pack = fetch_live_index_bars(market=mkt, limit=120, use_cache=False) or {}
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
    return fundamentals if isinstance(fundamentals, dict) and fundamentals else None


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
) -> Dict[str, Optional[float]]:
    """开盘决策信息集即时算 dual_y 分数（不读账本/簿）。

    ``hist_bars``：不含当日的日线（因子截止 T−1）。
    ``day_bar`` / ``quote``：提供 open[T]（缺口）；勿用收盘价冒充开盘决策现价。
    ``pool_gaps`` / ``sector_gap_breadth``：截面缺口（批量算分时传入，增强 ŷ_τ）。
    ``sector_gap_median``：同行/截面参照缺口（``gap_vs_sector = gap − median``）。
    ``index_bars`` / ``fundamentals``：可选；缺省时拉本地指数+估值，对齐数据中心相对强弱等。
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

    # 单票缺截面时用活跃簿宇宙补 gap_vs_sector / breadth（对齐刷簿）
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
        try:
            from core.research.tau_panel import GAP_ATR_WINDOW
        except Exception:  # noqa: BLE001
            GAP_ATR_WINDOW = 14
        # 开盘 PIT：τ / EOD / 决策用开盘 quote（price=open）；勿把当日 close 喂进 ŷ_ON
        # （否则表列 y_on≈当日 OC，与 τ实假相关）。
        dual_bars = list(hist[-max(int(GAP_ATR_WINDOW) + 6, 20) :])
        # 当日 bar 仅供 asof 对齐；hist_bars_pit 会剥掉 asof 日 K，不吃 T 振幅
        if day is not None:
            dual_bars = dual_bars + [day]
        attach_dual_score_pit(
            item,
            quote=q,
            bars=dual_bars,
            rem_model_doc=rem,
            sector_gap_breadth=item.get("sector_gap_breadth"),
            fuse_intraday=bool(fuse_intraday),
            sector_gap_median=item.get("_sector_gap_median"),
        )
        # 复盘对照：路径价 ŷ_ON（可含当日 close）写入旁路字段，不覆盖决策 y_on
        try:
            from core.signal.dual_score.on import attach_on_score_pit

            q_path = _enrich_quote_path_price(dict(q or {}), day, code=raw)
            open_on = item.get("predicted_score_on")
            open_feats = (
                dict(item.get("features_on") or {})
                if isinstance(item.get("features_on"), dict)
                else {}
            )
            attach_on_score_pit(
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
            path_feats = item.get("features_on")
            # 恢复开盘决策口径
            item["predicted_score_on"] = open_on
            item["y_on"] = open_on
            if open_feats:
                item["features_on"] = open_feats
            if path_on is not None:
                item["predicted_score_on_path"] = path_on
                item["y_on_path"] = path_on
            if isinstance(path_feats, dict) and path_feats:
                item["features_on_path"] = dict(path_feats)
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
        _attach_y_path_to_item(item, hist_bars=hist)
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
            bars, _src = bars_and_source(code, limit=max(20, int(limit or 80)))
        except Exception:  # noqa: BLE001
            logger.debug("tau pool bars load failed for %s", code, exc_info=True)
            continue
        seq = [b for b in (bars or []) if isinstance(b, dict)]
        if len(seq) >= 2:
            out[code] = seq
    return out


def active_book_codes_for_tau_pool(*, cap: int = 120) -> List[str]:
    """活跃分池簿代码（刷簿同宇宙），供单票 T0 回测补截面。"""
    try:
        from core.signal.cluster.live import load_active_cluster_book

        book_doc = load_active_cluster_book() or {}
    except Exception:  # noqa: BLE001
        logger.debug("load active book for tau pool failed", exc_info=True)
        return []
    codes: List[str] = []
    seen = set()
    for row in list(book_doc.get("scored_all") or []) + list(book_doc.get("book") or []):
        if not isinstance(row, dict):
            continue
        c = str(row.get("stock_code") or row.get("code") or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        codes.append(c)
        if len(codes) >= max(8, int(cap or 120)):
            break
    return codes


def compute_scores_map_from_bars(
    specs: Sequence[Dict[str, Any]],
    *,
    fuse_intraday: bool = True,
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量开盘算分：共享模型缓存，并用截面 open 缺口作 pool_gaps。

    每个 spec: ``{code, hist_bars, day_bar?}``。
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
    try:
        from core.signal.cluster.live import load_active_cluster_book

        book_doc = load_active_cluster_book() or {}
        key = str(code or "").strip()
        for row in list(book_doc.get("scored_all") or []) + list(book_doc.get("book") or []):
            if not isinstance(row, dict):
                continue
            rc = str(row.get("stock_code") or row.get("code") or "").strip()
            if rc == key:
                try:
                    from core.signal.dual_score import align_trade_score_fields

                    align_trade_score_fields(row, write_score=False)
                except Exception:  # noqa: BLE001
                    logger.debug("align live book row failed", exc_info=True)
                sc = scores_from_item(row)
                if scores_have_any(sc):
                    sc["_score_source"] = "live_book"
                    return sc
    except Exception:  # noqa: BLE001
        logger.debug("live book score lookup failed", exc_info=True)
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
) -> Dict[str, Optional[float]]:
    """按 ``source`` 解析 dual_y 分数；默认即时算。

    ``compute`` 失败时仅回退 live 簿（不读冻结账本，避免半日污染快照）。
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
) -> Dict[str, Dict[str, Optional[float]]]:
    """批量取 dual_y 分数。

    默认 ``source=compute``：用日线批量即时算（截面缺口共享）。
    ``prefer_live_book=True`` 仅兼容旧调用（等价 source=live_book）。
    """
    out: Dict[str, Dict[str, Optional[float]]] = {}
    want = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    if not want:
        return out

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
        computed = compute_scores_map_from_bars(specs, fuse_intraday=True)
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
            fuse_intraday=True,
            allow_fallback=allow_fallback,
        )
        if scores_have_any(sc):
            out[code] = sc
    return out
