"""原子 JSON 写盘（tmp + os.replace）。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
from typing import Any


def atomic_write_json(path: str, data: Any, *, indent: int = 2) -> str:
    """写入 JSON：先写 ``path.tmp`` 再 ``os.replace``，避免半文件被读取。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent, default=str)
    os.replace(tmp, path)
    return path
