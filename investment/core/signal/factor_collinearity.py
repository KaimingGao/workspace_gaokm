"""趋势族共线性摘要（FS1）：辅助人审 β，不自动删因子。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

TREND_FAMILY = (
    "momentum",
    "ma_slope",
    "technical_pattern",
    "weekly_confirm",
    "idio_momentum",
)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    ax = list(xs)[:n]
    ay = list(ys)[:n]
    mx = sum(ax) / n
    my = sum(ay) / n
    num = sum((a - mx) * (b - my) for a, b in zip(ax, ay))
    dx = sum((a - mx) ** 2 for a in ax) ** 0.5
    dy = sum((b - my) ** 2 for b in ay) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def trend_family_collinearity(
    rows: Sequence[Dict[str, Any]],
    *,
    names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """``rows`` 为若干 ``sub_scores`` 字典；返回相关矩阵与高相关对。"""
    cols = list(names or TREND_FAMILY)
    series: Dict[str, List[float]] = {c: [] for c in cols}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        vals = []
        ok = True
        for c in cols:
            v = row.get(c)
            if v is None:
                ok = False
                break
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                ok = False
                break
        if not ok:
            continue
        for c, v in zip(cols, vals):
            series[c].append(v)
    n = min((len(series[c]) for c in cols), default=0)
    matrix: Dict[str, Dict[str, Optional[float]]] = {}
    high_pairs: List[Dict[str, Any]] = []
    for a in cols:
        matrix[a] = {}
        for b in cols:
            if a == b:
                matrix[a][b] = 1.0
                continue
            r = _pearson(series[a], series[b])
            matrix[a][b] = r
            if a < b and r is not None and abs(r) >= 0.85:
                high_pairs.append({"a": a, "b": b, "corr": r})
    return {
        "success": True,
        "family": list(cols),
        "sample_count": n,
        "corr_matrix": matrix,
        "high_corr_pairs": high_pairs,
        "note": "仅诊断；晋升时人工审视重叠 β，不自动剔除。",
    }


def collinearity_from_panel_rows(
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """对面板行（sub_scores 字典序列）做趋势族共线性。"""
    clean = [r for r in (rows or []) if isinstance(r, dict) and r]
    if len(clean) < 3:
        return {
            "success": True,
            "family": list(TREND_FAMILY),
            "sample_count": len(clean),
            "corr_matrix": {},
            "high_corr_pairs": [],
            "note": "样本不足（需≥3 条 sub_scores）；跳过共线性摘要。",
        }
    return trend_family_collinearity(clean)


def collinearity_from_cluster_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """从分组/OLS 报告抽 sub_scores 做趋势族相关。

    优先 ``panel_xs`` / 各 cluster 的 panel；否则 per_stock.last_sub_scores。
    """
    rows: List[Dict[str, Any]] = []
    for row in report.get("panel_xs") or []:
        if isinstance(row, dict) and row:
            rows.append(row)
    if len(rows) < 3:
        for item in report.get("per_stock") or report.get("stocks") or []:
            if not isinstance(item, dict):
                continue
            subs = item.get("sub_scores") or item.get("last_sub_scores")
            if isinstance(subs, dict) and subs:
                rows.append(subs)
    return collinearity_from_panel_rows(rows)
