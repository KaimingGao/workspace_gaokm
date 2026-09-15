"""研究台因子 / ŷ_τ / 影子实验请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class CrossSectionRequest(BaseModel):
    codes: Optional[list] = None
    limit: int = Field(default=10, ge=1, le=30)
    min_score: Optional[float] = None
    horizon_days: int = Field(default=3, ge=1, le=3)


class FactorExperimentRequest(BaseModel):
    code: str = "茅台"
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ；0=普通 OLS（QR），>0 收缩斜率系数",
    )


class FactorOlsPoolRequest(BaseModel):
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=8, ge=2, le=20)
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ；0=普通 OLS（QR），>0 收缩斜率系数",
    )


class TauRidgeRequest(BaseModel):
    """open→close / τ→close ŷ_τ 头研究拟合（不写 EOD ŷ）。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 tau_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（确认后强制启用）",
    )
    note: str = Field(default="", max_length=200)
    tau_hm: Optional[str] = Field(
        default=None,
        description="open | 09:45 | 10:30；缺省跟随 dual_score.enable_minute_tau / minute_tau_hm",
        max_length=8,
    )


RemRidgeRequest = TauRidgeRequest


class TauTreeRequest(BaseModel):
    """ŷ_τ_tree 影子头：同面板 Holdout vs Ridge。无 persist，不进 live / 回测。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="open | 09:45 | 10:30；缺省跟随 dual_score.enable_minute_tau / minute_tau_hm",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default=None,
        description="xgboost | numpy_gbm | auto（缺省：有 xgboost 用 xgboost，否则 numpy 浅树）",
        max_length=16,
    )


TauBoostRequest = TauTreeRequest


class RTreeRequest(BaseModel):
    """ŷ_r_tree 影子头：同面板 Holdout vs Ridge。无 persist，不进 live / 回测。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="09:45 | 10:30；缺省 10:30。open 会强制改成 10:30（ŷ_r 需分钟价）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default=None,
        description="xgboost | numpy_gbm | auto（缺省：有 xgboost 用 xgboost，否则 numpy 浅树）",
        max_length=16,
    )


class T30TreeRequest(BaseModel):
    """ŷ_τ30_tree 影子头：同面板 Holdout vs Ridge。无 persist，不进 live / 回测。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="09:45 | 10:30；缺省 10:30。open 会强制改成 10:30（ŷ_τ30 需分钟价）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default=None,
        description="xgboost | numpy_gbm | auto（缺省：有 xgboost 用 xgboost，否则 numpy 浅树）",
        max_length=16,
    )


class T60TreeRequest(BaseModel):
    """ŷ_τ60_tree 影子头：同面板 Holdout vs Ridge。无 persist，不进 live / 回测。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="09:45 | 10:30；缺省 10:30。open 会强制改成 10:30（ŷ_τ60 需分钟价）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default=None,
        description="xgboost | numpy_gbm | auto（缺省：有 xgboost 用 xgboost，否则 numpy 浅树）",
        max_length=16,
    )


class T90TreeRequest(BaseModel):
    """ŷ_τ90_tree 影子头：同面板 Holdout vs Ridge。无 persist，不进 live / 回测。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="09:45 | 10:30；缺省 10:30。open 会强制改成 10:30（ŷ_τ90 需分钟价）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default=None,
        description="xgboost | numpy_gbm | auto（缺省：有 xgboost 用 xgboost，否则 numpy 浅树）",
        max_length=16,
    )


class OnRidgeRequest(BaseModel):
    """open[T+1]/close[T]-1 隔夜缺口 Ridge 拟合（风控旁路 ŷ_ON）。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 on_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    note: str = Field(default="", max_length=200)


class PathRidgeRequest(BaseModel):
    """ŷ_hl Ridge：开盘 Z + 多 τ 前缀分钟小包 + t_hi/t_lo 进度 → 全日极值序（dual_y · y_hl）。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    sell_trig_pct: Optional[float] = Field(
        default=None,
        ge=0.1,
        le=20.0,
        description="path 对照卖侧 % 标签；缺省 2.0",
    )
    buy_trig_pct: Optional[float] = Field(
        default=None,
        ge=0.1,
        le=20.0,
        description="path 对照买侧 % 标签；缺省 1.5",
    )
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 path_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)


class CxRidgeRequest(BaseModel):
    """ŷ_cx Ridge：开盘 Z + 多 τ 前缀分钟小包 + lag → 全日 5m 曲折度 1−D/L ∈[0,1]。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 cx_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)


