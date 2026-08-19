"""目标驱动的分区评分 / 损失函数（组池 R² + 组内 IC）。

对齐诉求：让每个组的 beta 拟合误差尽可能小、IC 尽可能大。
直接用于比较两个候选分区的「好 / 坏」，越小越好。

量纲（2026-08）：截面 |IC| 常 ≪ 0.1，而 (1−R²)∈[0,1]。若直接
``w_r2*(1-R²)+w_ic*(-IC)`` 且 w≈O(1)，IC 会被淹没。因此对 IC 项除以
``ic_ref_scale``（默认 0.05），使「IC≈+5pp」与「完美拟合」同量级后再加权。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# 默认权重 / 结构罚（live auto-k · 轻量贪心 · 研究 objective 共用）
DEFAULT_W_R2 = 1.0
DEFAULT_W_IC = 1.0
DEFAULT_IC_REF_SCALE = 0.05  # |IC|≈0.05 → ic_term≈±1，对齐 (1−R²)
DEFAULT_LAMBDA_IMBALANCE = 0.3
DEFAULT_IMBALANCE_MULT = 1.5  # max_share > mult×ideal 才罚（原 2.0 过宽）
DEFAULT_LAMBDA_SINGLETON = 0.75  # 质量项已含单票先验，结构罚减半（原 1.5）
DEFAULT_LAMBDA_UNUSABLE = 1.5
DEFAULT_SINGLETON_PRIOR_R2 = 0.05  # 单票 OLS 基线，避免按 0 双重最差
DEFAULT_SINGLETON_PRIOR_IC = 0.0


def extract_group_r2_from_pooled(ols_result: Dict[str, Any]) -> Optional[float]:
    """从 fit_factor_ols_from_panel 结果里取 R²，返回 [0,1] 或 None。"""
    if not isinstance(ols_result, dict):
        return None
    r2 = ols_result.get("r_squared") or ols_result.get("r2")
    if r2 is None:
        inner = ols_result.get("ols") or {}
        if isinstance(inner, dict):
            r2 = inner.get("r_squared") or inner.get("r2")
    if r2 is None:
        return None
    try:
        v = float(r2)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return float(max(0.0, min(1.0, v)))


def extract_group_ic_from_panel(
    group_ic_result: Dict[str, Any],
    *,
    use_abs: bool = False,
    icir_weight: float = 0.0,
) -> float:
    """从 group_cs_ic_panel 结果提取组 IC 得分，越大越好。缺失返回 0。"""
    if not isinstance(group_ic_result, dict):
        return 0.0
    rows = group_ic_result.get("rows") or group_ic_result.get("factors") or []
    if not rows:
        return 0.0
    ics: List[float] = []
    icirs: List[float] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        ic = r.get("ic")
        icir = r.get("icir")
        try:
            v = float(ic) if ic is not None else None
        except (TypeError, ValueError):
            v = None
        if v is None or not np.isfinite(v):
            continue
        ics.append(abs(v) if use_abs else v)
        try:
            iv = float(icir) if icir is not None else None
        except (TypeError, ValueError):
            iv = None
        if iv is not None and np.isfinite(iv):
            icirs.append(abs(iv) if use_abs else iv)
    if not ics:
        return 0.0
    ic_m = float(np.mean(ics))
    if icirs and icir_weight > 0:
        icir_m = float(np.mean(icirs))
        return (1.0 - icir_weight) * ic_m + icir_weight * icir_m
    return ic_m


def resolve_calendar_cut_date(
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    holdout_ratio: float = 0.3,
    codes: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """宇宙级日历切分日：全部决策日排序后的前 (1-holdout_ratio) 分位。

    有日期则跨票对齐；不足返回 None（调用方退回按票比例切）。
    """
    ratio = min(0.5, max(0.15, float(holdout_ratio or 0.3)))
    dates: List[str] = []
    code_iter = (
        [str(c).strip() for c in (codes or []) if str(c).strip()]
        if codes is not None
        else sorted(str(k) for k in (panel_by_code or {}).keys())
    )
    for code in code_iter:
        panel = panel_by_code.get(code) or {}
        ds = panel.get("dates") or []
        xs = panel.get("xs") or []
        n = min(len(ds), len(xs), len(panel.get("ys") or []))
        for i in range(n):
            d = str(ds[i] or "").strip()[:10]
            if len(d) >= 10:
                dates.append(d)
    if len(dates) < 8:
        return None
    dates.sort()
    # 前段终点 ≈ 排序后第 (1-ratio) 分位，使 hold 约占 ratio
    cut_i = max(0, int(round(len(dates) * (1.0 - ratio))) - 1)
    cut_i = max(0, min(len(dates) - 1, cut_i))
    return dates[cut_i]


def split_panel_by_cut_date(
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
    dates: Sequence[str],
    *,
    cut_date: str,
) -> Tuple[List[Dict[str, Any]], List[float], List[Dict[str, Any]], List[float]]:
    """单票：decision_date ≤ cut_date → train，否则 hold。"""
    cut = str(cut_date or "").strip()[:10]
    train_xs: List[Dict[str, Any]] = []
    train_ys: List[float] = []
    hold_xs: List[Dict[str, Any]] = []
    hold_ys: List[float] = []
    n = min(len(xs or []), len(ys or []), len(dates or []))
    for i in range(n):
        row = xs[i]
        if not isinstance(row, dict):
            continue
        try:
            yv = float(ys[i])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(yv):
            continue
        d = str(dates[i] or "").strip()[:10]
        if cut and d and d <= cut:
            train_xs.append(row)
            train_ys.append(yv)
        else:
            hold_xs.append(row)
            hold_ys.append(yv)
    return train_xs, train_ys, hold_xs, hold_ys


def _row_decision_date(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    for key in ("decision_date", "as_of", "date"):
        v = row.get(key)
        if v is None:
            continue
        s = str(v).strip()[:10]
        if s:
            return s
    return ""


def _pair_rows(
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
) -> List[Tuple[Dict[str, Any], float, str]]:
    """过滤有效 (x,y)，附带 decision_date（可空）。"""
    out: List[Tuple[Dict[str, Any], float, str]] = []
    n_pair = min(len(xs or []), len(ys or []))
    for i in range(n_pair):
        row = xs[i]
        if not isinstance(row, dict):
            continue
        try:
            yv = float(ys[i])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(yv):
            continue
        out.append((row, yv, _row_decision_date(row)))
    return out


def split_chrono_holdout_pairs(
    pairs: Sequence[Tuple[Dict[str, Any], float, str]],
    *,
    holdout_ratio: float = 0.3,
) -> Tuple[
    List[Tuple[Dict[str, Any], float]],
    List[Tuple[Dict[str, Any], float]],
    float,
]:
    """单票（或已按时间排好的）序列 → 前段 train / 尾段 hold。

    有 decision_date 则按日期排序；否则保持输入顺序（单票面板本就是按 bar 序）。
    """
    ratio = min(0.5, max(0.15, float(holdout_ratio or 0.3)))
    rows = list(pairs or [])
    if not rows:
        return [], [], ratio
    dated = sum(1 for _, _, d in rows if d)
    if dated >= max(4, int(0.5 * len(rows))):
        rows = sorted(rows, key=lambda t: (t[2],))
    n = len(rows)
    cut = max(1, int(n * (1.0 - ratio)))
    if n - cut < 4:
        cut = max(0, n - 4)
    train = [(r, y) for r, y, _ in rows[:cut]]
    hold = [(r, y) for r, y, _ in rows[cut:]]
    if len(hold) < 4:
        train = [(r, y) for r, y, _ in rows]
        hold = list(train)
    return train, hold, ratio


def collect_group_chrono_holdout(
    members: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
) -> Dict[str, Any]:
    """组内：每票切 train/hold，再合并（顺序按 code 稳定）。

    - 有 ``cut_date``：按宇宙日历日切（decision_date ≤ cut → train）。
    - 无日历：每票各自按比例切尾（避免「多票 concat 后再砍尾」）。
    """
    train_xs: List[Dict[str, Any]] = []
    train_ys: List[float] = []
    hold_xs: List[Dict[str, Any]] = []
    hold_ys: List[float] = []
    ratio_used = min(0.5, max(0.15, float(holdout_ratio or 0.3)))
    cut = str(cut_date or "").strip()[:10] or None
    n_members_used = 0
    n_calendar = 0
    for code in sorted(str(c).strip() for c in (members or []) if str(c).strip()):
        panel = panel_by_code.get(code) or {}
        xs = panel.get("xs") or []
        ys = panel.get("ys") or []
        dates = panel.get("dates") or []
        if cut and dates and len(dates) >= min(len(xs), len(ys)):
            tx, ty, hx, hy = split_panel_by_cut_date(xs, ys, dates, cut_date=cut)
            if len(hx) < 4 or len(ty) < 4:
                continue
            n_members_used += 1
            n_calendar += 1
            train_xs.extend(tx)
            train_ys.extend(ty)
            hold_xs.extend(hx)
            hold_ys.extend(hy)
            continue
        pairs = _pair_rows(xs, ys)
        if len(pairs) < 6:
            continue
        train, hold, ratio_used = split_chrono_holdout_pairs(
            pairs, holdout_ratio=holdout_ratio
        )
        if len(hold) < 4 or len(train) < 4:
            continue
        n_members_used += 1
        for row, yv in train:
            train_xs.append(row)
            train_ys.append(yv)
        for row, yv in hold:
            hold_xs.append(row)
            hold_ys.append(yv)
    return {
        "train_xs": train_xs,
        "train_ys": train_ys,
        "hold_xs": hold_xs,
        "hold_ys": hold_ys,
        "n_train": len(train_ys),
        "n_hold": len(hold_ys),
        "n_members_used": int(n_members_used),
        "n_members_calendar": int(n_calendar),
        "holdout_ratio": float(ratio_used),
        "cut_date": cut,
        "split_mode": "calendar" if cut and n_calendar else "per_stock_ratio",
    }


def _metrics_from_pred_act(
    preds: Sequence[float],
    acts: Sequence[float],
    *,
    holdout_ratio: float,
    n_full: int,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    from core.signal.factor_corr import pearson_with_reason

    empty: Dict[str, Any] = {
        "ok": False,
        "n": 0,
        "ic": None,
        "rmse": None,
        "r2": None,
        "holdout_ratio": float(holdout_ratio),
    }
    p_h = [float(v) for v in preds]
    a_h = [float(v) for v in acts]
    n = min(len(p_h), len(a_h))
    if n < 4:
        return {**empty, "n": n, "n_full": int(n_full), "reason": reason or "too_few_holdout"}
    p_h, a_h = p_h[:n], a_h[:n]
    ic, _reason = pearson_with_reason(p_h, a_h)
    err = [float(p_h[i]) - float(a_h[i]) for i in range(n)]
    rmse = float(math.sqrt(sum(e * e for e in err) / max(1, len(err))))
    y_bar = float(sum(a_h) / len(a_h))
    ss_tot = sum((float(a_h[i]) - y_bar) ** 2 for i in range(n))
    ss_res = sum(e * e for e in err)
    r2 = None
    if ss_tot > 1e-12:
        r2 = float(max(0.0, min(1.0, 1.0 - ss_res / ss_tot)))
    try:
        from core.signal.ic_contract import annotate_ic_block

        _ic_ann = annotate_ic_block(
            {"ic": None if ic is None else round(float(ic), 4)},
            kind="chrono_pearson",
            primary=False,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in partition_loss.py", exc_info=True)
        _ic_ann = {
            "ic": None if ic is None else round(float(ic), 4),
            "ic_kind": "chrono_pearson",
            "is_primary_ic": False,
        }
    return {
        "ok": True,
        "n": int(n),
        "n_full": int(n_full),
        "ic": _ic_ann.get("ic"),
        "ic_kind": _ic_ann.get("ic_kind"),
        "ic_label": _ic_ann.get("ic_label"),
        "ic_role": _ic_ann.get("ic_role"),
        "is_primary_ic": False,
        "rmse": round(rmse, 4),
        "r2": None if r2 is None else round(float(r2), 4),
        "holdout_ratio": float(holdout_ratio),
        "reason": reason,
    }


def score_yhat_on_rows(
    return_model: Optional[Dict[str, Any]],
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
    *,
    holdout_ratio: float = 0.3,
    n_full: Optional[int] = None,
) -> Dict[str, Any]:
    """用已有 return_model 在给定行上算 ŷ vs y 的 IC / RMSE / R²。"""
    from core.signal.return_score import ReturnScoreModel

    empty: Dict[str, Any] = {
        "ok": False,
        "n": 0,
        "ic": None,
        "rmse": None,
        "r2": None,
        "holdout_ratio": float(holdout_ratio),
    }
    model = ReturnScoreModel.from_dict(return_model)
    if model is None:
        return {**empty, "reason": "no_return_model"}
    preds: List[float] = []
    acts: List[float] = []
    n_pair = min(len(xs or []), len(ys or []))
    for i in range(n_pair):
        row = xs[i]
        if not isinstance(row, dict):
            continue
        try:
            yv = float(ys[i])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(yv):
            continue
        yh = model.predict(row)
        if yh is None:
            continue
        try:
            pv = float(yh)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(pv):
            continue
        preds.append(pv)
        acts.append(yv)
    return _metrics_from_pred_act(
        preds,
        acts,
        holdout_ratio=holdout_ratio,
        n_full=int(n_full if n_full is not None else len(preds)),
    )


def yhat_holdout_metrics(
    return_model: Optional[Dict[str, Any]],
    xs: Sequence[Dict[str, Any]],
    ys: Sequence[float],
    *,
    holdout_ratio: float = 0.3,
) -> Dict[str, Any]:
    """单序列尾段评估（兼容旧调用）：有日期则按日排序后再切。

    组级选 k 请用 ``yhat_group_holdout_metrics``（每票切尾 + 前段重拟合）。
    """
    pairs = _pair_rows(xs, ys)
    if len(pairs) < 6:
        return {
            "ok": False,
            "n": len(pairs),
            "ic": None,
            "rmse": None,
            "r2": None,
            "holdout_ratio": float(holdout_ratio),
            "reason": "too_few_pairs",
        }
    _train, hold, ratio = split_chrono_holdout_pairs(pairs, holdout_ratio=holdout_ratio)
    hold_xs = [r for r, _ in hold]
    hold_ys = [y for _, y in hold]
    return score_yhat_on_rows(
        return_model,
        hold_xs,
        hold_ys,
        holdout_ratio=ratio,
        n_full=len(pairs),
    )


def yhat_group_holdout_metrics(
    members: Sequence[str],
    panel_by_code: Dict[str, Dict[str, Any]],
    *,
    holdout_ratio: float = 0.3,
    cut_date: Optional[str] = None,
    return_model: Optional[Dict[str, Any]] = None,
    refit_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """组级真 holdout：尾段评 ŷ；提供 ``refit_fn`` 时必须前段重拟合成功。

    ``refit_fn(train_xs, train_ys) -> return_model dict | None``。
    - 有 ``refit_fn``：重拟合失败 → ``ok=False``（**不**退回全样本 β，避免泄漏）。
    - 无 ``refit_fn``：用传入的 ``return_model`` 直接评尾段（调用方自担泄漏）。
    - ``cut_date``：宇宙日历切分日（优先于按票比例）。
    """
    empty: Dict[str, Any] = {
        "ok": False,
        "n": 0,
        "ic": None,
        "rmse": None,
        "r2": None,
        "holdout_ratio": float(holdout_ratio),
        "refit": False,
        "cut_date": str(cut_date or "").strip()[:10] or None,
    }
    split = collect_group_chrono_holdout(
        members,
        panel_by_code,
        holdout_ratio=holdout_ratio,
        cut_date=cut_date,
    )
    n_train = int(split.get("n_train") or 0)
    n_hold = int(split.get("n_hold") or 0)
    if n_hold < 4 or n_train < 4:
        return {
            **empty,
            "n": n_hold,
            "n_full": n_train + n_hold,
            "n_train": n_train,
            "n_members_used": split.get("n_members_used"),
            "reason": "too_few_chrono_pairs",
        }

    rm = return_model
    refit_ok = False
    if callable(refit_fn):
        try:
            fitted = refit_fn(split["train_xs"], split["train_ys"])
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in partition_loss.py", exc_info=True)
            fitted = None
        if isinstance(fitted, dict) and fitted.get("coefficients"):
            rm = fitted
            refit_ok = True
        else:
            return {
                **empty,
                "n": n_hold,
                "n_full": n_train + n_hold,
                "n_train": n_train,
                "n_members_used": split.get("n_members_used"),
                "reason": "refit_failed",
            }

    out = score_yhat_on_rows(
        rm,
        split["hold_xs"],
        split["hold_ys"],
        holdout_ratio=float(split.get("holdout_ratio") or holdout_ratio),
        n_full=n_train + n_hold,
    )
    out["refit"] = bool(refit_ok)
    out["n_train"] = n_train
    out["n_members_used"] = int(split.get("n_members_used") or 0)
    out["cut_date"] = split.get("cut_date")
    out["split_mode"] = split.get("split_mode")
    if not out.get("ok") and not out.get("reason"):
        out["reason"] = "score_failed"
    return out


def partition_group_weight(member_count: int, total_sample_count: int) -> float:
    """组加权权重：票数占比平方根。照顾小组又不过度忽视大组。"""
    if total_sample_count < 1:
        return 1.0
    return float(np.sqrt(max(1, int(member_count))))


def compute_partition_loss(
    *,
    groups: Sequence[Dict[str, Any]],
    w_r2: float = DEFAULT_W_R2,
    w_ic: float = DEFAULT_W_IC,
    lambda_imbalance: float = DEFAULT_LAMBDA_IMBALANCE,
    lambda_singleton: float = DEFAULT_LAMBDA_SINGLETON,
    lambda_unusable: float = DEFAULT_LAMBDA_UNUSABLE,
    ic_use_abs: bool = False,
    include_singleton_quality: bool = True,
    singleton_prior_r2: float = DEFAULT_SINGLETON_PRIOR_R2,
    singleton_prior_ic: float = DEFAULT_SINGLETON_PRIOR_IC,
    ic_ref_scale: float = DEFAULT_IC_REF_SCALE,
    imbalance_mult: float = DEFAULT_IMBALANCE_MULT,
) -> Dict[str, Any]:
    """最小化 (1-R²) + (−IC)/ic_ref_scale + 结构惩罚。

    groups[i] 字段：member_count / pooled_r2 / ic_mean；可选 fit_ok / invalid /
    has_return_model。

    - ``ic_use_abs=False``（默认）：有符号 IC；负相关抬高 loss（与 live 选 k 一致）。
    - ``ic_ref_scale``：把典型截面 IC 放大到与 (1−R²) 同量级再乘 ``w_ic``。
    - 单票组默认计入质量项（先验 R²/IC），避免「踢成单票眼不见为净」；
      结构 ``lambda_singleton`` 已下调，减轻双重惩罚。
    - 拟合失败的多票组计入 ``unusable`` 惩罚，避免「空壳大组」优于全单票。
    """
    ic_scale = float(ic_ref_scale) if float(ic_ref_scale or 0) > 1e-9 else DEFAULT_IC_REF_SCALE
    imb_mult = float(imbalance_mult) if float(imbalance_mult or 0) > 0 else DEFAULT_IMBALANCE_MULT

    total_n = sum(int(g.get("member_count") or 0) for g in groups)
    if total_n < 1:
        return {
            "loss": 1e9,
            "loss_r2": 0.0,
            "loss_ic": 0.0,
            "penalty_imbalance": 0.0,
            "penalty_singleton": 0.0,
            "penalty_unusable": 0.0,
            "details": [],
            "note": "空分区",
            "ic_use_abs": bool(ic_use_abs),
            "ic_ref_scale": ic_scale,
            "imbalance_mult": imb_mult,
            "singleton_count": 0,
            "singleton_members": 0,
            "singleton_share": 0.0,
            "unusable_members": 0,
            "unusable_share": 0.0,
        }

    def _parse_r2(g: Dict[str, Any]) -> Optional[float]:
        r2_raw = g.get("pooled_r2")
        if r2_raw is not None:
            try:
                return float(max(0.0, min(1.0, float(r2_raw))))
            except (TypeError, ValueError):
                return None
        return extract_group_r2_from_pooled(g)

    def _parse_ic(g: Dict[str, Any], *, default: float) -> float:
        if "ic_mean" not in g or g.get("ic_mean") is None:
            return float(default)
        try:
            return float(g.get("ic_mean"))
        except (TypeError, ValueError):
            return float(default)

    def _is_unusable_multi(g: Dict[str, Any], n_g: int) -> bool:
        """无可用交易模型的多票组（空壳），不是 holdout 偶发失败。"""
        if n_g < 2:
            return False
        if g.get("invalid") is True or g.get("fit_ok") is False:
            return True
        if g.get("unusable") is True:
            return True
        if g.get("has_return_model") is False:
            return True
        return False

    def _accumulate(n_g: int, r2: float, ic: float, *, tag: Optional[str] = None) -> None:
        nonlocal loss_r2_num, loss_ic_num, wsum
        w = partition_group_weight(n_g, total_n)
        if ic_use_abs:
            ic = abs(ic)
        r2_term = 1.0 - float(r2)
        ic_term = (-float(ic)) / ic_scale
        loss_r2_num += w * r2_term
        loss_ic_num += w * ic_term
        wsum += w
        row = {
            "member_count": n_g,
            "weight": round(w, 4),
            "r2": round(float(r2), 4),
            "ic_mean": round(float(ic), 4),
            "r2_term": round(r2_term, 4),
            "ic_term": round(ic_term, 4),
        }
        if tag:
            row[tag] = True
        details.append(row)

    loss_r2_num, loss_ic_num, wsum = 0.0, 0.0, 0.0
    singleton_members = 0
    unusable_members = 0
    sizes: List[int] = []
    details: List[Dict[str, Any]] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        n_g = max(0, int(g.get("member_count") or 0))
        sizes.append(n_g)
        if n_g < 1:
            continue

        r2_parsed = _parse_r2(g)
        if _is_unusable_multi(g, n_g):
            unusable_members += n_g
            # 空壳多票组：质量项按最差先验计入，避免只靠结构罚还显得「干净」
            ic = _parse_ic(g, default=0.0)
            _accumulate(n_g, 0.0, ic, tag="unusable")
            continue

        if n_g < 2:
            singleton_members += n_g
            if not include_singleton_quality:
                continue
            if r2_parsed is None:
                r2 = float(singleton_prior_r2)
            else:
                r2 = float(r2_parsed)
            ic = _parse_ic(g, default=float(singleton_prior_ic))
            _accumulate(n_g, r2, ic, tag="singleton")
            continue

        r2 = 0.0 if r2_parsed is None else float(r2_parsed)
        ic = _parse_ic(g, default=0.0)
        _accumulate(n_g, r2, ic)

    loss_r2 = loss_r2_num / max(wsum, 1e-9)
    loss_ic = loss_ic_num / max(wsum, 1e-9)

    k = max(1, len(sizes))
    ideal = 1.0 / k
    max_share = (max(sizes) / total_n) if total_n > 0 else 1.0
    penalty_imbalance = max(0.0, max_share - imb_mult * ideal) * lambda_imbalance

    # 按票数占比罚，避免「组数很多但单票很少」与「空壳大组」口径不一致
    singleton_share = singleton_members / float(total_n)
    unusable_share = unusable_members / float(total_n)
    penalty_singleton = singleton_share * float(lambda_singleton)
    penalty_unusable = unusable_share * float(lambda_unusable)
    singleton_group_count = int(sum(1 for n in sizes if n == 1))

    loss = (
        w_r2 * loss_r2
        + w_ic * loss_ic
        + penalty_imbalance
        + penalty_singleton
        + penalty_unusable
    )
    return {
        "loss": float(loss),
        "loss_r2": float(loss_r2),
        "loss_ic": float(loss_ic),
        "penalty_imbalance": float(penalty_imbalance),
        "penalty_singleton": float(penalty_singleton),
        "penalty_unusable": float(penalty_unusable),
        "group_count": int(k),
        # singleton_count = 单票「组」个数（展示）；惩罚用的是票数占比 singleton_share
        "singleton_count": singleton_group_count,
        "singleton_members": int(singleton_members),
        "singleton_share": round(float(singleton_share), 4),
        "unusable_members": int(unusable_members),
        "unusable_share": round(float(unusable_share), 4),
        "max_share": round(max_share, 4),
        "details": details,
        "ic_use_abs": bool(ic_use_abs),
        "ic_ref_scale": ic_scale,
        "imbalance_mult": imb_mult,
        "note": (
            "loss 越小越好：有符号 IC↑（/ic_ref_scale）、R²↑；单票计入质量先验；"
            "空壳多票组另计 unusable 罚。"
            "penalty_singleton 按 singleton_members/total_n，"
            "勿与 singleton_count/group_count 组占比混淆。"
        ),
    }
