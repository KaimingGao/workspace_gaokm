#!/usr/bin/env python3
"""Investment 量化交易 CLI 入口（融合 AI 编排）。"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.env import load_env_file
from core.paths import ROOT_DIR

load_env_file(os.path.join(ROOT_DIR, ".env"))

from agent.agent import InvestmentAgent

import logging

from core.market import register_symbol_resolver
try:
    from adapters.market.quote_api import StockAPI
    register_symbol_resolver(StockAPI.resolve_symbol)
except Exception:
    logger = logging.getLogger(__name__)
    logger.debug("StockAPI resolver not registered (skills layer unavailable at import time)")

logger = logging.getLogger(__name__)


def main():
    print("=" * 60)
    print("  Investment · 量化交易（融合 AI）")
    print("  能力: 行情/对比/选股/短线/K线/基本面/同行/相对强弱/资讯/持仓/投顾建议")
    print("  输入 quit/exit 退出，reset 重置对话，usage 查看 token 累计")
    print("=" * 60)

    agent = InvestmentAgent()
    llm = agent.llm

    if not llm.api_key:
        print("\n未检测到 DASHSCOPE_API_KEY。")
        print("请在 investment/.env 中填写，或执行：")
        print("  export DASHSCOPE_API_KEY=your_api_key")
        print("  export DASHSCOPE_ENDPOINT=https://your-workspace-id.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        print("  export DASHSCOPE_MODEL=qwen-plus")
        print("\n提示：无 LLM 时仍可直接测试行情模块：")
        print("  python3 -c \"from adapters.market.quote_api import StockAPI; print(StockAPI.query('茅台'))\"")
        sys.exit(1)

    if not llm.is_available():
        print("\n已读取 API Key，但无法连接到大模型 API。")
        print(f"  ENDPOINT: {llm.endpoint}")
        print(f"  MODEL: {llm.model}")
        print("请检查 Key 权限、模型名（如 qwen-plus）是否正确，以及网络是否可达。")
        detail = llm.get_last_error()
        if detail:
            print(f"详情: {detail}")
        sys.exit(1)

    print(f"\n已加载工具: {list(agent.handlers.keys()) or '（按需加载）'}")
    print("声明: 量化研究与模拟，市场有风险，不保证收益，不代客下单。\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("再见!")
            break
        if user_input.lower() == "reset":
            agent.reset()
            print("对话已重置（token 累计已清零）\n")
            continue
        if user_input.lower() in ("usage", "tokens"):
            print(agent.format_session_usage())
            print(agent.format_last_turn_usage())
            print()
            continue

        try:
            reply = agent.chat(user_input)
            print(f"\n投顾: {reply}\n")
        except Exception as e:
            logger.exception('unexpected error in main')
            print(f"\n出错了: {e}\n")


if __name__ == "__main__":
    main()
