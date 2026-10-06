"""τ 后窗口概率头：P(窗口收益>0 | X≤τ)。

ŷ_τ30/45/60/75/90 只出 p_up∈(0,1)；不做幅度 Ridge。
正T p_agree=p_up，反T p_agree=1−p_up。票 f=sign(p_up−mid)；
mid 默认 47%（ŷ_τ* 共用中位点）；|p_up−mid|≤y_tw_vote_margin（默认 5pp）不投票。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

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
HORIZON_VOTE_MIDPOINT_PP = 47.0  # ŷ_τ* 共用中位点（百分点）
HORIZON_VOTE_MIDPOINT = 0.47  # = PP/100
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
    """ŷ_τ* 共用中位点。缺省 47%；>1 视为百分点。"""
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
    """sign(p_up−mid)；|p−mid|≤margin_pp（默认 5pp）或缺分不投票。mid 默认 47%。"""
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


def predict_p_up_rows(
    fit: Dict[str, Any],
    xs: List[dict],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    logits = _predict_rows(fit, xs, impute_missing=impute_missing)
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
    return {
        "n_valid": buckets.get("n_valid") if isinstance(buckets, dict) else None,
        "ic": _ic(preds_p, y_list) if y_list else None,
        "sign_hit": _sign_hit(margin, y_list) if y_list else None,
        "acc_at_50": acc,
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


def horizon_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote：OOS AUC / acc@0.5 / n_test。不再用 |ŷ|≥0.6。"""
    rep = report if isinstance(report, dict) else {}
    oos = rep.get("oos") if isinstance(rep.get("oos"), dict) else {}
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
    min_std_exempt: Optional[Sequence[str]] = None,
    collinearity_policy: str = "drop_redundant",
) -> Dict[str, Any]:
    """面板 logistic Ridge：先走与 OLS 相同的完整子面板 / z-score / 共线策略，再 IRLS。

    入模列缺测按列均值填后再挑行（标准化后 z=0），与
    ``_predict_rows(impute_missing=True)`` / live 打分一致。
    """
    names = [str(n) for n in (feature_names or []) if n]
    ys = [1.0 if _f(y) is not None and float(y) > 0.5 else 0.0 for y in ys_bin]
    ols = fit_factor_ols_from_panel(
        xs,
        ys,
        feature_names=feature_names,
        ridge_lambda=ridge_lambda,
        feature_zscore=True,
        sample_weights=sample_weights,
        min_std_exempt=list(min_std_exempt or []),
        collinearity_policy=collinearity_policy,
        impute_keys=names,
        y_spec={"formula": "I(y>0)", "unit": "prob"},
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
    rows_x: List[List[float]] = []
    rows_y: List[float] = []
    rows_w: List[float] = []
    w_src = list(sample_weights) if sample_weights is not None else None
    for i, row in enumerate(xs or []):
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


def train_eval_horizon_prob(
    xs_tr: List[dict],
    ys_tr_pct: List[float],
    xs_te: List[dict],
    ys_te_pct: List[float],
    metas_te: List[dict],
    xs_all: List[dict],
    ys_all_pct: List[float],
    *,
    feat_names: List[str],
    ridge_lambda: float,
    weights: Optional[List[float]],
    weights_all: Optional[List[float]],
    min_std_exempt: Sequence[str],
) -> Dict[str, Any]:
    """Holdout 训/测 + 全样本 refit。返回 p_up 预测与概率 OOS。"""
    y_mean_pct = (
        sum(float(y) for y in ys_tr_pct) / float(len(ys_tr_pct)) if ys_tr_pct else 0.0
    )
    y_mean_all = (
        sum(float(y) for y in ys_all_pct) / float(len(ys_all_pct)) if ys_all_pct else 0.0
    )
    ys_tr_bin = binary_labels(ys_tr_pct)
    fit = fit_logistic_ridge_from_panel(
        xs_tr,
        ys_tr_bin,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=weights,
        min_std_exempt=list(min_std_exempt),
        collinearity_policy="drop_redundant",
    )
    preds_te = predict_p_up_rows(fit, xs_te) if xs_te else []
    oos_core = oos_prob_pack(preds_te, ys_te_pct, metas_te) if ys_te_pct else {}
    oos_core["y_label_mean"] = round(y_mean_pct, 6)
    oos_core["y_up_rate_train"] = (
        round(sum(ys_tr_bin) / float(len(ys_tr_bin)), 4) if ys_tr_bin else None
    )

    fit_full = fit_logistic_ridge_from_panel(
        xs_all,
        binary_labels(ys_all_pct),
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        sample_weights=weights_all,
        min_std_exempt=list(min_std_exempt),
        collinearity_policy="drop_redundant",
    )
    model = dict(fit_full if fit_full.get("success") else fit)
    model["y_label_mean"] = round(y_mean_all, 6)
    model["y_up_rate"] = (
        round(sum(1.0 for y in ys_all_pct if float(y) > 0.0) / float(len(ys_all_pct)), 4)
        if ys_all_pct
        else None
    )
    model = _stamp_prob_model(model)

    research = dict(fit)
    research["model_role"] = "research"
    research["y_label_mean"] = round(y_mean_pct, 6)
    research = _stamp_prob_model(research)
    return {
        "fit": fit,
        "preds_te": preds_te,
        "oos_core": oos_core,
        "model": model,
        "research_model": research,
        "y_label_mean": y_mean_pct,
        "y_label_mean_all": y_mean_all,
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
