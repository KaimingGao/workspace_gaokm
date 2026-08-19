"""因子正交化与风险归因模块。

提供因子共线性消除（对称正交化 / Gram-Schmidt 正交化）、因子协方差估计、
风险归因（因子模型 + Brinson）以及共线性诊断能力。
仅依赖 numpy + 标准库，不引入 scipy。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def symmetric_orthogonalize(factor_matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """对称正交化（Schmidt 正交化的对称版本）。

    通过协方差矩阵的特征值分解构造正交化矩阵，消除因子间共线性，
    不依赖因子顺序，保持因子整体信息。

    参数:
        factor_matrix: (T, K) 因子矩阵，T=时间期数, K=因子数。

    返回:
        ``(orthogonal_factors (T, K), transform_matrix (K, K))``。
        满足 ``orthogonal_factors = factor_matrix_centered @ transform_matrix``，
        且正交因子的协方差（1/T 意义下）为单位阵。
    """
    F = np.asarray(factor_matrix, dtype=float)
    if F.ndim != 2:
        raise ValueError("factor_matrix 必须为 2 维 (T, K)")
    T, K = F.shape
    if T < 1 or K < 1:
        return np.zeros((T, K)), np.zeros((K, K))
    # a. 中心化
    Fc = F - F.mean(axis=0, keepdims=True)
    # b. 协方差 Σ = (1/T) F' F
    Sigma = (Fc.T @ Fc) / T
    # c. 特征值分解 Σ = V diag(λ) V'（eigh 适用于对称矩阵，返回升序）
    eigvals, eigvecs = np.linalg.eigh(Sigma)
    # f. 截断接近 0 的特征值，避免数值爆炸
    tol = max(float(eigvals.max()) * 1e-12, 1e-12) if eigvals.size else 1e-12
    inv_sqrt = np.zeros_like(eigvals)
    mask = eigvals > tol
    inv_sqrt[mask] = 1.0 / np.sqrt(eigvals[mask])
    # d. transform = V diag(1/sqrt(λ)) V'  （即 Σ^(-1/2)）
    transform = (eigvecs * inv_sqrt) @ eigvecs.T
    # e. 正交因子 = F_centered @ transform （等价 P' 转置回 (T, K)）
    orthogonal_factors = Fc @ transform
    return orthogonal_factors, transform


def gram_schmidt_orthogonalize(
    factor_matrix: np.ndarray, *, order: Optional[Sequence[int]] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """经典 Gram-Schmidt 正交化（按指定顺序）。

    参数:
        factor_matrix: (T, K) 因子矩阵。
        order: 因子正交化顺序（如 ``[0, 2, 1]`` 表示先正交化第 0 列，再第 2 列，
            再第 1 列）；None 表示按原序。靠前的因子保留原始信息更多，
            靠后的因子被残差化。

    返回:
        ``(orthogonal_factors (T, K), transform_matrix (K, K))``。
        满足 ``orthogonal_factors = factor_matrix_centered @ transform_matrix``，
        各列单位正交；线性相关列置零。
    """
    F = np.asarray(factor_matrix, dtype=float)
    if F.ndim != 2:
        raise ValueError("factor_matrix 必须为 2 维 (T, K)")
    T, K = F.shape
    if T < 1 or K < 1:
        return np.zeros((T, K)), np.zeros((K, K))
    Fc = F - F.mean(axis=0, keepdims=True)
    if order is None:
        order_list = list(range(K))
    else:
        order_list = [int(i) for i in order]
        if sorted(order_list) != list(range(K)):
            raise ValueError("order 必须为 0..K-1 的排列")
    Q = np.zeros((T, K))
    Tmat = np.zeros((K, K))
    tol = 1e-12
    for p, j in enumerate(order_list):
        # 经典 GS：用原始列 Fc[:, j] 向已正交化的单位向量求投影系数
        coefs: List[float] = []
        v = Fc[:, j].copy()
        for prev in range(p):
            jp = order_list[prev]
            c = float(Q[:, jp] @ Fc[:, j])
            coefs.append(c)
            v = v - c * Q[:, jp]
        norm = float(np.linalg.norm(v))
        if norm < tol:
            # 与已有正交基线性相关，置零
            Q[:, j] = 0.0
            Tmat[:, j] = 0.0
            continue
        Q[:, j] = v / norm
        # 反解 transform 列：Tmat[:, j] = (e_j - Σ c_prev * Tmat[:, jp]) / norm
        col = np.zeros(K)
        col[j] = 1.0
        for prev in range(p):
            jp = order_list[prev]
            col = col - coefs[prev] * Tmat[:, jp]
        Tmat[:, j] = col / norm
    return Q, Tmat


def _ledoit_wolf_shrinkage(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf 收缩协方差估计（收缩到 μI），numpy 实现，不依赖 scipy。"""
    X = np.asarray(returns, dtype=float)
    T, K = X.shape
    if T < 2:
        return np.zeros((K, K))
    Xc = X - X.mean(axis=0, keepdims=True)
    S = (Xc.T @ Xc) / T
    mu = float(np.trace(S)) / K if K > 0 else 0.0
    F = mu * np.eye(K)
    d2 = float(np.sum((S - F) ** 2))
    # b̄² = (1/T²) Σ_t ||x_t x_t' - S||²_F
    #    = [Σ_t ||x_t||⁴ - T * trace(S²)] / T²
    sq_norms = np.sum(Xc ** 2, axis=1)
    sum_sq = float(np.sum(sq_norms ** 2))
    trace_s2 = float(np.sum(S * S))
    b_bar2 = (sum_sq - T * trace_s2) / (T ** 2)
    b_bar2 = max(b_bar2, 0.0)
    b2 = min(b_bar2, d2)
    shrinkage = b2 / d2 if d2 > 0 else 0.0
    return shrinkage * F + (1.0 - shrinkage) * S


