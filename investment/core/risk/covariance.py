"""协方差矩阵估计模块。

提供 Ledoit-Wolf 收缩估计、EWMA 协方差、最近半正定投影，以及组合波动率与
风险贡献分解等工具。仅依赖 numpy 与标准库，不引入 scipy。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Tuple

import numpy as np


def ledoit_wolf_shrinkage(
    returns: np.ndarray, *, target: str = "diagonal"
) -> Dict[str, Any]:
    """Ledoit-Wolf 收缩协方差估计器。

    参考 Ledoit & Wolf (2004) "A well-conditioned estimator for large-dimensional
    covariance matrices"。通过将样本协方差 S 向一个良条件的目标矩阵 F 收缩，
    得到在 Frobenius 范数下均方误差更小的估计。

    参数:
        returns: (T, N) 收益率矩阵，T 为时间期数，N 为资产数。
        target: 收缩目标。
            - "diagonal": F = diag(diag(S))，保留各资产样本方差、协方差置零；
            - "identity": F = (trace(S)/N) · I，按平均方差缩放的单位阵。

    返回:
        包含以下键的字典:
            - cov: 收缩后协方差矩阵 (N, N)；
            - shrinkage: 收缩强度 delta ∈ [0, 1]；
            - sample_cov: 样本协方差 S (N, N)；
            - target_cov: 目标矩阵 F (N, N)；
            - pi: pi_hat，样本协方差各元素渐近方差之和；
            - rho: rho_hat，目标与样本协方差的渐近协方差之和；
            - mu: mu_hat，平均样本方差 trace(S)/N。

    算法原理:
        a. 中心化 X，样本协方差 S = (1/T) X' X（有偏，除以 T）。
        b. 构造目标矩阵 F（见 target 参数说明），mu_hat = trace(S)/N。
        c. pi_hat = Σ_{i,j} Var(s_ij)，以 (1/T) Σ_t (x_ti x_tj - s_ij)^2 估计；
           等价于 (1/T) Σ_t ||x_t x_t' - S||_F^2。
        d. rho_hat = Σ_{i,j} Cov(f_ij, s_ij)：
           - diagonal 模式下 f_ij=0 (i≠j)，rho_hat = Σ_i Var(s_ii)
             = (1/T) Σ_t Σ_i (x_ti^2 - s_ii)^2；
           - identity 模式下 f_ii = m = trace(S)/N，
             rho_hat = (1/(N·T)) Σ_t (||x_t||^2 - trace(S))^2。
        e. 收缩强度 delta = max(0, min(1, (pi_hat - rho_hat) / (T · ||F - S||_F^2)))；
           当 ||F - S||_F^2 ≈ 0（目标与样本几乎一致）时取 delta = 1。
        f. 收缩协方差 = delta · F + (1 - delta) · S，并强制对称以消除数值误差。

        边界情况：当 N > T 时样本协方差奇异，pi_hat 显著大于 rho_hat，公式自然
        给出接近 1 的收缩强度，从而依赖良条件的目标矩阵。
    """
    X = np.asarray(returns, dtype=float)
    if X.ndim != 2:
        raise ValueError("returns 必须为二维数组 (T, N)")
    T, N = X.shape
    if T < 2:
        raise ValueError("时间期数 T 必须 >= 2")
    if target not in ("diagonal", "identity"):
        raise ValueError("target 必须为 'diagonal' 或 'identity'")

    Xc = X - X.mean(axis=0)
    S = (Xc.T @ Xc) / T
    mu_hat = float(np.trace(S)) / N

    if target == "diagonal":
        F = np.diag(np.diag(S)).astype(float)
    else:
        F = (mu_hat * np.eye(N)).astype(float)

    norm2_t = np.sum(Xc ** 2, axis=1)              # (T,) ||x_t||^2
    xSx_t = np.sum((Xc @ S) * Xc, axis=1)          # (T,) x_t' S x_t
    S_frob2 = float(np.sum(S ** 2))                # ||S||_F^2
    per_t = norm2_t ** 2 - 2.0 * xSx_t + S_frob2
    pi_hat = float(np.sum(per_t)) / T

    if target == "diagonal":
        d = np.diag(S)                             # (N,) s_ii
        diff_diag = Xc ** 2 - d[None, :]           # (T, N) x_ti^2 - s_ii
        rho_hat = float(np.sum(diff_diag ** 2)) / T
    else:
        trace_S = float(np.trace(S))
        rho_hat = float(np.sum((norm2_t - trace_S) ** 2)) / (N * T)

    frob2_FS = float(np.sum((F - S) ** 2))
    if frob2_FS <= 1e-12:
        delta = 1.0
    else:
        delta = (pi_hat - rho_hat) / (T * frob2_FS)
        delta = float(max(0.0, min(1.0, delta)))

    cov = delta * F + (1.0 - delta) * S
    cov = (cov + cov.T) * 0.5

    return {
        "cov": cov,
        "shrinkage": delta,
        "sample_cov": S,
        "target_cov": F,
        "pi": pi_hat,
        "rho": rho_hat,
        "mu": mu_hat,
    }


def ewma_covariance(
    returns: np.ndarray, *, halflife: float = 21.0, min_periods: int = 10
) -> np.ndarray:
    """指数加权移动平均协方差（RiskMetrics 风格）。

    近期观测赋予更大权重，对波动率/相关性结构的变化反应更灵敏。

    参数:
        returns: (T, N) 收益率矩阵。
        halflife: 半衰期（交易日），衰减因子 lambda = 0.5^(1/halflife)。
            halflife 越大，权重衰减越慢、记忆越长。
        min_periods: 最少需要的观测期数；T < min_periods 时抛出 ValueError。

    返回:
        (N, N) 加权协方差矩阵。

    算法原理:
        衰减因子 lambda = 0.5^(1/halflife)。第 t 期（t=0 最旧，t=T-1 最新）
        权重 w_t = lambda^(T-1-t)，归一化为 Σ w_t = 1。以加权均值 μ_w = Σ w_t x_t
        中心化后，加权协方差 = Σ_t w_t (x_t - μ_w)(x_t - μ_w)'。
    """
    X = np.asarray(returns, dtype=float)
    if X.ndim != 2:
        raise ValueError("returns 必须为二维数组 (T, N)")
    T = X.shape[0]
    if T < max(2, int(min_periods)):
        raise ValueError(f"数据长度 T={T} 小于 min_periods={int(min_periods)}")
    if halflife <= 0:
        raise ValueError("halflife 必须为正数")

    lam = 0.5 ** (1.0 / halflife)
    t_idx = np.arange(T)
    w = lam ** (T - 1 - t_idx)
    w = w / w.sum()

    mean = (w[:, None] * X).sum(axis=0)
    Xc = X - mean
    cov = (Xc * w[:, None]).T @ Xc
    cov = (cov + cov.T) * 0.5
    return cov


def _project_psd(matrix: np.ndarray) -> np.ndarray:
    """将对称矩阵投影到半正定锥：特征值负值置零后重建。"""
    M = (matrix + matrix.T) * 0.5
    w, V = np.linalg.eigh(M)
    w_clip = np.maximum(w, 0.0)
    return (V * w_clip) @ V.T


def nearest_psd(
    matrix: np.ndarray, *, max_iter: int = 100, tol: float = 1e-8
) -> np.ndarray:
    """将矩阵投影到最近的半正定矩阵（Higham 2002 交替投影法）。

    用于修正数值误差或含缺失数据处理导致的非半正定协方差矩阵。

    参数:
        matrix: 待修正的方阵 (n, n)，允许非对称。
        max_iter: 交替投影最大迭代次数。
        tol: Frobenius 范数相对变化收敛阈值。

    返回:
        最近半正定矩阵 (n, n)（对称）。

    算法原理:
        在两个凸集间交替投影直至收敛：
        - 对称矩阵集 S：投影为 (M + M') / 2；
        - 半正定锥 P：对称化后做特征分解，将负特征值置零再重建。
        两集交集中的最近点即为最近半正定矩阵。
    """
    A = np.asarray(matrix, dtype=float)
    if A.ndim != 2 or A.shape[0] != A.shape[1]:
        raise ValueError("matrix 必须为方阵")

    B = A.astype(float, copy=True)
    for _ in range(int(max_iter)):
        B_prev = B
        B = (B + B.T) * 0.5
        B = _project_psd(B)
        denom = max(1.0, float(np.linalg.norm(B_prev, ord="fro")))
        diff = float(np.linalg.norm(B - B_prev, ord="fro")) / denom
        if diff < tol:
            break
    return (B + B.T) * 0.5


def cov_to_corr(cov: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """协方差矩阵转相关系数矩阵。

    参数:
        cov: (N, N) 协方差矩阵。

    返回:
        (corr, vol):
            - corr: (N, N) 相关系数矩阵，对角线为 1（方差为 0 的资产对角线置 0）；
            - vol: (N,) 波动率向量（各资产标准差）。

    算法原理:
        vol_i = sqrt(cov_ii)，corr_ij = cov_ij / (vol_i · vol_j)。
        方差为 0 的资产对应行列置 0 以避免除零。
    """
    C = np.asarray(cov, dtype=float)
    if C.ndim != 2 or C.shape[0] != C.shape[1]:
        raise ValueError("cov 必须为方阵")

    vol = np.sqrt(np.maximum(np.diag(C), 0.0))
    denom = np.outer(vol, vol)
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 1e-12, C / denom, 0.0)
    diag = np.where(vol > 1e-12, 1.0, 0.0)
    np.fill_diagonal(corr, diag)
    return corr, vol


def portfolio_volatility(weights: np.ndarray, cov: np.ndarray) -> float:
    """计算组合波动率 σ_p = sqrt(w' Σ w)。

    参数:
        weights: (N,) 权重向量。
        cov: (N, N) 协方差矩阵。

    返回:
        组合波动率（标量）。当 w' Σ w 因数值误差为微小负值时按 0 处理。
    """
    w = np.asarray(weights, dtype=float).ravel()
    C = np.asarray(cov, dtype=float)
    var = float(w @ C @ w)
    return float(np.sqrt(max(var, 0.0)))


def marginal_contribution(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """边际风险贡献 MRC = (Σ w) / sqrt(w' Σ w)。

    表示组合波动率对各资产权重的偏导数 ∂σ_p/∂w_i。

    参数:
        weights: (N,) 权重向量。
        cov: (N, N) 协方差矩阵。

    返回:
        (N,) 边际风险贡献向量。
    """
    w = np.asarray(weights, dtype=float).ravel()
    C = np.asarray(cov, dtype=float)
    port_var = float(w @ C @ w)
    port_vol = np.sqrt(max(port_var, 1e-300))
    return (C @ w) / port_vol


def component_contribution(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """成分风险贡献（百分比），归一化后总和为 1。

    CRC_i = w_i · MRC_i，反映各资产对组合波动率的绝对贡献；由于
    Σ_i w_i · MRC_i = w' Σ w / σ_p = σ_p，归一化后即得各资产贡献占比。

    参数:
        weights: (N,) 权重向量。
        cov: (N, N) 协方差矩阵。

    返回:
        (N,) 成分风险贡献占比向量（和为 1）。
    """
    w = np.asarray(weights, dtype=float).ravel()
    mrc = marginal_contribution(w, cov)
    crc = w * mrc
    total = float(crc.sum())
    if abs(total) > 1e-300:
        return crc / total
    return np.zeros_like(crc)


def annualize_cov(cov: np.ndarray, periods_per_year: int = 252) -> np.ndarray:
    """年化协方差矩阵（如日频 → 年频）。

    参数:
        cov: (N, N) 低频协方差矩阵。
        periods_per_year: 低频周期对应的年化期数（日频=252，周频=52，月频=12）。

    返回:
        (N, N) 年化协方差矩阵。

    算法原理:
        收益率方差与期数成正比，故协方差矩阵整体乘以 periods_per_year；
        波动率随之放大 sqrt(periods_per_year) 倍，相关系数保持不变。
    """
    C = np.asarray(cov, dtype=float)
    if C.ndim != 2:
        raise ValueError("cov 必须为二维矩阵")
    return C * float(periods_per_year)


if __name__ == "__main__":
    # 简单自测
    rng = np.random.default_rng(42)
    raw = rng.standard_normal((60, 5)) * 0.02
    res = ledoit_wolf_shrinkage(raw)
    print(f"shrinkage={res['shrinkage']:.4f}, shape={res['cov'].shape}")
    print(f"sample diag={np.diag(res['sample_cov'])}")
    print(f"shrunk diag={np.diag(res['cov'])}")
    ewma = ewma_covariance(raw, halflife=21)
    print(f"ewma diag={np.diag(ewma)}")
    w = np.array([0.2, 0.2, 0.2, 0.2, 0.2])
    print(f"port vol={portfolio_volatility(w, res['cov']):.6f}")
    print(f"component contrib={component_contribution(w, res['cov'])}")
