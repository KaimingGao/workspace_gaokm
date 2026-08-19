"""编排层 / 能力层共享契约（与 README「接口设计」对齐）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import json
from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class SkillHandler(Protocol):
    """Agent 对 Skill 的唯一硬接口。"""

    def execute(self, function_call: dict) -> str:
        """执行工具调用，返回 JSON 字符串。"""


class BaseSkillHandler:
    """
    Skill Handler 模板：子类实现 handle(params) -> dict。
    execute 统一序列化与异常包装，避免各 Skill 复制粘贴。
    """

    error_prefix: str = "工具执行失败"

    def handle(self, params: dict) -> dict:
        raise NotImplementedError

    def execute(self, function_call: dict) -> str:
        try:
            params = function_call.get("parameters") or {}
            if not isinstance(params, dict):
                params = {}
            result = self.handle(params)
            if not isinstance(result, dict):
                result = {"success": False, "error": "Handler 必须返回 dict"}
            return dump_tool_result(result)
        except Exception as e:
            return dump_tool_result(
                {"success": False, "error": f"{self.error_prefix}：{e}"}
            )


def dump_tool_result(result: Dict[str, Any]) -> str:
    """工具结果 → role=tool 的 content。"""
    return json.dumps(result, ensure_ascii=False)
