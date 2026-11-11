"""研究台因子 / ŷ_τ / 排序头请求模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from core.data.policy import BARS_DAILY_LOOKBACK
from core.watching.store import MODEL_FIT_MAX_SIZE

FIT_LOOKBACK_MIN = 100
FIT_LOOKBACK_MAX = 1000


class _CrossSectionZscore(BaseModel):
    """日截面 Z（产品固定开；False 拒）。"""

    cross_section_zscore: bool = Field(
        default=True,
        description="产品固定 True=日截面 z；False 已下线（训练窗全局 μ/σ）",
    )

    @field_validator("cross_section_zscore")
    @classmethod
    def _cs_z_only(cls, value: bool) -> bool:
        from core.research.feature_standardize import reject_global_zscore

        err = reject_global_zscore(value)
        if err:
            raise ValueError(err)
        return True


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


class TcLgbRequest(_CrossSectionZscore):
    """τ→close ŷ_τc LightGBM 研究拟合（不写 ŷ_oo）。"""

    lookback: int = Field(default=120, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=2,
        le=MODEL_FIT_MAX_SIZE,
        description=f"观察池上限（默认满池 {MODEL_FIT_MAX_SIZE}）",
    )
    gap_trigger_pct: float = Field(default=2.0, ge=0.5, le=10.0)
    theme_boost: float = Field(default=1.5, ge=0.5, le=5.0)
    persist: bool = Field(
        default=False,
        description="True=人审写入模型文件（路径由 persist_role 决定）",
    )
    persist_role: str = Field(
        default="live",
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练（Holdout）；只训前段一套模型",
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
    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:30…11:00（每 5m）；"
            "09:30=0 前缀（标签 open→close）；缺省跟随 enable_minute_tau / tau_hm"
        ),
        max_length=8,
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/tau-lgb",
    )









class OoRankRequest(_CrossSectionZscore):
    """ŷ_oo_rank：单头 LambdaRank；成交 oo_rank=1..n；进合成 rank_score（买序/阈值）。"""

    lookback: int = Field(default=700, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=8,
        le=MODEL_FIT_MAX_SIZE,
        description=(
            f"观察池截断（默认满池 {MODEL_FIT_MAX_SIZE}）。"
            "截面 z 与损失宇宙均用该池（训练档已下线）。"
        ),
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="OOS holdout 交易日。默认 20，允许 1～60；拟合按请求值切分",
    )
    topk_track: int = Field(
        default=10, ge=2, le=40, description="OOS TopK 跑路对照宽度"
    )
    ndcg_k: int = Field(
        default=10, ge=2, le=40, description="OOS NDCG@K 的 K（与训练目标对齐）"
    )
    l2: float = Field(
        default=5.0,
        ge=0.0,
        le=100.0,
        description="LambdaRank L2；默认 5（与 LAMBDARANK_L2 鲁棒默认一致）",
    )
    backend: str = Field(
        default="auto",
        description="auto / lambdarank（同义）；ridge 拒",
        max_length=24,
    )
    persist: bool = Field(
        default=False,
        description="True=写入 LambdaRank 到模型文件（路径由 persist_role 决定；不重训）",
    )
    persist_role: str = Field(
        default="live",
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    note: str = Field(default="", max_length=200)

    @field_validator("backend")
    @classmethod
    def _rank_backend(cls, value: str) -> str:
        raw = str(value or "auto").strip().lower() or "auto"
        if raw in ("auto", "lambdarank", "lightgbm", "lgbm"):
            return "lambdarank" if raw != "auto" else "auto"
        raise ValueError("排序头仅支持 auto / lambdarank")


class TcRankRequest(OoRankRequest):
    """ŷ_τc_rank：单头 LambdaRank；标签 price[τ]→close[T]。启用只落盘；不进 fuse；进合成 rank_score（买序/阈值）。"""

    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:30…11:00（与 ŷ_τc 同组滑条；09:30=0 前缀 open→close；"
            "省略默认 09:30）"
        ),
        max_length=5,
    )


class T30RidgeRequest(_CrossSectionZscore):
    """ŷ_τ30 Ridge：开盘 Z+≤τ 分钟−OC 形状+本头序列（无 alpha_oo）→ P(窗收益>0)。进 ŷ_τw；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=2,
        le=MODEL_FIT_MAX_SIZE,
        description=f"观察池上限（默认满池 {MODEL_FIT_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
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
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练（Holdout）；只训前段一套 β",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    tau_hm: Optional[str] = Field(
        default=None,
        description="兼容旧字段；优先用 tau_end_hm",
        max_length=8,
    )
    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:35…11:00（每 5m）；"
            "网格自 09:35 至该钟共享 β（仅分钟；09:30 上抬）；缺省满格至 11:00"
        ),
        max_length=8,
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t30-ridge",
    )


