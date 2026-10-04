"""研究台因子 / ŷ_τ / 影子实验请求模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.watching.store import WATCHING_MAX_SIZE

class CrossSectionRequest(BaseModel):
    codes: Optional[list] = None
    limit: int = Field(default=10, ge=1, le=30)
    min_score: Optional[float] = None
    horizon_days: int = Field(default=3, ge=1, le=3)


class ExprEvalRequest(BaseModel):
    """DSL 表达式因子求值请求。"""
    code: str = "茅台"
    expr: str = Field(default="ROC($close, 5)", min_length=1, description="DSL 表达式，如 ROC($close, 5)")
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=5, ge=1, le=20)


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
    """open→close / τ→close ŷ_τ 头研究拟合（不写 ŷ_oo）。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）",
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
        default=20,
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
        description="open | 09:45 | 10:30；缺省跟随 dual_score.enable_minute_tau（开则训 09:30…11:00 网格）",
        max_length=8,
    )
    include_alpha158: bool = Field(
        default=True,
        description="Ridge 吃 raw_alpha158_*（≤T−1）；与 ŷ_oo 日线 X 可能重叠",
    )
    label_demean: bool = Field(
        default=True,
        description="训练标签全局去均值，截距加回；默认 True（历史 ŷ_τc 口径）",
    )


class OoTreeRequest(BaseModel):
    """ŷ_oo_tree：日线面板 Holdout vs Ridge。写入 oo_tree_model.json，调仓回测选 Tree。不进 live。"""

    lookback: int = Field(default=600, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）",
    )
    horizon_days: int = Field(default=1, ge=1, le=10)
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    backend: Optional[str] = Field(
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )
    include_alpha158: bool = Field(
        default=True,
        description="树侧吃 raw_alpha158_*（≤T−1）；Ridge 对照不含",
    )


class CoTreeRequest(BaseModel):
    """ŷ_co_tree：隔夜缺口面板 Holdout vs Ridge。写入 co_tree_model.json，调仓回测选 Tree。不进 live。"""

    lookback: int = Field(default=600, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    backend: Optional[str] = Field(
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )
    include_alpha158: bool = Field(
        default=True,
        description="树侧吃 raw_alpha158_*（≤T−1）；Ridge 对照仅 CO Z",
    )


class TauTreeRequest(BaseModel):
    """ŷ_τc_tree：同面板 Holdout vs Ridge。写入 tc_tree_model.json，调仓回测选 Tree。不进 live。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="open | 09:45 | 10:30；缺省跟随 dual_score.enable_minute_tau（开则训 09:30…11:00 网格）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )
    include_alpha158: bool = Field(
        default=True,
        description="树侧吃 raw_alpha158_*（≤T−1）；Ridge 对照不含，防与 ŷ_oo 双重计权",
    )


class T30TreeRequest(BaseModel):
    """ŷ_τ30_tree：同面板 Holdout vs Ridge。写入 t30_tree_model.json，做 T 回测选 Tree。不进 live。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
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
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )


class T45TreeRequest(BaseModel):
    """ŷ_τ45_tree：同面板 Holdout vs Ridge。写入 t45_tree_model.json，做 T 回测选 Tree。不进 live。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="09:45 | 10:30；缺省 10:30。open 会强制改成 10:30（ŷ_τ45 需分钟价）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )


class T60TreeRequest(BaseModel):
    """ŷ_τ60_tree：同面板 Holdout vs Ridge。写入 t60_tree_model.json，做 T 回测选 Tree。不进 live。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
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
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )


class T75TreeRequest(BaseModel):
    """ŷ_τ75_tree：同面板 Holdout vs Ridge。写入 t75_tree_model.json，做 T 回测选 Tree。不进 live。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练，与 Ridge 对照共用",
    )
    tau_hm: Optional[str] = Field(
        default=None,
        description="09:45 | 10:30；缺省 10:30。open 会强制改成 10:30（ŷ_τ75 需分钟价）",
        max_length=8,
    )
    backend: Optional[str] = Field(
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )


class T90TreeRequest(BaseModel):
    """ŷ_τ90_tree：同面板 Holdout vs Ridge。写入 t90_tree_model.json，做 T 回测选 Tree。不进 live。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    holdout_trading_days: int = Field(
        default=20,
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
        default="lightgbm",
        description="仅 lightgbm",
        max_length=16,
    )


class CoRidgeRequest(BaseModel):
    """open[T+1]/close[T]-1 隔夜缺口 Ridge 拟合（风控旁路 ŷ_co）。"""

    lookback: int = Field(default=600, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）",
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
        description="live=执行套 co_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进研究套训练，专供历史回测",
    )
    label_demean: bool = Field(
        default=False,
        description="训练标签全局去均值，截距加回；供拟合/回测对照",
    )
    note: str = Field(default="", max_length=200)


class OoRankRequest(BaseModel):
    """ŷ_oo_rank LambdaRank（影子头；不进 live ranking）。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=200,
        ge=8,
        le=2000,
        description=(
            f"日线研究截断：优先 research_universe（可至 2000）；"
            f"宇宙为空时回退观察池（实际仍≤{WATCHING_MAX_SIZE}）。不进分钟暖仓。"
        ),
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="OOS holdout 交易日；oo_rank 默认 20（短窗噪声大）",
    )
    feature_mode: str = Field(
        default="raw",
        description="特征消融：raw（默认，消融胜出）| cs_rank | cs_z | raw_cs",
    )
    pair_preset: str = Field(
        default="wide",
        description="pair 采样：wide（头尾约 35%）| topk_focus（约 15% + gap）",
    )
    top_k: Optional[int] = Field(
        default=None,
        ge=2,
        le=100,
        description="覆盖 pair_preset 的头带宽绝对下限；缺省用 preset",
    )
    bottom_k: Optional[int] = Field(
        default=None,
        ge=2,
        le=100,
        description="覆盖 pair_preset 的尾带宽绝对下限；缺省用 preset",
    )
    topk_track: int = Field(
        default=10, ge=2, le=40, description="OOS TopK 跑路对照宽度"
    )
    l2: float = Field(default=1.0, ge=0.0, le=100.0)
    backend: str = Field(
        default="lambdarank",
        description="仅 lambdarank",
        max_length=24,
    )
    persist: bool = Field(
        default=False,
        description="True=写入 oo_rank_pairwise_model.json（仍不进 live 决策）",
    )
    note: str = Field(default="", max_length=200)


class T30RidgeRequest(BaseModel):
    """ŷ_τ30 Ridge：与 ŷ_τc 同 X → mean(price(τ⊕25/30/35))/price(τ)−1。进 ŷ_τw 投票；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
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
        default=20,
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


class T45RidgeRequest(BaseModel):
    """ŷ_τ45 Ridge：与 ŷ_τc 同 X → mean(price(τ⊕40/45/50))/price(τ)−1。进 ŷ_τw 投票；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 t45_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=20,
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
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t45-ridge",
    )


class T60RidgeRequest(BaseModel):
    """ŷ_τ60 Ridge：与 ŷ_τc 同 X → mean(price(τ⊕55/60/65))/price(τ)−1。进 ŷ_τw 投票；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
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
        default=20,
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