class TpdRidgeRequest(BaseModel):
    """ŷ_tpd Ridge：开盘 Z + 多 τ 前缀分钟小包 + lag → 全日 5m 转折点密度 ∈[0,1]。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 tpd_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)


class RRidgeRequest(BaseModel):
    """ŷ_τc Ridge：与 ŷ_oc 同 X → close[T]/price(τ)−1（百分点）。与 remaining(ŷ_oc) 融合成 R̂_τ / ĉ。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 r_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/r-ridge",
    )


class T30RidgeRequest(BaseModel):
    """ŷ_τ30 Ridge：与 ŷ_oc 同 X → price(τ⊕30m)/price(τ)−1。做 T 破带同号旁路。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 t30_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t30-ridge",
    )


class T60RidgeRequest(BaseModel):
    """ŷ_τ60 Ridge：与 ŷ_oc 同 X → price(τ⊕60m)/price(τ)−1。做 T 破带同号旁路。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 t60_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t60-ridge",
    )


class FactorOlsClusterRequest(BaseModel):
    """研究池：单票 OLS β 聚类 → 组内共用权草案（不写 config）。"""

    lookback: int = Field(default=80, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进定组/选 k；研究套组 β 再隔离 horizon h",
    )
    watching_limit: int = Field(
        default=200,
        ge=3,
        le=200,
        description="观察池截断：universe_mode=watching 时取名单前 N 只（与 clamp_watching_limit 对齐，默认/上限 200）",
    )
    n_clusters: Optional[int] = Field(
        default=None,
        ge=2,
        le=100,
        description="目标组数；null=自动约 n/5（夹在 4～10）；显式 ≥2，实际上限=有效票数-1",
    )
    cluster_method: str = Field(
        default="hierarchical",
        description="hierarchical（默认，配合 cluster_linkage）| kmeans",
    )
    cluster_linkage: str = Field(
        default="complete",
        description="层次连接：complete（默认，控大团）| average",
    )
    within_dist_quantile: float = Field(
        default=0.75,
        ge=0.05,
        le=0.95,
        description="类内直径 τ = 两两距离分位数（默认 0.75）；越小越紧、单票组越多",
    )
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ 种子；select_ridge=true 时作网格起点/回退",
    )
    select_ridge: bool = Field(
        default=True,
        description="B3：时间切分网格选 Ridge λ（写入 ridge_lambda_selected）",
    )
    collinearity_policy: str = Field(
        default="drop_redundant",
        description="B3：趋势族共线进模 keep_all|drop_redundant|orthogonalize_lite",
    )
    respect_regime: bool = Field(
        default=True,
        description="B5：与 live regime 白名单对齐裁剪因子。枢纽 UI 默认不勾（发 false=全因子）；勾选后表内未进白名单的因子会显示未算",
    )
    pit_fundamentals: bool = Field(
        default=True,
        description="FH5：默认 PIT 基本面；false 时报告带非 PIT 红旗",
    )
    sync: bool = Field(
        default=False,
        description="FH2：true=同步跑（单测/兼容）；默认入队 Job 轮询",
    )
    beta_scale: str = Field(
        default="feature_zscore",
        description="β 尺度：feature_zscore|l2|none；默认因子维 z-score，避免极端票独占一组",
    )
    l2_normalize_betas: Optional[bool] = Field(
        default=None,
        description="兼容旧参：True 强制 l2；优先用 beta_scale",
    )
    run_oos_gate: bool = Field(
        default=True,
        description="为每组跑组内建议权 vs 当前权的 Top-K OOS 对照",
    )
    oos_tol_pp: float = Field(default=1.0, ge=0.0, le=10.0)
    run_group_score: bool = Field(
        default=True,
        description="每组用组权打分并组内排序（对照全局权统一排名）",
    )
    run_pool_merge: bool = Field(
        default=True,
        description="各组 Top-N 合成候选簿 + 分池对照回测（研究预览，不写纸面）",
    )
    top_n_per_group: int = Field(
        default=10,
        ge=1,
        le=10,
        description="每组取组内排名前 N 只合成候选（默认 10）",
    )
    refresh_bars: bool = Field(
        default=False,
        description=(
            "兼容字段：true=本跑强制增量拉日线到最新（脚本/调试用）。"
            "UI 已移除勾选；默认 false。"
            "无论本字段如何，当日第一次分组仍会自动强制更新日线。"
        ),
    )


