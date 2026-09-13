"""B 轨 · 回归准确性：y_spec、样本指纹、共线进模、λ 网格。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def build_y_spec(
    *,
    horizon_days: Optional[int] = None,
    include_cost: bool = False,
    halt_policy: str = "keep_bar",
    formula: str = "open[T+1]/open[T]-1",
    unit: str = "pct",
    note: str = "",
    excess_mode: str = "none",
) -> Dict[str, Any]:
    """前瞻收益标签契约（写入 return_model / OLS / cluster artifact）。

    ``excess_mode``（P2b 研究臂）：
      - ``none``：绝对收益（现网默认）
      - ``index``：个股收益 − 同期指数收益（需面板提供 index 对齐）
    """
    if horizon_days is None:
        try:
            from core.signal.config import load_signal_config

            cfg_h = (load_signal_config() or {}).get("scoring", {}).get("horizon_days")
            horizon_days = int(cfg_h) if cfg_h is not None else 3
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in beta_accuracy.py", exc_info=True)
            horizon_days = 3
    h = max(1, min(int(horizon_days or 3), 20))
    em = str(excess_mode or "none").strip().lower()
    if em in ("index", "excess", "vs_index", "benchmark"):
        em = "index"
        formula = "stock_ret[t→t+h] - index_ret[t→t+h]"
    else:
        em = "none"
    base_note = (
        f"y=(open[t+{h}]/open[t]-1)*100；"
        "X 用到 T−1 收，T 开可进 quote；今收不进 X。"
        "默认不含交易成本；停牌日若无 bar 则该样本跳过。"
    )
    if em == "index":
        base_note = (
            f"y=个股前瞻% − 指数同期%（excess_mode=index）；"
            f"h={h}；研究臂，进生产须人审重跑分组。"
        )
    return {
        "horizon_days": h,
        "formula": formula,
        "unit": unit,
        "include_cost": bool(include_cost),
        "halt_policy": str(halt_policy or "keep_bar"),
        "excess_mode": em,
        "note": note or base_note,
        "track": "B2",
    }


def apply_excess_to_forward_return(
    stock_fwd_pct: Optional[float],
    index_fwd_pct: Optional[float],
    *,
    excess_mode: str = "none",
) -> Optional[float]:
    """按 y_spec.excess_mode 把绝对前瞻收益映成超额（P2b）。"""
    if stock_fwd_pct is None:
        return None
    em = str(excess_mode or "none").strip().lower()
    if em in ("", "none", "absolute", "total"):
        return float(stock_fwd_pct)
    if em in ("index", "excess", "vs_index", "benchmark"):
        if index_fwd_pct is None:
            return None
        return round(float(stock_fwd_pct) - float(index_fwd_pct), 6)
    return float(stock_fwd_pct)


def sample_fingerprint(
    *,
    n_obs: int = 0,
    n_names: int = 0,
    date_span: Optional[str] = None,
    date_min: Optional[str] = None,
    date_max: Optional[str] = None,
    dropped: Optional[dict] = None,
    min_obs: int = 24,
    min_names: int = 3,
) -> Dict[str, Any]:
    """拟合样本指纹；不足则 promote_ok=False。"""
    n_obs_i = int(n_obs or 0)
    n_names_i = int(n_names or 0)
    span = date_span
    if not span and date_min and date_max:
        span = f"{date_min}→{date_max}"
    ok = n_obs_i >= int(min_obs) and n_names_i >= int(min_names)
    blockers: List[str] = []
    if n_obs_i < int(min_obs):
        blockers.append(f"n_obs={n_obs_i} < min_obs={min_obs}")
    if n_names_i < int(min_names):
        blockers.append(f"n_names={n_names_i} < min_names={min_names}")
    return {
        "n_obs": n_obs_i,
        "n_names": n_names_i,
        "date_span": span,
        "date_min": date_min,
        "date_max": date_max,
        "dropped": dict(dropped or {}),
        "min_obs": int(min_obs),
        "min_names": int(min_names),
        "promote_ok": ok,
        "blockers": blockers,
        "track": "B1",
    }


MIN_CLUSTER_OBS = 24


def fingerprint_blocker_is_group_local(msg: str, *, from_group: bool = False) -> bool:
    """组级拦阻不连坐整份产物：单票/双票 n_names，或该组 n_obs 不足。

    次新单独成组时 n_obs<24，应跳过该组回退全局 β，不应否掉其余组的对照/promote。
    全产物自己的 ``n_obs < min_obs``（无「组内：」前缀）仍硬拦。
    """
    s = str(msg or "")
    tagged = s.startswith("组内：")
    if tagged:
        s = s[len("组内：") :]
    if "n_names=" in s and "min_names=" in s:
        try:
            n_part = s.split("n_names=")[1].split("<")[0].strip()
            if int(float(n_part)) < 3:
                return True
        except (TypeError, ValueError):
            pass
    if (tagged or from_group) and "n_obs=" in s and "min_obs=" in s:
        return True
    return False


def dates_span_from_panel_rows(
    xs: Sequence[Dict[str, Any]],
    *,
    date_key: str = "decision_date",
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    dates = []
    for row in xs or []:
        if not isinstance(row, dict):
            continue
        d = str(row.get(date_key) or row.get("date") or "")[:10]
        if len(d) >= 10 and d[4] == "-":
            dates.append(d)
    if not dates:
        return None, None, None
    dates.sort()
    return dates[0], dates[-1], f"{dates[0]}→{dates[-1]}"


def apply_collinearity_policy(
    xs: List[Dict[str, float]],
    active: List[str],
    *,
    policy: str = "drop_redundant",
    corr_threshold: float = 0.85,
    prefer_by_abs_corr_with_y: Optional[Sequence[float]] = None,
    ys: Optional[Sequence[float]] = None,
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """趋势族/高相关对进模策略。

    keep_all | drop_redundant（默认）| orthogonalize_lite（当前等同 drop_redundant 记录）。
    返回 (kept_active, dropped, meta)。
    """
    from core.signal.factors.meta.collinearity import TREND_FAMILY

    pol = str(policy or "drop_redundant").strip().lower()
    meta: Dict[str, Any] = {
        "collinearity_policy": pol,
        "corr_threshold": float(corr_threshold),
        "pairs": [],
        "dropped": [],
    }
    if pol in ("keep_all", "off", "none") or len(active) < 2 or not xs:
        return list(active), [], meta

    # 仅对趋势族内做冗余剔除；其它因子保留
    family = [n for n in active if n in TREND_FAMILY]
    if len(family) < 2:
        return list(active), [], meta

    def _corr(a: str, b: str) -> Optional[float]:
        xa, xb = [], []
        for row in xs:
            va, vb = row.get(a), row.get(b)
            if va is None or vb is None:
                continue
            try:
                xa.append(float(va))
                xb.append(float(vb))
            except (TypeError, ValueError):
                continue
        n = min(len(xa), len(xb))
        if n < 5:
            return None
        xa, xb = xa[:n], xb[:n]
        ma, mb = sum(xa) / n, sum(xb) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
        da = math.sqrt(sum((x - ma) ** 2 for x in xa))
        db = math.sqrt(sum((y - mb) ** 2 for y in xb))
        if da < 1e-12 or db < 1e-12:
            return None
        return num / (da * db)

    def _ic(name: str) -> float:
        if ys is None:
            return 0.0
        xv, yv = [], []
        for row, y in zip(xs, ys):
            v = row.get(name)
            if v is None:
                continue
            try:
                xv.append(float(v))
                yv.append(float(y))
            except (TypeError, ValueError):
                continue
        n = min(len(xv), len(yv))
        if n < 5:
            return 0.0
        xv, yv = xv[:n], yv[:n]
        mx, my = sum(xv) / n, sum(yv) / n
        num = sum((a - mx) * (b - my) for a, b in zip(xv, yv))
        dx = math.sqrt(sum((a - mx) ** 2 for a in xv))
        dy = math.sqrt(sum((b - my) ** 2 for b in yv))
        if dx < 1e-12 or dy < 1e-12:
            return 0.0
        return abs(num / (dx * dy))

    dropped: List[str] = []
    kept = set(active)
    thr = float(corr_threshold)
    # greedy: highest |corr| pairs first
    pairs = []
    for i, a in enumerate(family):
        for b in family[i + 1 :]:
            c = _corr(a, b)
            if c is not None and abs(c) >= thr:
                pairs.append((abs(c), a, b, c))
    pairs.sort(reverse=True)
    for abs_c, a, b, c in pairs:
        if a not in kept or b not in kept:
            continue
        # 保留与 y 相关更强的一侧
        ia, ib = _ic(a), _ic(b)
        drop = b if ia >= ib else a
        keep = a if drop == b else b
        kept.discard(drop)
        dropped.append(drop)
        meta["pairs"].append(
            {
                "a": a,
                "b": b,
                "corr": round(float(c), 4),
                "kept": keep,
                "dropped": drop,
            }
        )
    meta["dropped"] = list(dropped)
    if pol == "orthogonalize_lite":
        meta["note"] = "orthogonalize_lite 当前降级为 drop_redundant（择一保留）"
    # 保持原 active 顺序
    new_active = [n for n in active if n in kept]
    return new_active, dropped, meta


def select_ridge_lambda(
    xs: List[Dict[str, float]],
    ys: List[float],
    active: List[str],
    *,
    grid: Optional[Sequence[float]] = None,
    n_splits: int = 3,
) -> Dict[str, Any]:
    """时间切分选 Ridge λ：最小化后段 MSE（简化 walk-forward）。"""
    from core.research.factor_ols_fit import _ols_with_intercept, clamp_ridge_lambda

    grid_vals = [clamp_ridge_lambda(x, 0.0) for x in (grid or (0.0, 0.1, 1.0, 5.0, 10.0))]
    n = len(ys)
    if n < 12 or not active or len(xs) != n:
        return {
            "ridge_lambda_selected": 0.0,
            "grid": grid_vals,
            "scores": {},
            "note": "样本不足，λ=0",
        }

    # 简单：用后 1/3 作验证
    cut = max(8, int(n * 2 / 3))
    if cut >= n - 2:
        cut = n - 3
    train_x, train_y = xs[:cut], ys[:cut]
    test_x, test_y = xs[cut:], ys[cut:]
    scores: Dict[str, float] = {}
    best_lam = 0.0
    best_mse = float("inf")
    for lam in grid_vals:
        fit = _ols_with_intercept(
            train_x, train_y, list(active), [], ridge_lambda=lam
        )
        if not fit:
            continue
        intercept = float(fit.get("intercept") or 0.0)
        coefs = {
            k: float(v)
            for k, v in (fit.get("coefficients") or {}).items()
            if v is not None
        }
        se = 0.0
        m = 0
        for row, y in zip(test_x, test_y):
            pred = intercept
            for k, b in coefs.items():
                try:
                    pred += float(b) * float(row.get(k) or 0.0)
                except (TypeError, ValueError):
                    pass
            se += (pred - float(y)) ** 2
            m += 1
        if m <= 0:
            continue
        mse = se / m
        scores[str(lam)] = round(mse, 6)
        if mse < best_mse:
            best_mse = mse
            best_lam = lam
    return {
        "ridge_lambda_selected": best_lam,
        "grid": grid_vals,
        "scores": scores,
        "holdout_mse": None if best_mse == float("inf") else round(best_mse, 6),
        "train_n": cut,
        "test_n": n - cut,
        "track": "B3",
    }


def ann_missing_top_codes(coverage: dict, *, limit: int = 20) -> List[Dict[str, Any]]:
    """从 fundamentals_history_coverage 抽出缺 ann 的码列表。"""
    rows = []
    for r in coverage.get("rows") or []:
        if not isinstance(r, dict):
            continue
        n = int(r.get("ann_missing_points") or 0)
        if n <= 0:
            continue
        rows.append(
            {
                "code": r.get("code"),
                "ann_missing_points": n,
                "history_count": r.get("history_count"),
                "status": r.get("status"),
            }
        )
    rows.sort(key=lambda x: (-int(x.get("ann_missing_points") or 0), str(x.get("code"))))
    return rows[: max(1, int(limit))]
