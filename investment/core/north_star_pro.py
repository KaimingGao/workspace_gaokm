"""北极星 R1 增强：拟合度趋势 + 量化归因 + 收益归因 + 三项乘积 + 退化告警。

R0（core/north_star.py）负责基础仪表的可靠产出；本模块在其之上做深度分析，
不破坏 R0 现有接口，也不改变数据文件格式。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


# ========== 工具：从 R0 模块导入（延迟导入避免循环） ==========

def _import_r0():
    from core.backtest_curve_store import _curve_points, _paper_daily_equities
    from core.north_star import compute_realization
    from core.risk_metrics import period_returns, pearson, rolling_sharpe, tracking_error_pct
    return _paper_daily_equities, _curve_points, compute_realization, period_returns, pearson, rolling_sharpe, tracking_error_pct


# ========== 模块1：拟合度趋势 + 量化归因 ==========

def rolling_realization(
    paper_snapshots: Sequence[dict],
    backtest_curve: Sequence[dict],
    *,
    window: int = 30,
    step: int = 5,
    min_align: int = 10,
) -> Dict[str, Any]:
    """滚动窗口 Corr/TE 趋势。

    对 paper / backtest 的共同日期，按 window 天窗口、step 天步长滚动，
    每个窗口算 Corr 与 TE，返回趋势序列与方向判断。
    """
    _paper_daily_equities, _curve_points, _, period_returns, pearson, _, tracking_error_pct = _import_r0()

    paper_pts = _paper_daily_equities(list(paper_snapshots or []))
    bt_pts = _curve_points(list(backtest_curve or []))

    pmap = {d: e for d, e in paper_pts}
    bmap = {d: e for d, e in bt_pts}
    common = sorted(set(pmap) & set(bmap))
    if len(common) < max(3, min_align):
        return {
            "trend": [],
            "corr_latest": None,
            "corr_mean": None,
            "corr_trend": "insufficient",
            "te_latest": None,
            "te_mean": None,
            "n_windows": 0,
            "note": f"共同交易日 {len(common)} < min_align {min_align}，无法滚动。",
        }

    # 按日期取纸面/回测权益
    p_eq = [pmap[d] for d in common]
    b_eq = [bmap[d] for d in common]
    p0, b0 = p_eq[0], b_eq[0]
    if not p0 or not b0 or p0 <= 0 or b0 <= 0:
        return {
            "trend": [], "corr_latest": None, "corr_mean": None,
            "corr_trend": "insufficient", "te_latest": None, "te_mean": None,
            "n_windows": 0, "note": "起始权益异常。",
        }
    p_n = [e / p0 for e in p_eq]
    b_n = [e / b0 for e in b_eq]
    p_rets = list(period_returns(p_n))
    b_rets = list(period_returns(b_n))
    rets_dates = common[1:]  # 收益序列的对应日期

    n = len(p_rets)
    trend: List[Dict[str, Any]] = []
    w = max(5, int(window or 30))
    s = max(1, int(step or 5))
    for start in range(0, max(1, n - w + 1), s):
        end = min(start + w, n)
        if end - start < max(5, min_align - 1):
            continue
        pr = p_rets[start:end]
        br = b_rets[start:end]
        c = pearson(pr, br)
        te = tracking_error_pct(pr, br)
        trend.append({
            "window_start": rets_dates[start],
            "window_end": rets_dates[end - 1],
            "corr": round(float(c), 5) if c is not None else None,
            "tracking_error_pct": round(float(te), 5) if te is not None else None,
            "n_days": end - start,
        })

    if not trend:
        return {
            "trend": [], "corr_latest": None, "corr_mean": None,
            "corr_trend": "insufficient", "te_latest": None, "te_mean": None,
            "n_windows": 0, "note": "没有可计算的窗口（window 过大或样本不足）。",
        }

    corr_vals = [t["corr"] for t in trend if t["corr"] is not None]
    te_vals = [t["tracking_error_pct"] for t in trend if t["tracking_error_pct"] is not None]

    corr_latest = corr_vals[-1] if corr_vals else None
    corr_mean = float(np.mean(corr_vals)) if corr_vals else None
    te_latest = te_vals[-1] if te_vals else None
    te_mean = float(np.mean(te_vals)) if te_vals else None

    # 趋势：前半 vs 后半
    corr_trend = "insufficient"
    m = len(corr_vals)
    if m >= 3:
        first_half = corr_vals[: m // 2]
        second_half = corr_vals[m // 2:]
        if first_half and second_half:
            fm = float(np.mean(first_half))
            sm = float(np.mean(second_half))
            diff = sm - fm
            if abs(diff) < 1e-6:
                corr_trend = "stable"
            elif diff > 0:
                corr_trend = "improving"
            else:
                corr_trend = "declining"

    return {
        "trend": trend,
        "corr_latest": corr_latest,
        "corr_mean": corr_mean,
        "corr_trend": corr_trend,
        "te_latest": te_latest,
        "te_mean": te_mean,
        "n_windows": len(trend),
        "note": f"滚动窗口 {w} 天/步长 {s}；共同交易日 {len(common)}。",
    }


def quantify_fit_gap_attribution(
    paper_returns: Sequence[float],
    bt_returns: Sequence[float],
    *,
    cost_pct: float = 0.0,
) -> Dict[str, Any]:
    """量化拟合缺口的因素贡献占比。

    总缺口方差 = Var(paper_ret - bt_ret)；分解为：
    - mean_shift: 系统性均值偏移（纸面整体低/高于回测）
    - vol_diff: 波动率差异
    - correlation_loss: 时间序列对齐缺失（相关性不足导致的残余）
    - cost: 固定成本（如纸面扣费而回测未扣）
    """
    pr = np.asarray(list(paper_returns), dtype=float)
    br = np.asarray(list(bt_returns), dtype=float)
    if pr.size < 3 or br.size < 3 or pr.size != br.size:
        return {
            "total_gap_variance": None,
            "factors": {"cost": 0.0, "mean_shift": 0.0, "vol_diff": 0.0, "correlation_loss": 0.0},
            "factor_pct": {"cost": 0.0, "mean_shift": 0.0, "vol_diff": 0.0, "correlation_loss": 0.0},
            "dominant_factor": "insufficient",
            "note": "样本不足或两序列长度不匹配。",
        }

    diff = pr - br
    total = float(np.var(diff, ddof=1)) if diff.size > 1 else float(np.var(diff))
    if total <= 0 or not math.isfinite(total):
        return {
            "total_gap_variance": total,
            "factors": {"cost": 0.0, "mean_shift": 0.0, "vol_diff": 0.0, "correlation_loss": 0.0},
            "factor_pct": {"cost": 0.0, "mean_shift": 0.0, "vol_diff": 0.0, "correlation_loss": 0.0},
            "dominant_factor": "aligned",
            "note": "缺口方差接近 0，拟合已对齐。",
        }

    mean_p = float(np.mean(pr))
    mean_b = float(np.mean(br))
    std_p = float(np.std(pr, ddof=1)) if pr.size > 1 else float(np.std(pr))
    std_b = float(np.std(br, ddof=1)) if br.size > 1 else float(np.std(br))
    cov_pb = float(np.cov(pr, br, ddof=1)[0, 1]) if pr.size > 2 else float(np.cov(pr, br)[0, 1])

    cost_factor = (float(cost_pct) / 100.0) ** 2
    mean_shift = (mean_p - mean_b) ** 2
    vol_diff = (std_p - std_b) ** 2
    # 残余相关性损耗
    corr_loss = max(0.0, total - mean_shift - vol_diff - cost_factor)

    factors = {
        "cost": cost_factor,
        "mean_shift": mean_shift,
        "vol_diff": vol_diff,
        "correlation_loss": corr_loss,
    }
    factor_pct = {k: round(v / total, 4) for k, v in factors.items()}
    # 归一化（若截断误差造成和非 1）
    s = sum(factor_pct.values())
    if s > 0 and abs(s - 1.0) > 1e-6:
        factor_pct = {k: round(v / s, 4) for k, v in factor_pct.items()}

    dominant = max(factors.items(), key=lambda kv: kv[1])[0]

    return {
        "total_gap_variance": round(total, 8),
        "factors": {k: round(v, 8) for k, v in factors.items()},
        "factor_pct": factor_pct,
        "dominant_factor": dominant,
        "note": "正 gap=纸面好于回测；dominant_factor 是缺口方差贡献最大的来源。",
    }


# ========== 模块3：收益归因接入北极星 ==========

def north_star_attribution(
    portfolio_returns: np.ndarray,
    factor_returns: np.ndarray,
    *,
    factor_names: Optional[Sequence[str]] = None,
    rf: float = 0.0,
) -> Dict[str, Any]:
    """北极星视角的收益归因（包装 factor_risk.risk_attribution）。

    返回：alpha 年化、alpha 占比、主因子、系统/特质占比、风格判定。
    """
    from core.signal.factor_risk import risk_attribution

    names = list(factor_names) if factor_names else [f"f{i}" for i in range(np.asarray(factor_returns).shape[1])]
    attr = risk_attribution(
        np.asarray(portfolio_returns, dtype=float),
        np.asarray(factor_returns, dtype=float),
        factor_names=names,
        rf=float(rf),
    )

    alpha = float(attr.get("alpha") or 0.0)
    alpha_annualized = alpha * 252.0
    sys_risk = float(attr.get("systematic_risk") or 0.0)
    idio_risk = float(attr.get("idiosyncratic_risk") or 0.0)
    total_risk = sys_risk + idio_risk
    systematic_pct = sys_risk / total_risk if total_risk > 0 else 0.0
    idiosyncratic_pct = 1.0 - systematic_pct
    alpha_pct = 0.0
    if total_risk > 0 and alpha_annualized != 0:
        # alpha 对总年化风险的比值
        alpha_pct = min(1.0, abs(alpha_annualized) / (math.sqrt(252.0) * math.sqrt(total_risk) + 1e-12))

    contributions = attr.get("factor_contributions") or {}
    factor_dominant = max(contributions.items(), key=lambda kv: abs(float(kv[1])))[0] if contributions else None

    if systematic_pct > 0.7:
        verdict = "factor_driven"
    elif systematic_pct < 0.3:
        verdict = "stock_picking"
    else:
        verdict = "balanced"

    attr["alpha_annualized"] = round(alpha_annualized, 6)
    attr["alpha_pct"] = round(alpha_pct, 4)
    attr["factor_dominant"] = factor_dominant
    attr["systematic_pct"] = round(systematic_pct, 4)
    attr["idiosyncratic_pct"] = round(idiosyncratic_pct, 4)
    attr["verdict"] = verdict
    return attr


# ========== 模块5：三项乘积 + 退化告警 ==========

def composite_north_star_score(
    sharpe: Optional[float],
    ttm_hours: Optional[float],
    corr: Optional[float],
    te: Optional[float],
    *,
    sharpe_target: float = 1.5,
    ttm_target_hours: float = 8.0,
    corr_target: float = 0.7,
    te_target_pct: float = 2.0,
) -> Dict[str, Any]:
    """三项乘积综合分：收益 × 速度 × 拟合的几何平均（0–1）。"""
    # a：收益维度（Sharpe 归一化，负值记 0）
    a = 0.0
    if sharpe is not None and math.isfinite(float(sharpe)):
        a = max(0.0, min(float(sharpe) / max(0.01, float(sharpe_target)), 1.0))
    # b：速度维度（越快越高，TTM < target 满分）
    b = 0.0
    if ttm_hours is not None and math.isfinite(float(ttm_hours)) and float(ttm_hours) > 0:
        b = min(float(ttm_target_hours) / float(ttm_hours), 1.0)
    # c：拟合维度（Corr 主导 + TE 惩罚）
    c = 0.0
    if corr is not None and math.isfinite(float(corr)):
        cv = max(0.0, min(1.0, float(corr)))
        tv = 1.0 - min((float(te) if te is not None and math.isfinite(float(te)) else 0.0) / max(0.01, float(te_target_pct)), 1.0)
        c = cv * (0.5 + 0.5 * tv)

    denom = 0.0
    for v in (a, b, c):
        if v > 0:
            denom += 1.0
    if denom > 0:
        prod = a * b * c
        composite = math.pow(prod, 1.0 / 3.0) if prod > 0 else 0.0
    else:
        composite = 0.0

    if composite > 0.7:
        verdict = "strong"
    elif composite > 0.4:
        verdict = "moderate"
    else:
        verdict = "weak"

    dims = {"return": round(a, 4), "velocity": round(b, 4), "fit": round(c, 4)}
    bottleneck = min(dims.items(), key=lambda kv: kv[1])[0]

    return {
        "composite_score": round(composite, 4),
        "dimension_scores": dims,
        "targets": {
            "sharpe": sharpe_target,
            "ttm_hours": ttm_target_hours,
            "corr": corr_target,
            "te_pct": te_target_pct,
        },
        "achieved_pct": round(composite * 100.0, 2),
        "verdict": verdict,
        "bottleneck_dimension": bottleneck,
        "note": "几何平均 Composite = (收益×速度×拟合)^(1/3)；高=三项均达标。",
    }


def degradation_alert(
    metric_series: Sequence,
    *,
    metric_name: str = "",
    window: int = 5,
    decline_threshold: float = 0.1,
    higher_is_better: bool = True,
) -> Dict[str, Any]:
    """退化告警：对比前半 vs 后半段均值，加连降计数。

    - higher_is_better=True: Sharpe/Corr 等（退化=数值下降）
    - higher_is_better=False: TTM/TE 等（退化=数值上升，自动取反处理）
    """
    values = [float(v) for v in metric_series if v is not None and math.isfinite(float(v))]
    if not higher_is_better:
        values = [-v for v in values]

    if len(values) < max(4, int(window) * 2 - 1):
        return {
            "metric_name": metric_name,
            "alert": "insufficient",
            "recent_mean": None,
            "prior_mean": None,
            "decline_rate": None,
            "consecutive_decline": 0,
            "threshold": float(decline_threshold),
            "note": f"样本 {len(values)} < 需求 {max(4, int(window)*2-1)}，无法判断趋势。",
        }

    w = max(3, int(window))
    recent = values[-w:]
    prior = values[-2 * w:-w] if len(values) >= 2 * w else values[:-w]
    if not prior:
        return {
            "metric_name": metric_name, "alert": "insufficient",
            "recent_mean": float(np.mean(recent)), "prior_mean": None,
            "decline_rate": None, "consecutive_decline": 0, "threshold": float(decline_threshold),
            "note": "无基线数据。",
        }

    rm = float(np.mean(recent))
    pm = float(np.mean(prior))
    if abs(pm) < 1e-12:
        decline_rate = 0.0 if abs(rm - pm) < 1e-12 else (1.0 if rm < pm else -1.0)
    else:
        decline_rate = (pm - rm) / abs(pm)  # 正 = 退化

    # 连续下降计数（从末尾往前）
    consec = 0
    for i in range(len(values) - 1, 0, -1):
        if values[i] < values[i - 1] - 1e-12:
            consec += 1
        else:
            break

    if consec >= 3 and decline_rate > float(decline_threshold):
        alert = "degrading"
    elif decline_rate < -float(decline_threshold):
        alert = "improving"
    elif abs(decline_rate) <= float(decline_threshold):
        alert = "stable"
    else:
        alert = "marginal"

    return {
        "metric_name": metric_name,
        "alert": alert,
        "recent_mean": round(rm, 6),
        "prior_mean": round(pm, 6),
        "decline_rate": round(decline_rate, 4),
        "consecutive_decline": consec,
        "threshold": float(decline_threshold),
        "note": f"前半 {pm:.4f} vs 后半 {rm:.4f}；正 decline_rate = 退化。",
    }


def north_star_degradation_report(
    sharpe_series: Sequence,
    corr_series: Sequence,
    ttm_series: Sequence,
    *,
    window: int = 5,
    decline_threshold: float = 0.1,
) -> Dict[str, Any]:
    """北极星三支柱退化报告（Sharpe、Corr 越高越好；TTM 越低越好）。"""
    a_sharpe = degradation_alert(sharpe_series, metric_name="sharpe", window=window,
                                  decline_threshold=decline_threshold, higher_is_better=True)
    a_corr = degradation_alert(corr_series, metric_name="corr", window=window,
                                decline_threshold=decline_threshold, higher_is_better=True)
    a_ttm = degradation_alert(ttm_series, metric_name="ttm", window=window,
                               decline_threshold=decline_threshold, higher_is_better=False)

    alerts = {"sharpe": a_sharpe, "corr": a_corr, "ttm": a_ttm}
    degrading = [n for n, x in alerts.items() if x["alert"] == "degrading"]
    any_degrading = bool(degrading)

    return {
        "alerts": alerts,
        "any_degrading": any_degrading,
        "degrading_dimensions": degrading,
        "window": window,
        "decline_threshold": decline_threshold,
        "note": "三支柱趋势检测；any_degrading=True 时建议人工复盘。",
    }


# ========== 自测 ==========

if __name__ == "__main__":
    rng = np.random.default_rng(42)
    # 拟合度趋势
    dates = [f"2024-01-{d:02d}" for d in range(1, 31)]
    paper_snap = [{"ts": f"{d}T15:00:00", "equity": 1.0 + i * 0.001 + rng.standard_normal() * 0.002} for i, d in enumerate(dates)]
    bt_curve = [{"date": d, "equity": 1.0 + i * 0.001 + rng.standard_normal() * 0.002} for i, d in enumerate(dates)]
    rr = rolling_realization(paper_snap, bt_curve, window=15, step=3)
    print("rolling_realization: n_windows=%d, corr_trend=%s" % (rr["n_windows"], rr["corr_trend"]))

    # 量化归因
    pr_arr = np.array([0.01, -0.005, 0.008, -0.01, 0.003, 0.012, -0.006, 0.004, -0.002, 0.009])
    br_arr = np.array([0.008, -0.004, 0.006, -0.008, 0.002, 0.010, -0.005, 0.003, -0.001, 0.007])
    qa = quantify_fit_gap_attribution(pr_arr.tolist(), br_arr.tolist(), cost_pct=0.05)
    print("quantify_fit_gap: dominant=%s, factors=%s" % (qa["dominant_factor"], {k: round(v, 4) for k, v in qa["factor_pct"].items()}))

    # 收益归因
    T, K = 60, 3
    fr_arr = rng.standard_normal((T, K)) * 0.01
    true_beta = np.array([0.4, 0.3, 0.2])
    port_ret = fr_arr @ true_beta + rng.standard_normal(T) * 0.002
    attr = north_star_attribution(port_ret, fr_arr, factor_names=["mom", "val", "qual"])
    print("attribution: verdict=%s, dominant=%s, sys_pct=%.2f" % (attr["verdict"], attr["factor_dominant"], attr["systematic_pct"]))

    # 三项乘积
    cs = composite_north_star_score(sharpe=1.2, ttm_hours=6.0, corr=0.65, te=1.5)
    print("composite: score=%.3f, verdict=%s, bottleneck=%s" % (cs["composite_score"], cs["verdict"], cs["bottleneck_dimension"]))

    # 退化告警
    series = [1.0, 1.1, 1.05, 0.95, 0.85, 0.8, 0.7]
    da = degradation_alert(series, metric_name="sharpe", window=3)
    dr = da["decline_rate"] if da["decline_rate"] is not None else 0.0
    print("degradation: alert=%s, decline_rate=%.3f, consec=%d" % (da["alert"], dr, da["consecutive_decline"]))

    # 退化报告
    rep = north_star_degradation_report(
        [1.5, 1.4, 1.3, 1.2, 1.1, 1.0],
        [0.7, 0.65, 0.6, 0.55, 0.5, 0.45],
        [4, 5, 6, 7, 8, 9],
        window=3,
    )
    print("degradation_report: any_degrading=%s, dims=%s" % (rep["any_degrading"], rep["degrading_dimensions"]))
