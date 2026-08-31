"""ŷ_path Ridge：与 ŷ_τ 同因子键（开盘 Z + 早盘前缀分钟小包）→ 极值序 signed range %。"""

from __future__ import annotations

import json
import logging
import os
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.factor_ols_fit import fit_factor_ols_from_panel
from core.research.path_panel import (
    DEFAULT_PATH_MINUTE_TAU_HM,
    DEFAULT_PATH_TAU_GRID,
    PATH_Z_FEATURES,
    build_path_panels_from_bars,
)
from core.research.tau_panel import normalize_minute_tau_grid
from core.research.tau_ridge import _predict_rows, _subset, _time_split_indices
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS

PATH_MIN_STD_EXEMPT = PATH_Z_FEATURES
PATH_PROMOTE_MIN_SIGN_HIT = 0.55  # 全样本：软条件（warnings）
PATH_PROMOTE_MIN_N_TEST = 80  # 全样本：软条件
PATH_PROMOTE_MIN_STRONG_HIT = 0.55  # |ŷ|≥2% 强桶
PATH_PROMOTE_MIN_STRONG_N = 40
PATH_STRONG_BUCKET = "abs_ge_2"
DEFAULT_SELL_TRIG_PCT = 2.0
DEFAULT_BUY_TRIG_PCT = 1.5
PATH_LABEL_STRONG_ABS = 2.0
PATH_LABEL_BUCKET_MID = 1.5


def _stack_panels(enriched: Sequence[Dict[str, Any]]) -> tuple:
    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    for p in enriched:
        xs_all.extend(p.get("xs") or [])
        ys_all.extend(p.get("ys") or [])
        dates_all.extend(p.get("dates") or [])
        metas_all.extend(p.get("metas") or [])
    return xs_all, ys_all, dates_all, metas_all


def _z_only_xs(xs: List[dict]) -> List[dict]:
    """始终输出完整 PATH_Z_FEATURES 键（缺值保留 None），避免 yclose_loc 被静默丢掉。"""
    out: List[dict] = []
    for row in xs:
        if not isinstance(row, dict):
            continue
        out.append({k: row.get(k) for k in PATH_Z_FEATURES})
    return out


def _balance_signed_train(
    xs: List[dict],
    ys: List[float],
    metas: List[dict],
    *,
    seed: int = 42,
) -> Tuple[List[dict], List[float], List[dict], Dict[str, Any]]:
    """正负类下采样平衡（按少数类对齐）。"""
    pos_i = [i for i, y in enumerate(ys) if float(y) > 0]
    neg_i = [i for i, y in enumerate(ys) if float(y) < 0]
    info: Dict[str, Any] = {
        "n_pos": len(pos_i),
        "n_neg": len(neg_i),
        "balanced": False,
    }
    if not pos_i or not neg_i:
        return xs, ys, metas, info
    n = min(len(pos_i), len(neg_i))
    rng = random.Random(seed)
    keep = rng.sample(pos_i, n) + rng.sample(neg_i, n)
    keep.sort()
    info["balanced"] = True
    info["n_keep"] = len(keep)
    return (
        [xs[i] for i in keep],
        [ys[i] for i in keep],
        [metas[i] for i in keep] if metas else [],
        info,
    )