def factor_covariance_matrix(
    factor_returns: np.ndarray, *, method: str = "sample"
) -> np.ndarray:
    """因子收益率协方差矩阵。

    参数:
        factor_returns: (T, K) 因子收益率序列。
        method: ``"sample"``（样本协方差）或 ``"ledoit_wolf"``（Ledoit-Wolf 收缩）。
            ``ledoit_wolf`` 优先调用 ``core.risk.covariance``；不可用时回退到内置实现。

    返回:
        (K, K) 协方差矩阵。
    """
    R = np.asarray(factor_returns, dtype=float)
    if R.ndim != 2:
        raise ValueError("factor_returns 必须为 2 维 (T, K)")
    T, K = R.shape
    if method == "sample":
        if T < 2:
            return np.zeros((K, K))
        return np.atleast_2d(np.cov(R, rowvar=False))
    if method == "ledoit_wolf":
        try:
            import core.risk.covariance as _covmod

            _fn = getattr(_covmod, "ledoit_wolf_shrinkage", None)
            if _fn is not None:
                res = _fn(R)
                return np.atleast_2d(np.asarray(res["cov"], dtype=float))
        except ImportError:
            pass
        return _ledoit_wolf_shrinkage(R)
    raise ValueError(f"未知 method: {method}（支持 sample / ledoit_wolf）")