class ClusterBarsRefreshRequest(BaseModel):
    """观察池日线更新（不跑 OLS 分组）。"""

    lookback: int = Field(default=80, ge=40, le=500)
    watching_limit: int = Field(default=200, ge=3, le=200)
    mode: str = Field(
        default="topup",
        description="topup=增量补齐到最新；full=整窗强更（仓坏/复权兜底）",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/cluster-bars-refresh",
    )


class ClusterMinuteRefreshRequest(BaseModel):
    """观察池 5m 分钟线预热（ŷ_hl / T0 回测）。"""

    period: str = Field(default="5", description="分钟周期；默认 5m")
    lookback_days: int = Field(default=30, ge=5, le=90)
    watching_limit: int = Field(default=200, ge=3, le=200)
    min_span_days: int = Field(default=20, ge=10, le=120)
    mode: str = Field(
        default="full",
        description="full=强更全窗口；topup=增量补齐（已对齐跳过 · 跨度够只补近几日）",
    )
    topup_lookback_days: int = Field(
        default=5,
        ge=2,
        le=15,
        description="mode=topup 时跨度已够的票补齐窗口（日历日）",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/cluster-minute-refresh",
    )


class T90RidgeRequest(BaseModel):
    """ŷ_τ90 Ridge：与 ŷ_oc 同 X → price(τ⊕90m)/price(τ)−1。做 T 破带同号旁路。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(
        default=200,
        ge=2,
        le=200,
        description="观察池上限（默认满池 200）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    minute_lookback_days: int = Field(
        default=150,
        ge=20,
        le=240,
        description="兼容字段；拟合已改为只读缓存，不再按此天数拉远端",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 t90_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t90-ridge",
    )


class FactorOlsClusterRequest(BaseModel):
    """研究池：单票 OLS β 聚类 → 组内共用权草案（不写 config）。"""

    lookback: int = Field(default=80, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    holdout_trading_days: int = Field(
        default=10,
        ge=1,
        le=60,
        description="近 N 个交易日不进定组/选 k；研究套组 β 再隔离 horizon h",
    )
    watching_limit: int = Field(
        default=200,
        ge=3,
        le=200,
        description="观察池截断：universe_mode=watching 时取名单前 N 只（与 clamp_watching_limit 对齐，默认/上限 200）",
    )
    n_clusters: Optional[int] = Field(
        default=None,
        ge=2,
        le=100,
        description="目标组数；null=自动约 n/5（夹在 4～10）；显式 ≥2，实际上限=有效票数-1",
    )
    cluster_method: str = Field(
        default="hierarchical",
        description="hierarchical（默认，配合 cluster_linkage）| kmeans",
    )
    cluster_linkage: str = Field(
        default="complete",
        description="层次连接：complete（默认，控大团）| average",
    )
    within_dist_quantile: float = Field(
        default=0.75,
        ge=0.05,
        le=0.95,
        description="类内直径 τ = 两两距离分位数（默认 0.75）；越小越紧、单票组越多",
    )
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ 种子；select_ridge=true 时作网格起点/回退",
    )
    select_ridge: bool = Field(
        default=True,
        description="B3：时间切分网格选 Ridge λ（写入 ridge_lambda_selected）",
    )
    collinearity_policy: str = Field(
        default="drop_redundant",
        description="B3：趋势族共线进模 keep_all|drop_redundant|orthogonalize_lite",
    )
    respect_regime: bool = Field(
        default=True,
        description="B5：与 live regime 白名单对齐裁剪因子。枢纽 UI 默认不勾（发 false=全因子）；勾选后表内未进白名单的因子会显示未算",
    )
    pit_fundamentals: bool = Field(
        default=True,
        description="FH5：默认 PIT 基本面；false 时报告带非 PIT 红旗",
    )
    sync: bool = Field(
        default=False,
        description="FH2：true=同步跑（单测/兼容）；默认入队 Job 轮询",
    )
    beta_scale: str = Field(
        default="feature_zscore",
        description="β 尺度：feature_zscore|l2|none；默认因子维 z-score，避免极端票独占一组",
    )
    l2_normalize_betas: Optional[bool] = Field(
        default=None,
        description="兼容旧参：True 强制 l2；优先用 beta_scale",
    )
    run_oos_gate: bool = Field(
        default=True,
        description="为每组跑组内建议权 vs 当前权的 Top-K OOS 对照",
    )
    oos_tol_pp: float = Field(default=1.0, ge=0.0, le=10.0)
    run_group_score: bool = Field(
        default=True,
        description="每组用组权打分并组内排序（对照全局权统一排名）",
    )
    run_pool_merge: bool = Field(
        default=True,
        description="各组 Top-N 合成候选簿 + 分池对照回测（研究预览，不写纸面）",
    )
    top_n_per_group: int = Field(
        default=10,
        ge=1,
        le=10,
        description="每组取组内排名前 N 只合成候选（默认 10）",
    )
    refresh_bars: bool = Field(
        default=False,
        description=(
            "兼容字段：true=本跑强制增量拉日线到最新（脚本/调试用）。"
            "UI 已移除勾选；默认 false。"
            "无论本字段如何，当日第一次分组仍会自动强制更新日线。"
        ),
    )


class ClusterBarsRefreshRequest(BaseModel):
    """观察池日线更新（不跑 OLS 分组）。"""

    lookback: int = Field(default=80, ge=40, le=500)
    watching_limit: int = Field(default=200, ge=3, le=200)
    mode: str = Field(
        default="topup",
        description="topup=增量补齐到最新；full=整窗强更（仓坏/复权兜底）",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/cluster-bars-refresh",
    )


class ClusterMinuteRefreshRequest(BaseModel):
    """观察池 5m 分钟线预热（ŷ_hl / T0 回测）。"""

    period: str = Field(default="5", description="分钟周期；默认 5m")
    lookback_days: int = Field(default=30, ge=5, le=90)
    watching_limit: int = Field(default=200, ge=3, le=200)
    min_span_days: int = Field(default=20, ge=10, le=120)
    mode: str = Field(
        default="full",
        description="full=强更全窗口；topup=增量补齐（已对齐跳过 · 跨度够只补近几日）",
    )
    topup_lookback_days: int = Field(
        default=5,
        ge=2,
        le=15,
        description="mode=topup 时跨度已够的票补齐窗口（日历日）",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/cluster-minute-refresh",
    )


class FactorCsIcRequest(BaseModel):
    """S1 · 研究池逐因子日频截面 IC。"""

    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=12, ge=3, le=30)
    min_names: int = Field(default=5, ge=3, le=20)
    pit_fundamentals: bool = True


class AbCompareRequest(BaseModel):
    """S2 · A/B 对照指纹。"""

    label_a: str = "A"
    label_b: str = "B"
    result_a: Optional[dict] = None
    result_b: Optional[dict] = None
    config_a: Optional[dict] = None
    config_b: Optional[dict] = None
    note: str = ""


class ReturnModelFitRequest(BaseModel):
    """拟合收益排序模型并可选落研究草稿。"""

    codes: Optional[list] = None
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=12, ge=3, le=40)
    ridge_lambda: float = Field(default=0.0, ge=0.0, le=100.0)
    min_samples: int = Field(default=24, ge=8, le=500)
    save_draft: bool = True


class ReturnModelPromoteRequest(BaseModel):
    note: str = ""


class WeightSuggestRequest(BaseModel):
    code: str = "茅台"
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    use_cs_ic: bool = Field(
        default=True,
        description="优先用研究池截面 IC/ICIR 驱动建议；池不足回退单票",
    )
    watching_limit: int = Field(default=12, ge=3, le=30)
    run_oos_gate: bool = Field(
        default=True,
        description="建议权 vs 当前权的研究池 Top-K OOS 门禁",
    )
    oos_tol_pp: float = Field(default=1.0, ge=0.0, le=10.0)
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="嵌入 OLS 回退时的 Ridge λ；0=普通 OLS",
    )


class ThresholdSuggestRequest(BaseModel):
    code: str = "茅台"
    lookback: int = Field(default=120, ge=40, le=500)
    use_watching: bool = False
    watching_limit: int = Field(default=5, ge=2, le=10)


class YhatResidualShadowRequest(BaseModel):
    """ŷ 行业残差 on/off 影子对照（不写盘）。"""

    watching_limit: int = Field(default=36, ge=3, le=80)
    top_k: int = Field(default=10, ge=3, le=40)
    prefer_cluster_book: bool = Field(
        default=False, description="已停用：分池簿不再使用；一律 live 打分观察池"
    )


class ExcessModeShadowRequest(BaseModel):
    """绝对 y vs 指数超额 y 影子对照。"""

    lookback: int = Field(default=120, ge=40, le=500)
    watching_limit: int = Field(default=36, ge=2, le=40)
    horizon_days: int = Field(default=1, ge=1, le=10)
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)