def _oos_sign_metrics(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
) -> Dict[str, Any]:
    hits = 0
    n_valid = 0
    buckets = {
        f"abs_ge_{PATH_LABEL_BUCKET_MID}": {"n": 0, "hit": 0},
        f"abs_ge_{int(PATH_LABEL_STRONG_ABS)}": {"n": 0, "hit": 0},
    }
    pos_hit = pos_n = neg_hit = neg_n = 0
    for pred, y in zip(preds, ys):
        if pred is None:
            continue
        n_valid += 1
        ok = (pred > 0) == (y > 0)
        if ok:
            hits += 1
        if y > 0:
            pos_n += 1
            if ok:
                pos_hit += 1
        elif y < 0:
            neg_n += 1
            if ok:
                neg_hit += 1
        ap = abs(float(pred))
        for key, thr in (
            (f"abs_ge_{PATH_LABEL_BUCKET_MID}", PATH_LABEL_BUCKET_MID),
            (f"abs_ge_{int(PATH_LABEL_STRONG_ABS)}", PATH_LABEL_STRONG_ABS),
        ):
            if ap >= thr:
                buckets[key]["n"] += 1
                if ok:
                    buckets[key]["hit"] += 1
    out: Dict[str, Any] = {
        "n_valid": n_valid,
        "sign_hit_rate": round(hits / n_valid, 4) if n_valid else None,
        "sign_hit": None,
        "pos_recall": round(pos_hit / pos_n, 4) if pos_n else None,
        "neg_recall": round(neg_hit / neg_n, 4) if neg_n else None,
        "n_pos": pos_n,
        "n_neg": neg_n,
        "buckets": {},
    }
    out["sign_hit"] = out["sign_hit_rate"]
    for key, pack in buckets.items():
        n = int(pack["n"])
        out["buckets"][key] = {
            "n": n,
            "sign_hit": round(pack["hit"] / n, 4) if n else None,
        }
    return out


def path_label_triggers(
    model_doc: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float]:
    """从模型文档读取训练标签触发（path实对照同口径）。"""
    doc = model_doc if model_doc is not None else load_path_model()
    if not isinstance(doc, dict):
        return DEFAULT_SELL_TRIG_PCT, DEFAULT_BUY_TRIG_PCT
    rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else {}
    sell = rm.get("sell_trig_pct")
    if sell is None:
        sell = doc.get("sell_trig_pct")
    buy = rm.get("buy_trig_pct")
    if buy is None:
        buy = doc.get("buy_trig_pct")
    try:
        sell_f = float(sell) if sell is not None else DEFAULT_SELL_TRIG_PCT
    except (TypeError, ValueError):
        sell_f = DEFAULT_SELL_TRIG_PCT
    try:
        buy_f = float(buy) if buy is not None else DEFAULT_BUY_TRIG_PCT
    except (TypeError, ValueError):
        buy_f = DEFAULT_BUY_TRIG_PCT
    return sell_f, buy_f


