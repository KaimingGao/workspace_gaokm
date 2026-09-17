"""评分台账请求模型。"""

from __future__ import annotations

from typing import List, Optional

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