class T45RidgeRequest(_CrossSectionZscore):
    """ŷ_τ45 Ridge：开盘 Z+≤τ 分钟−OC 形状+本头序列（无 alpha_oo）→ P(窗收益>0)。进 ŷ_τw；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=2,
        le=MODEL_FIT_MAX_SIZE,
        description=f"观察池上限（默认满池 {MODEL_FIT_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
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
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练（Holdout）；只训前段一套 β",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    tau_hm: Optional[str] = Field(
        default=None,
        description="兼容旧字段；优先用 tau_end_hm",
        max_length=8,
    )
    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:35…11:00（每 5m）；"
            "网格自 09:35 至该钟共享 β（仅分钟；09:30 上抬）；缺省满格至 11:00"
        ),
        max_length=8,
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t45-ridge",
    )


class T60RidgeRequest(_CrossSectionZscore):
    """ŷ_τ60 Ridge：开盘 Z+≤τ 分钟−OC 形状+本头序列（无 alpha_oo）→ P(窗收益>0)。进 ŷ_τw；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=2,
        le=MODEL_FIT_MAX_SIZE,
        description=f"观察池上限（默认满池 {MODEL_FIT_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
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
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练（Holdout）；只训前段一套 β",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    tau_hm: Optional[str] = Field(
        default=None,
        description="兼容旧字段；优先用 tau_end_hm",
        max_length=8,
    )
    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:35…11:00（每 5m）；"
            "网格自 09:35 至该钟共享 β（仅分钟；09:30 上抬）；缺省满格至 11:00"
        ),
        max_length=8,
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t60-ridge",
    )


