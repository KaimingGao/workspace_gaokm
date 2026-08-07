"""尾部风险度量模块：VaR / CVaR / 蒙特卡洛模拟 / 情景回放 / 回测检验。

仅依赖 numpy + 标准库（不引入 scipy）。正态分位数用 Acklam 算法有理逼近 +
Halley 迭代修正；卡方分布 p 值用 ``math.erfc`` 闭式计算。
"""

from __future__ import annotations

import math
from typing import Any, Dict, Sequence, Tuple

import numpy as np

# 常用显著性水平对应的标准正态分位数（查表优先，避免逼近误差）
_Z_TABLE: Dict[float, float] = {
    0.001: -3.0902,
    0.005: -2.5758,
    0.01: -2.3263,
    0.025: -1.9600,
    0.05: -1.6449,
    0.10: -1.2816,
    0.15: -1.0364,
    0.20: -0.8416,
}


def _norm_ppf(p: float) -> float:
    """标准正态分布分位数近似（Acklam 算法），避免 scipy 依赖。

    参数:
        p: 概率值，取值 (0, 1)。

    返回:
        对应的标准正态分位数 z，满足 Phi(z) = p。

    算法:
        Acklam 有理逼近分三段（下尾 / 中段 / 上尾），再用一次 Halley 迭代
        修正残差，相对误差 < 1e-9。
    """
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p <= phigh:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    else:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    # 一次 Halley 迭代修正
    e = 0.5 * math.erfc(-x / math.sqrt(2)) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    x = x - u / (1 + x * u / 2)
    return x


def _z_alpha(alpha: float) -> float:
    """获取显著性水平 alpha 对应的标准正态分位数。

    参数:
        alpha: 显著性水平，取值 (0, 1)。

    返回:
        标准正态的 alpha 分位数 z_alpha（alpha<0.5 时为负数）。

    算法:
        优先查 ``_Z_TABLE`` 常数表；不在表中的值回退到 Acklam 逼近。
    """
    if alpha in _Z_TABLE:
        return _Z_TABLE[alpha]
    return _norm_ppf(alpha)


def historical_var(returns: np.ndarray, alpha: float = 0.05) -> Dict[str, Any]:
    """历史模拟法 VaR。

    参数:
        returns: 收益率序列，形状 (T,) 单资产或 (T, N) 多资产。
                 多资产时按等权组合转换为组合收益后再计算。
        alpha: 显著性水平（0.05 = 95% 置信度）。

    返回:
        ``{"var", "alpha", "confidence", "method", "n_samples"}``。
        ``var`` 为负数表示损失。

    算法:
        对收益率排序后取 alpha 分位数，即为历史模拟 VaR。该方法不假设
        收益分布形态，直接用经验分布的尾部分位估计潜在损失。
    """
    arr = np.asarray(returns, dtype=float)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    else:
        arr = arr.ravel()
    arr = arr[~np.isnan(arr)]
    n = int(arr.size)
    var = float(np.quantile(arr, alpha))
    return {
        "var": var,
        "alpha": alpha,
        "confidence": 1.0 - alpha,
        "method": "historical",
        "n_samples": n,
    }


def parametric_var(mean: float, std: float, alpha: float = 0.05) -> Dict[str, Any]:
    """参数法（方差-协方差法）VaR，假设收益服从正态分布。

    参数:
        mean: 平均收益。
        std: 收益标准差。
        alpha: 显著性水平。

    返回:
        ``{"var", "mean", "std", "alpha", "z_score", "method"}``。

    算法:
        VaR = mean + z_alpha * std，其中 z_alpha 为标准正态的 alpha 分位数
        （alpha=0.05 时 z≈-1.6449，对应 95% 置信度下的损失分位）。该方法
        计算快速但对肥尾 / 非正态分布会低估尾部风险。
    """
    z = _z_alpha(alpha)
    var = float(mean + z * std)
    return {
        "var": var,
        "mean": float(mean),
        "std": float(std),
        "alpha": alpha,
        "z_score": z,
        "method": "parametric_normal",
    }


def monte_carlo_var(
    mean: np.ndarray,
    cov: np.ndarray,
    weights: np.ndarray,
    alpha: float = 0.05,
    n_sims: int = 10000,
    seed: int = 42,
) -> Dict[str, Any]:
    """蒙特卡洛模拟 VaR（多资产）。

    参数:
        mean: (N,) 资产平均收益向量。
        cov: (N, N) 协方差矩阵（须对称半正定）。
        weights: (N,) 组合权重。
        alpha: 显著性水平。
        n_sims: 模拟次数。
        seed: 随机数种子，保证可复现。

    返回:
        ``{"var", "alpha", "n_sims", "method", "simulated_returns"}``。
        ``simulated_returns`` 为 (n_sims,) 组合收益数组。

    算法:
        a. Cholesky 分解 cov = L @ L'
        b. 生成标准正态随机数 Z (n_sims, N)
        c. 模拟多资产收益 R = mean + Z @ L'
        d. 组合收益 = R @ weights
        e. 取组合收益的 alpha 分位数作为 VaR
    """
    mean_arr = np.asarray(mean, dtype=float).ravel()
    cov_arr = np.asarray(cov, dtype=float)
    w_arr = np.asarray(weights, dtype=float).ravel()
    L = np.linalg.cholesky(cov_arr)
    rng = np.random.default_rng(seed)
    Z = rng.standard_normal((n_sims, mean_arr.size))
    R = mean_arr + Z @ L.T
    port = R @ w_arr
    var = float(np.quantile(port, alpha))
    return {
        "var": var,
        "alpha": alpha,
        "n_sims": int(n_sims),
        "method": "monte_carlo",
        "simulated_returns": port,
    }


