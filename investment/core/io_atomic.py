"""原子 JSON 写盘（同目录唯一 tmp + os.replace）。"""


import logging

logger = logging.getLogger(__name__)
import json
import os
import tempfile
from typing import Any


def atomic_write_json(path: str, data: Any, *, indent: int = 2) -> str:
    """写入 JSON：同目录 ``mkstemp`` 再 ``os.replace``，避免半文件与并发抢同一 ``.tmp``。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return path
