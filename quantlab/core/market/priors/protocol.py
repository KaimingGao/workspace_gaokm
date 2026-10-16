"""Prior 包形状约定（文档 + 可选 Protocol；各 prior 不必强行对齐）。

常见形态（不完全一致）：
- ``get_*_cfg(config)`` → 合并 DEFAULT + signal_config 片段
- ``build_*(...)`` → 构造 prior pack（``active`` / ``actions`` / ``warnings``）
- ``apply_*_to_buy`` / ``apply_*_to_hold`` → 对仓位比例做 gate/risk（ŷ 外）

个股舆情默认 ``mode=off``（仅徽章）；市场级 prior 可 gate。勿改产品语义。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class PriorPack(Protocol):
    """松散协议：配置读取 + 构建 pack。方法名因 prior 而异时可不实现。"""

    def get_cfg(self, config: Optional[dict] = None) -> Dict[str, Any]:
        """合并默认与 signal_config 中的 prior 段。"""
        ...

    def build(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """构建 prior 结果包（含 active / actions / warnings）。"""
        ...


@runtime_checkable
class PriorActions(Protocol):
    """可选：对买入/持仓施加 prior 动作（不改 ŷ）。"""

    def apply_to_buy(
        self,
        item: Optional[dict],
        *,
        position_ratio: float,
        config: Optional[dict] = None,
    ) -> Dict[str, Any]:
        ...

    def apply_to_hold(
        self,
        item: Optional[dict],
        *,
        position_ratio: float,
        config: Optional[dict] = None,
    ) -> Dict[str, Any]:
        ...


__all__ = ["PriorActions", "PriorPack"]
