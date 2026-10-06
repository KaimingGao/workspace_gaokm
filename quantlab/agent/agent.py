"""量化 Agent：多轮对话 + 多工具调度（可多轮串联工具）。"""

import json
import logging
from typing import Dict, List

from agent.artifacts import build_artifact
from agent.contracts import SkillHandler
from agent.context import build_session_context
from agent.llm_client import (
    LLMClient,
    add_usage,
    empty_usage,
    format_usage,
    parse_usage,
)
from agent.prompts import DISCLAIMER, SYSTEM_PROMPT
from agent.registry import (
    get_handler,
    load_tool_definitions,
)
from agent.routing import (
    build_user_hints,
    enrich_tool_result,
    needs_disclaimer,
    prepare_tool_params,
)

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5

_EMPTY_REPLY_HINT = (
    "模型未返回有效正文（常见于工具链过长、联网搜索偏慢，或当前模型响应异常）。"
    "建议：① 缩短问题或分步提问；② 在 .env 增大 DASHSCOPE_TIMEOUT（如 180）；"
    "③ 暂时设 DASHSCOPE_ENABLE_SEARCH=0 后重试。"
)

__all__ = ["Agent", "MAX_TOOL_ROUNDS"]


class Agent:
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
        # 热更新 prompts / LLM 模型（平台改 .env 后无需重启会话）
        # 注入会话上下文（纸面持仓 / 观察池 / 近期决策 / 简报），让 Agent 感知用户当前状态
        ctx = build_session_context()
        system_content = SYSTEM_PROMPT + ("\n\n" + ctx if ctx else "")
        self.messages[0] = {"role": "system", "content": system_content}
        from agent.llm_client import resolve_llm_model

        model, source = resolve_llm_model()
        if model != self.llm.model:
            self.llm.model = model
            self.llm.model_source = source
            self.llm._tested = False
            self.llm._available = None
        effective = build_user_hints(user_input, llm=self.llm)
        self.messages.append({"role": "user", "content": effective})
        turn_usage = empty_usage()
        self.last_artifacts = []

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.llm.chat(self.messages, tools=self.tools)
            add_usage(turn_usage, parse_usage(response))
            tool_calls = self.llm.extract_function_calls(response)

            if not tool_calls:
                content = self._response_content(response, turn_usage)
                if not content:
                    content = _EMPTY_REPLY_HINT
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
                    logger.exception('unexpected error in chat')
                    result = json.dumps(
                        {"success": False, "error": str(e)},
                        ensure_ascii=False,
                    )

                try:
                    self.last_artifacts.append(
                        build_artifact(tool_name, params, result)
                    )
                except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
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
        content = self._response_content(response, turn_usage) or _EMPTY_REPLY_HINT
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

    def _finish_reason(self, response: Dict) -> str:
        choices = response.get("choices") or []
        if not choices:
            return ""
        return str(choices[0].get("finish_reason") or "")

    def _response_content(
        self,
        response: Dict,
        turn_usage: Dict,
        *,
        retry_without_search: bool = True,
    ) -> str:
        content = (self.llm.get_response_content(response) or "").strip()
        if content:
            return content
        logger.warning(
            "LLM empty content model=%s finish=%s",
            self.llm.model,
            self._finish_reason(response),
        )
        if not retry_without_search:
            return ""
        retry = self.llm.chat(self.messages, enable_search=False)
        add_usage(turn_usage, parse_usage(retry))
        return (self.llm.get_response_content(retry) or "").strip()

    def _ensure_disclaimer(self, content: str, user_input: str) -> str:
        if needs_disclaimer(user_input, content) and DISCLAIMER not in content:
            return content.rstrip() + "\n\n" + DISCLAIMER
        return content

    def reset(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.last_turn_usage = empty_usage()
        self.last_artifacts = []
        self.llm.reset_usage()
