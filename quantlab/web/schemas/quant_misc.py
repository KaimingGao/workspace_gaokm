"""量化报告与解读请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class QuantReportRequest(BaseModel):
    code: str = "茅台"
    save: bool = True
    include_cross_section: bool = False
    include_portfolio_backtest: bool = True


class QuantInterpretRequest(BaseModel):
    use_saved: bool = True
    code: str = "茅台"
    save_before_interpret: bool = False
    offline: bool = False


class QuantReportDeleteRequest(BaseModel):
    """清理归档日报：单日 stamp/date 或批量。只删 quant_daily_*.{md,html}。"""

    stamp: Optional[str] = Field(default=None, description="单日 YYYYMMDD")
    date: Optional[str] = Field(default=None, description="单日 YYYY-MM-DD")
    stamps: Optional[List[str]] = Field(default=None, description="批量 YYYYMMDD")
    dates: Optional[List[str]] = Field(default=None, description="批量 YYYY-MM-DD")

