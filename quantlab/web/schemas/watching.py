"""观察池请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from core.watching.store import WATCHING_MAX_SIZE


class WatchingWatchAdd(BaseModel):
    query: str = Field(..., min_length=1, max_length=64)
    sync_paper: bool = False


class WatchingWatchRemove(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    sync_paper: bool = False


class WatchingSyncPaper(BaseModel):
    """加入模拟账户（现价假买进持仓）；codes 为空或不传则处理全部观察，传则只处理勾选。

    定量优先：shares_by_code / amount_by_code > amount_per_code > position_pct > shares。
    都不传时默认按金额（每只 2 万）。
    """

    codes: Optional[list] = None
    shares: Optional[int] = Field(default=None, ge=100, le=1_000_000)
    shares_by_code: Optional[Dict[str, int]] = None
    amount_per_code: Optional[float] = Field(default=None, gt=0, le=100_000_000)
    amount_by_code: Optional[Dict[str, float]] = None
    position_pct: Optional[float] = Field(default=None, gt=0, le=1)


class WatchingFile(BaseModel):
    version: int = 1
    name: str = "default"
    max_size: int = Field(default=30, ge=5, le=WATCHING_MAX_SIZE)
    sources: list = Field(default_factory=list)
    watchlist: list = Field(default_factory=list)
    watchlist_origins: Optional[list] = None
    watchlist_names: Optional[list] = None

