"""环境变量加载（CLI / Web 共用）。"""

from __future__ import annotations

import os


def load_env_file(path: str) -> bool:
    """加载 .env；优先 python-dotenv，否则简易解析；不覆盖已有环境变量。"""
    if not os.path.isfile(path):
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(path)
        return True
    except ImportError:
        pass

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        return True
    except OSError:
        return False