class T75RidgeRequest(BaseModel):
    """ŷ_τ75 Ridge：与 ŷ_τc 同 X → mean(price(τ⊕70/75/80))/price(τ)−1。进 ŷ_τw 投票；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
    )
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="live=执行套 t75_ridge_model.json；research=研究套 *_research.json",
    )
    holdout_trading_days: int = Field(
        default=20,
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
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t75-ridge",
    )


class FactorOlsClusterRequest(BaseModel):
    """分组 OLS 请求体（路径已退役；保留 B-track 默认值供兼容/单测）。"""

    lookback: int = Field(default=80, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=3,
        le=WATCHING_MAX_SIZE,
    )
    select_ridge: bool = Field(
        default=True,
        description="B3：时间切分网格选 Ridge λ",
    )
    collinearity_policy: str = Field(
        default="drop_redundant",
        description="B3：趋势族共线进模 keep_all|drop_redundant|orthogonalize_lite",
    )
    respect_regime: bool = Field(
        default=True,
        description="B5：与 live regime 白名单对齐裁剪因子",
    )
    sync: bool = Field(
        default=False,
        description="兼容字段；分组路径已 410",
    )


class ClusterBarsRefreshRequest(BaseModel):
    """观察池日线更新（不跑 OLS 分组）。"""

    lookback: int = Field(default=600, ge=40, le=700)
    watching_limit: int = Field(default=WATCHING_MAX_SIZE, ge=3, le=WATCHING_MAX_SIZE)
    mode: str = Field(
        default="topup",
        description="topup=增量补齐到最新（短仓整窗重拉）；full=整窗强更（仓坏/复权兜底）",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/cluster-bars-refresh",
    )


class ClusterMinuteRefreshRequest(BaseModel):
    """观察池 5m 分钟线预热（ŷ_hl / T0 回测）。"""

    period: str = Field(default="5", description="分钟周期；默认 5m")
    lookback_days: int = Field(default=120, ge=5, le=120)
    watching_limit: int = Field(default=WATCHING_MAX_SIZE, ge=3, le=WATCHING_MAX_SIZE)
    min_span_days: int = Field(default=40, ge=10, le=120)
    mode: str = Field(
        default="full",
        description="full=强更全窗口；topup=增量补齐；repair=东财补缺（只写更齐的交易日）",
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
    """ŷ_τ90 Ridge：与 ŷ_τc 同 X → mean(price(τ⊕85/90/95))/price(τ)−1。进 ŷ_τw 投票；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=40, le=700)
    watching_limit: int = Field(
        default=WATCHING_MAX_SIZE,
        ge=2,
        le=WATCHING_MAX_SIZE,
        description=f"观察池上限（默认满池 {WATCHING_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
    )
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    minute_period: str = Field(
        default="5",
        max_length=4,
        description="分钟周期；默认 5m，与做 T 回测一致",
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
        default=20,
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
    lookback: int = Field(default=600, ge=40, le=700)
    horizon_days: int = Field(default=1, ge=1, le=10)
    watching_limit: int = Field(default=WATCHING_MAX_SIZE, ge=3, le=WATCHING_MAX_SIZE)
    ridge_lambda: float = Field(default=1.0, ge=0.0, le=100.0)
    min_samples: int = Field(default=24, ge=8, le=500)
    save_draft: bool = True
    holdout_trading_days: int = Field(
        default=20,
        ge=3,
        le=60,
        description="近 N 个交易日 Holdout，只测不训（与 ŷ_oo 卡片 Holdout 共用）",
    )
    label_demean: bool = Field(
        default=False,
        description="训练标签全局去均值，截距加回；供拟合/回测对照",
    )


class ReturnModelPromoteRequest(BaseModel):
    note: str = ""
    persist_role: str = Field(
        default="live",
        description="research=研究套（回测）；live=执行套（交易）",
    )


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


class ResearchTaskRequest(BaseModel):
    """已登记研究头的统一入口。params 传给对应 run_*_experiment。"""

    head: str = Field(min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)


class YhatResidualShadowRequest(BaseModel):
    """ŷ 行业残差 on/off 影子对照（不写盘）。"""

    watching_limit: int = Field(default=36, ge=3, le=80)
    top_k: int = Field(default=10, ge=3, le=40)
    prefer_cluster_book: bool = Field(
        default=False, description="已停用：分池簿不再使用；一律 live 打分观察池"
    )


