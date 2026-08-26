"""策略晋升、评测与日更请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class StrategyPromoteRequest(BaseModel):
    strategy: str = Field(default="short", min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)
    apply_to_paper: bool = False
    overrides: Optional[Dict] = None


class EvalRunRequest(BaseModel):
    case_id: Optional[str] = None
    use_mock: bool = True
    with_agent: bool = False
    with_presets: bool = False
    quant_only: bool = False
    background: bool = False


class DailyRunRequest(BaseModel):
    preset: Optional[str] = None
    paper_run: Optional[bool] = None
    paper_holding_cycle: Optional[bool] = None  # alias for paper_run
    paper_buy: Optional[bool] = None
    eval_mock: Optional[bool] = None
    eval_agent: Optional[bool] = None
    quant_report: Optional[bool] = None
    watching_refresh: Optional[bool] = None
    cross_section: Optional[bool] = None
    sync_paper_watchlist: Optional[bool] = None
    paper_rebalance: Optional[bool] = None
    paper_cross_section_rebalance: Optional[bool] = None  # alias for paper_rebalance
    export_quant_report: Optional[bool] = None
    portfolio_neutral_compare: Optional[bool] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=40)
    horizon_days: Optional[int] = Field(default=None, ge=1, le=10)
    lookback: Optional[int] = Field(default=None, ge=30, le=500)

