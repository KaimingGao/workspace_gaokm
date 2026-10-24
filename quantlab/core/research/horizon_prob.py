"""τ 后窗口概率头：P(窗口收益>0 | X≤τ)。

ŷ_τ30/45/60/75/90 只出 p_up∈(0,1)；不做幅度 Ridge。
正T p_agree=p_up，反T p_agree=1−p_up。票 f=sign(p_up−mid)；
mid 默认 50%（ŷ_τ* 共用中位点）；|p_up−mid|≤y_tw_vote_margin（默认 5pp）不投票。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.research.factor_ols_fit import (
    clamp_ridge_lambda,
    fit_factor_ols_from_panel,
)
from core.research.tc_ridge import (
    _ic,
    _oos_by_tau,
    _oos_by_theme,
    _oos_sign_buckets,
    _predict_rows,
    _sign_hit,
)

HORIZON_PROMOTE_MIN_N_TEST = 80
HORIZON_PROMOTE_MIN_AUC = 0.52
HORIZON_PROMOTE_MIN_ACC = 0.52
HORIZON_P_CLIP = 1e-6
HORIZON_ENTER_MAX = 0.7
HORIZON_VOTE_MARGIN_PP = 5.0  # 默认弃权带宽（百分点）
HORIZON_VOTE_MARGIN_PP_MAX = 20.0
HORIZON_VOTE_MARGIN = 0.05  # = PP/100；|p_up−mid|≤此值不投票
HORIZON_VOTE_MIDPOINT_PP = 50.0  # ŷ_τ* 共用中位点（百分点）
HORIZON_VOTE_MIDPOINT = 0.50  # = PP/100
LOGIT_CLIP = 30.0

HORIZON_STRONG_KEYS = (
    "y_t30_strong",
    "y_τ30_strong",
    "y_t45_strong",
    "y_τ45_strong",
    "y_t60_strong",
    "y_τ60_strong",
    "y_t75_strong",
    "y_τ75_strong",
    "y_t90_strong",
    "y_τ90_strong",
)
HORIZON_ENTER_KEYS = (
    "y_t30_enter",
    "y_τ30_enter",
    "y_t30_enter_alt",
    "y_τ30_enter_alt",
    "y_t45_enter",
    "y_τ45_enter",
    "y_t45_enter_alt",
    "y_τ45_enter_alt",
    "y_t60_enter",
    "y_τ60_enter",
    "y_t60_enter_alt",
    "y_τ60_enter_alt",
    "y_t75_enter",
    "y_τ75_enter",
    "y_t75_enter_alt",
    "y_τ75_enter_alt",
    "y_t90_enter",
    "y_τ90_enter",
    "y_t90_enter_alt",
    "y_τ90_enter_alt",
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def sigmoid(z: Any) -> Optional[float]:
    x = _f(z)
    if x is None:
        return None
    x = max(-LOGIT_CLIP, min(float(x), LOGIT_CLIP))
    if x >= 0:
        ez = math.exp(-x)
        p = 1.0 / (1.0 + ez)
    else:
        ez = math.exp(x)
        p = ez / (1.0 + ez)
    return max(HORIZON_P_CLIP, min(p, 1.0 - HORIZON_P_CLIP))


def binary_labels(ys: Sequence[Any], *, eps: float = 0.0) -> List[float]:
    out: List[float] = []
    for y in ys:
        v = _f(y)
        out.append(1.0 if v is not None and float(v) > float(eps) else 0.0)
    return out


def p_agree(direction: Optional[str], p_up: Any) -> Optional[float]:
    """正T=p_up，反T=1−p_up。缺方向/缺分 → None。"""
    d = str(direction or "").strip()
    p = _f(p_up)
    if d not in ("sell_then_buy", "buy_then_sell") or p is None:
        return None
    if d == "buy_then_sell":
        return float(p)
    return 1.0 - float(p)


def horizon_vote_band(margin_pp: Any = None) -> float:
    """``y_tw_vote_margin`` 为百分点（5=5pp）。返回 |p−mid| 的概率带宽。"""
    m = _f(margin_pp)
    if m is None:
        return HORIZON_VOTE_MARGIN
    return max(0.0, min(float(m), HORIZON_VOTE_MARGIN_PP_MAX)) / 100.0


def horizon_vote_midpoint(midpoint: Any = None) -> float:
    """ŷ_τ* 共用中位点。缺省 50%；>1 视为百分点。"""
    m = _f(midpoint)
    if m is None:
        return HORIZON_VOTE_MIDPOINT
    if abs(float(m)) > 1.0 + 1e-12:
        m = float(m) / 100.0
    return max(HORIZON_P_CLIP, min(float(m), 1.0 - HORIZON_P_CLIP))


def p_up_vote(
    p_up: Any,
    *,
    margin_pp: Any = None,
    midpoint: Any = None,
) -> Optional[int]:
    """sign(p_up−mid)；|p−mid|≤margin_pp（默认 5pp）或缺分不投票。mid 默认 50%。"""
    p = _f(p_up)
    if p is None:
        return None
    mid = horizon_vote_midpoint(midpoint)
    if abs(float(p) - mid) <= horizon_vote_band(margin_pp) + 1e-12:
        return None
    return 1 if float(p) > mid else -1


def horizon_band_agree(
    direction: Optional[str],
    p_up: Any,
    *,
    floor: float = 0.5,
) -> Optional[bool]:
    pa = p_agree(direction, p_up)
    if pa is None:
        return None
    return float(pa) + 1e-12 >= float(floor)


def is_prob_head(obj: Optional[dict]) -> bool:
    if not isinstance(obj, dict):
        return False
    rm = obj.get("return_model") if isinstance(obj.get("return_model"), dict) else obj
    if not isinstance(rm, dict):
        return False
    if str(rm.get("head_kind") or "") == "prob":
        return True
    spec = rm.get("y_spec") if isinstance(rm.get("y_spec"), dict) else {}
    return str(spec.get("unit") or "") == "prob"


def _model_role(doc: Optional[dict]) -> Optional[str]:
    if not isinstance(doc, dict):
        return None
    role = doc.get("model_role")
    if role:
        return str(role)
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
    nested = rm.get("model_role") if isinstance(rm, dict) else None
    return str(nested) if nested else None


def stamp_horizon_explain(
    expl: Optional[Dict[str, Any]],
    *,
    head: str,
    model_doc: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """拆解表补 head_kind/p_up：合计仍是 logit，p_up=sigmoid(logit)。"""
    if not expl:
        return None
    out = dict(expl)
    out["head"] = str(head)
    out["head_kind"] = "prob"
    role = _model_role(model_doc) or out.get("model_role")
    if role:
        out["model_role"] = str(role)
    total = _f(out.get("total"))
    if total is not None:
        out["logit"] = round(float(total), 6)
        p = sigmoid(total)
        if p is not None:
            out["p_up"] = round(float(p), 6)
    return out


def _signed_margin(preds_p: Sequence[Optional[float]]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for p in preds_p:
        v = _f(p)
        out.append((float(v) - 0.5) if v is not None else None)
    return out


def roc_auc(preds: Sequence[Optional[float]], ys_bin: Sequence[float]) -> Optional[float]:
    pairs = [
        (float(p), 1 if float(y) > 0.5 else 0)
        for p, y in zip(preds, ys_bin)
        if p is not None
    ]
    if len(pairs) < 5:
        return None
    pos = [p for p, y in pairs if y == 1]
    neg = [p for p, y in pairs if y == 0]
    if not pos or not neg:
        return None
    # Mann–Whitney：对每个正样本，负样本中严格更小的比例 + 平局一半
    neg_s = sorted(neg)
    auc_sum = 0.0
    for p in pos:
        lo = 0
        hi = len(neg_s)
        while lo < hi:
            mid = (lo + hi) // 2
            if neg_s[mid] < p:
                lo = mid + 1
            else:
                hi = mid
        n_lt = lo
        lo2 = 0
        hi2 = len(neg_s)
        while lo2 < hi2:
            mid = (lo2 + hi2) // 2
            if neg_s[mid] <= p:
                lo2 = mid + 1
            else:
                hi2 = mid
        n_le = lo2
        n_eq = n_le - n_lt
        auc_sum += n_lt + 0.5 * n_eq
    return round(auc_sum / (float(len(pos)) * float(len(neg))), 4)


def brier_score(preds: Sequence[Optional[float]], ys_bin: Sequence[float]) -> Optional[float]:
    errs: List[float] = []
    for p, y in zip(preds, ys_bin):
        if p is None:
            continue
        errs.append((float(p) - float(y)) ** 2)
    if len(errs) < 5:
        return None
    return round(sum(errs) / float(len(errs)), 6)


def log_loss(preds: Sequence[Optional[float]], ys_bin: Sequence[float]) -> Optional[float]:
    acc = 0.0
    n = 0
    for p, y in zip(preds, ys_bin):
        if p is None:
            continue
        q = max(HORIZON_P_CLIP, min(float(p), 1.0 - HORIZON_P_CLIP))
        yy = 1.0 if float(y) > 0.5 else 0.0
        acc += -(yy * math.log(q) + (1.0 - yy) * math.log(1.0 - q))
        n += 1
    if n < 5:
        return None
    return round(acc / float(n), 6)


def acc_at_50(preds: Sequence[Optional[float]], ys_bin: Sequence[float]) -> Optional[float]:
    """Acc@0.5：``I(p_up≥0.5)`` 与二值标签 ``I(y>0)`` 同号比例。

    与做 T ŷ_τw 投票中点（mid=50%）同口径；AUC 高但本项≈基率时说明概率未校准。
    """
    n = 0
    hit = 0
    for p, y in zip(preds, ys_bin):
        if p is None:
            continue
        n += 1
        pred_up = float(p) >= 0.5
        y_up = float(y) > 0.5
        if pred_up == y_up:
            hit += 1
    if n < 5:
        return None
    return round(hit / float(n), 4)


def precision_recall_at_50(
    preds: Sequence[Optional[float]], ys_bin: Sequence[float]
) -> Dict[str, Optional[float]]:
    """阈值 0.5 的 Precision / Recall（正类=窗收益>0）。

    - Precision@0.5：报涨（p≥0.5）里真涨占比；报涨极少时为 None
    - Recall@0.5：真涨里被报涨占比；几乎从不报涨时 → 接近 0
    - pred_up_rate：p≥0.5 占比（看概率是否压在 0.5 下）
    """
    tp = fp = fn = 0
    n = 0
    for p, y in zip(preds, ys_bin):
        if p is None:
            continue
        n += 1
        pred_up = float(p) >= 0.5
        y_up = float(y) > 0.5
        if pred_up and y_up:
            tp += 1
        elif pred_up and not y_up:
            fp += 1
        elif (not pred_up) and y_up:
            fn += 1
    if n < 5:
        return {
            "precision_at_50": None,
            "recall_at_50": None,
            "pred_up_rate": None,
            "n_pred_up": 0,
        }
    n_pred_up = tp + fp
    n_pos = tp + fn
    prec = round(tp / float(n_pred_up), 4) if n_pred_up >= 5 else None
    rec = round(tp / float(n_pos), 4) if n_pos >= 5 else None
    return {
        "precision_at_50": prec,
        "recall_at_50": rec,
        "pred_up_rate": round(n_pred_up / float(n), 4),
        "n_pred_up": int(n_pred_up),
    }


def predict_p_up_rows(
    fit: Dict[str, Any],
    xs: List[dict],
    *,
    impute_missing: bool = True,
    row_dates: Optional[Sequence[str]] = None,
) -> List[Optional[float]]:
    rows = xs
    if row_dates is not None and len(row_dates) == len(xs):
        from core.research.feature_standardize import (
            cross_section_zscore_dicts,
            is_cross_section_zscore,
        )

        if is_cross_section_zscore(fit):
            names = [
                str(n)
                for n in (fit.get("active_features") or [])
                if (fit.get("coefficients") or {}).get(n) is not None
            ]
            if names:
                try:
                    from core.signal.factors.z_th import stamp_z_th_aliases

                    stamped = []
                    for r in xs:
                        row = dict(r) if isinstance(r, dict) else {}
                        stamp_z_th_aliases(row)
                        stamped.append(row)
                    xs_use = stamped
                except Exception:  # noqa: BLE001
                    xs_use = xs
                try:
                    from core.research.materialize.cube_z import cs_z_dicts_prefer_cube
                    from core.research.materialize.consumer import MaterializeRequired

                    rows = cs_z_dicts_prefer_cube(xs_use, names, row_dates)
                except MaterializeRequired:
                    raise
                except Exception:  # noqa: BLE001
                    rows = cross_section_zscore_dicts(xs_use, names, row_dates)
    logits = _predict_rows(fit, rows, impute_missing=impute_missing)
    return [sigmoid(p) if p is not None else None for p in logits]


def oos_prob_pack(
    preds_p: Sequence[Optional[float]],
    ys_pct: Sequence[float],
    metas: Optional[Sequence[dict]] = None,
    *,
    use_minute: bool = True,
) -> Dict[str, Any]:
    ys_bin = binary_labels(ys_pct)
    margin = _signed_margin(preds_p)
    y_list = [float(y) for y in ys_pct]
    meta_list = list(metas or [])
    buckets = _oos_sign_buckets(margin, y_list) if y_list else {}
    by_theme = _oos_by_theme(margin, y_list, meta_list) if y_list and meta_list else {}
    by_tau = (
        _oos_by_tau(margin, y_list, meta_list)
        if y_list and meta_list and use_minute
        else {}
    )
    auc = roc_auc(preds_p, ys_bin)
    brier = brier_score(preds_p, ys_bin)
    acc = acc_at_50(preds_p, ys_bin)
    pr50 = precision_recall_at_50(preds_p, ys_bin)
    return {
        "n_valid": buckets.get("n_valid") if isinstance(buckets, dict) else None,
        "ic": _ic(preds_p, y_list) if y_list else None,
        "sign_hit": _sign_hit(margin, y_list) if y_list else None,
        "acc_at_50": acc,
        # 对照表别名：与 Acc@0.5 / acc_at_50 同值
        "hit_rate": acc,
        "precision_at_50": pr50.get("precision_at_50"),
        "recall_at_50": pr50.get("recall_at_50"),
        "pred_up_rate": pr50.get("pred_up_rate"),
        "n_pred_up": pr50.get("n_pred_up"),
        "auc": auc,
        "brier": brier,
        "logloss": log_loss(preds_p, ys_bin),
        "residual_var": brier,
        "by_theme": by_theme,
        "by_tau": by_tau,
        "buckets": (buckets.get("buckets") or {}) if isinstance(buckets, dict) else {},
        "pos_recall": buckets.get("pos_recall") if isinstance(buckets, dict) else None,
        "neg_recall": buckets.get("neg_recall") if isinstance(buckets, dict) else None,
        "n_pos": buckets.get("n_pos") if isinstance(buckets, dict) else None,
        "n_neg": buckets.get("n_neg") if isinstance(buckets, dict) else None,
        "head_kind": "prob",
        "y_up_rate": round(sum(ys_bin) / float(len(ys_bin)), 4) if ys_bin else None,
    }


def _oos_metric_float(m: Any, key: str) -> Optional[float]:
    if not isinstance(m, dict):
        return None
    if m.get("success") is False:
        return None
    v = m.get(key)
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def select_horizon_backend(
    oos_tree: Optional[Dict[str, Any]] = None,
    oos_ridge: Optional[Dict[str, Any]] = None,
) -> str:
    """按 Holdout AUC 选更好的 backend；打平或缺指标 → ``ridge``。

    次序：AUC（高更好）→ Brier（低更好）→ 默认 ridge。
    """
    ta = _oos_metric_float(oos_tree, "auc")
    ra = _oos_metric_float(oos_ridge, "auc")
    if ta is not None and ra is not None:
        if ta > ra + 1e-12:
            return "tree"
        if ra > ta + 1e-12:
            return "ridge"
    elif ta is not None and ra is None:
        return "tree"
    elif ra is not None and ta is None:
        return "ridge"

    tb = _oos_metric_float(oos_tree, "brier")
    rb = _oos_metric_float(oos_ridge, "brier")
    if tb is not None and rb is not None:
        if tb < rb - 1e-12:
            return "tree"
        if rb < tb - 1e-12:
            return "ridge"
    elif tb is not None and rb is None:
        return "tree"
    elif rb is not None and tb is None:
        return "ridge"
    return "ridge"


def _horizon_oos_side_usable(side: Any) -> bool:
    """嵌套 oos.ridge / oos.tree 是否含可用晋级指标（空 dict 不算）。"""
    if not isinstance(side, dict) or not side:
        return False
    return (
        side.get("auc") is not None
        or side.get("acc_at_50") is not None
        or side.get("sign_hit") is not None
        or side.get("n_test") is not None
        or side.get("n_valid") is not None
    )


def resolve_horizon_oos_for_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """取晋级闸用的 OOS：双 backend 时用胜出侧，否则用扁平 ``oos``。"""
    rep = report if isinstance(report, dict) else {}
    oos = rep.get("oos") if isinstance(rep.get("oos"), dict) else {}
    if not oos:
        return {}
    rec = str(
        rep.get("recommended_backend")
        or rep.get("backend")
        or oos.get("recommended_backend")
        or ""
    ).strip().lower()
    if rec in ("tree", "ridge") and _horizon_oos_side_usable(oos.get(rec)):
        return dict(oos[rec])
    # 兼容：扁平 winner 指标叠在 oos 顶层（含 auc）
    if oos.get("auc") is not None or oos.get("acc_at_50") is not None:
        return dict(oos)
    if _horizon_oos_side_usable(oos.get("ridge")):
        return dict(oos["ridge"])
    if _horizon_oos_side_usable(oos.get("tree")):
        return dict(oos["tree"])
    return dict(oos)


def horizon_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote：OOS AUC / Acc@0.5（acc_at_50）/ n_test。不再用 |ŷ|≥0.6。"""
    oos = resolve_horizon_oos_for_gate(report)
    n = oos.get("n_valid")
    if n is None:
        n = oos.get("n_test")
    try:
        n_i = int(n or 0)
    except (TypeError, ValueError):
        n_i = 0
    auc = oos.get("auc")
    acc = oos.get("acc_at_50")
    if acc is None:
        acc = oos.get("hit_rate")
    if acc is None:
        acc = oos.get("sign_hit")
    try:
        auc_f = float(auc) if auc is not None else None
    except (TypeError, ValueError):
        auc_f = None
    try:
        acc_f = float(acc) if acc is not None else None
    except (TypeError, ValueError):
        acc_f = None
    blockers: List[str] = []
    if n_i < HORIZON_PROMOTE_MIN_N_TEST:
        blockers.append(f"n_test={n_i}<{HORIZON_PROMOTE_MIN_N_TEST}")
    if auc_f is None and acc_f is None:
        blockers.append("缺 OOS AUC/acc")
    else:
        auc_ok = auc_f is not None and auc_f + 1e-12 >= HORIZON_PROMOTE_MIN_AUC
        acc_ok = acc_f is not None and acc_f + 1e-12 >= HORIZON_PROMOTE_MIN_ACC
        if not auc_ok and not acc_ok:
            parts = []
            if auc_f is not None:
                parts.append(f"auc={auc_f:.3f}<{HORIZON_PROMOTE_MIN_AUC}")
            if acc_f is not None:
                parts.append(f"acc={acc_f:.3f}<{HORIZON_PROMOTE_MIN_ACC}")
            blockers.append("；".join(parts) or "OOS 未过概率闸")
    backend = None
    if isinstance(report, dict):
        backend = report.get("recommended_backend") or report.get("backend")
    return {
        "ok": not blockers,
        "blockers": blockers,
        "min_auc": HORIZON_PROMOTE_MIN_AUC,
        "min_acc": HORIZON_PROMOTE_MIN_ACC,
        "min_n_test": HORIZON_PROMOTE_MIN_N_TEST,
        "n_test": n_i,
        "auc": auc_f,
        "acc_at_50": acc_f,
        "head_kind": "prob",
        "backend": backend,
    }


