"""组合回测请求模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class PaperReplayBacktestRequest(BaseModel):
    """产品历史回测（``/api/quant/portfolio-backtest``）：只跑 paper_replay / rank_lots。

    旧客户端若仍传 ``engine`` / ``top_k`` / ``horizon_days`` 等字段，Pydantic 默认忽略，
    服务端不切换引擎。
    """

    codes: Optional[list] = None
    lookback: int = Field(default=10, ge=10, le=500)
    score_model_role: str = Field(
        default="research",
        description="回测模型：research=研究槽（Holdout 前训 β，默认）；live=执行槽（由研究槽晋级的同一份 β）。",
        max_length=16,
    )
    score_backend: str = Field(
        default="ridge",
        description="调仓打分头：仅 ridge（影子树头已下线；传入 tree 会被忽略）。",
        max_length=16,
    )
    apply_costs: bool = True
    fetch_fundamentals: Optional[bool] = False
    exclude_st: bool = True
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_benchmark: bool = True
    benchmark_code: str = Field(
        default="pool",
        description="超额基准：pool/tier_a=观察池可预测性 A 档等权买持；或指数代码如 000300。",
    )
    fusion_w_co: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="隔夜 ŷ_co 叠进 ŷ_τc 的系数；0=不叠",
    )
    fusion_w_oo: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_oo_fuse 中 ŷ_oo 权重",
    )
    fusion_w_oc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_oo_fuse 中 ŷ_τc 权重",
    )
    fuse_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="y_oo_fuse 入场下限（净收益，0.001=0.1%；UI 用百分数）",
    )
    fuse_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="y_oo_fuse 强手门槛（净收益；回测单一手数时与入场同）",
    )
    y_oo_gt0: bool = Field(
        default=False,
        description="开=入场须 y_oo>0；关=不看。缺分不拦。未过则已持仓清仓",
    )
    y_τc_gt0: Optional[bool] = Field(
        default=None,
        description="开=入场须 ŷ_τc>0；关=不看。缺分不拦。未过则已持仓清仓",
    )
    oo_rank_max: Optional[int] = Field(
        default=None,
        ge=1,
        le=500,
        description="开则入场另须 max(y_oo_rank, y_τc_rank) < max（UI「rank 入场」默认 10）；None/缺=关。两侧都缺不拦。",
    )
    oo_rank_top_frac: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="旧相对名次闸：开则入场另须 max(y_oo_rank, y_τc_rank) < n·frac；与 oo_rank_max 并存时优先绝对 max。两侧都缺或不齐 n 不拦。",
    )
    initial_cash: float = Field(
        default=200_000.0,
        ge=10_000.0,
        le=100_000_000.0,
        description="历史回测本金（元）。默认 20 万；不套持仓市值帽",
    )
    fill_clock: str = Field(
        default="09:30",
        description="调仓成交钟 09:30–10:00 每 5 分钟；成交用该档 5 分钟 K（09:30 用首根开盘）",
    )
    lot_base_amount: float = Field(
        default=10_000.0,
        ge=1_000.0,
        le=1_000_000.0,
        description="调仓入场金额（元）。默认 1 万；按成交价换算整手，不够一手则跳过。保存规则同步到交易执行自动调仓",
    )
    lot_strong_amount: float = Field(
        default=20_000.0,
        ge=1_000.0,
        le=1_000_000.0,
        description="调仓强档金额（元）。默认 2 万；不少于入场金额；单票持仓市值上限=此值×120%。保存规则同步到交易执行",
    )
    use_predictability_tiers: bool = Field(
        default=False,
        description=(
            "复用研究枢纽「观察池分档」过滤宇宙（须先跑分档）；"
            "回测天数仍用 lookback（与 Holdout 独立）。桌面勾选同步到 live 调仓闸。"
        ),
    )
    predictability_tiers: Optional[list] = Field(
        default=None,
        description="允许入回测/live 的可预测性档，默认 [\"A\",\"B\"]。仅 use_predictability_tiers 时生效。",
    )
    holdout_trading_days: Optional[int] = Field(
        default=None,
        ge=2,
        le=90,
        description="ŷ_oo 卡片 Holdout；须与枢纽 last.holdout_n 一致，否则拒跑并提示重跑「观察池分档」。回测天数仍用 lookback。",
    )
    predictability_head: str = Field(
        default="oo",
        max_length=8,
        description="分档协议头（仅 oo；枢纽 last 亦为 oo）。",
    )
    price_space_gate: bool = Field(
        default=True,
        description="调仓回测日分价闸：有分钟时 |日开/分开−1| 或 |日昨/分昨−1| 超阈则跳过。阈与做 T 共用。不改 live",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/portfolio-backtest",
    )

    @model_validator(mode="after")
    def _sync_fuse_thresholds(self) -> "PaperReplayBacktestRequest":
        if self.fuse_enter is None:
            self.fuse_enter = 0.001
        if self.fuse_strong is None:
            self.fuse_strong = 0.001
        self.fuse_enter = float(self.fuse_enter)
        self.fuse_strong = float(self.fuse_strong)
        return self


class PortfolioBacktestRequest(BaseModel):
    """研究口请求（中性化对照等）：仍含 TopK 腿参数，≠ 产品 /replay。"""

    codes: Optional[list] = None
    lookback: int = Field(default=30, ge=10, le=500)
    # 研究 Top-K 默认 3（小组合探路；纸面 max_positions 仍见 StrategySpec）
    top_k: int = Field(default=3, ge=1, le=40)
    horizon_days: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=55.0, ge=0, le=100)
    apply_costs: bool = True
    include_wf_slices: bool = False
    include_cost_compare: bool = False
    wf_n_splits: int = Field(default=3, ge=1, le=6)
    fetch_fundamentals: Optional[bool] = False
    weight_mode: str = "score_budget"
    max_position_pct: float = Field(default=25.0, ge=5.0, le=100.0)
    max_sector_pct: float = Field(default=40.0, ge=10.0, le=100.0)
    dropout_n: int = Field(default=0, ge=0, le=10)
    exclude_st: bool = True
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_score_ic: bool = False
    include_quantile: bool = False
    include_benchmark: bool = True
    benchmark_code: str = "000300"
    rank_mode: str = Field(
        default="predicted_score",
        description="排序键：仅 predicted_score（收益分）；其它值亦按收益分",
    )
    min_predicted_score: Optional[float] = Field(
        default=None,
        description="收益分下限（百分点）；None=用 scoring.min_predicted_score",
    )
    return_model_min_samples: int = Field(default=24, ge=8, le=500)
    return_model_ridge_lambda: float = Field(default=0.0, ge=0.0, le=100.0)
    fusion_w_co: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="研究口隔夜系数；产品回测用 PaperReplay 的 fusion_w_co（0～10）",
    )
    fusion_w_oo: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_oo_fuse 中 ŷ_oo 权重",
    )
    fusion_w_oc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_oo_fuse 中 ŷ_τc 权重",
    )
    fuse_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="历史回测 y_oo_fuse 入场下限（净收益，0.012=1.2%；UI 用百分数）",
    )
    fuse_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="历史回测 y_oo_fuse 强手门槛（净收益，0.012=1.2%；超过买强档金额）",
    )

    @model_validator(mode="after")
    def _sync_fuse_thresholds(self) -> "PortfolioBacktestRequest":
        if self.fuse_enter is None:
            self.fuse_enter = 0.012
        if self.fuse_strong is None:
            self.fuse_strong = 0.012
        self.fuse_enter = float(self.fuse_enter)
        self.fuse_strong = float(self.fuse_strong)
        return self
