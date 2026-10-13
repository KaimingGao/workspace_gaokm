"""ŷ_τc Ridge：Holdout OOS 报告 / 研究·执行套落盘（对齐 ``oo_ridge``）。

面板拼装与矩阵拟合见 ``tc_ridge_compact``（对齐 ``oo_ridge_compact``）。

**alpha_τc** := 开盘 Z（``TAU_Z_FEATURES`` = ``z_τc_*``，含 ≤τ 分钟键）∪ ``raw_atc_*``（alpha_tc）。
现网固定完整 **alpha_τc**（z_τc_*∪raw_atc_*）。面板/live 仍写旧裸名 Z；``feature_get`` 双向别名。

这就是调仓 fuse（y_oo_fuse）里的 ŷ_τc。打分写入 ``y_τc``（含时钟对齐）。
供买入闸、fuse 的 τc 项、做 T 估 C_τ。与 ŷ_oo 独立，不改写 ``predicted_score``。
``raw_atc_*`` 锚 price[τ]；ŷ_oo 用开锚 ``z_oo_* ∪ raw_aoo_*`` 全量。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.tau_panel import (
    TAU_LAG_FEAT_LABELS,
    T30_LAG_FEAT_LABELS,
)
from core.research.tc_ridge_compact import (
    TAU_FEATURE_EXTRA,
    TAU_FIT_DROP_ALIASES,
    TAU_HORIZON_DROP_OC_SHAPE,
    TAU_HORIZON_TREE_SHAPE_FEATURES,
    TAU_MIN_STD_EXEMPT,
    TAU_Z_FEATURES,
    _predict_rows,
    _stack_panels,
    _subset,
    build_tau_panels_from_bars,
    fit_tau_ridge_matrices,
    with_horizon_tree_shape,
)
from core.signal.factors.z_tc import (
    Z_TC_LEGACY_KEYS,
    feature_get,
    z_tc_feat_labels,
)
from core.signal.minute_tau_feats import MINUTE_TAU_FEAT_LABELS

# 报告/落盘元数据：特征包名（Z 列 = z_τc_* + raw_atc_*）
ALPHA_TC_PACK_NOTE = "alpha_τc=z_τc_* ∪ raw_atc_*"


def alpha_tc_report_meta(*, n_alpha_tc_features: int = 0) -> Dict[str, Any]:
    """拟合/promote 报告挂的特征包字段。"""
    return {
        "feature_pack": "alpha_τc",
        "feature_pack_key": "alpha_tc",
        "feature_pack_formula": "z_τc_* ∪ raw_atc_*",
        "include_alpha_tc": True,
        "n_alpha_tc_features": int(n_alpha_tc_features or 0),
    }

def _ic(preds: List[Optional[float]], ys: List[float]) -> Optional[float]:
    pairs = [(float(p), float(y)) for p, y in zip(preds, ys) if p is not None]
    if len(pairs) < 5:
        return None
    import math

    n = len(pairs)
    mx = sum(p for p, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    num = sum((p - mx) * (y - my) for p, y in pairs)
    dx = math.sqrt(sum((p - mx) ** 2 for p, _ in pairs))
    dy = math.sqrt(sum((y - my) ** 2 for _, y in pairs))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _sign_hit(
    preds: List[Optional[float]],
    ys: List[float],
    *,
    min_abs: float = 0.05,
) -> Optional[float]:
    """方向命中率。默认跳过 |ŷ|<min_abs（百分收益弱信号）。

    强正则下 ŷ 常落在 0.05 以内；此时调用方应 ``min_abs=0`` 回退。
    """
    hits = 0
    n = 0
    thr = max(0.0, float(min_abs))
    for p, y in zip(preds, ys):
        if p is None:
            continue
        if thr > 0.0 and abs(float(p)) < thr:
            continue
        n += 1
        if (float(p) > 0 and float(y) > 0) or (float(p) < 0 and float(y) < 0):
            hits += 1
    if n < 5:
        return None
    return round(hits / n, 4)


def _oos_sign_buckets(
    preds: Sequence[Optional[float]],
    ys: Sequence[float],
) -> Dict[str, Any]:
    """分桶同号率 + 正负召回（展示/ promote 用）。

    固定阈 ``|ŷ|≥0.4/0.6`` 适合百分收益尺度；ŷ 幅度较小时
    另附 ``abs_top_30`` / ``abs_top_15``（按 |ŷ| 分位）以免强信号桶空缺。
    """
    buckets = {
        "abs_ge_0_4": {"n": 0, "hit": 0},
        "abs_ge_0_6": {"n": 0, "hit": 0},
    }
    pos_hit = pos_n = neg_hit = neg_n = 0
    n_valid = 0
    abs_ok: List[Tuple[float, bool]] = []
    for pred, y in zip(preds, ys):
        if pred is None:
            continue
        n_valid += 1
        ok = (float(pred) > 0) == (float(y) > 0)
        if float(y) > 0:
            pos_n += 1
            if ok:
                pos_hit += 1
        elif float(y) < 0:
            neg_n += 1
            if ok:
                neg_hit += 1
        ap = abs(float(pred))
        abs_ok.append((ap, ok))
        for key, thr in (("abs_ge_0_4", 0.4), ("abs_ge_0_6", 0.6)):
            if ap >= thr:
                buckets[key]["n"] += 1
                if ok:
                    buckets[key]["hit"] += 1
    out: Dict[str, Any] = {
        "n_valid": n_valid,
        "pos_recall": round(pos_hit / pos_n, 4) if pos_n else None,
        "neg_recall": round(neg_hit / neg_n, 4) if neg_n else None,
        "n_pos": pos_n,
        "n_neg": neg_n,
        "buckets": {},
    }
    for key, pack in buckets.items():
        n = int(pack["n"])
        out["buckets"][key] = {
            "n": n,
            "sign_hit": round(pack["hit"] / n, 4) if n else None,
        }
    # 分位强信号：top30% / top15%（阈=|ŷ| 的 70/85 分位）
    if len(abs_ok) >= 10:
        import numpy as np

        abs_arr = np.asarray([a for a, _ in abs_ok], dtype=np.float64)
        for key, q in (("abs_top_30", 70.0), ("abs_top_15", 85.0)):
            thr = float(np.percentile(abs_arr, q))
            n = hit = 0
            for ap, ok in abs_ok:
                if ap >= thr and (thr > 1e-15 or ap > 1e-15):
                    n += 1
                    if ok:
                        hit += 1
            out["buckets"][key] = {
                "n": n,
                "sign_hit": round(hit / n, 4) if n >= 5 else None,
                "thr": round(thr, 6),
                "percentile": q,
            }
    else:
        for key in ("abs_top_30", "abs_top_15"):
            out["buckets"][key] = {"n": 0, "sign_hit": None, "thr": None}
    return out


TAU_PROMOTE_MIN_SIGN_HIT = 0.55
TAU_PROMOTE_MIN_N_TEST = 80
TAU_PROMOTE_MIN_STRONG_HIT = 0.58  # |ŷ|≥0.6 桶


def tau_promote_gate(report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """promote 闸：OOS sign_hit / n_test / 强信号桶。"""
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
    try:
        hit_f = float(hit) if hit is not None else None
    except (TypeError, ValueError):
        hit_f = None
    blockers: List[str] = []
    if n_i < TAU_PROMOTE_MIN_N_TEST:
        blockers.append(f"n_test={n_i}<{TAU_PROMOTE_MIN_N_TEST}")
    if hit_f is None:
        blockers.append("缺 OOS sign_hit")
    elif hit_f < TAU_PROMOTE_MIN_SIGN_HIT:
        blockers.append(f"sign_hit={hit_f:.3f}<{TAU_PROMOTE_MIN_SIGN_HIT}")
    buckets = oos.get("buckets") if isinstance(oos.get("buckets"), dict) else {}
    strong = buckets.get("abs_ge_0_6") if isinstance(buckets.get("abs_ge_0_6"), dict) else {}
    s_hit = strong.get("sign_hit")
    s_n = int(strong.get("n") or 0)
    try:
        s_hit_f = float(s_hit) if s_hit is not None else None
    except (TypeError, ValueError):
        s_hit_f = None
    if s_n >= 30 and s_hit_f is not None and s_hit_f < TAU_PROMOTE_MIN_STRONG_HIT:
        blockers.append(
            f"|ŷ|≥0.6 sign_hit={s_hit_f:.3f}<{TAU_PROMOTE_MIN_STRONG_HIT}"
        )
    return {
        "ok": not blockers,
        "blockers": blockers,
        "min_sign_hit": TAU_PROMOTE_MIN_SIGN_HIT,
        "min_n_test": TAU_PROMOTE_MIN_N_TEST,
        "min_strong_hit": TAU_PROMOTE_MIN_STRONG_HIT,
        "n_test": n_i,
        "sign_hit": hit_f,
        "strong_sign_hit": s_hit_f,
        "strong_n": s_n,
    }


def _residual_var(
    preds: List[Optional[float]], ys: List[float]
) -> Optional[float]:
    errs: List[float] = []
    for p, y in zip(preds, ys):
        if p is None:
            continue
        try:
            errs.append((float(y) - float(p)) ** 2)
        except (TypeError, ValueError):
            continue
    if len(errs) < 5:
        return None
    return round(sum(errs) / float(len(errs)), 6)


def _oos_by_theme(
    preds: List[Optional[float]],
    ys: List[float],
    metas: List[dict],
) -> Dict[str, Any]:
    """主题日 vs 普通日分层 OOS（同一切分测试集）。"""

    def _slice(theme_flag: Optional[int]) -> Dict[str, Any]:
        ps: List[Optional[float]] = []
        zs: List[float] = []
        for p, y, m in zip(preds, ys, metas):
            th = int((m or {}).get("theme_day") or 0)
            if theme_flag is not None and th != int(theme_flag):
                continue
            ps.append(p)
            zs.append(float(y))
        return {
            "n": len(zs),
            "ic": _ic(ps, zs) if zs else None,
            "sign_hit": _sign_hit(ps, zs) if zs else None,
            "residual_var": _residual_var(ps, zs) if zs else None,
        }

    return {
        "theme": _slice(1),
        "normal": _slice(0),
        "all": _slice(None),
    }


def _oos_by_tau(
    preds: List[Optional[float]],
    ys: List[float],
    metas: List[dict],
) -> Dict[str, Any]:
    """按决策钟 τ 分层 OOS。

    τ→close 标签不含开→τ，晚钟 hit 不会被已实现路径机械垫高（旧 OC 口径才有）。
    人审看各档 IC，勿把 by_tau hit 曲线当越好。
    """
    buckets: Dict[str, Dict[str, List[Any]]] = {}
    for p, y, m in zip(preds, ys, metas):
        tau = str((m or {}).get("tau") or "open").strip() or "open"
        slot = buckets.setdefault(tau, {"ps": [], "zs": []})
        slot["ps"].append(p)
        slot["zs"].append(float(y))
    out: Dict[str, Any] = {}
    for tau in sorted(buckets.keys()):
        ps = buckets[tau]["ps"]
        zs = buckets[tau]["zs"]
        out[tau] = {
            "n": len(zs),
            "ic": _ic(ps, zs) if zs else None,
            "sign_hit": _sign_hit(ps, zs) if zs else None,
            "residual_var": _residual_var(ps, zs) if zs else None,
        }
    return out


def _theme_counts(metas: Sequence[dict]) -> Dict[str, Any]:
    n = len(metas or [])
    n_th = sum(1 for m in (metas or []) if int((m or {}).get("theme_day") or 0) == 1)
    return {
        "n": n,
        "n_theme": n_th,
        "theme_rate": round(n_th / float(n), 4) if n else None,
    }


def _theme_trigger_sensitivity(
    metas: Sequence[dict],
    *,
    triggers: Sequence[float] = (1.5, 2.0, 2.5),
) -> Dict[str, Any]:
    """报告用：不同 gap_trigger 下本票 |gap| 主题正例率（不重跑截面）。"""
    from core.research.tau_theme import resolve_theme_day

    out: Dict[str, Any] = {}
    rows = list(metas or [])
    n = len(rows)
    for thr in triggers:
        n_th = 0
        for m in rows:
            gap = (m or {}).get("gap_pct")
            b = (m or {}).get("sector_gap_breadth")
            if (
                resolve_theme_day(
                    gap_pct=gap,
                    sector_breadth=b,
                    gap_trigger_pct=float(thr),
                )
                >= 1.0
            ):
                n_th += 1
        key = str(thr).replace(".", "_")
        out[key] = {
            "trigger": float(thr),
            "n_theme": n_th,
            "theme_rate": round(n_th / float(n), 4) if n else None,
        }
    return out



def fit_tau_ridge_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    ridge_lambda: float = 1.0,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    theme_boost: float = 1.5,
    holdout_trading_days: int = 20,
    use_theme_weights: bool = True,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """池化拟合 ŷ_τ 头 Ridge + Holdout OOS。

    标签为 close[T]/price[τ]−1（τ→close）；分钟仅作 ≤τ 特征。开盘时 price[τ]=open。
    ``tau_hm`` 非 open 时换信息集（前缀分钟路径）；``tau_grid`` 变长前缀共享 β。
    默认近 ``holdout_trading_days`` 个交易日只测；**只训 Holdout 前一套 β**
    （启用研究/执行只是版本槽）。
    ``cross_section_zscore``：True=日截面 z；False=训练样本内全局 μ/σ。
    """
    packed = fit_tau_ridge_matrices(
        stock_bars,
        ridge_lambda=ridge_lambda,
        min_history=min_history,
        gap_trigger_pct=gap_trigger_pct,
        theme_boost=theme_boost,
        holdout_trading_days=holdout_trading_days,
        use_theme_weights=use_theme_weights,
        tau_hm=tau_hm,
        tau_grid=tau_grid,
        cross_section_zscore=cross_section_zscore,
    )
    if not packed.get("success"):
        return {
            "success": False,
            "error": packed.get("error") or "拟合失败",
            "task": "tc_ridge",
            "sample_count": packed.get("sample_count"),
            "stock_count": packed.get("stock_count"),
            "tau": packed.get("tau"),
            "tau_grid": packed.get("tau_grid"),
        }

    tau_key = packed["tau_key"]
    use_minute = packed["use_minute"]
    grid = packed["grid"]
    target = packed["target"]
    n_stocks = packed["n_stocks"]
    ys = packed["ys"]
    ys_use = packed["ys_use"]
    dates_use = packed["dates_use"]
    metas_use = packed["metas_use"]
    ys_tr = packed["ys_tr"]
    ys_te = packed["ys_te"]
    metas_tr = packed["metas_tr"]
    metas_te = packed["metas_te"]
    preds_te = packed["preds_te"]
    hold_n = packed["hold_n"]
    fill_keys = packed["fill_keys"]
    feature_fill = packed["feature_fill"]
    use_cs = packed["use_cs"]
    theme_boost_v = packed["theme_boost"]
    use_theme_weights = packed["use_theme_weights"]
    model = packed["model"]
    split_meta = packed["split_meta"]

    by_theme = _oos_by_theme(preds_te, ys_te, metas_te) if ys_te else {}
    by_tau = _oos_by_tau(preds_te, ys_te, metas_te) if ys_te and use_minute else {}
    bucket_pack = _oos_sign_buckets(preds_te, ys_te) if ys_te else {}
    oos = {
        "n_train": len(ys_tr),
        "n_test": len(ys_te),
        "n_valid": bucket_pack.get("n_valid"),
        "ic": _ic(preds_te, ys_te) if ys_te else None,
        "sign_hit": _sign_hit(preds_te, ys_te) if ys_te else None,
        "residual_var": _residual_var(preds_te, ys_te) if ys_te else None,
        "by_theme": by_theme,
        "by_tau": by_tau,
        "theme_counts": {
            "train": _theme_counts(metas_tr),
            "oos": _theme_counts(metas_te),
            "all": _theme_counts(metas_use),
        },
        "theme_trigger_sensitivity": _theme_trigger_sensitivity(metas_use),
        "feature_fill": feature_fill,
        "buckets": bucket_pack.get("buckets") or {},
        "pos_recall": bucket_pack.get("pos_recall"),
        "neg_recall": bucket_pack.get("neg_recall"),
        "n_pos": bucket_pack.get("n_pos"),
        "n_neg": bucket_pack.get("n_neg"),
        "holdout_trading_days": hold_n,
        "theme_boost": theme_boost_v if use_theme_weights else None,
        "target": target,
        "residualized": False,
        "tau": tau_key,
        "include_alpha_tc": True,
    }
    if ys_te:
        try:
            from core.research.daily_cs_ic import attach_daily_cs_ic

            attach_daily_cs_ic(oos, preds_te, ys_te, metas_te)
        except Exception:  # noqa: BLE001
            logger.debug("tau attach_daily_cs_ic failed", exc_info=True)

    model["horizon_mode"] = "tau_to_close"
    model["target"] = target
    model["residualized"] = False
    if use_minute:
        from core.signal.minute_tau_grid import format_shared_tau_formula

        y_formula = format_shared_tau_formula("close[T]/price[τ]-1", grid or [tau_key])
    else:
        y_formula = "close[T]/price[τ]-1"
    model["y_spec"] = {
        "formula": y_formula,
        "unit": "pct",
        "tau": tau_key,
        "tau_grid": list(grid) if grid else None,
        "note": (
            "变长前缀 5m 槽共享 β；标签=τ→close（close[T]/price[τ]−1）；"
            "open 时钟 price[τ]=open；"
            "live 调仓前缀=因果末根（≤10:00）"
            + "；alpha_τc=z_τc_*∪raw_atc_*"
            if use_minute
            else "alpha_τc=z_τc_*∪raw_atc_* τ→close（开盘 price[τ]=open）；theme_day+yclose+tau_lag1/ma5；live 写 y_τc"
        ),
    }
    model["extra_features"] = list(fill_keys)
    model["feat_labels"] = z_tc_feat_labels(
        {**dict(MINUTE_TAU_FEAT_LABELS), **dict(TAU_LAG_FEAT_LABELS)}
    )
    model["include_alpha_tc"] = True
    if use_minute:
        from core.signal.minute_tau_grid import LIVE_PREFIX_CAUSAL_REBALANCE

        model["live_prefix"] = LIVE_PREFIX_CAUSAL_REBALANCE
    model["model_role"] = "research"

    from core.signal.factors.alpha_tc import is_alpha_tc_raw_key
    from core.research.holdout import attach_holdout_meta

    n_atc = sum(
        1 for k in (model.get("extra_features") or []) if is_alpha_tc_raw_key(str(k))
    )
    pack = alpha_tc_report_meta(n_alpha_tc_features=n_atc)
    pack_note = ALPHA_TC_PACK_NOTE
    day_keys = set()
    for m, d in zip(metas_use, dates_use):
        code = ""
        if isinstance(m, dict):
            code = str(m.get("stock_code") or m.get("code") or "").strip()
        day = str(d or "")[:10]
        if day:
            day_keys.add((code, day))
    report = {
        "success": True,
        "task": "tc_ridge",
        "stock_count": n_stocks,
        "sample_count": len(ys_use),
        "sample_count_raw": len(ys),
        "sample_count_day": len(day_keys),
        "oos": oos,
        "return_model": model,
        "return_model_research": model,
        "tau": tau_key,
        "tau_grid": list(grid) if grid else None,
        "live_prefix": model.get("live_prefix"),
        "y_spec": dict(model.get("y_spec") or {}),
        "schema": "tau_ridge_v15",
        "target": target,
        "residualized": False,
        "include_alpha_tc": True,
        "n_alpha_tc_features": n_atc,
        **pack,
        "note": (
            (
                f"ŷ_τc({pack_note})；变长前缀 5m 槽共享 β；τ→close 标签；OOS.by_tau；按日 OOS；live 调仓=因果末根"
            )
            if use_minute
            else (
                f"ŷ_τc({pack_note})；独立估 τ→close（开盘 price[τ]=open）；theme+|gap|；yclose_loc；PIT tau_lag1/ma5；与 EOD 解耦"
            )
        ),
    }
    attach_holdout_meta(report, split_meta)
    report["cross_section_zscore"] = use_cs
    report["promote_gate"] = tau_promote_gate(report)
    return report


def tau_model_path() -> str:
    """Live ŷ_τ 模型主路径。"""
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_ridge_model.json")


def tau_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "tau_ridge_last_report.json")


_JSON_MODEL_CACHE: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


def _load_json_model(path: str) -> Optional[Dict[str, Any]]:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _JSON_MODEL_CACHE.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load tau json failed: %s", path, exc_info=True)
        _JSON_MODEL_CACHE[path] = (mtime, None)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        _JSON_MODEL_CACHE[path] = (mtime, None)
        return None
    if doc.get("success") is False:
        _JSON_MODEL_CACHE[path] = (mtime, None)
        return None
    _JSON_MODEL_CACHE[path] = (mtime, doc)
    if len(_JSON_MODEL_CACHE) > 16:
        # 保当前这条，丢掉最旧的若干（模型文件就 2–4 个）
        extra = [k for k in _JSON_MODEL_CACHE if k != path]
        for k in extra[: max(0, len(_JSON_MODEL_CACHE) - 12)]:
            _JSON_MODEL_CACHE.pop(k, None)
    return doc


def save_tau_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    from core.research.holdout import stamp_fitted_at

    stamp_fitted_at(report)
    path = tau_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)
    _JSON_MODEL_CACHE.pop(path, None)


def load_tau_last_report() -> Optional[Dict[str, Any]]:
    doc = _load_json_model(tau_last_report_path())
    if doc and doc.get("success"):
        return doc
    return None


def persist_tau_model(
    report: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
    role: str = "live",
) -> Dict[str, Any]:
    """启用研究=报告→研究槽；启用执行=研究槽→执行槽（晋级）。"""
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        normalize_model_role,
        require_research_suite,
        research_model_path,
        select_fit_return_model,
        stamp_live_suite_doc,
    )

    role_n = normalize_model_role(role)
    live_path = tau_model_path()

    if role_n == MODEL_ROLE_LIVE:
        research_doc, err = require_research_suite(live_path)
        if err:
            return err
        doc = stamp_live_suite_doc(
            research_doc,
            note=note or "tau_ridge research → live promote",
        )
        doc["role"] = "live"
        os.makedirs(os.path.dirname(live_path), exist_ok=True)
        atomic_write_json(live_path, doc)
        _JSON_MODEL_CACHE.pop(live_path, None)
        return {
            "success": True,
            "path": live_path,
            "promoted_at": doc.get("promoted_at"),
            "tau": (doc.get("return_model") or {}).get("tau") or doc.get("tau"),
            "schema": doc.get("schema"),
            "promote_gate": doc.get("promote_gate"),
            "model_role": MODEL_ROLE_LIVE,
            "promoted_from": "research",
        }

    if not report.get("success"):
        return {"success": False, "error": report.get("error") or "no report"}
    rm = select_fit_return_model(report)
    if not isinstance(rm, dict):
        return {"success": False, "error": "return_model missing"}
    if not rm.get("coefficients") and rm.get("intercept") is None:
        return {"success": False, "error": "return_model missing"}
    gate = tau_promote_gate(report)
    if not force and not gate.get("ok"):
        return {
            "success": False,
            "error": "promote 未过闸：" + "；".join(gate.get("blockers") or []),
            "promote_gate": gate,
        }
    from core.numbers import now_iso_utc

    y_spec = dict(rm.get("y_spec") or {})
    if not y_spec:
        y_spec = {
            "formula": "close[T]/price[τ]-1",
            "unit": "pct",
            "tau": "open",
            "note": "alpha_τc=z_τc_*∪raw_atc_* τ→close",
        }
    tau = str(y_spec.get("tau") or rm.get("horizon_mode") or "open")
    if tau in ("open_to_close", "open→close", "tau_to_close", "τ→close"):
        tau = "open"
    y_spec.setdefault("tau", tau)
    y_spec.setdefault("unit", "pct")
    rm = dict(rm)
    rm["y_spec"] = y_spec
    rm["horizon_mode"] = rm.get("horizon_mode") or "tau_to_close"
    rm["tau"] = tau
    target = str(report.get("target") or rm.get("target") or "tau_to_close_z")
    residualized = False
    rm["target"] = target
    rm["residualized"] = residualized
    rm["model_role"] = MODEL_ROLE_RESEARCH
    if "include_alpha_tc" in report:
        rm["include_alpha_tc"] = bool(report.get("include_alpha_tc"))
    elif "include_alpha_tc" not in rm:
        rm["include_alpha_tc"] = True
    # 旧包字段不再落盘
    rm.pop("include_alpha_oo", None)
    rm.pop("n_alpha_oo_features", None)

    raw_schema = str(report.get("schema") or "tau_ridge_v15")
    if raw_schema.startswith("rem_ridge"):
        raw_schema = "tau_ridge_v15"
    schema = raw_schema
    from core.research.holdout import model_fit_id

    fitted_at = model_fit_id(report)
    n_atc = int(
        report.get("n_alpha_tc_features")
        or report.get("n_alpha_oo_features")  # 旧报告兼容
        or 0
    )
    pack = alpha_tc_report_meta(n_alpha_tc_features=n_atc)
    doc = {
        "success": True,
        "fitted_at": fitted_at,
        "promoted_at": now_iso_utc(),
        "note": note or "tau_ridge promote research",
        "return_model": rm,
        "oos": report.get("oos"),
        "sample_count": report.get("sample_count"),
        "stock_count": report.get("stock_count"),
        "schema": schema,
        "target": target,
        "residualized": residualized,
        "tau": tau,
        "as_of_tau": tau,
        "y_spec": y_spec,
        "y_spec_tau": y_spec,
        "promote_gate": gate,
        "model_role": MODEL_ROLE_RESEARCH,
        "fit_end": report.get("fit_end"),
        "eval_start": report.get("eval_start"),
        "holdout_trading_days": report.get("holdout_trading_days"),
        "dual_score_head": "predicted_score_tau",
        "contract_note": (
            f"ŷ_τc({ALPHA_TC_PACK_NOTE}) "
            "估 τ→close → y_τc；fuse 用这一列；与 ŷ_oo 独立；不覆盖 predicted_score。"
        ),
        **pack,
    }
    if isinstance(report, dict) and "cross_section_zscore" in report:
        doc["cross_section_zscore"] = bool(report.get("cross_section_zscore"))
    path = research_model_path(live_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    _JSON_MODEL_CACHE.pop(path, None)
    out = {
        "success": True,
        "path": path,
        "promoted_at": doc["promoted_at"],
        "tau": tau,
        "schema": doc["schema"],
        "promote_gate": gate,
        "model_role": MODEL_ROLE_RESEARCH,
    }
    return out


def load_tau_model(*, role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    from core.research.holdout import (
        MODEL_ROLE_RESEARCH,
        current_scoring_model_role,
        research_model_path,
    )

    role_n = role if role is not None else current_scoring_model_role()
    if role_n == MODEL_ROLE_RESEARCH:
        return _load_json_model(research_model_path(tau_model_path()))
    doc = _load_json_model(tau_model_path())
    if doc:
        return doc
    return None


def predict_tau_from_features(
    features: Dict[str, Optional[float]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    doc = model_doc if model_doc is not None else load_tau_model()
    if not doc:
        return None
    rm = doc.get("return_model") or {}
    preds = _predict_rows(rm, [features])
    return preds[0] if preds else None


_TAU_FEAT_LABELS = z_tc_feat_labels(
    {
        "gap_pct": "跳空 %",
        "open_gap": "开盘缺口",
        "sector_gap_breadth": "同业缺口广度",
        "theme_day": "主题日",
        "gap_atr": "缺口 / ATR",
        "gap_vs_sector": "行业相对缺口",
        "yclose_loc": "昨收位置",
        **TAU_LAG_FEAT_LABELS,
        **T30_LAG_FEAT_LABELS,
        **MINUTE_TAU_FEAT_LABELS,
    }
)


def explain_tau_prediction(
    features: Optional[Dict[str, Any]],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """结构化拆解 ŷ_τ（与 ``predict_tau_from_features`` 同口径），供 tip 表格。

    缺特征填 z=0；截面模型输入按已标准化值理解。
    """
    doc = model_doc if model_doc is not None else load_tau_model()
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
    row = features or {}
    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        factor_label = lambda k: str(k)  # noqa: E731

    terms: List[Dict[str, Any]] = []
    total = intercept
    for name in active:
        beta = float(coefs.get(name) or 0.0)
        v = feature_get(row, name)
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
        label = _TAU_FEAT_LABELS.get(name) or factor_label(name) or name
        term: Dict[str, Any] = {
            "key": str(name),
            "label": str(label),
            "beta": round(beta, 6),
            "z": round(float(z), 4),
            "contrib": round(float(contrib), 6),
        }
        if imputed:
            term["note"] = "缺特征·z≈0"
        terms.append(term)
    if not terms and abs(intercept) < 1e-12:
        return None
    # 旧 τ 头仍含日线 β 时：缺特征的非 Z/aoo 行对 tip 无信息，只保留 Z（含缺特征）与有实值的项
    from core.signal.factors.alpha_oo import is_alpha_oo_raw_key

    z_keys = set(TAU_Z_FEATURES) | set(Z_TC_LEGACY_KEYS) | {"open_gap", "z_τc_open_gap"}
    slim: List[Dict[str, Any]] = []
    for t in terms:
        key = str(t.get("key") or "")
        if t.get("note") and key not in z_keys and not is_alpha_oo_raw_key(key):
            continue
        slim.append(t)
    if slim:
        terms = slim
    # 展示只列有实值的项。缺特征已按均值填进合计（contrib=0），不占表。
    missing_terms = [t for t in terms if t.get("note")]
    n_missing = len(missing_terms)
    shown = [t for t in terms if not t.get("note")]
    if shown:
        terms = shown
    terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0)))
    out = {
        "intercept": round(intercept, 6),
        "terms": terms[:32],
        "total": round(total, 6),
        "head": "tau",
    }
    role = str((doc or {}).get("model_role") or "").strip()
    if role:
        out["model_role"] = role
    if n_missing:
        out["missing_n"] = int(n_missing)
        out["missing_keys"] = [
            str(t.get("label") or t.get("key") or "")
            for t in missing_terms[:8]
            if str(t.get("label") or t.get("key") or "")
        ]
    return out

