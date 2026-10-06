"""打分门槛、先验与双轨配置请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ScoringFloorsRequest(BaseModel):
    """Y0 · 人审写入 ŷ 滞回门槛（不改 weights）。"""

    min_predicted_score: Optional[float] = Field(
        default=None, description="买入/入簿 ŷ% 下限；省略则不改"
    )
    min_hold_predicted_score: Optional[float] = Field(
        default=None, description="卖出 ŷ% 上限（低于则卖）；省略则不改"
    )
    note: str = Field(default="", max_length=500)


class StanceThresholdsRequest(BaseModel):
    """人审写入 stance_thresholds（不改 weights / scoring）。"""

    avoid: Optional[float] = Field(default=None, description="avoid 档 ŷ% 或旧分；省略则保留当前")
    wait: Optional[float] = Field(default=None, description="wait 档")
    probe: Optional[float] = Field(default=None, description="probe 档")
    note: str = Field(default="", max_length=500)


class SentimentPriorRequest(BaseModel):
    """舆情先验旁路（不进 ŷ）；人审写 sentiment.prior。"""

    mode: Optional[str] = Field(default=None, description="off | risk | gate")
    block_new_buys: Optional[bool] = None
    scale_buy_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scale_holds: Optional[bool] = None
    note: str = Field(default="", max_length=500)


class MarketPriorRequest(BaseModel):
    """M 层 prior 旁路（不进 ŷ）；人审写 cross_market / market_prior_policy。"""

    cross_market_mode: Optional[str] = Field(
        default=None, description="off | risk | gate"
    )
    tech_drag_trigger_pct: Optional[float] = Field(
        default=None, description="海外科技隔夜跌幅触发阈值（%）"
    )
    scale_buy_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scale_holds: Optional[bool] = None
    market_sentiment_mode: Optional[str] = Field(
        default=None, description="情绪周期 prior：off | risk | gate"
    )
    market_sentiment_scale_buy_pct: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )
    market_sentiment_scale_holds: Optional[bool] = None
    regulatory_mode: Optional[str] = Field(
        default=None, description="监管 prior：off | risk | gate"
    )
    regulatory_scale_buy_pct: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )
    ipo_drain_mode: Optional[str] = Field(
        default=None, description="IPO 虹吸 prior：off | risk | gate"
    )
    ipo_drain_scale_buy_pct: Optional[float] = Field(
        default=None, ge=0.0, le=1.0
    )
    ipo_drain_ratio_high: Optional[float] = Field(
        default=None, description="IPO 虹吸比触发阈值"
    )
    merge_mode: Optional[str] = Field(
        default=None, description="min_scale | chain；多 prior 合并策略"
    )
    note: str = Field(default="", max_length=500)


class DualScoreRequest(BaseModel):
    """双层 ŷ fusion；人审写 dual_score（不改 weights / scoring）。"""

    fusion_mode: Optional[str] = Field(
        default=None, description="仅 blend；其它入参归一为 blend"
    )
    min_predicted_score_tau: Optional[float] = Field(
        default=None, description="ŷ_τ 买入闸下限（%）；省略不改"
    )
    w_eod: Optional[float] = Field(
        default=None, description="ŷ_oo 融合权重"
    )
    w_tau: Optional[float] = Field(
        default=None, description="ŷ_τ 融合权重"
    )
    w_mode: Optional[str] = Field(
        default=None,
        description="fixed | theme_boost | variance；kalman 已退役并回退为 fixed",
    )
    block_buy_if_tau_missing: Optional[bool] = None
    note: str = Field(default="", max_length=500)