def _logit_mean(ys_bin: Sequence[float]) -> float:
    if not ys_bin:
        return 0.0
    p = sum(float(y) for y in ys_bin) / float(len(ys_bin))
    p = max(HORIZON_P_CLIP, min(p, 1.0 - HORIZON_P_CLIP))
    return math.log(p / (1.0 - p))


def _irls_logistic(
    x: np.ndarray,
    y: np.ndarray,
    *,
    ridge_lambda: float = 1.0,
    sample_weights: Optional[np.ndarray] = None,
    beta0: Optional[np.ndarray] = None,
    max_iter: int = 40,
) -> Optional[np.ndarray]:
    n, m = x.shape
    if n < 4 or m < 1 or y.shape[0] != n:
        return None
    lam = clamp_ridge_lambda(ridge_lambda, 1.0)
    beta = (
        np.asarray(beta0, dtype=np.float64).reshape(-1)
        if beta0 is not None and int(np.asarray(beta0).size) == m
        else np.zeros(m, dtype=np.float64)
    )
    if beta.size != m:
        beta = np.zeros(m, dtype=np.float64)
    if not np.any(np.abs(beta) > 0):
        beta[0] = _logit_mean(y.tolist())
    w_row = (
        np.asarray(sample_weights, dtype=np.float64).reshape(-1)
        if sample_weights is not None
        else np.ones(n, dtype=np.float64)
    )
    if w_row.shape[0] != n:
        w_row = np.ones(n, dtype=np.float64)
    w_row = np.where(np.isfinite(w_row) & (w_row > 0), w_row, 1e-6)
    penalty = np.zeros((m, m), dtype=np.float64)
    if lam > 0 and m > 1:
        for j in range(1, m):
            penalty[j, j] = float(lam)
    last = beta.copy()
    for _ in range(max(8, int(max_iter))):
        eta = np.clip(x @ beta, -LOGIT_CLIP, LOGIT_CLIP)
        p = 1.0 / (1.0 + np.exp(-eta))
        p = np.clip(p, HORIZON_P_CLIP, 1.0 - HORIZON_P_CLIP)
        var = p * (1.0 - p)
        w = w_row * var
        z = eta + (y - p) / var
        xtw = x.T * w
        hess = xtw @ x + penalty
        rhs = xtw @ z
        try:
            beta = np.linalg.solve(hess, rhs)
        except np.linalg.LinAlgError:
            try:
                beta = np.linalg.lstsq(hess, rhs, rcond=None)[0]
            except Exception:  # noqa: BLE001
                return last if np.all(np.isfinite(last)) else None
        if not np.all(np.isfinite(beta)):
            return last if np.all(np.isfinite(last)) else None
        if float(np.max(np.abs(beta - last))) < 1e-8:
            return beta
        last = beta.copy()
    return beta


