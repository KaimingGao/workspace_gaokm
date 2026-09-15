"""组合回测请求模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PaperReplayBacktestRequest(BaseModel):
    """产品历史回测（``/api/quant/portfolio-backtest``）：只跑 paper_replay / rank_lots。

    旧客户端若仍传 ``engine`` / ``top_k`` / ``horizon_days`` 等字段，Pydantic 默认忽略，
    服务端不切换引擎。
    """

    codes: Optional[list] = None
    lookback: int = Field(default=30, ge=10, le=500)
    apply_costs: bool = True
    fetch_fundamentals: Optional[bool] = False
    exclude_st: bool = True
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_benchmark: bool = True
    benchmark_code: str = "000300"
    y_on_alpha: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="历史回测 ranking 隔夜系数 α；0=不乘 y_on，1=按原权重乘入",
    )
    fusion_w_oo: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ranking 中 ŷ_oo 权重；缺则用 fusion_w_trade",
    )
    fusion_w_oc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ranking 中 ŷ_oc 权重；缺则用 fusion_w_nowcast",
    )
    fusion_w_trade: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="旧别名 fusion_w_oo",
    )
    fusion_w_nowcast: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="旧别名 fusion_w_oc",
    )
    rank_enter: float = Field(
        default=0.001,
        ge=0.0,
        le=10.0,
        description="门槛1 ranking 入场下限（净收益，0.001=0.1%；UI 用百分数）",
    )
    rank_strong: float = Field(
        default=0.001,
        ge=0.0,
        le=10.0,
        description="历史回测 ranking 强手门槛（净收益；回测单一手数时与入场同）",
    )
    rank_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="门槛2 ranking 入场下限；缺则跟随门槛1",
    )
    y_enter_enabled: bool = Field(default=True, description="门槛1 启用")
    y_enter_alt_enabled: bool = Field(default=True, description="门槛2 启用")
    y_hl_enabled: bool = Field(
        default=True,
        description="开=门槛1/2 看 y_hl 入场；关=完全不看 y_hl",
    )
    y_oo_enter: float = Field(
        default=0.1,
        ge=0.0,
        le=100.0,
        description="门槛1 ŷ_oo 入场下限（百分点）；0=关；缺分不拦",
    )
    y_oc_enter: float = Field(
        default=0.1,
        ge=0.0,
        le=100.0,
        description="门槛1 ŷ_oc 入场下限（百分点）；0=关；缺分不拦",
    )
    y_hl_enter: float = Field(
        default=0.1,
        ge=0.0,
        le=100.0,
        description="门槛1 y_hl 入场下限（百分点）；0=关；缺分不拦；y_hl_enabled 关时不看",
    )
    y_oo_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 ŷ_oo 入场下限；缺则跟随门槛1",
    )
    y_oc_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 ŷ_oc 入场下限；缺则跟随门槛1",
    )
    y_hl_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 y_hl 入场下限；缺则跟随门槛1；y_hl_enabled 关时不看",
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
    lot_base: int = Field(
        default=200,
        ge=100,
        le=10000,
        description="历史回测每笔股数。默认 200；整百。保存规则不改交易执行 200/500",
    )
    lot_strong: int = Field(
        default=200,
        ge=100,
        le=10000,
        description="历史回测强档手数（股）。默认与每笔股数相同",
    )
    universe_fit_tiers: Optional[list] = Field(
        default=None,
        description="观察池拟合档过滤（A/B/C 可多选）。空则用 live cluster_scoring.universe_fit_tiers；满三档=不过滤",
    )
    price_space_gate: bool = Field(
        default=True,
        description="调仓回测日分价闸：有分钟时 |日开/分开−1| 或 |日昨/分昨−1| 超阈则跳过。阈与做 T 共用。不改 live",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/portfolio-backtest",
    )


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
    y_on_alpha: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="历史回测 ranking 隔夜系数 α；0=不乘 y_on，1=按原权重乘入",
    )
    fusion_w_oo: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ranking 中 ŷ_oo 权重；缺则用 fusion_w_trade",
    )
    fusion_w_oc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ranking 中 ŷ_oc 权重；缺则用 fusion_w_nowcast",
    )
    fusion_w_trade: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="旧别名 fusion_w_oo",
    )
    fusion_w_nowcast: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="旧别名 fusion_w_oc",
    )
    rank_enter: float = Field(
        default=0.012,
        ge=0.0,
        le=10.0,
        description="历史回测 ranking 入场下限（净收益，0.012=1.2%；UI 用百分数）",
    )
    rank_strong: float = Field(
        default=0.012,
        ge=0.0,
        le=10.0,
        description="历史回测 ranking 强手门槛（净收益，0.012=1.2%；超过买 200 股）",
    )
