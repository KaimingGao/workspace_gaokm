"""原子 JSON 文档读写门面。"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from core.io_atomic import atomic_write_json as _atomic_write_json


def atomic_write_json(path: str, data: Any, *, indent: int = 2) -> str:
    """写入 JSON：委托 ``core.io_atomic.atomic_write_json``。"""
    return _atomic_write_json(path, data, indent=indent)


def read_json_file(path: str, *, default: Any = None) -> Any:
    """读 JSON 文件；不存在或损坏时返回 ``default``（默认 ``None``）。"""
    if not path or not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def read_json_object(path: str) -> Optional[dict]:
    """读 JSON 对象；非对象或失败返回 ``None``。"""
    data = read_json_file(path)
    return data if isinstance(data, dict) else None