def risk_attribution(
    portfolio_returns: np.ndarray,
    factor_returns: np.ndarray,
    *,
    factor_names: Optional[Sequence[str]] = None,
    rf: float = 0.0,
) -> Dict[str, Any]:
    """基于因子模型的风险归因分解。

    回归 ``r_p - rf = alpha + Σ beta_k * f_k + epsilon``（OLS），分解系统风险与特质风险。

    参数:
        portfolio_returns: (T,) 组合收益率。
        factor_returns: (T, K) 因子收益率。
        factor_names: 因子名称；None 时命名为 ``f0, f1, ...``。
        rf: 无风险利率（日频）。

    返回:
        包含 alpha、betas、各类风险、R²、各因子风险贡献及占比的字典。
    """
    r_p = np.asarray(portfolio_returns, dtype=float).reshape(-1)
    F = np.asarray(factor_returns, dtype=float)
    if F.ndim != 2:
        raise ValueError("factor_returns 必须为 2 维 (T, K)")
    T, K = F.shape
    if r_p.shape[0] != T:
        raise ValueError("portfolio_returns 与 factor_returns 的期数不一致")
    names = list(factor_names) if factor_names is not None else [f"f{i}" for i in range(K)]
    if len(names) != K:
        raise ValueError("factor_names 长度与因子数不一致")
    # a. OLS 估计 alpha + beta
    y = r_p - float(rf)
    X = np.column_stack([np.ones(T), F])
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    alpha = float(coefs[0])
    betas = coefs[1:]
    eps = y - X @ coefs
    # b. 因子协方差
    Sigma_f = np.atleast_2d(factor_covariance_matrix(F, method="sample"))
    # c. 系统风险 = beta' Σ_f beta
    systematic_risk = float(betas @ Sigma_f @ betas)
    # d. 特质风险 = Var(epsilon)
    idiosyncratic_risk = float(np.var(eps, ddof=1)) if T > 1 else 0.0
    # e. 总风险
    total_risk = systematic_risk + idiosyncratic_risk
    # R²
    ss_res = float(np.sum(eps ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    # f. 各因子风险贡献（标准 Euler 分解）：CRC_k = beta_k * (Σ_f @ beta)_k
    #    成分贡献之和 = beta' Σ_f beta = 系统风险，占比之和 = 100%
    mrc = Sigma_f @ betas  # 边际风险贡献 (K,)
    factor_contributions: Dict[str, float] = {}
    for k in range(K):
        factor_contributions[names[k]] = float(betas[k] * mrc[k])
    # g. 风险贡献占比
    factor_contrib_pct: Dict[str, float] = {}
    for k in range(K):
        factor_contrib_pct[names[k]] = (
            factor_contributions[names[k]] / systematic_risk
            if systematic_risk > 0
            else 0.0
        )
    residual_std = float(np.std(eps, ddof=1)) if T > 1 else 0.0
    return {
        "alpha": alpha,
        "betas": betas,
        "factor_names": names,
        "systematic_risk": systematic_risk,
        "idiosyncratic_risk": idiosyncratic_risk,
        "total_risk": total_risk,
        "r_squared": float(r_squared),
        "factor_contributions": factor_contributions,
        "factor_contrib_pct": factor_contrib_pct,
        "residual_std": residual_std,
    }


def brinson_attribution(
    portfolio_weights: Dict[str, float],
    benchmark_weights: Dict[str, float],
    asset_returns: Dict[str, float],
    *,
    sectors: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Brinson 归因（分配效应 + 选择效应 + 交互效应），按板块分组。

    参数:
        portfolio_weights: ``{code: weight}`` 组合权重。
        benchmark_weights: ``{code: weight}`` 基准权重。
        asset_returns: ``{code: return_pct}`` 资产收益率。
        sectors: ``{code: sector_name}`` 资产所属板块；None 时不分板块，整体计算。

    返回:
        包含组合/基准收益、超额收益、三类效应及分板块明细的字典。
    """
    codes = list(set(portfolio_weights) | set(benchmark_weights) | set(asset_returns))
    if sectors is None:
        sector_of = {c: "__ALL__" for c in codes}
    else:
        sector_of = {c: sectors.get(c, "__UNKNOWN__") for c in codes}

    def _wp(c: str) -> float:
        return float(portfolio_weights.get(c, 0.0) or 0.0)

    def _wb(c: str) -> float:
        return float(benchmark_weights.get(c, 0.0) or 0.0)

    def _r(c: str) -> float:
        return float(asset_returns.get(c, 0.0) or 0.0)

    r_p_total = sum(_wp(c) * _r(c) for c in codes)
    r_b_total = sum(_wb(c) * _r(c) for c in codes)

    sector_names = sorted(set(sector_of.values()))
    by_sector: Dict[str, Dict[str, float]] = {}
    total_alloc = total_sel = total_inter = 0.0
    for s in sector_names:
        s_codes = [c for c in codes if sector_of[c] == s]
        w_p_s = sum(_wp(c) for c in s_codes)
        w_b_s = sum(_wb(c) for c in s_codes)
        r_p_s = (sum(_wp(c) * _r(c) for c in s_codes) / w_p_s) if w_p_s != 0 else 0.0
        r_b_s = (sum(_wb(c) * _r(c) for c in s_codes) / w_b_s) if w_b_s != 0 else 0.0
        # a. 分配效应 / 选择效应 / 交互效应
        allocation = (w_p_s - w_b_s) * (r_b_s - r_b_total)
        selection = w_b_s * (r_p_s - r_b_s)
        interaction = (w_p_s - w_b_s) * (r_p_s - r_b_s)
        by_sector[s] = {
            "allocation": allocation,
            "selection": selection,
            "interaction": interaction,
            "excess": allocation + selection + interaction,
        }
        total_alloc += allocation
        total_sel += selection
        total_inter += interaction

    return {
        "total_return_p": r_p_total,
        "total_return_b": r_b_total,
        "excess_return": r_p_total - r_b_total,
        "allocation": total_alloc,
        "selection": total_sel,
        "interaction": total_inter,
        "by_sector": by_sector,
    }


def factor_correlation_matrix(
    factor_scores: np.ndarray,
    factor_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """因子相关系数矩阵（用于诊断因子共线性）。

    参数:
        factor_scores: (T, K) 因子得分。
        factor_names: 因子名称；None 时命名为 ``f0, f1, ...``。

    返回:
        包含相关系数矩阵、高相关因子对（|corr|>0.7）、最大相关系数、
        方差膨胀因子（VIF = 1/(1-R²_i)）的字典。VIF > 10 表示严重共线性。
    """
    X = np.asarray(factor_scores, dtype=float)
    if X.ndim != 2:
        raise ValueError("factor_scores 必须为 2 维 (T, K)")
    T, K = X.shape
    names = list(factor_names) if factor_names is not None else [f"f{i}" for i in range(K)]
    if len(names) != K:
        raise ValueError("factor_names 长度与因子数不一致")
    corr = np.atleast_2d(np.corrcoef(X, rowvar=False))
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    high_corr_pairs: List[Tuple[int, int, float]] = []
    max_corr = 0.0
    for i in range(K):
        for j in range(i + 1, K):
            c = float(corr[i, j])
            if abs(c) > 0.7:
                high_corr_pairs.append((i, j, c))
            if abs(c) > abs(max_corr):
                max_corr = c
    # VIF：对每个因子 i，用其余因子回归它，VIF_i = 1/(1-R²_i)
    vif: List[float] = []
    for i in range(K):
        if K == 1 or T < 2:
            vif.append(1.0)
            continue
        target = X[:, i]
        others = np.delete(X, i, axis=1)
        Xo = np.column_stack([np.ones(T), others])
        coefs, *_ = np.linalg.lstsq(Xo, target, rcond=None)
        resid = target - Xo @ coefs
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((target - target.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        denom = 1.0 - r2
        if denom < 1e-12:
            vif.append(float("inf"))
        else:
            vif.append(1.0 / denom)
    return {
        "corr": corr,
        "factor_names": names,
        "high_corr_pairs": high_corr_pairs,
        "max_corr": float(max_corr),
        "vif": vif,
    }


def condition_number(factor_matrix: np.ndarray) -> float:
    """条件数（衡量矩阵病态程度）。

    算法：最大奇异值 / 最小奇异值（numpy SVD）。
    条件数 > 30 提示共线性严重。
    """
    M = np.asarray(factor_matrix, dtype=float)
    if M.ndim != 2:
        raise ValueError("factor_matrix 必须为 2 维 (T, K)")
    if M.size == 0:
        return 0.0
    s = np.linalg.svd(M, compute_uv=False)
    s_max = float(s.max())
    s_min = float(s.min())
    if s_min <= 0:
        return float("inf")
    return s_max / s_min


def summarize_factor_collinearity(
    factor_scores: np.ndarray, factor_names: Sequence[str]
) -> Dict[str, Any]:
    """因子共线性诊断摘要。

    返回条件数、最大 VIF、高相关因子对、严重共线性因子及处理建议。
    建议：条件数 > 30 或 max_vif > 10 → ``"orthogonalize"``；
    存在 VIF > 5 → ``"drop"``；否则 ``"ok"``。
    """
    X = np.asarray(factor_scores, dtype=float)
    if X.ndim != 2:
        raise ValueError("factor_scores 必须为 2 维 (T, K)")
    names = list(factor_names)
    K = X.shape[1]
    if len(names) != K:
        raise ValueError("factor_names 长度与因子数不一致")
    cond = condition_number(X)
    corr_rep = factor_correlation_matrix(X, factor_names=names)
    vifs = corr_rep["vif"]
    max_vif = max(vifs) if vifs else 1.0
    collinear_factors = [names[i] for i, v in enumerate(vifs) if v > 10]
    if cond > 30 or max_vif > 10:
        recommendation = "orthogonalize"
    elif max_vif > 5:
        recommendation = "drop"
    else:
        recommendation = "ok"
    return {
        "condition_number": cond,
        "max_vif": max_vif,
        "high_corr_pairs": corr_rep["high_corr_pairs"],
        "collinear_factors": collinear_factors,
        "recommendation": recommendation,
    }


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    T, K = 100, 3
    # 构造有共线性的因子
    f1 = rng.standard_normal(T)
    f2 = 0.8 * f1 + 0.2 * rng.standard_normal(T)  # 与 f1 高相关
    f3 = rng.standard_normal(T)
    F = np.column_stack([f1, f2, f3])
    names = ["momentum", "value", "quality"]

    # 正交化
    orth_sym, trans_sym = symmetric_orthogonalize(F)
    orth_gs, trans_gs = gram_schmidt_orthogonalize(F)
    print("原始因子相关:", np.corrcoef(F.T)[0, 1])
    print("对称正交后相关:", np.corrcoef(orth_sym.T)[0, 1])

    # 共线性诊断
    diag = summarize_factor_collinearity(F, names)
    print(
        "共线性诊断:",
        diag["recommendation"],
        "cond=",
        diag["condition_number"],
        "max_vif=",
        diag["max_vif"],
    )

    # 风险归因
    true_beta = np.array([0.5, 0.3, 0.2])
    port_ret = F @ true_beta + rng.standard_normal(T) * 0.1
    attr = risk_attribution(port_ret, F, factor_names=names)
    print(
        "风险归因: systematic=%.4f, idio=%.4f, R²=%.3f"
        % (attr["systematic_risk"], attr["idiosyncratic_risk"], attr["r_squared"])
    )
    print("因子贡献:", attr["factor_contrib_pct"])

    # Brinson
    pw = {"000001": 0.3, "000002": 0.4, "600001": 0.3}
    bw = {"000001": 0.4, "000002": 0.3, "600001": 0.3}
    ar = {"000001": 0.05, "000002": 0.02, "600001": -0.01}
    sec = {"000001": "主板", "000002": "主板", "600001": "科创"}
    br = brinson_attribution(pw, bw, ar, sectors=sec)
    print(
        "Brinson: excess=%.4f, alloc=%.4f, select=%.4f, inter=%.4f"
        % (
            br["excess_return"],
            br["allocation"],
            br["selection"],
            br["interaction"],
        )
    )
