"""把因子行折成 float64 面板，再在矩阵上做 keep-all Ridge。

截面广度仍要读缺口和路径收益，所以每只股票先写入矩阵，只把广度用到的十几列
留在瘦 dict 里。Alpha158 原始列不再在全池 dict 里停留。
Ridge 口径与 ``fit_factor_ols_from_panel`` 对齐：√w + keep_all；拟合用完整行。
``cross_section_zscore=True``（默认）且有决策日 → 当天截面 z；``False`` → 训练窗
全局 μ/σ。截面模型预测缺测填 z=0。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_BREADTH_BASE_KEYS = (
    "gap_pct",
    "ret_open_to_tau",
    "sector_gap_breadth",
    "theme_day",
    "gap_vs_sector",
    "sector_ret_to_tau",
    "ret_vs_sector",
)


def _breadth_keys() -> Tuple[str, ...]:
    from core.research.tau_panel import HORIZON_SEQ_CS_SPECS

    keys = list(_BREADTH_BASE_KEYS)
    seen = set(keys)
    for ret_key, sector_key, vs_key in HORIZON_SEQ_CS_SPECS:
        for k in (ret_key, sector_key, vs_key):
            if k not in seen:
                seen.add(k)
                keys.append(k)
    return tuple(keys)


class PanelMatrix:
    """按列名累积 float64 行。``raw_alpha158_*`` 可在见到数值时加列。"""

    def __init__(self, names: Sequence[str], *, widen_prefix: str = "raw_alpha158_"):
        self.names: List[str] = [str(n) for n in names]
        self._index: Dict[str, int] = {n: i for i, n in enumerate(self.names)}
        self._widen_prefix = str(widen_prefix or "")
        self._chunks: List[np.ndarray] = []

    def _widen(self, name: str) -> int:
        have = self._index.get(name)
        if have is not None:
            return have
        j = len(self.names)
        self.names.append(name)
        self._index[name] = j
        pad_chunks: List[np.ndarray] = []
        for chunk in self._chunks:
            pad = np.full((chunk.shape[0], 1), np.nan, dtype=np.float64)
            pad_chunks.append(np.concatenate([chunk, pad], axis=1))
        self._chunks = pad_chunks
        return j

    def add_rows(self, rows: Sequence[dict]) -> None:
        extra: List[str] = []
        prefix = self._widen_prefix
        if prefix:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for k, v in row.items():
                    ks = str(k)
                    if ks in self._index or not ks.startswith(prefix):
                        continue
                    if v is None or v == "":
                        continue
                    try:
                        float(v)
                    except (TypeError, ValueError):
                        continue
                    if ks not in extra:
                        extra.append(ks)
        for name in extra:
            self._widen(name)
        n = len(rows)
        if n <= 0:
            return
        p = len(self.names)
        block = np.full((n, p), np.nan, dtype=np.float64)
        index = self._index
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                j = index.get(str(k))
                if j is None or v is None or v == "":
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(fv):
                    block[i, j] = fv
        self._chunks.append(block)

    def finalize(self) -> np.ndarray:
        if not self._chunks:
            return np.zeros((0, len(self.names)), dtype=np.float64)
        if len(self._chunks) == 1:
            out = self._chunks[0]
        else:
            out = np.vstack(self._chunks)
        self._chunks.clear()
        return out


def _slim_rows(rows: Sequence[dict], keys: Sequence[str]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        src = row if isinstance(row, dict) else {}
        out.append({k: src.get(k) for k in keys})
    return out


def _panel_xs_count(panels: Sequence[Dict[str, Any]]) -> int:
    return sum(len(p.get("xs") or []) for p in panels)


def _write_breadth_columns(
    X: np.ndarray,
    names: Sequence[str],
    panels: Sequence[Dict[str, Any]],
    keys: Sequence[str],
) -> None:
    n_xs = _panel_xs_count(panels)
    if n_xs != int(X.shape[0]):
        raise RuntimeError(
            f"截面广度行数不一致 matrix={int(X.shape[0])} xs={n_xs}"
        )
    index = {str(n): i for i, n in enumerate(names)}
    cols = [(k, index[k]) for k in keys if k in index]
    row_i = 0
    for panel in panels:
        for src in panel.get("xs") or []:
            row = src if isinstance(src, dict) else {}
            for key, j in cols:
                v = row.get(key)
                if v is None or v == "":
                    X[row_i, j] = np.nan
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    X[row_i, j] = np.nan
                    continue
                X[row_i, j] = fv if math.isfinite(fv) else np.nan
            row_i += 1


def _absorb_stock(
    block: PanelMatrix,
    slim_panels: List[Dict[str, Any]],
    *,
    code: str,
    xs: Sequence[dict],
    ys: Sequence[float],
    dates: Sequence[str],
    metas: Sequence[dict],
    slim_keys: Sequence[str],
) -> None:
    n = len(xs)
    if n != len(ys) or n != len(dates) or n != len(metas):
        raise RuntimeError(
            f"面板行数不一致 {code}: xs={n} y={len(ys)} dates={len(dates)} metas={len(metas)}"
        )
    block.add_rows(xs)
    slim_panels.append(
        {
            "code": code,
            "xs": _slim_rows(xs, slim_keys),
            "ys": list(ys),
            "dates": list(dates),
            "metas": list(metas),
        }
    )


def _finish_breadth(
    block: PanelMatrix,
    slim_panels: List[Dict[str, Any]],
    *,
    gap_trigger_pct: float,
    slim_keys: Sequence[str],
    head: str,
    n_stocks: int,
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict]]:
    from core.research.tau_panel import attach_cross_section_breadth

    enriched = attach_cross_section_breadth(
        slim_panels, gap_trigger_pct=gap_trigger_pct
    )
    X = block.finalize()
    _write_breadth_columns(X, block.names, enriched, slim_keys)
    ys: List[float] = []
    dates: List[str] = []
    metas: List[dict] = []
    for panel in enriched:
        ys.extend(float(y) for y in (panel.get("ys") or []))
        dates.extend(str(d)[:10] for d in (panel.get("dates") or []))
        metas.extend(panel.get("metas") or [])
    n_rows = int(X.shape[0])
    if not (len(ys) == len(dates) == len(metas) == n_rows):
        raise RuntimeError(
            f"{head} 面板行数不一致 matrix={n_rows} y={len(ys)} "
            f"dates={len(dates)} metas={len(metas)}"
        )
    logger.info(
        "compact panel head=%s rows=%s cols=%s stocks=%s",
        head,
        int(X.shape[0]),
        int(X.shape[1]),
        int(n_stocks),
    )
    return X, list(block.names), ys, dates, metas


def collect_tau_compact(
    stock_bars: Sequence[Dict[str, Any]],
    names: Sequence[str],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    tau_hm: str = "open",
    tau_grid: Optional[Sequence[str]] = None,
    include_alpha158: bool = True,
    head: str = "y_tc",
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], int]:
    """逐股折成矩阵后再做截面广度。返回 (X, names, ys, dates, metas, n_stocks)。"""
    from core.research.tau_panel import (
        collect_tau_intraday_panel,
        collect_tau_open_panel,
        normalize_minute_tau_grid,
    )

    use_minute = str(tau_hm or "open").strip().lower() not in ("", "open")
    grid = (
        normalize_minute_tau_grid(tau_hm=tau_hm, tau_grid=tau_grid)
        if use_minute
        else None
    )
    block = PanelMatrix(
        names, widen_prefix="raw_alpha158_" if include_alpha158 else ""
    )
    slim_keys = _breadth_keys()
    slim_panels: List[Dict[str, Any]] = []
    n_stocks = 0
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 2:
            item.pop("minute_bars", None)
            continue
        if use_minute:
            xs, ys, dates, metas = collect_tau_intraday_panel(
                bars,
                item.get("minute_bars"),
                tau_hm=str(tau_hm),
                tau_grid=grid,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                include_alpha158=include_alpha158,
            )
            item.pop("minute_bars", None)
        else:
            xs, ys, dates, metas = collect_tau_open_panel(
                bars,
                min_history=min_history,
                index_bars=item.get("index_bars"),
                fundamentals=item.get("fundamentals"),
                stock_code=code,
                include_alpha158=include_alpha158,
            )
        if len(ys) < 4:
            continue
        _absorb_stock(
            block,
            slim_panels,
            code=code,
            xs=xs,
            ys=ys,
            dates=dates,
            metas=metas,
            slim_keys=slim_keys,
        )
        del xs
        n_stocks += 1
    X, col_names, ys_out, dates_out, metas_out = _finish_breadth(
        block,
        slim_panels,
        gap_trigger_pct=gap_trigger_pct,
        slim_keys=slim_keys,
        head=head,
        n_stocks=n_stocks,
    )
    return X, col_names, ys_out, dates_out, metas_out, n_stocks


def collect_co_compact(
    stock_bars: Sequence[Dict[str, Any]],
    names: Sequence[str],
    *,
    min_history: int = 12,
    gap_trigger_pct: float = 2.0,
    include_alpha158: bool = True,
    head: str = "y_co",
) -> Tuple[np.ndarray, List[str], List[float], List[str], List[dict], int]:
    from core.research.co_panel import collect_co_panel

    block = PanelMatrix(
        names, widen_prefix="raw_alpha158_" if include_alpha158 else ""
    )
    slim_keys = _breadth_keys()
    slim_panels: List[Dict[str, Any]] = []
    n_stocks = 0
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or item.get("stock_code") or "").strip()
        bars = list(item.get("bars") or [])
        if len(bars) < min_history + 3:
            continue
        xs, ys, dates, metas = collect_co_panel(
            bars,
            min_history=min_history,
            stock_code=code,
            include_alpha158=include_alpha158,
        )
        if len(ys) < 4:
            continue
        _absorb_stock(
            block,
            slim_panels,
            code=code,
            xs=xs,
            ys=ys,
            dates=dates,
            metas=metas,
            slim_keys=slim_keys,
        )
        del xs
        n_stocks += 1
    X, col_names, ys_out, dates_out, metas_out = _finish_breadth(
        block,
        slim_panels,
        gap_trigger_pct=gap_trigger_pct,
        slim_keys=slim_keys,
        head=head,
        n_stocks=n_stocks,
    )
    return X, col_names, ys_out, dates_out, metas_out, n_stocks


def named_columns(
    X: np.ndarray, names: Sequence[str], wanted: Sequence[str]
) -> Tuple[np.ndarray, List[str]]:
    """按名字取出列，得到一块连续 float64。缺列跳过。"""
    index = {str(n): i for i, n in enumerate(names)}
    cols: List[int] = []
    kept: List[str] = []
    for name in wanted:
        key = str(name)
        j = index.get(key)
        if j is None:
            continue
        cols.append(j)
        kept.append(key)
    if not cols:
        return np.zeros((int(X.shape[0]), 0), dtype=np.float64), []
    return np.ascontiguousarray(X[:, cols]), kept


def raw_alpha158_finite(
    X: np.ndarray,
    names: Sequence[str],
    row_idx: Optional[Sequence[int]] = None,
) -> List[str]:
    """训练行（或全表）上至少有一个有限值的 ``raw_alpha158_*``，按列序。"""
    if X.size == 0 or X.shape[0] == 0:
        return []
    if row_idx is None:
        view = X
    else:
        idx = np.asarray(list(row_idx), dtype=np.int64)
        if idx.size == 0:
            return []
        view = X[idx]
    out: List[str] = []
    for j, name in enumerate(names):
        if not str(name).startswith("raw_alpha158_"):
            continue
        if np.isfinite(view[:, j]).any():
            out.append(str(name))
    return out


def finite_name_set(X: np.ndarray, names: Sequence[str]) -> set:
    present = set()
    if X.size == 0 or X.shape[0] == 0:
        return present
    finite = np.isfinite(X)
    for j, name in enumerate(names):
        if bool(finite[:, j].any()):
            present.add(str(name))
    return present


def fill_rates_from_matrix(
    X: np.ndarray, names: Sequence[str], keys: Sequence[str]
) -> Dict[str, Any]:
    """与 ``feature_fill_rates`` 相同结构：有限值视为已填。"""
    n = int(X.shape[0]) if X.ndim == 2 else 0
    index = {str(name): i for i, name in enumerate(names)}
    finite = np.isfinite(X) if n and X.ndim == 2 else None
    out: Dict[str, Any] = {"n": n, "keys": {}}
    for key in keys:
        j = index.get(str(key))
        filled = int(finite[:, j].sum()) if finite is not None and j is not None else 0
        out["keys"][str(key)] = {
            "filled": filled,
            "rate": round(filled / float(n), 4) if n else None,
        }
    return out


def _ridge_solve_np(
    design: np.ndarray,
    y: np.ndarray,
    ridge_lambda: float,
) -> Optional[np.ndarray]:
    """Ridge QR（截距列不惩罚）。"""
    from core.research.factor_ols_fit import _qr_solve_np, clamp_ridge_lambda

    lam = clamp_ridge_lambda(ridge_lambda, 0.0)
    m = int(design.shape[1])
    xs: List[np.ndarray] = [design]
    ys: List[np.ndarray] = [np.asarray(y, dtype=np.float64)]
    if lam > 0 and m > 1:
        extra = np.zeros((m - 1, m), dtype=np.float64)
        extra[:, 1:] = np.eye(m - 1, dtype=np.float64) * math.sqrt(lam)
        xs.append(extra)
        ys.append(np.zeros(m - 1, dtype=np.float64))
    if len(xs) == 1:
        return _qr_solve_np(xs[0], ys[0])
    return _qr_solve_np(np.vstack(xs), np.concatenate(ys))



def _prepare_complete_matrix(
    X: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    *,
    min_std: float,
    min_std_exempt: Sequence[str],
    min_samples_over_p: int = 3,
    eps: float = 1e-6,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], List[str], List[str], Dict[str, Any]]:
    """与 ``_prepare_complete_panel`` 同一套剔除：稀疏、常数、低方差、覆盖。"""
    exempt = {str(n) for n in min_std_exempt if n}
    n_raw = int(y.shape[0])
    meta: Dict[str, Any] = {
        "raw_sample_count": n_raw,
        "dropped_sparse": [],
        "dropped_constant": [],
        "dropped_low_variance": [],
        "dropped_for_coverage": [],
        "min_std": float(min_std),
        "min_std_exempt": sorted(exempt),
    }
    if n_raw < 4 or X.shape[1] == 0:
        return None, None, [], list(names), meta

    finite = np.isfinite(X)
    min_obs = max(8, min(n_raw // 4, 40))
    candidates: List[str] = []
    for j, name in enumerate(names):
        n_obs = int(finite[:, j].sum())
        if n_obs < min_obs:
            meta["dropped_sparse"].append(name)
            continue
        col = X[finite[:, j], j]
        if float(col.max() - col.min()) <= eps:
            meta["dropped_constant"].append(name)
            continue
        candidates.append(name)

    if not candidates:
        return None, None, [], list(names), meta

    active = list(candidates)
    index = {str(n): i for i, n in enumerate(names)}
    min_std_eff = float(min_std)
    while active:
        cols = [index[n] for n in active]
        mask = finite[:, cols].all(axis=1)
        rows_idx = np.flatnonzero(mask)
        n = int(rows_idx.size)
        p = len(active)
        if n < max(4, p + min_samples_over_p):
            miss = sorted(
                (int((~finite[:, index[name]]).sum()), name) for name in active
            )
            drop = miss[-1][1]
            active.remove(drop)
            meta["dropped_for_coverage"].append(drop)
            continue

        still_var: List[str] = []
        low_var_batch: List[Dict[str, Any]] = []
        for name in active:
            vals = X[rows_idx, index[name]]
            if float(vals.max() - vals.min()) <= eps:
                meta["dropped_constant"].append(name)
                continue
            mean = float(vals.mean())
            var = float(np.mean((vals - mean) ** 2))
            std = math.sqrt(var) if var > 0 else 0.0
            if min_std_eff > 0 and std < min_std_eff and name not in exempt:
                low_var_batch.append(
                    {"name": name, "std": round(std, 4), "n": int(vals.size)}
                )
                continue
            still_var.append(name)
        if len(still_var) < 2 and low_var_batch:
            meta["min_std_relaxed"] = True
            meta["min_std_relaxed_from"] = float(min_std)
            meta["dropped_low_variance_skipped"] = list(low_var_batch)
            still_var = still_var + [d["name"] for d in low_var_batch]
            low_var_batch = []
            min_std_eff = 0.0
        else:
            meta["dropped_low_variance"].extend(low_var_batch)
        if len(still_var) < len(active):
            active = still_var
            if not active:
                break
            continue

        excluded = (
            list(meta["dropped_sparse"])
            + list(meta["dropped_constant"])
            + [
                d["name"] if isinstance(d, dict) else d
                for d in meta["dropped_low_variance"]
            ]
            + list(meta["dropped_for_coverage"])
        )
        meta["complete_row_indices"] = [int(i) for i in rows_idx]
        meta["complete_sample_count"] = n
        meta["active_feature_count"] = len(active)
        x_c = np.ascontiguousarray(X[rows_idx][:, [index[n] for n in active]])
        y_c = np.ascontiguousarray(y[rows_idx])
        return x_c, y_c, active, excluded, meta

    excluded = (
        list(meta["dropped_sparse"])
        + list(meta["dropped_constant"])
        + [
            d["name"] if isinstance(d, dict) else d
            for d in meta["dropped_low_variance"]
        ]
        + list(meta["dropped_for_coverage"])
    )
    return None, None, [], excluded, meta


def fit_keepall_ridge_matrix(
    X: np.ndarray,
    y: Sequence[float],
    names: Sequence[str],
    *,
    ridge_lambda: float = 1.0,
    sample_weights: Optional[Sequence[float]] = None,
    min_std: float = 5.0,
    min_std_exempt: Optional[Sequence[str]] = None,
    feature_zscore: bool = True,
    row_dates: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Dict[str, Any]:
    """keep-all、标准化、可选 √w。系数四舍五入口径与 dict OLS 相同。

    ``cross_section_zscore=True``（且提供 ``row_dates``）：按日截面 z。
    ``False``：训练样本内全局 μ/σ（落盘 zscore_means/stds）。
    """
    from core.signal.factors.alpha158 import (
        ALPHA158_FACTOR_KEY,
        is_alpha158_raw_key,
        merge_alpha158_min_std_exempt,
    )

    y_arr = np.asarray(y, dtype=np.float64)
    x_arr = np.asarray(X, dtype=np.float64)
    feat_names = [str(n) for n in names]
    if any(is_alpha158_raw_key(n) for n in feat_names):
        feat_names = [n for n in feat_names if n != ALPHA158_FACTOR_KEY]
        if x_arr.ndim == 2 and x_arr.shape[1] != len(feat_names):
            src_index = {str(n): i for i, n in enumerate(names)}
            cols = [src_index[n] for n in feat_names if n in src_index]
            x_arr = x_arr[:, cols] if cols else np.zeros((x_arr.shape[0], 0))
            feat_names = [n for n in feat_names if n in src_index]
    exempt = merge_alpha158_min_std_exempt(feat_names, min_std_exempt)
    if x_arr.ndim != 2 or y_arr.ndim != 1 or x_arr.shape[0] != y_arr.shape[0]:
        return {"success": False, "error": "面板形状与标签不一致"}
    if x_arr.shape[1] != len(feat_names):
        return {"success": False, "error": "列名与矩阵宽度不一致"}

    x_c, y_c, active, excluded, prep_meta = _prepare_complete_matrix(
        x_arr,
        y_arr,
        feat_names,
        min_std=float(min_std),
        min_std_exempt=exempt,
    )
    row_weights = None
    idxs = prep_meta.get("complete_row_indices") or []
    if (
        sample_weights is not None
        and x_c is not None
        and len(sample_weights) == int(y_arr.shape[0])
        and len(idxs) == int(x_c.shape[0])
    ):
        row_weights = []
        for i in idxs:
            try:
                w = float(sample_weights[int(i)])
            except (TypeError, ValueError, IndexError):
                w = 1.0
            if not math.isfinite(w) or w <= 0:
                w = 1e-6
            row_weights.append(float(w))
        row_weights = np.asarray(row_weights, dtype=np.float64)

    z_means: Dict[str, float] = {}
    z_stds: Dict[str, float] = {}
    zscore_scope = ""
    beta = None
    if x_c is not None and y_c is not None and active:
        x_fit = x_c
        from core.research.feature_standardize import (
            cross_section_zscore_matrix,
            dates_for_complete_rows,
        )

        dates_c = (
            dates_for_complete_rows(row_dates, idxs if isinstance(idxs, list) else [])
            if cross_section_zscore
            else None
        )
        if (
            feature_zscore
            and cross_section_zscore
            and dates_c is not None
            and len(dates_c) == int(x_fit.shape[0])
        ):
            x_fit = cross_section_zscore_matrix(x_fit, dates_c)
            x_fit = np.where(np.isfinite(x_fit), x_fit, 0.0)
            zscore_scope = "cross_section"
        elif feature_zscore:
            eps2 = 1e-12
            means = x_fit.mean(axis=0)
            var = np.mean((x_fit - means) ** 2, axis=0)
            stds = np.sqrt(np.maximum(var, 0.0))
            stds = np.where(var > eps2, stds, 1.0)
            x_fit = (x_fit - means) / stds
            for i, name in enumerate(active):
                z_means[name] = float(means[i])
                z_stds[name] = float(stds[i])
        n = int(x_fit.shape[0])
        p = len(active)
        if n >= max(4, p + 3):
            design = np.column_stack(
                [np.ones(n, dtype=np.float64), np.asarray(x_fit, dtype=np.float64)]
            )
            y_fit = np.asarray(y_c, dtype=np.float64)
            if row_weights is not None:
                sw = np.sqrt(row_weights)
                design_w = design * sw[:, None]
                y_w = y_fit * sw
                beta = _ridge_solve_np(design_w, y_w, ridge_lambda)
            else:
                beta = _ridge_solve_np(design, y_fit, ridge_lambda)
            if beta is not None:
                y_mean = float(y_fit.mean())
                ss_tot = float(np.sum((y_fit - y_mean) ** 2))
                pred = design @ beta
                ss_res = float(np.sum((y_fit - pred) ** 2))
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else None
                coef_map: Dict[str, Optional[float]] = {
                    name: round(float(beta[i + 1]), 6) for i, name in enumerate(active)
                }
                for name in excluded:
                    coef_map[name] = None
                from core.research.factor_ols_fit import clamp_ridge_lambda

                lam = clamp_ridge_lambda(ridge_lambda, 0.0)
                note_bits = [
                    (
                        "float64 面板 Ridge；系数基于当天截面 z-score。"
                        if zscore_scope == "cross_section"
                        else "float64 面板 Ridge；系数基于样本内 z-score。"
                    )
                ]
                if row_weights is not None:
                    note_bits.append(" 已按 sample_weights 做加权 OLS（√w 变换）。")
                out = {
                    "success": True,
                    "intercept": round(float(beta[0]), 6),
                    "coefficients": coef_map,
                    "active_features": list(active),
                    "excluded_features": list(excluded),
                    "zscore_means": {k: round(v, 4) for k, v in z_means.items()},
                    "zscore_stds": {k: round(v, 4) for k, v in z_stds.items()},
                    "zscore_scope": zscore_scope,
                    "feature_zscore": bool(feature_zscore),
                    "sample_count": n,
                    "n_obs": n,
                    "r_squared": round(r2, 4) if r2 is not None else None,
                    "ridge_lambda": lam,
                    "solver": "ridge" if lam > 0 else "qr",
                    "collinearity_policy": "keep_all",
                    "weighted_ols": bool(row_weights is not None),
                    "prep_meta": prep_meta,
                    "note": "".join(note_bits),
                }
                return out
    return {
        "success": False,
        "error": "样本不足或矩阵奇异，无法拟合 OLS",
        "sample_count": int(prep_meta.get("raw_sample_count") or y_arr.shape[0]),
        "excluded_features": excluded,
        "prep_meta": prep_meta,
        "feature_zscore": bool(feature_zscore),
    }


def predict_ridge_matrix(
    fit: Dict[str, Any],
    X: np.ndarray,
    names: Sequence[str],
    row_dates: Optional[Sequence[str]] = None,
) -> List[Optional[float]]:
    """缺测按训练均值填成 z=0。截面模型改为按 ``row_dates`` 当天标准化后再打分。"""
    coefs = fit.get("coefficients") or {}
    intercept = float(fit.get("intercept") or 0.0)
    means = fit.get("zscore_means") or fit.get("z_means") or {}
    stds = fit.get("zscore_stds") or fit.get("z_stds") or {}
    from core.research.feature_standardize import (
        cross_section_zscore_matrix,
        is_cross_section_zscore,
    )

    x_arr = np.asarray(X, dtype=np.float64)
    if (
        row_dates is not None
        and is_cross_section_zscore(fit)
        and x_arr.ndim == 2
        and len(row_dates) == int(x_arr.shape[0])
    ):
        x_arr = cross_section_zscore_matrix(x_arr, row_dates)
        x_arr = np.where(np.isfinite(x_arr), x_arr, 0.0)
        means = {}
        stds = {}
    active = [
        n
        for n in (fit.get("active_features") or list(coefs.keys()))
        if coefs.get(n) is not None
    ]
    if x_arr.ndim != 2 or x_arr.shape[0] == 0 or not active:
        return []
    index = {str(n): i for i, n in enumerate(names)}
    impute = bool(means)
    out: List[Optional[float]] = []
    for i in range(int(x_arr.shape[0])):
        pred = intercept
        ok = True
        for name in active:
            j = index.get(str(name))
            raw = float(x_arr[i, j]) if j is not None else float("nan")
            if j is None or not math.isfinite(raw):
                if not impute:
                    ok = False
                    break
                z = 0.0
            else:
                mu = float(means.get(name, 0.0)) if means else 0.0
                sd = float(stds.get(name, 1.0)) if stds else 1.0
                if sd < 1e-9:
                    sd = 1.0
                z = (raw - mu) / sd if means else raw
            pred += float(coefs.get(name) or 0.0) * z
        out.append(round(pred, 6) if ok else None)
    return out


def keepall_ridge_oos(
    X_tr: np.ndarray,
    y_tr: Sequence[float],
    X_te: np.ndarray,
    names: Sequence[str],
    *,
    ridge_lambda: float,
    sample_weights: Optional[Sequence[float]] = None,
    min_std_exempt: Optional[Sequence[str]] = None,
    row_dates_tr: Optional[Sequence[str]] = None,
    row_dates_te: Optional[Sequence[str]] = None,
    cross_section_zscore: bool = True,
) -> Tuple[Dict[str, Any], List[Optional[float]]]:
    """训练集 keep-all Ridge，测试集预测。失败时退回训练均值截距。"""
    y_arr = np.asarray(list(y_tr), dtype=np.float64)
    n = int(y_arr.shape[0])
    y_mean = sum(float(v) for v in y_arr) / max(1, n)
    fit = fit_keepall_ridge_matrix(
        X_tr,
        y_arr,
        names,
        ridge_lambda=ridge_lambda,
        sample_weights=sample_weights,
        min_std_exempt=min_std_exempt,
        feature_zscore=True,
        row_dates=row_dates_tr if cross_section_zscore else None,
        cross_section_zscore=bool(cross_section_zscore),
    )
    if not fit.get("success"):
        fit = {
            "success": True,
            "intercept": round(float(y_mean), 6),
            "coefficients": {},
            "active_features": [],
            "zscore_means": {},
            "zscore_stds": {},
        }
    te = np.asarray(X_te, dtype=np.float64)
    preds = (
        predict_ridge_matrix(fit, te, names, row_dates=row_dates_te)
        if te.ndim == 2 and te.shape[0]
        else []
    )
    return fit, preds