def conditional_var(returns: np.ndarray, alpha: float = 0.05) -> Dict[str, Any]:
    """条件 VaR（CVaR / Expected Shortfall）。

    参数:
        returns: (T,) 收益率序列。
        alpha: 显著性水平。

    返回:
        ``{"cvar", "var", "alpha", "tail_count", "method"}``。

    算法:
        先用历史模拟法计算 VaR(alpha)，再取所有 <= VaR 的尾部收益的均值，
        即为 CVaR。CVaR 度量尾部损失的期望，是 VaR 的连贯风险测度补充，
        能反映突破 VaR 后的平均损失幅度。
    """
    arr = np.asarray(returns, dtype=float).ravel()
    arr = arr[~np.isnan(arr)]
    var = float(np.quantile(arr, alpha))
    tail = arr[arr <= var]
    cvar = float(tail.mean()) if tail.size > 0 else var
    return {
        "cvar": cvar,
        "var": var,
        "alpha": alpha,
        "tail_count": int(tail.size),
        "method": "historical",
    }


def historical_cvar(returns: np.ndarray, alpha: float = 0.05) -> Dict[str, Any]:
    """历史模拟法 CVaR（``conditional_var`` 的明确命名别名）。

    参数:
        returns: (T,) 收益率序列。
        alpha: 显著性水平。

    返回:
        同 :func:`conditional_var`。
    """
    return conditional_var(returns, alpha=alpha)


def scenario_replay(
    returns: np.ndarray,
    dates: Sequence[str],
    scenarios: Dict[str, Tuple[str, str]],
) -> Dict[str, Any]:
    """历史情景回放（压力测试）。

    参数:
        returns: (T,) 或 (T, N) 收益率序列。多资产时按等权组合聚合为
                 (T,) 组合收益后再计算各指标。
        dates: (T,) 对应日期字符串列表（``YYYY-MM-DD`` 格式）。
        scenarios: ``{情景名: (起始日期, 结束日期)}``，日期为 ``YYYY-MM-DD`` 格式。
                   ISO 日期字符串可按字典序正确比较。

    返回:
        ``{情景名: {cum_return, max_drawdown, volatility, var_95, cvar_95,
         n_days, start_date, end_date}}``。

    算法:
        对每个情景，按日期区间筛选收益率子序列，计算：
        - 累计收益 = prod(1 + r) - 1
        - 最大回撤 = max(1 - 累计净值 / 历史最高净值)
        - 波动率 = 子序列收益标准差
        - VaR(95%) / CVaR(95%) 用历史模拟法
    """
    arr = np.asarray(returns, dtype=float)
    if arr.ndim == 2:
        arr = arr.mean(axis=1)
    else:
        arr = arr.ravel()
    dates_list = [str(d) for d in dates]
    result: Dict[str, Any] = {}
    for name, (start, end) in scenarios.items():
        idx = [i for i, d in enumerate(dates_list) if start <= d <= end]
        seg = arr[idx] if idx else np.array([], dtype=float)
        if seg.size == 0:
            result[name] = {
                "cum_return": 0.0,
                "max_drawdown": 0.0,
                "volatility": 0.0,
                "var_95": 0.0,
                "cvar_95": 0.0,
                "n_days": 0,
                "start_date": start,
                "end_date": end,
            }
            continue
        wealth = np.cumprod(1.0 + seg)
        cum_return = float(wealth[-1] - 1.0)
        running_max = np.maximum.accumulate(wealth)
        drawdowns = 1.0 - wealth / running_max
        max_drawdown = float(drawdowns.max())
        volatility = float(seg.std())
        var_95 = float(np.quantile(seg, 0.05))
        tail = seg[seg <= var_95]
        cvar_95 = float(tail.mean()) if tail.size > 0 else var_95
        result[name] = {
            "cum_return": cum_return,
            "max_drawdown": max_drawdown,
            "volatility": volatility,
            "var_95": var_95,
            "cvar_95": cvar_95,
            "n_days": int(seg.size),
            "start_date": start,
            "end_date": end,
        }
    return result


