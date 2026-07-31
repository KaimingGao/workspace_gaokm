"""量化 Agent：多轮对话 + 多工具调度（可多轮串联工具）。"""

from __future__ import annotations

import json
from typing import Dict, List

from agent.contracts import SkillHandler
from agent.llm_client import (
    LLMClient,
    add_usage,
    empty_usage,
    format_usage,
    parse_usage,
)
from agent.prompts import DISCLAIMER, SYSTEM_PROMPT
from agent.registry import (
    TOOL_NAMES,
    get_handler,
    load_tool_definitions,
)
from agent.routing import (
    build_user_hints,
    enrich_tool_result,
    needs_disclaimer,
    prepare_tool_params,
)
from agent.artifacts import build_artifact

MAX_TOOL_ROUNDS = 5

# 对外兼容：仍可 from agent.agent import TOOL_NAMES
__all__ = ["InvestmentAgent", "TOOL_NAMES", "MAX_TOOL_ROUNDS"]


class InvestmentAgent:
    def __init__(self):
        self.llm = LLMClient()
        self.tools: List[Dict] = load_tool_definitions()
        from agent.registry import validate_tools

        validate_tools(self.tools)
        self.handlers: Dict[str, SkillHandler] = {}
        self.messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.last_turn_usage = empty_usage()
        self.last_artifacts: List[Dict] = []

    def chat(self, user_input: str) -> str:
        # 热更新 prompts 后，旧会话仍对齐最新 system 规则
        self.messages[0] = {"role": "system", "content": SYSTEM_PROMPT}
        effective = build_user_hints(user_input)
        self.messages.append({"role": "user", "content": effective})
        turn_usage = empty_usage()
        self.last_artifacts = []

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.llm.chat(self.messages, tools=self.tools)
            add_usage(turn_usage, parse_usage(response))
            tool_calls = self.llm.extract_function_calls(response)

            if not tool_calls:
                content = self.llm.get_response_content(response) or ""
                content = self._ensure_disclaimer(content, user_input)
                self.messages.append({"role": "assistant", "content": content})
                self.last_turn_usage = turn_usage
                return self._with_usage_footer(content)

            assistant_msg = self.llm.get_assistant_message(response)
            self.messages.append(assistant_msg)

            for call in tool_calls:
                tool_name = call["name"]
                params = prepare_tool_params(
                    tool_name, call.get("parameters", {}), user_input
                )
                try:
                    handler = self.handlers.get(tool_name)
                    if handler is None:
                        handler = get_handler(tool_name)
                        self.handlers[tool_name] = handler
                    result = handler.execute(
                        {"name": tool_name, "parameters": params}
                    )
                    result = enrich_tool_result(tool_name, result)
                except KeyError:
                    result = json.dumps(
                        {"success": False, "error": f"未找到工具: {tool_name}"},
                        ensure_ascii=False,
                    )
                except Exception as e:
                    result = json.dumps(
                        {"success": False, "error": str(e)},
                        ensure_ascii=False,
                    )

                try:
                    self.last_artifacts.append(
                        build_artifact(tool_name, params, result)
                    )
                except Exception:
                    pass

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", "call_1"),
                        "content": result,
                    }
                )

        response = self.llm.chat(self.messages)
        add_usage(turn_usage, parse_usage(response))
        content = self.llm.get_response_content(response) or "抱歉，处理超时，请换个问法再试。"
        content = self._ensure_disclaimer(content, user_input)
        self.messages.append({"role": "assistant", "content": content})
        self.last_turn_usage = turn_usage
        return self._with_usage_footer(content)

    def get_last_artifacts(self) -> List[Dict]:
        return list(self.last_artifacts)

    def _with_usage_footer(self, content: str) -> str:
        """把本轮 token 附在用户可见回复末尾；不写入 messages，避免污染下一轮上下文。"""
        footer = (
            f"\n\n---\n"
            f"{format_usage(self.last_turn_usage, prefix='本轮 token：')}"
            f" ｜ {format_usage(self.get_session_usage(), prefix='会话累计：')}"
        )
        return content.rstrip() + footer

    def get_last_turn_usage(self) -> Dict[str, int]:
        """上一轮用户问题触发的全部 LLM 调用合计（含多轮 tool loop）。"""
        return dict(self.last_turn_usage)

    def get_session_usage(self) -> Dict[str, int]:
        return self.llm.get_session_usage()

    def format_last_turn_usage(self) -> str:
        return format_usage(self.last_turn_usage, prefix="本轮 token: ")

    def format_session_usage(self) -> str:
        return format_usage(self.get_session_usage(), prefix="会话累计 token: ")

    def _ensure_disclaimer(self, content: str, user_input: str) -> str:
        if needs_disclaimer(user_input, content) and DISCLAIMER not in content:
            return content.rstrip() + "\n\n" + DISCLAIMER
        return content

    def reset(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.last_turn_usage = empty_usage()
        self.last_artifacts = []
        self.llm.reset_usage()