def _stamp_prob_model(fit: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(fit or {})
    out["head_kind"] = "prob"
    spec = dict(out.get("y_spec") or {}) if isinstance(out.get("y_spec"), dict) else {}
    spec["unit"] = "prob"
    spec.setdefault("label", "I(window_return>0)")
    out["y_spec"] = spec
    return out


def _empty_prob_fit(*, intercept: float = 0.0, note: str = "") -> Dict[str, Any]:
    return _stamp_prob_model(
        {
            "success": True,
            "intercept": round(float(intercept), 6),
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": note or "Z 方差不足，ŷ 用训练基率",
            "solver": "logistic_ridge",
        }
    )


def fit_logistic_ridge_from_panel(
    xs: List[Dict[str, Optional[float]]],
    ys_bin: List[float],
    *,
    feature_names: Optional[List[str]] = None,
    ridge_lambda: float = 1.0,
    sample_weights: Optional[List[float]] = None,
    collinearity_policy: str = "drop_redundant",
    row_dates: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """面板 logistic Ridge：先走与 OLS 相同的完整子面板 / z-score / 共线策略，再 IRLS。

    入模列缺测按列均值填后再挑行（标准化后 z=0），与
    ``_predict_rows(impute_missing=True)`` / live 打分一致。
    ``cross_section_zscore=False`` 时不传日期，走训练窗全局 μ/σ。
    """
    names = [str(n) for n in (feature_names or []) if n]
    ys = [1.0 if _f(y) is not None and float(y) > 0.5 else 0.0 for y in ys_bin]
    use_cs = bool(cross_section_zscore) and row_dates is not None
    ols = fit_factor_ols_from_panel(
        xs,
        ys,
        feature_names=feature_names,
        ridge_lambda=ridge_lambda,
        feature_zscore=True,
        sample_weights=sample_weights,
        collinearity_policy=collinearity_policy,
        impute_keys=names,
        y_spec={"formula": "I(y>0)", "unit": "prob"},
        row_dates=row_dates if use_cs else None,
    )
    if not ols.get("success"):
        return _empty_prob_fit(intercept=_logit_mean(ys), note=str(ols.get("error") or ""))

    active = [str(n) for n in (ols.get("active_features") or []) if n]
    coefs = ols.get("coefficients") or {}
    active = [n for n in active if coefs.get(n) is not None]
    intercept0 = float(ols.get("intercept") or 0.0)
    if not active:
        out = dict(ols)
        out["intercept"] = round(_logit_mean(ys), 6)
        out["solver"] = "logistic_ridge"
        out["r_squared"] = None
        return _stamp_prob_model(out)

    means = ols.get("zscore_means") or ols.get("z_means") or {}
    stds = ols.get("zscore_stds") or ols.get("z_stds") or {}
    from core.research.feature_standardize import (
        cross_section_zscore_dicts,
        is_cross_section_zscore,
    )

    xs_irls = xs
    if is_cross_section_zscore(ols) and row_dates is not None and len(row_dates) == len(xs):
        # IRLS 与推理同口径：先日截面 z，再按 μ=0/σ=1 入模
        try:
            from core.research.materialize.cube_z import cs_z_dicts_prefer_cube
            from core.research.materialize.consumer import MaterializeRequired

            xs_irls = cs_z_dicts_prefer_cube(xs, active, row_dates)
        except MaterializeRequired:
            raise
        except Exception:  # noqa: BLE001
            xs_irls = cross_section_zscore_dicts(xs, active, row_dates)
    rows_x: List[List[float]] = []
    rows_y: List[float] = []
    rows_w: List[float] = []
    w_src = list(sample_weights) if sample_weights is not None else None
    for i, row in enumerate(xs_irls or []):
        src = row if isinstance(row, dict) else {}
        vec = [1.0]
        ok = True
        for name in active:
            v = src.get(name)
            if v is None:
                z = 0.0
            else:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    z = 0.0
                else:
                    mu = float(means.get(name, 0.0) or 0.0)
                    sd = float(stds.get(name, 1.0) or 1.0)
                    if sd < 1e-9:
                        sd = 1.0
                    z = (fv - mu) / sd
            vec.append(z)
        if not ok:
            continue
        yy = ys[i] if i < len(ys) else 0.0
        rows_x.append(vec)
        rows_y.append(float(yy))
        if w_src is not None and i < len(w_src):
            try:
                ww = float(w_src[i])
            except (TypeError, ValueError):
                ww = 1.0
            rows_w.append(ww if math.isfinite(ww) and ww > 0 else 1e-6)
        else:
            rows_w.append(1.0)

    if len(rows_y) < 8:
        return _empty_prob_fit(intercept=_logit_mean(ys))

    x = np.asarray(rows_x, dtype=np.float64)
    y = np.asarray(rows_y, dtype=np.float64)
    w = np.asarray(rows_w, dtype=np.float64)
    beta0 = np.array(
        [intercept0] + [float(coefs.get(n) or 0.0) for n in active],
        dtype=np.float64,
    )
    beta = _irls_logistic(
        x,
        y,
        ridge_lambda=ridge_lambda,
        sample_weights=w,
        beta0=beta0,
    )
    if beta is None or not np.all(np.isfinite(beta)):
        return _empty_prob_fit(intercept=_logit_mean(ys), note="logistic IRLS 未收敛")

    eta = np.clip(x @ beta, -LOGIT_CLIP, LOGIT_CLIP)
    p = 1.0 / (1.0 + np.exp(-eta))
    p = np.clip(p, HORIZON_P_CLIP, 1.0 - HORIZON_P_CLIP)
    # McFadden 伪 R²
    ll = float(np.sum(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
    pbar = float(np.mean(y))
    pbar = max(HORIZON_P_CLIP, min(pbar, 1.0 - HORIZON_P_CLIP))
    ll0 = float(
        np.sum(y * math.log(pbar) + (1.0 - y) * math.log(1.0 - pbar))
    )
    pseudo_r2 = 1.0 - ll / ll0 if abs(ll0) > 1e-12 else None

    coef_map = dict(coefs)
    for i, name in enumerate(active):
        coef_map[name] = round(float(beta[i + 1]), 6)
    out = dict(ols)
    out["success"] = True
    out["intercept"] = round(float(beta[0]), 6)
    out["coefficients"] = coef_map
    out["active_features"] = list(active)
    out["r_squared"] = round(pseudo_r2, 4) if pseudo_r2 is not None else None
    out["pseudo_r2"] = round(pseudo_r2, 4) if pseudo_r2 is not None else None
    out["solver"] = "logistic_ridge"
    out["ridge_lambda"] = clamp_ridge_lambda(ridge_lambda, 1.0)
    return _stamp_prob_model(out)


def _slice_feat_matrix(
    X: Any,
    col_names: Sequence[str],
    feat_names: Sequence[str],
    row_idx: Sequence[int],
) -> Tuple[np.ndarray, List[str]]:
    """从紧凑 ``X`` 按列名+行下标切片，避免 dict 物化。

    越界行填 NaN 行（不抛）；缺列跳过。``X`` 非 2D 时返回空矩阵。
    """
    try:
        arr = np.asarray(X, dtype=np.float64)
    except (TypeError, ValueError):
        return np.zeros((len(list(row_idx or [])), 0), dtype=np.float64), []
    if arr.ndim != 2:
        return np.zeros((len(list(row_idx or [])), 0), dtype=np.float64), []
    name_index = {str(n): i for i, n in enumerate(col_names or [])}
    cols: List[int] = []
    used: List[str] = []
    for n in feat_names or []:
        j = name_index.get(str(n))
        if j is None:
            continue
        if j < 0 or j >= int(arr.shape[1]):
            continue
        cols.append(j)
        used.append(str(n))
    n_req = len(list(row_idx or []))
    if not used:
        return np.zeros((n_req, 0), dtype=np.float64), []
    if n_req == 0:
        return np.zeros((0, len(used)), dtype=np.float64), used
    n_rows = int(arr.shape[0])
    out = np.full((n_req, len(used)), np.nan, dtype=np.float64)
    for i, raw in enumerate(row_idx or []):
        try:
            ii = int(raw)
        except (TypeError, ValueError):
            continue
        if ii < 0 or ii >= n_rows:
            continue
        out[i, :] = arr[ii, cols]
    return np.ascontiguousarray(out), used


def _population_z_matrix(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """列总体 μ/σ 标准化；缺测先按列均值填。返回 ``(z, means, stds)``。"""
    n_obs = np.sum(np.isfinite(x), axis=0).astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        means = np.nanmean(x, axis=0)
    means = np.where(np.isfinite(means), means, 0.0)
    filled = np.where(np.isfinite(x), x, means)
    centered = filled - means
    with np.errstate(invalid="ignore", divide="ignore"):
        var = np.mean(centered * centered, axis=0)
    var = np.where(np.isfinite(var), var, 0.0)
    stds = np.sqrt(var)
    stds = np.where((n_obs > 0) & (var > 1e-12), stds, 1.0)
    z = centered / stds
    return z, means, stds


def _drop_near_constant_cols(
    x: np.ndarray, names: Sequence[str], *, eps: float = 1e-12
) -> Tuple[np.ndarray, List[str]]:
    keep: List[int] = []
    kept_names: List[str] = []
    for j, name in enumerate(names):
        col = x[:, j]
        finite = col[np.isfinite(col)]
        if finite.size < 4:
            continue
        if float(np.max(finite) - np.min(finite)) <= eps:
            continue
        keep.append(j)
        kept_names.append(str(name))
    if not keep:
        return np.zeros((x.shape[0], 0), dtype=np.float64), []
    return np.ascontiguousarray(x[:, keep]), kept_names


def fit_logistic_ridge_from_matrix(
    X: np.ndarray,
    ys_bin: Sequence[float],
    feat_names: Sequence[str],
    *,
    ridge_lambda: float = 1.0,
    sample_weights: Optional[Sequence[float]] = None,
    row_groups: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
    collinearity_policy: str = "drop_redundant",
) -> Dict[str, Any]:
    """矩阵 logistic Ridge（与 panel 路径同口径：CS-z → IRLS）。

    供 horizon 紧凑 ``X`` 路径使用，跳过 ``List[dict]`` 物化。
    ``MaterializeRequired`` 向上抛；其它异常降级为基率空模（不拖垮双训）。
    """
    from core.research.feature_standardize import stamp_cross_section_zscore
    from core.research.materialize.consumer import MaterializeRequired

    y = np.asarray(
        [1.0 if _f(v) is not None and float(v) > 0.5 else 0.0 for v in ys_bin],
        dtype=np.float64,
    )
    names = [str(n) for n in feat_names if n]
    try:
        x_raw = np.asarray(X, dtype=np.float64)
    except (TypeError, ValueError):
        return _empty_prob_fit(intercept=_logit_mean(y.tolist()), note="矩阵不可读")
    if x_raw.ndim != 2 or x_raw.shape[0] != y.shape[0] or not names:
        return _empty_prob_fit(intercept=_logit_mean(y.tolist()), note="矩阵维度异常")
    if int(x_raw.shape[0]) < 8:
        return _empty_prob_fit(intercept=_logit_mean(y.tolist()), note="训练样本过少")
    if x_raw.shape[1] != len(names):
        # 列数与名不对齐时按最短截断
        p = min(int(x_raw.shape[1]), len(names))
        logger.warning(
            "horizon matrix cols!=names (%s vs %s); truncate to %s",
            int(x_raw.shape[1]),
            len(names),
            p,
        )
        x_raw = x_raw[:, :p]
        names = names[:p]

    use_cs = bool(cross_section_zscore) and row_groups is not None
    z_means: Dict[str, float] = {}
    z_stds: Dict[str, float] = {}
    groups_ok = (
        use_cs
        and row_groups is not None
        and len(row_groups) == int(x_raw.shape[0])
    )
    if use_cs and not groups_ok:
        logger.warning(
            "horizon matrix CS groups len=%s != n=%s; fall back to train-window z",
            len(row_groups or []),
            int(x_raw.shape[0]),
        )
        use_cs = False
    if groups_ok:
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        try:
            x_z = cs_z_matrix_prefer_cube(x_raw, names, list(row_groups or []))
        except MaterializeRequired:
            raise
        except Exception:  # noqa: BLE001
            from core.research.feature_standardize import cross_section_zscore_matrix

            try:
                x_z = cross_section_zscore_matrix(x_raw, list(row_groups or []))
                x_z = np.where(np.isfinite(x_z), x_z, 0.0)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "horizon matrix CS-z failed; fall back to train-window z",
                    exc_info=True,
                )
                x_z, mu, sd = _population_z_matrix(x_raw)
                use_cs = False
                z_means = {names[j]: float(mu[j]) for j in range(len(names))}
                z_stds = {names[j]: float(sd[j]) for j in range(len(names))}
    else:
        use_cs = False
        x_z, mu, sd = _population_z_matrix(x_raw)
        z_means = {names[j]: float(mu[j]) for j in range(len(names))}
        z_stds = {names[j]: float(sd[j]) for j in range(len(names))}

    x_z, active = _drop_near_constant_cols(x_z, names)
    if not active:
        return _empty_prob_fit(intercept=_logit_mean(y.tolist()), note="Z 方差不足")

    from core.research.beta_accuracy import apply_collinearity_on_columns

    cols = {active[j]: x_z[:, j] for j in range(len(active))}
    kept, _dropped, _meta = apply_collinearity_on_columns(
        cols,
        list(active),
        y.tolist(),
        policy=collinearity_policy,
    )
    if kept != list(active):
        pos = {n: i for i, n in enumerate(active)}
        idx = [pos[n] for n in kept if n in pos]
        x_z = np.ascontiguousarray(x_z[:, idx])
        active = list(kept)
    if not active:
        return _empty_prob_fit(intercept=_logit_mean(y.tolist()), note="共线剔除后无特征")

    w = None
    if sample_weights is not None:
        try:
            w = np.asarray(list(sample_weights), dtype=np.float64)
        except (TypeError, ValueError):
            w = None
        if w is not None and w.shape[0] != y.shape[0]:
            logger.warning(
                "horizon matrix weights len=%s != n=%s; ignore weights",
                int(w.shape[0]),
                int(y.shape[0]),
            )
            w = None

    ones = np.ones((x_z.shape[0], 1), dtype=np.float64)
    design = np.concatenate([ones, x_z], axis=1)
    # 线性概率 OLS 作 IRLS 初值；失败则用 logit 基率
    lam = clamp_ridge_lambda(ridge_lambda, 1.0)
    beta0 = np.zeros(design.shape[1], dtype=np.float64)
    beta0[0] = _logit_mean(y.tolist())
    try:
        xtx = design.T @ design
        if lam > 0 and design.shape[1] > 1:
            for j in range(1, design.shape[1]):
                xtx[j, j] += float(lam)
        try:
            beta0 = np.linalg.solve(xtx, design.T @ y)
        except np.linalg.LinAlgError:
            beta0 = np.linalg.lstsq(xtx, design.T @ y, rcond=None)[0]
        if not np.all(np.isfinite(beta0)):
            beta0 = np.zeros(design.shape[1], dtype=np.float64)
            beta0[0] = _logit_mean(y.tolist())
    except Exception:  # noqa: BLE001
        logger.debug("horizon matrix OLS init failed", exc_info=True)
    beta = _irls_logistic(
        design,
        y,
        ridge_lambda=ridge_lambda,
        sample_weights=w,
        beta0=beta0,
    )
    if beta is None or not np.all(np.isfinite(beta)):
        return _empty_prob_fit(
            intercept=_logit_mean(y.tolist()), note="logistic IRLS 未收敛"
        )

    eta = np.clip(design @ beta, -LOGIT_CLIP, LOGIT_CLIP)
    p = 1.0 / (1.0 + np.exp(-eta))
    p = np.clip(p, HORIZON_P_CLIP, 1.0 - HORIZON_P_CLIP)
    ll = float(np.sum(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
    pbar = float(np.mean(y))
    pbar = max(HORIZON_P_CLIP, min(pbar, 1.0 - HORIZON_P_CLIP))
    ll0 = float(np.sum(y * math.log(pbar) + (1.0 - y) * math.log(1.0 - pbar)))
    pseudo_r2 = 1.0 - ll / ll0 if abs(ll0) > 1e-12 else None

    coef_map = {name: round(float(beta[i + 1]), 6) for i, name in enumerate(active)}
    out: Dict[str, Any] = {
        "success": True,
        "intercept": round(float(beta[0]), 6),
        "coefficients": coef_map,
        "active_features": list(active),
        "excluded_features": [n for n in names if n not in set(active)],
        "r_squared": round(pseudo_r2, 4) if pseudo_r2 is not None else None,
        "pseudo_r2": round(pseudo_r2, 4) if pseudo_r2 is not None else None,
        "solver": "logistic_ridge",
        "ridge_lambda": clamp_ridge_lambda(ridge_lambda, 1.0),
        "feature_zscore": True,
        "zscore_means": z_means if not use_cs else {},
        "zscore_stds": z_stds if not use_cs else {},
        "y_spec": {"formula": "I(y>0)", "unit": "prob"},
    }
    if use_cs:
        stamp_cross_section_zscore(out)
    return _stamp_prob_model(out)


def predict_p_up_matrix(
    fit: Dict[str, Any],
    X: np.ndarray,
    feat_names: Sequence[str],
    *,
    row_groups: Optional[Sequence[str]] = None,
) -> List[Optional[float]]:
    """矩阵推理 p_up；截面 z 时 ``row_groups`` 与行对齐。"""
    from core.research.feature_standardize import is_cross_section_zscore
    from core.research.materialize.consumer import MaterializeRequired

    def _base_rate(n: int) -> List[Optional[float]]:
        p0 = sigmoid(float((fit or {}).get("intercept") or 0.0))
        return [p0] * max(0, int(n))

    try:
        arr = np.asarray(X, dtype=np.float64)
    except (TypeError, ValueError):
        return []
    n = int(arr.shape[0]) if arr.ndim == 2 else 0
    if n == 0:
        return []

    active = [
        str(n0)
        for n0 in ((fit or {}).get("active_features") or [])
        if ((fit or {}).get("coefficients") or {}).get(n0) is not None
    ]
    if not active:
        return _base_rate(n)

    name_index = {str(n0): i for i, n0 in enumerate(feat_names or [])}
    cols = [name_index[n0] for n0 in active if n0 in name_index]
    used = [n0 for n0 in active if n0 in name_index]
    if not used or arr.ndim != 2:
        return _base_rate(n)
    # 跳过越界列
    cols = [j for j in cols if 0 <= int(j) < int(arr.shape[1])]
    used = used[: len(cols)]
    if not used:
        return _base_rate(n)

    x_raw = np.ascontiguousarray(arr[:, cols])
    use_cs = (
        is_cross_section_zscore(fit)
        and row_groups is not None
        and len(row_groups) == x_raw.shape[0]
    )
    if use_cs:
        from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

        try:
            x_z = cs_z_matrix_prefer_cube(x_raw, used, list(row_groups))
        except MaterializeRequired:
            raise
        except Exception:  # noqa: BLE001
            from core.research.feature_standardize import cross_section_zscore_matrix

            try:
                x_z = cross_section_zscore_matrix(x_raw, list(row_groups))
                x_z = np.where(np.isfinite(x_z), x_z, 0.0)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "predict_p_up_matrix CS-z failed; use raw/impute", exc_info=True
                )
                x_z = np.where(np.isfinite(x_raw), x_raw, 0.0)
    else:
        means_m = (fit or {}).get("zscore_means") or {}
        stds_m = (fit or {}).get("zscore_stds") or {}
        mu = np.array(
            [float(means_m.get(n0, 0.0) or 0.0) for n0 in used], dtype=np.float64
        )
        sd = np.array(
            [max(1e-9, float(stds_m.get(n0, 1.0) or 1.0)) for n0 in used],
            dtype=np.float64,
        )
        filled = np.where(np.isfinite(x_raw), x_raw, mu)
        x_z = (filled - mu) / sd

    coefs = (fit or {}).get("coefficients") or {}
    beta = np.array(
        [float((fit or {}).get("intercept") or 0.0)]
        + [float(coefs.get(n0) or 0.0) for n0 in used],
        dtype=np.float64,
    )
    ones = np.ones((x_z.shape[0], 1), dtype=np.float64)
    eta = np.clip(np.concatenate([ones, x_z], axis=1) @ beta, -LOGIT_CLIP, LOGIT_CLIP)
    return [sigmoid(float(v)) for v in eta.tolist()]


def train_eval_horizon_prob_matrix(
    X: np.ndarray,
    col_names: Sequence[str],
    ys_tr_pct: Sequence[float],
    ys_te_pct: Sequence[float],
    metas_te: Sequence[dict],
    train_idx: Sequence[int],
    test_idx: Sequence[int],
    *,
    feat_names: List[str],
    ridge_lambda: float,
    weights: Optional[Sequence[float]],
    groups_tr: Optional[Sequence[str]] = None,
    groups_te: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """Holdout 训/测（紧凑矩阵）；只训 Holdout 前一套 β。"""
    y_mean_pct = (
        sum(float(y) for y in ys_tr_pct) / float(len(ys_tr_pct)) if ys_tr_pct else 0.0
    )
    ys_tr_bin = binary_labels(ys_tr_pct)
    use_cs = bool(cross_section_zscore)
    n_tr = len(list(train_idx or []))
    n_te = len(list(test_idx or []))
    if len(ys_tr_pct) != n_tr:
        logger.warning(
            "train_eval matrix ys_tr=%s != train_idx=%s", len(ys_tr_pct), n_tr
        )
    if groups_tr is not None and len(groups_tr) != n_tr:
        logger.warning(
            "train_eval matrix groups_tr=%s != train_idx=%s; disable CS",
            len(groups_tr),
            n_tr,
        )
        groups_tr = None
        use_cs = False
    if groups_te is not None and len(groups_te) != n_te:
        logger.warning(
            "train_eval matrix groups_te=%s != test_idx=%s; drop te CS",
            len(groups_te),
            n_te,
        )
        groups_te = None
    x_tr, used = _slice_feat_matrix(X, col_names, feat_names, train_idx)
    x_te, used_te = _slice_feat_matrix(X, col_names, used or feat_names, test_idx)
    if used_te and used and used_te != used:
        # 列对齐：按 used 再切测集
        x_te, _ = _slice_feat_matrix(X, col_names, used, test_idx)
    fit = fit_logistic_ridge_from_matrix(
        x_tr,
        ys_tr_bin,
        used or feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=weights,
        row_groups=groups_tr if use_cs else None,
        cross_section_zscore=use_cs,
        collinearity_policy="drop_redundant",
    )
    preds_te = (
        predict_p_up_matrix(
            fit,
            x_te,
            used or feat_names,
            row_groups=groups_te if use_cs else None,
        )
        if n_te
        else []
    )
    # 预测长度与测试标签对齐（防 OOS 错位）
    if len(preds_te) != len(ys_te_pct):
        logger.warning(
            "train_eval matrix preds=%s != ys_te=%s; pad/truncate",
            len(preds_te),
            len(ys_te_pct),
        )
        if len(preds_te) < len(ys_te_pct):
            pad = sigmoid(float(fit.get("intercept") or 0.0))
            preds_te = list(preds_te) + [pad] * (len(ys_te_pct) - len(preds_te))
        else:
            preds_te = list(preds_te)[: len(ys_te_pct)]
    oos_core = oos_prob_pack(preds_te, ys_te_pct, metas_te) if ys_te_pct else {}
    oos_core["y_label_mean"] = round(y_mean_pct, 6)
    oos_core["y_up_rate_train"] = (
        round(sum(ys_tr_bin) / float(len(ys_tr_bin)), 4) if ys_tr_bin else None
    )

    research = dict(fit)
    research["model_role"] = "research"
    research["y_label_mean"] = round(y_mean_pct, 6)
    research["y_up_rate"] = (
        round(sum(1.0 for y in ys_tr_pct if float(y) > 0.0) / float(len(ys_tr_pct)), 4)
        if ys_tr_pct
        else None
    )
    research = _stamp_prob_model(research)
    model = dict(research)
    return {
        "fit": fit,
        "preds_te": preds_te,
        "oos_core": oos_core,
        "model": model,
        "research_model": research,
        "y_label_mean": y_mean_pct,
    }


def train_eval_horizon_prob(
    xs_tr: List[dict],
    ys_tr_pct: List[float],
    xs_te: List[dict],
    ys_te_pct: List[float],
    metas_te: List[dict],
    *,
    feat_names: List[str],
    ridge_lambda: float,
    weights: Optional[List[float]],
    dates_tr: Optional[Sequence[str]] = None,
    dates_te: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """Holdout 训/测；只训 Holdout 前一套 β。"""
    y_mean_pct = (
        sum(float(y) for y in ys_tr_pct) / float(len(ys_tr_pct)) if ys_tr_pct else 0.0
    )
    ys_tr_bin = binary_labels(ys_tr_pct)
    use_cs = bool(cross_section_zscore)
    fit = fit_logistic_ridge_from_panel(
        xs_tr,
        ys_tr_bin,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=weights,
        collinearity_policy="drop_redundant",
        row_dates=dates_tr if use_cs else None,
        cross_section_zscore=use_cs,
    )
    preds_te = (
        predict_p_up_rows(fit, xs_te, row_dates=dates_te if use_cs else None)
        if xs_te
        else []
    )
    oos_core = oos_prob_pack(preds_te, ys_te_pct, metas_te) if ys_te_pct else {}
    oos_core["y_label_mean"] = round(y_mean_pct, 6)
    oos_core["y_up_rate_train"] = (
        round(sum(ys_tr_bin) / float(len(ys_tr_bin)), 4) if ys_tr_bin else None
    )

    research = dict(fit)
    research["model_role"] = "research"
    research["y_label_mean"] = round(y_mean_pct, 6)
    research["y_up_rate"] = (
        round(sum(1.0 for y in ys_tr_pct if float(y) > 0.0) / float(len(ys_tr_pct)), 4)
        if ys_tr_pct
        else None
    )
    research = _stamp_prob_model(research)
    model = dict(research)
    return {
        "fit": fit,
        "preds_te": preds_te,
        "oos_core": oos_core,
        "model": model,
        "research_model": research,
        "y_label_mean": y_mean_pct,
    }


def migrate_horizon_gate_cfg(cfg: Dict[str, Any]) -> None:
    """旧幅度闸 → 概率闸。strong∈(0,1)→0；enter∈(0,0.5) 或 >0.7→0。"""
    if not isinstance(cfg, dict):
        return
    for k in HORIZON_STRONG_KEYS:
        v = _f(cfg.get(k))
        if v is None:
            continue
        if 0.0 < float(v) < 1.0 - 1e-12:
            cfg[k] = 0.0
        else:
            cfg[k] = 1.0 if float(v) >= 1.0 - 1e-12 else 0.0
    for k in HORIZON_ENTER_KEYS:
        v = _f(cfg.get(k))
        if v is None:
            continue
        x = float(v)
        if x <= 0.0:
            cfg[k] = 0.0
        elif x < 0.5 - 1e-12 or x > HORIZON_ENTER_MAX + 1e-12:
            cfg[k] = 0.0
        else:
            cfg[k] = max(0.5, min(x, HORIZON_ENTER_MAX))