def var_backtest(
    returns: np.ndarray,
    var_estimates: np.ndarray,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """VaR 回测（Kupiec POF 检验）。

    参数:
        returns: (T,) 实际收益率序列。
        var_estimates: (T,) 每日 VaR 估计值（与 returns 等长）。
        alpha: 显著性水平（期望突破率）。

    返回:
        ``{"breaches", "expected_breaches", "breach_rate", "expected_rate",
        "kupiec_stat", "kupiec_pvalue_approx", "accepted"}``。
        ``accepted`` 为 ``True`` 表示 POF 统计量 < 3.841（95% 临界值），
        不能拒绝 VaR 模型正确的原假设。

    算法:
        a. 突破次数 breaches = sum(returns < var_estimates)
        b. 突破率 = breaches / T
        c. Kupiec POF 统计量：
           POF = -2 * (ln(L_null) - ln(L_alt))
           L_null = (1-p)^(N-X) * p^X   （原假设：突破率 = p = alpha）
           L_alt  = (1-π)^(N-X) * π^X   （备择：突破率 = π = 实际突破率）
           其中 N = T，X = breaches
        d. POF 服从卡方分布 df=1，95% 临界值 3.841；
           p 值用 ``erfc(sqrt(POF/2))`` 闭式计算（卡方 df=1 上尾）。
    """
    ret = np.asarray(returns, dtype=float).ravel()
    var_arr = np.asarray(var_estimates, dtype=float).ravel()
    n = int(ret.size)
    x = int(np.sum(ret < var_arr))
    pi = x / n if n > 0 else 0.0

    # 原假设对数似然（突破率 = alpha）
    ll_null = (n - x) * math.log(1 - alpha) + x * math.log(alpha)

    # 备择对数似然（突破率 = pi）；x=0 或 x=n 时按 0*log(0)=0 约定处理
    if x == 0 or x == n:
        ll_alt = 0.0
    else:
        ll_alt = (n - x) * math.log(1 - pi) + x * math.log(pi)

    pof = -2.0 * (ll_null - ll_alt)

    # 卡方 df=1 上尾 p 值：P(X > pof) = erfc(sqrt(pof / 2))
    pvalue = math.erfc(math.sqrt(max(pof, 0.0) / 2.0))

    return {
        "breaches": x,
        "expected_breaches": float(alpha * n),
        "breach_rate": float(pi),
        "expected_rate": alpha,
        "kupiec_stat": float(pof),
        "kupiec_pvalue_approx": float(pvalue),
        "accepted": bool(pof < 3.841),
    }


def tail_ratio(returns: np.ndarray, alpha: float = 0.05) -> float:
    """尾部比率 = |右尾均值| / |左尾均值|。

    参数:
        returns: (T,) 收益率序列。
        alpha: 尾部显著性水平。

    返回:
        尾部比率（float）。> 1 表示右尾厚于左尾（偏向上行收益），
        < 1 表示左尾更厚（下行风险更大）。左尾均值为 0 时返回 inf。

    算法:
        右尾 = 高于 (1-alpha) 分位数的收益，左尾 = 低于 alpha 分位数的收益，
        返回 |右尾均值| / |左尾均值|。
    """
    arr = np.asarray(returns, dtype=float).ravel()
    arr = arr[~np.isnan(arr)]
    left_q = float(np.quantile(arr, alpha))
    right_q = float(np.quantile(arr, 1.0 - alpha))
    left = arr[arr <= left_q]
    right = arr[arr >= right_q]
    left_mean = float(np.mean(left)) if left.size > 0 else 0.0
    right_mean = float(np.mean(right)) if right.size > 0 else 0.0
    if left_mean == 0.0:
        return float("inf") if right_mean != 0.0 else 0.0
    return abs(right_mean) / abs(left_mean)


if __name__ == "__main__":
    rng = np.random.default_rng(42)
    rets = rng.standard_normal(250) * 0.01 - 0.0002
    print("historical_var:", historical_var(rets))
    print("parametric_var:", parametric_var(float(rets.mean()), float(rets.std())))
    print("conditional_var:", conditional_var(rets))
    print("tail_ratio:", tail_ratio(rets))
    # 蒙特卡洛
    mean = np.array([0.001, 0.0005, -0.0002])
    cov = np.array([[0.0004, 0.0001, 0.0], [0.0001, 0.0003, 0.0001], [0.0, 0.0001, 0.0005]])
    w = np.array([0.4, 0.3, 0.3])
    print("monte_carlo_var:", monte_carlo_var(mean, cov, w, n_sims=5000))
    # 情景回放
    dates = [f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)][:250]
    scenarios = {"2020疫情": ("2020-02-01", "2020-04-30")}
    print("scenario_replay:", scenario_replay(rets, dates, scenarios))
    # VaR 回测
    var_arr = np.full(250, -0.0164)
    print("var_backtest:", var_backtest(rets, var_arr))