class T75RidgeRequest(_CrossSectionZscore):
    """ŷ_τ75 Ridge：开盘 Z+≤τ 分钟−OC 形状+本头序列（无 alpha_oo）→ P(窗收益>0)。进 ŷ_τw；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=2,
        le=MODEL_FIT_MAX_SIZE,
        description=f"观察池上限（默认满池 {MODEL_FIT_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
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
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练（Holdout）；只训前段一套 β",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    tau_hm: Optional[str] = Field(
        default=None,
        description="兼容旧字段；优先用 tau_end_hm",
        max_length=8,
    )
    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:35…11:00（每 5m）；"
            "网格自 09:35 至该钟共享 β（仅分钟；09:30 上抬）；缺省满格至 11:00"
        ),
        max_length=8,
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认 persist=false 时入队 Job，轮询 GET /api/jobs/t75-ridge",
    )


class BarsRefreshRequest(BaseModel):
    """观察池日线更新（不跑 OLS 分组）。"""

    lookback: int = Field(
        default=BARS_DAILY_LOOKBACK,
        ge=FIT_LOOKBACK_MIN,
        le=FIT_LOOKBACK_MAX,
    )
    watching_limit: int = Field(default=MODEL_FIT_MAX_SIZE, ge=3, le=MODEL_FIT_MAX_SIZE)
    mode: str = Field(
        default="topup",
        description="topup=只补缺日（短仓/前复权重锚才整窗替换）；full=整窗覆盖存量（仓坏/复权兜底）",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/bars-refresh",
    )


class MaterializeRequest(BaseModel):
    """独立物化 L1 Feature Lake / L2 DayCube（不训模型、不出 ŷ）。"""

    families: List[str] = Field(
        default_factory=lambda: ["oo", "tc"],
        description="L1 family：oo / tc / oo_rank",
    )
    clocks: List[str] = Field(
        default_factory=lambda: ["open"],
        description="L2 clock：open 或 tau_HHMM",
    )
    do_l1: bool = True
    do_l1_tau_grid: bool = Field(
        default=False,
        description="物化训练网格 tc_HHMM L1（τc∪窗头；须本地 5m）",
    )
    do_l2: bool = True
    do_session_open_l2: bool = Field(
        default=False,
        description="盘中补齐会话日官方 open L2（今开行情 + ≤T−1 日线落盘）",
    )
    cycle_day: Optional[str] = Field(
        default=None,
        description="会话日 YYYY-MM-DD；do_session_open_l2 时默认 oo_cycle_date()",
    )
    lookback: int = Field(
        default=BARS_DAILY_LOOKBACK,
        ge=FIT_LOOKBACK_MIN,
        le=FIT_LOOKBACK_MAX,
        description="日线 L1 与 open L2 共用回看交易日；分钟钟 L2 用内部近端短窗",
    )
    watching_limit: int = Field(default=MODEL_FIT_MAX_SIZE, ge=3, le=MODEL_FIT_MAX_SIZE)
    sync: bool = Field(
        default=False,
        description="true=同步；默认 Job，轮询 GET /api/jobs/materialize-l1l2",
    )


class MinuteRefreshRequest(BaseModel):
    """观察池 5m 分钟线预热（ŷ_τc / T0 回测）。"""

    period: str = Field(default="5", description="分钟周期；默认 5m")
    watching_limit: int = Field(default=MODEL_FIT_MAX_SIZE, ge=3, le=MODEL_FIT_MAX_SIZE)
    min_span_days: int = Field(default=40, ge=10, le=120)
    mode: str = Field(
        default="full",
        description="full=强更（新浪近端）；topup=增量补齐",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/minute-refresh",
    )


class T90RidgeRequest(_CrossSectionZscore):
    """ŷ_τ90 Ridge：开盘 Z+≤τ 分钟−OC 形状+本头序列（无 alpha_oo）→ P(窗收益>0)。进 ŷ_τw；个股旁路闸已下线。"""

    lookback: int = Field(default=120, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    watching_limit: int = Field(
        default=MODEL_FIT_MAX_SIZE,
        ge=2,
        le=MODEL_FIT_MAX_SIZE,
        description=f"观察池上限（默认满池 {MODEL_FIT_MAX_SIZE}）；拟合只读本地 5m 缓存、不拉远端",
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
        description="research=报告→研究槽；live=研究槽→执行槽（晋级）",
    )
    holdout_trading_days: int = Field(
        default=20,
        ge=1,
        le=60,
        description="近 N 个交易日不进训练（Holdout）；只训前段一套 β",
    )
    force_promote: bool = Field(
        default=False,
        description="True=跳过 OOS promote 闸（仅调试）",
    )
    note: str = Field(default="", max_length=200)
    tau_hm: Optional[str] = Field(
        default=None,
        description="兼容旧字段；优先用 tau_end_hm",
        max_length=8,
    )
    tau_end_hm: Optional[str] = Field(
        default=None,
        description=(
            "训练前缀终点 09:35…11:00（每 5m）；"
            "网格自 09:35 至该钟共享 β（仅分钟；09:30 上抬）；缺省满格至 11:00"
        ),
        max_length=8,
    )
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


class ReturnModelFitRequest(_CrossSectionZscore):
    """拟合 ŷ_oo LightGBM 并可选落研究草稿。"""

    codes: Optional[list] = None
    lookback: int = Field(default=600, ge=FIT_LOOKBACK_MIN, le=FIT_LOOKBACK_MAX)
    horizon_days: int = Field(default=1, ge=1, le=10)
    watching_limit: int = Field(default=MODEL_FIT_MAX_SIZE, ge=3, le=MODEL_FIT_MAX_SIZE)
    ridge_lambda: float = Field(
        default=1.0,
        ge=0.0,
        le=100.0,
        description="兼容旧请求体；ŷ_oo 已切 LightGBM，不参与拟合",
    )
    min_samples: int = Field(default=24, ge=8, le=500)
    save_draft: bool = True
    holdout_trading_days: int = Field(
        default=20,
        ge=3,
        le=60,
        description="近 N 个交易日 Holdout，只测不训（与 ŷ_oo 卡片 Holdout 共用）",
    )


class ReturnModelPromoteRequest(BaseModel):
    note: str = ""
    persist_role: str = Field(
        default="live",
        description="research=草稿→研究槽（回测）；live=研究槽→执行槽（晋级）",
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
        description=(
            "兼容旧开关；ŷ_oo 已切 LightGBM 后 weights 不进打分，"
            "门禁固定解耦跳过（看 IC/健康后人审）"
        ),
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


