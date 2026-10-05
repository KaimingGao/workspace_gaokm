"""LLM 意图分类：替代 routing.py 的纯关键词匹配，解决「不可以买」误判等问题。

分类器返回结构化意图（intent + required_tools + flags），供 routing 注入 hint。
LLM 调用失败 / 无 API Key 时回退到关键词路由（见 routing.build_user_hints）。
"""

import logging

logger = logging.getLogger(__name__)
import json
from typing import Any, Dict, List, Optional

# 意图枚举（与 prompts / routing 的 hint 类型对齐）
INTENT_BUY = "buy"              # 能否买入 / 该不该买
INTENT_POSITION = "position"    # 持仓 / 加减仓 / 止损
INTENT_QUANT = "quant"          # 量化研究 / 回测 / 横截面
INTENT_COMPARE = "compare"      # 多股对比
INTENT_SCREEN = "screen"        # 条件选股
INTENT_SIGNAL = "signal"        # 短线观察池 / score
INTENT_QUOTE = "quote"          # 查价 / 行情
INTENT_KLINE = "kline"          # K 线形态
INTENT_FUNDAMENTALS = "fundamentals"  # 基本面
INTENT_NEWS = "news"            # 资讯
INTENT_GENERAL = "general"      # 其他 / 闲聊

_INTENT_SYSTEM = (
    "你是量化助手的意图分类器。把用户问题归入一个 intent，并判断需要哪些工具。\n"
    "只输出 JSON，不要解释。格式：\n"
    '{"intent": "<intent>", "required_tools": ["<tool>"], '
    '"wants_stance": true|false, "is_quant": true|false, "is_position": true|false}\n'
    "intent 可选：buy|position|quant|compare|screen|signal|quote|kline|fundamentals|news|general\n"
    "规则：\n"
    "- 含「不可以买」「不能买」「别买」→ intent=general（否定买入，不触发 buy 路由）\n"
    "- 「能否买/该不该买/可以买」且未被否定 → intent=buy, wants_stance=true\n"
    "- 持仓/加减仓/止损 → intent=position, is_position=true；涉及买入倾向时 wants_stance=true\n"
    "- 量化/横截面/回测/IC/因子 → intent=quant, is_quant=true\n"
    "- 多股对比 → intent=compare；条件选股 → intent=screen\n"
    "- 查价/行情 → intent=quote；K线 → intent=kline；基本面 → intent=fundamentals；资讯 → intent=news\n"
    "required_tools 从 [quote,compare,screen,signal,advise,backtest,quant,kline,fundamentals,peer,index,news,position] 选。"
)


def classify_intent(
    user_input: str,
    llm,
    *,
    timeout: float = 15.0,
) -> Optional[Dict[str, Any]]:
    """用 LLM 分类意图。失败返回 None（由调用方回退关键词路由）。"""
    text = (user_input or "").strip()
    if not text:
        return None
    if not llm or not getattr(llm, "api_key", ""):
        return None
    messages = [
        {"role": "system", "content": _INTENT_SYSTEM},
        {"role": "user", "content": text},
    ]
    try:
        # 分类不需要联网搜索，关闭以提速
        resp = llm.chat(messages, enable_search=False)
        content = (llm.get_response_content(resp) or "").strip()
        if not content:
            return None
        # 容忍 ```json ... ``` 包裹
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:]
        data = json.loads(content.strip())
        if not isinstance(data, dict):
            return None
        intent = str(data.get("intent") or INTENT_GENERAL)
        tools = data.get("required_tools") or []
        if not isinstance(tools, list):
            tools = []
        return {
            "intent": intent,
            "required_tools": [str(t) for t in tools],
            "wants_stance": bool(data.get("wants_stance", False)),
            "is_quant": bool(data.get("is_quant", False)),
            "is_position": bool(data.get("is_position", False)),
        }
    except Exception:  # noqa: BLE001 — 分类失败不阻塞主流程，回退关键词
        logger.debug("classify_intent failed", exc_info=True)
        return None
