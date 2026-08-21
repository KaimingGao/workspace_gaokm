"""分池评分与纸面预演请求模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class ClusterPaperPreviewRequest(BaseModel):
    """分池候选簿 → 纸面调仓预演；confirm=true 才写 paper.json（不写 signal_config）。"""

    book: List[dict] = Field(default_factory=list, description="pool_merge.book.book")
    top_k: Optional[int] = Field(default=None, ge=1, le=30)
    confirm: bool = Field(
        default=False,
        description="true=写入纸面账户；默认仅预演",
    )
    artifact: Optional[dict] = Field(
        default=None,
        description="可选 pool_artifact 摘要，落账时记入 last_cluster_pool",
    )


class ClusterMultiScoreRequest(BaseModel):
    """用 code_map 多权复打分（仅组内序；不写 config）。"""

    artifact: Optional[dict] = Field(
        default=None,
        description="含 code_map 的产物；空则读 last_cluster_pool_artifact.json",
    )
    lookback: int = Field(default=80, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=20, ge=1, le=40)


class ClusterPromoteRequest(BaseModel):
    """晋升研究产物为 live active 映射。"""

    artifact: Optional[dict] = None
    from_draft: bool = True
    note: str = Field(default="", max_length=500)
    force: bool = False


class ClusterRollbackRequest(BaseModel):
    to_version: Optional[int] = Field(default=None, ge=1, le=10000)


class ClusterModeRequest(BaseModel):
    mode: str = Field(default="shadow", pattern="^(off|shadow|active)$")
    enabled: Optional[bool] = None
    force: bool = False


class ClusterApplyShortcutRequest(BaseModel):
    """一键应用分组：晋升 + mode + 刷新分池簿。"""

    artifact: Optional[dict] = None
    from_draft: bool = True
    mode: str = Field(default="shadow", pattern="^(off|shadow|active)$")
    note: str = Field(default="", max_length=500)
    force: bool = False