def path_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote 闸：硬看 |ŷ|≥2% 强桶；全样本 hit/n 仅 warnings。"""
    rep = report if isinstance(report, dict) else {}
    oos = rep.get("oos") if isinstance(rep.get("oos"), dict) else {}
    n = oos.get("n_valid")
    if n is None:
        n = oos.get("n_test")
    try:
        n_i = int(n or 0)
    except (TypeError, ValueError):
        n_i = 0
    hit = oos.get("sign_hit")
    if hit is None:
        hit = oos.get("sign_hit_rate")
    try:
        hit_f = float(hit) if hit is not None else None
    except (TypeError, ValueError):
        hit_f = None
    buckets = oos.get("buckets") if isinstance(oos.get("buckets"), dict) else {}
    strong = buckets.get(PATH_STRONG_BUCKET) if isinstance(buckets.get(PATH_STRONG_BUCKET), dict) else {}
    s_n = int(strong.get("n") or 0)
    try:
        s_hit_f = float(strong["sign_hit"]) if strong.get("sign_hit") is not None else None
    except (TypeError, ValueError):
        s_hit_f = None

    blockers: List[str] = []
    if s_n < PATH_PROMOTE_MIN_STRONG_N:
        blockers.append(f"|ŷ|≥{PATH_LABEL_STRONG_ABS}% n={s_n}<{PATH_PROMOTE_MIN_STRONG_N}")
    if s_hit_f is None:
        blockers.append(f"缺 |ŷ|≥{PATH_LABEL_STRONG_ABS}% 桶 sign_hit")
    elif s_hit_f < PATH_PROMOTE_MIN_STRONG_HIT:
        blockers.append(
            f"|ŷ|≥{PATH_LABEL_STRONG_ABS}% sign_hit={s_hit_f:.3f}<{PATH_PROMOTE_MIN_STRONG_HIT}"
        )

    warn: List[str] = []
    if n_i < PATH_PROMOTE_MIN_N_TEST:
        warn.append(f"n_test={n_i}<{PATH_PROMOTE_MIN_N_TEST}（软）")
    if hit_f is None:
        warn.append("缺全样本 OOS sign_hit（软）")
    elif hit_f < PATH_PROMOTE_MIN_SIGN_HIT:
        warn.append(f"全样本 sign_hit={hit_f:.3f}<{PATH_PROMOTE_MIN_SIGN_HIT}（软）")
    sq = oos.get("sample_quality") if isinstance(oos.get("sample_quality"), dict) else {}
    for w in sq.get("warnings") or []:
        warn.append(str(w))
    med_span = sq.get("minute_span_days_med")
    try:
        if med_span is not None and int(med_span) < 40:
            warn.append(f"分钟跨度中位 {int(med_span)} 日过短（软）")
    except (TypeError, ValueError):
        pass
    intercept = None
    rm = rep.get("return_model") if isinstance(rep.get("return_model"), dict) else {}
    if rm.get("intercept") is not None:
        try:
            intercept = abs(float(rm.get("intercept")))
        except (TypeError, ValueError):
            intercept = None
    if intercept is not None and intercept > 8:
        warn.append(f"|intercept|={intercept:.1f} 偏置偏大")
    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warn,
        "min_sign_hit": PATH_PROMOTE_MIN_SIGN_HIT,
        "min_n_test": PATH_PROMOTE_MIN_N_TEST,
        "min_strong_hit": PATH_PROMOTE_MIN_STRONG_HIT,
        "min_strong_n": PATH_PROMOTE_MIN_STRONG_N,
        "n_test": n_i,
        "sign_hit": hit_f,
        "strong_sign_hit": s_hit_f,
        "strong_n": s_n,
    }


def _oos_by_tau(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
    metas: Sequence[dict],
) -> Dict[str, Any]:
    """按决策钟 τ 分层 OOS（极值序标签；晚 τ 特征更贴标签，须分桶看）。"""
    buckets: Dict[str, Dict[str, List[Any]]] = {}
    for p, y, m in zip(preds, ys, metas):
        tau = str((m or {}).get("tau") or (m or {}).get("minute_tau_hm") or "10:30").strip()
        slot = buckets.setdefault(tau, {"ps": [], "zs": []})
        slot["ps"].append(p)
        slot["zs"].append(float(y))
    out: Dict[str, Any] = {}
    for tau in sorted(buckets.keys()):
        ps = buckets[tau]["ps"]
        zs = buckets[tau]["zs"]
        pack = _oos_sign_metrics(ps, zs) if zs else {}
        out[tau] = {
            "n": len(zs),
            "sign_hit": pack.get("sign_hit"),
            "n_valid": pack.get("n_valid"),
            "pos_recall": pack.get("pos_recall"),
            "neg_recall": pack.get("neg_recall"),
        }
    return out


def fit_path_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    minute_by_code_date: Optional[Dict[str, Dict[str, Sequence[dict]]]] = None,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    sell_trig_pct: float = DEFAULT_SELL_TRIG_PCT,
    buy_trig_pct: float = DEFAULT_BUY_TRIG_PCT,
    train_frac: float = 0.7,
    minute_tau_hm: Optional[str] = None,
    tau_grid: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """池化拟合 ŷ_path + 时间 OOS。

    ``tau_grid``：变长前缀少数时钟共享 β（同日标签=全日极值序，特征≤各 τ）；
    默认 ``DEFAULT_PATH_TAU_GRID``。live 决策钟见 ``minute_tau_hm``（默认 10:30）。
    """
    live_hm = str(minute_tau_hm or DEFAULT_PATH_MINUTE_TAU_HM).strip() or DEFAULT_PATH_MINUTE_TAU_HM
    grid = normalize_minute_tau_grid(
        tau_hm=live_hm,
        tau_grid=list(tau_grid) if tau_grid is not None else list(DEFAULT_PATH_TAU_GRID),
    )
    enriched = build_path_panels_from_bars(
        stock_bars,
        minute_by_code_date=minute_by_code_date,
        sell_trig_pct=sell_trig_pct,
        buy_trig_pct=buy_trig_pct,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        minute_tau_hm=live_hm,
        tau_grid=grid,
    )
    xs, ys, dates, metas = _stack_panels(enriched)
    if len(ys) < 20:
        return {
            "success": False,
            "error": f"path 样本不足 n={len(ys)}（需≥20 且需分钟线）",
            "task": "path_ridge",
            "sample_count": len(ys),
            "stock_count": len(enriched),
            "tau_grid": list(grid),
            "minute_tau_hm": live_hm,
        }

    xs_z = _z_only_xs(xs)
    train_idx, test_idx = _time_split_indices(dates, train_frac=train_frac)
    xs_tr, ys_tr, metas_tr = _subset(xs_z, ys, metas, train_idx)
    xs_te, ys_te, metas_te = _subset(xs_z, ys, metas, test_idx)

    xs_tr, ys_tr, metas_tr, bal_info = _balance_signed_train(xs_tr, ys_tr, metas_tr)

    # 去训练均值，减轻 ±100 标签导致的强截距偏置；推理时加回
    y_mean = sum(float(y) for y in ys_tr) / max(1, len(ys_tr))
    ys_tr_dm = [float(y) - y_mean for y in ys_tr]

    feat_names: List[str] = list(PATH_Z_FEATURES)

    fit = fit_factor_ols_from_panel(
        xs_tr,
        ys_tr_dm,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        standardize=True,
        min_std_exempt=list(PATH_MIN_STD_EXEMPT),
        collinearity_policy="keep_all",
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": 0.0,
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
            "note": "Z 方差不足，ŷ_path 用训练均值",
        }

    # OOS：demeaned 预测 + y_mean → 展示尺度
    oos: Dict[str, Any] = {"n_test": len(test_idx)}
    if xs_te:
        preds_dm = _predict_rows(fit, xs_te)
        preds = [
            (float(p) + y_mean) if p is not None else None for p in (preds_dm or [])
        ]
        oos.update(_oos_sign_metrics(preds, ys_te))
        if grid and len(grid) > 1:
            oos["by_tau"] = _oos_by_tau(preds, ys_te, metas_te)
    oos["y_label_mean"] = round(y_mean, 4)
    oos["balance"] = bal_info
    oos["tau_grid"] = list(grid)
    oos["minute_tau_hm"] = live_hm
    try:
        from core.research.path_panel import audit_path_minute_coverage, feature_fill_rates

        oos["feature_fill"] = feature_fill_rates(xs_z, PATH_Z_FEATURES)
        oos["sample_quality"] = audit_path_minute_coverage(
            stock_bars,
            minute_by_code_date,
            sell_trig_pct=sell_trig_pct,
            buy_trig_pct=buy_trig_pct,
        )
    except Exception:  # noqa: BLE001
        logger.debug("path sample quality audit failed", exc_info=True)

    # 触达率：有效非零标签 /（有分钟日近似用 sample+丢弃难精确，用样本相对股票日）
    n_pos = sum(1 for y in ys if float(y) > 0)
    n_neg = sum(1 for y in ys if float(y) < 0)
    oos["label_touch"] = {
        "n_labeled": len(ys),
        "n_pos": n_pos,
        "n_neg": n_neg,
        "pos_share": round(n_pos / float(len(ys)), 4) if ys else None,
        "n_unique_days": len({str(d)[:10] for d in dates}),
        "rows_per_day": round(len(ys) / max(1, len({str(d)[:10] for d in dates})), 2),
    }

    model = dict(fit)
    # 展示/闸门用：截距加回标签均值（推理仍用 demeaned 系数 + y_label_mean）
    try:
        model["intercept"] = round(float(fit.get("intercept") or 0.0) + y_mean, 6)
    except (TypeError, ValueError):
        model["intercept"] = round(y_mean, 6)
    model["intercept_demeaned"] = round(float(fit.get("intercept") or 0.0), 6)
    model["y_label_mean"] = round(y_mean, 6)
    model["y_demeaned"] = True
    model["path_label_mode"] = "extreme_order"
    model["sell_trig_pct"] = float(sell_trig_pct)
    model["buy_trig_pct"] = float(buy_trig_pct)
    if grid and len(grid) > 1:
        y_formula = f"extreme_order(low,high) · τ∈{{{','.join(grid)}}}"
        y_note = (
            "变长前缀少数时钟共享 β；标签=全日极值序 signed range%；"
            "τ 越晚特征更贴标签，看 OOS.by_tau；live 决策钟=minute_tau_hm"
        )
    else:
        y_formula = f"extreme_order(low,high) · τ={live_hm}"
        y_note = "单 τ 前缀分钟小包 + 开盘 Z；训练 demean+类别平衡"
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct_signed_range",
        "tau": live_hm,
        "tau_grid": list(grid),
        "note": y_note,
    }
    model["extra_features"] = list(PATH_Z_FEATURES)
    model["minute_tau_hm"] = live_hm
    model["tau_grid"] = list(grid)

    report = {
        "success": True,
        "task": "path_ridge",
        "stock_count": len(enriched),
        "sample_count": len(ys),
        "oos": oos,
        "return_model": model,
        "schema": "path_ridge_v3",
        "target": "extreme_order_signed_range",
        "sell_trig_pct": float(sell_trig_pct),
        "buy_trig_pct": float(buy_trig_pct),
        "minute_tau_hm": live_hm,
        "tau_grid": list(grid),
        "note": (
            "开盘 Z + 多 τ 前缀分钟小包 → 全日极值序；供 dual_y 与 y_τ 联合选向"
            if grid and len(grid) > 1
            else "开盘 Z + 前缀分钟小包 → 全日极值序；供 dual_y 与 y_τ 联合选向"
        ),
    }
    report["promote_gate"] = path_promote_gate(report)
    return report


def path_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "path_ridge_model.json")


def path_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "path_ridge_last_report.json")


def save_path_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = path_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_path_last_report() -> Optional[Dict[str, Any]]:
    path = path_last_report_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load_path_last_report failed", exc_info=True)
        return None
    if not isinstance(doc, dict) or not doc.get("success"):
        return None
    if not isinstance(doc.get("return_model"), dict):
        return None
    return doc


def load_path_model() -> Optional[Dict[str, Any]]:
    path = path_model_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and isinstance(doc.get("return_model"), dict):
                return doc
        except Exception:  # noqa: BLE001
            logger.debug("load_path_model failed", exc_info=True)
    # 已拟合未 promote 时用 last report 影子推理（显式 promote 写 path_ridge_model.json）
    fallback = load_path_last_report()
    if fallback:
        out = dict(fallback)
        out["_shadow"] = True
        return out
    return None


def persist_path_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = report.get("return_model")
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    gate = path_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    doc = {
        "success": True,
        "promoted_at": now_iso_utc(),
        "note": note or "path_ridge promote",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": report.get("schema") or "path_ridge_v3",
        "sell_trig_pct": report.get("sell_trig_pct", rm.get("sell_trig_pct")),
        "buy_trig_pct": report.get("buy_trig_pct", rm.get("buy_trig_pct")),
        "minute_tau_hm": report.get("minute_tau_hm") or rm.get("minute_tau_hm"),
        "tau_grid": report.get("tau_grid") or rm.get("tau_grid"),
        "promote_gate": gate,
        "dual_score_head": "y_path",
        "contract_note": "ŷ_path：开盘 Z + 多 τ 前缀分钟小包 → 全日极值序 signed range%；live=minute_tau_hm",
    }
    path = path_model_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    return {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "promote_gate": gate,
    }


def predict_path_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_path_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    row = {k: features.get(k) for k in PATH_Z_FEATURES}
    # 若模型以 demean 训练：系数对应 demeaned y；截距已含 y_mean 时直接预测
    # 存盘 intercept = intercept_dm + y_mean，与 _predict_rows 一致即可
    preds = _predict_rows(rm, [row])
    if not preds or preds[0] is None:
        return None
    return round(float(preds[0]), 4)


_PATH_FEAT_LABELS = {
    "gap_pct": "跳空 %",
    "sector_gap_breadth": "同业缺口广度",
    "theme_day": "主题日",
    "gap_atr": "缺口 / ATR",
    "gap_vs_sector": "行业相对缺口",
    "yclose_loc": "昨收位置",
    "mom3_pct": "近3日动量 %",
    **MINUTE_TAU_FEAT_LABELS,
}


def explain_path_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """结构化拆解 ŷ_path（与 ``predict_path_from_features`` 同口径），供 tip 表格。"""
    doc = model_doc if model_doc is not None else load_path_model()
    if not doc:
        return None
    fit = doc.get("return_model") or {}
    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    if not coefs and abs(intercept) < 1e-12:
        return None
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    active = [
        n
        for n in (fit.get("active_features") or coefs.keys())
        if coefs.get(n) is not None
    ]
    # 确保 PATH 特征顺序中有值的都尽量出现
    for name in PATH_Z_FEATURES:
        if name not in active and coefs.get(name) is not None:
            active.append(name)
    row = features or {}
    terms: List[Dict[str, Any]] = []
    total = intercept
    for name in active:
        beta = float(coefs.get(name) or 0.0)
        v = row.get(name)
        imputed = False
        if v is None:
            z = 0.0
            imputed = True
        else:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                z = 0.0
                imputed = True
            else:
                mu = float(means.get(name, 0.0)) if means else 0.0
                sd = float(stds.get(name, 1.0)) if stds else 1.0
                if sd < 1e-9:
                    sd = 1.0
                z = (fv - mu) / sd if means else fv
        contrib = beta * z
        total += contrib
        term: Dict[str, Any] = {
            "key": str(name),
            "label": _PATH_FEAT_LABELS.get(name) or str(name),
            "beta": round(beta, 6),
            "z": round(float(z), 4),
            "contrib": round(float(contrib), 6),
        }
        if imputed:
            term["note"] = "缺特征·z≈0"
        terms.append(term)
    if not terms and abs(intercept) < 1e-12:
        return None
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
    return {
        "intercept": round(intercept, 6),
        "terms": terms[:16],
        "total": round(total, 6),
        "head": "path",
    }


def path_tip_model_snapshot(
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """tip 用轻量模型快照（前端缺 formula_terms_path 时可现场重算组成）。"""
    doc = model_doc if model_doc is not None else load_path_model()
    if not doc:
        return None
    fit = doc.get("return_model") or {}
    coefs = fit.get("coefficients") or {}
    active = [
        n
        for n in (fit.get("active_features") or coefs.keys())
        if coefs.get(n) is not None
    ]
    if not active and abs(float(fit.get("intercept") or 0.0)) < 1e-12:
        return None
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    slim_coefs = {k: round(float(coefs[k]), 6) for k in active if coefs.get(k) is not None}
    slim_means = {k: round(float(means[k]), 6) for k in active if means.get(k) is not None}
    slim_stds = {k: round(float(stds[k]), 6) for k in active if stds.get(k) is not None}
    return {
        "intercept": round(float(fit.get("intercept") or 0.0), 6),
        "coefficients": slim_coefs,
        "active_features": active,
        "zscore_means": slim_means,
        "zscore_stds": slim_stds,
        "sell_trig_pct": (doc.get("sell_trig_pct") or fit.get("sell_trig_pct")),
        "buy_trig_pct": (doc.get("buy_trig_pct") or fit.get("buy_trig_pct")),
    }
