"""组合回测请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class PortfolioBacktestRequest(BaseModel):
    codes: Optional[list] = None
    lookback: int = Field(default=30, ge=30, le=500)
    # 历史 Top-K 默认 3（研究用小组合；纸面 max_positions 仍见 StrategySpec）
    top_k: int = Field(default=3, ge=1, le=40)
    horizon_days: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=55.0, ge=0, le=100)
    apply_costs: bool = True
    # 交互 Top-K 默认关：大观察池时 WF/零成本对照会再跑整段回测，易超前端超时
    include_wf_slices: bool = False
    include_cost_compare: bool = False
    wf_n_splits: int = Field(default=3, ge=1, le=6)
    # 交互回测默认跳过慢速基本面批量，避免「回测中」卡住感
    fetch_fundamentals: Optional[bool] = False
    # equal | score_budget | risk_parity_lite（与纸面 optimize 同源；默认 score_budget）
    weight_mode: str = "score_budget"
    max_position_pct: float = Field(default=25.0, ge=5.0, le=100.0)
    max_sector_pct: float = Field(default=40.0, ge=10.0, le=100.0)
    # T7 TopK-Dropout 缓冲；0=硬截断
    dropout_n: int = Field(default=0, ge=0, le=10)
    exclude_st: bool = True
    # 池内成交额分位下限（0–100）；None=不过滤
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_score_ic: bool = False
    include_quantile: bool = False
    include_benchmark: bool = True
    # T10：000300 / 000905 / 399006 / pool（强制池等权）
    benchmark_code: str = "000300"
    # 仅 predicted_score；规则分已退役
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
    engine: str = Field(
        default="paper_replay",
        description="回测引擎：paper_replay=rank_lots（默认，对齐 Follow）；topk_research=研究独立腿",
    )
    y_on_alpha: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="历史回测 ranking 隔夜系数 α；0=不乘 y_on，1=按原权重乘入",
    )
    rank_enter: float = Field(
        default=0.01,
        ge=0.0,
        le=10.0,
        description="历史回测 ranking 入场下限（净收益，0.01=1%）",
    )
    rank_strong: float = Field(
        default=0.02,
        ge=0.0,
        le=10.0,
        description="历史回测 ranking 强手门槛（净收益，0.02=2%；超过买 2000 股）",
    )

