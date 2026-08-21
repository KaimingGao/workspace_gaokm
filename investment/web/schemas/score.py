"""评分台账与校准请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class ScoreReviewRequest(BaseModel):
    """昨日复盘：as_of=决策日；horizon=前瞻收益天数。"""

    as_of: Optional[str] = Field(
        default=None, description="决策日 YYYY-MM-DD；默认上一交易日"
    )
    horizon_days: int = Field(default=3, ge=1, le=10)
    autofill: bool = Field(default=True, description="缺 outcomes 时自动用日线回填")


class ScoreLedgerFreezeRequest(BaseModel):
    as_of: Optional[str] = Field(
        default=None, description="冻结日；默认当前会话交易日"
    )


class ScoreOutcomesFillRequest(BaseModel):
    as_of: Optional[str] = None
    horizon_days: int = Field(default=3, ge=1, le=10)


class ScoreLedgerDeleteRequest(BaseModel):
    """清理已冻结账本：单日 as_of 或批量 dates。"""

    as_of: Optional[str] = Field(default=None, description="单日 YYYY-MM-DD")
    dates: Optional[List[str]] = Field(default=None, description="批量日期")
    include_outcomes: bool = Field(
        default=True, description="是否同时删除 outcomes 回填文件"
    )


class ScoreCalibrationFitRequest(BaseModel):
    lookback_dates: int = Field(default=90, ge=20, le=250)
    train_frac: float = Field(default=0.75, ge=0.5, le=0.95)
    sample_source: str = Field(
        default="panel",
        description="panel=分组同源全宇宙历史（默认）；auto=不足才回退账本；ledger=仅账本",
    )
    lookback_bars: Optional[int] = Field(
        default=80, ge=40, le=120, description="panel 日线回看（与分组 lookback 对齐）"
    )
    horizon_days: Optional[int] = Field(
        default=None, ge=1, le=10, description="前瞻收益天数；默认 scoring.horizon_days"
    )
    watching_limit: int = Field(default=100, ge=3, le=240)


class ScoreCalibrationPersistRequest(BaseModel):
    note: str = Field(default="", max_length=200)
    enable: bool = Field(
        default=True,
        description="已废弃：有 knots 即 tip/校准列可读；不进排序/闸。保留字段兼容旧客户端。",
    )

