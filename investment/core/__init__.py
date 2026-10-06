"""领域层：确定性量化逻辑（无 LLM / Handler）。按需 import 子模块，避免包级循环依赖。"""

import logging

logger = logging.getLogger(__name__)
__all__ = [
    "advise",
    "facts",
    "paths",
    "env",
    "stance",
    "store",
]
