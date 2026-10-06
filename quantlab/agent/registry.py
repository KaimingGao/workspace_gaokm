"""Skill 单一注册表：TOOL 名 / Handler / tool_config 三者对齐。"""


import logging

logger = logging.getLogger(__name__)
import importlib
import json
import os
from typing import Dict, List, Sequence, Tuple

from agent.contracts import BaseSkillHandler, SkillHandler
from core.paths import SKILLS_DIR

# 单一事实源：顺序即 tools 列表顺序；Handler 类延迟加载，避免启动时拉满依赖图
SKILL_SPECS: Sequence[Tuple[str, str]] = (
    ("quote", "skills.quote.handler.QuoteHandler"),
    ("compare", "skills.compare.handler.CompareHandler"),
    ("screen", "skills.screen.handler.ScreenHandler"),
    ("signal", "skills.signal.handler.SignalHandler"),
    ("backtest", "skills.backtest.handler.BacktestHandler"),
    ("quant", "quant.skill.handler.QuantHandler"),
    ("kline", "skills.kline.handler.KlineHandler"),
    ("fundamentals", "skills.fundamentals.handler.FundamentalsHandler"),
    ("peer", "skills.peer.handler.PeerHandler"),
    ("index", "skills.index.handler.IndexHandler"),
    ("news", "skills.news.handler.NewsHandler"),
    ("position", "skills.position.handler.PositionHandler"),
    ("advise", "skills.advise.handler.AdviseHandler"),
)

TOOL_NAMES = tuple(name for name, _ in SKILL_SPECS)

_HANDLER_CACHE: Dict[str, SkillHandler] = {}


def _load_handler_class(dotted: str) -> type:
    module_path, class_name = dotted.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if not issubclass(cls, BaseSkillHandler):
        raise TypeError(f"{dotted} 未继承 BaseSkillHandler")
    return cls


def get_handler(name: str) -> SkillHandler:
    if name not in TOOL_NAMES:
        raise KeyError(f"未知工具: {name}")
    if name not in _HANDLER_CACHE:
        dotted = dict(SKILL_SPECS)[name]
        _HANDLER_CACHE[name] = _load_handler_class(dotted)()
    return _HANDLER_CACHE[name]


def create_handlers() -> Dict[str, SkillHandler]:
    return {name: get_handler(name) for name in TOOL_NAMES}


def load_tool_definitions(skills_dir: str = SKILLS_DIR) -> List[dict]:
    """加载 OpenAI tools，并校验 tool_config.name == 注册名。"""
    tools: List[dict] = []
    for name, _ in SKILL_SPECS:
        path = os.path.join(skills_dir, name, "tool_config.json")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"缺少 tool_config: {path}")
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
        cfg_name = config.get("name")
        if cfg_name != name:
            raise ValueError(
                f"tool_config.name 与注册名不一致: 目录/注册={name!r}, config={cfg_name!r}"
            )
        if "parameters" not in config:
            raise ValueError(f"tool_config 缺少 parameters: {path}")
        tools.append({"type": "function", "function": config})
    return tools


def validate_tools(tools: List[dict]) -> None:
    """校验 tool 定义与 SKILL_SPECS 名称一致（不实例化 Handler）。"""
    expected = set(TOOL_NAMES)
    tool_names = {t["function"]["name"] for t in tools}
    if tool_names != expected:
        raise ValueError(
            f"tools 与 SKILL_SPECS 不一致: "
            f"missing={expected - tool_names}, extra={tool_names - expected}"
        )


def validate_registry(
    handlers: Dict[str, SkillHandler],
    tools: List[dict],
) -> None:
    """启动时校验：注册表、handlers、tools 三者一致，且均实现 execute。"""
    expected = set(TOOL_NAMES)
    handler_keys = set(handlers)
    tool_names = {t["function"]["name"] for t in tools}

    if handler_keys != expected:
        raise ValueError(
            f"handlers 与 SKILL_SPECS 不一致: "
            f"missing={expected - handler_keys}, extra={handler_keys - expected}"
        )
    if tool_names != expected:
        raise ValueError(
            f"tools 与 SKILL_SPECS 不一致: "
            f"missing={expected - tool_names}, extra={tool_names - expected}"
        )

    for name, handler in handlers.items():
        if not isinstance(handler, SkillHandler):
            raise TypeError(f"Handler {name!r} 未实现 SkillHandler.execute")
