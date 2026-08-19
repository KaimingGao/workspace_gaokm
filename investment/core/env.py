"""环境变量加载（CLI / Web 共用）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import os
import tempfile
from typing import Dict, Optional

from core.paths import ROOT_DIR

DEFAULT_ENV_PATH = os.path.join(ROOT_DIR, ".env")


def default_env_path() -> str:
    return os.environ.get("INVESTMENT_ENV_FILE", DEFAULT_ENV_PATH)


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


def read_env_file_keys(path: Optional[str] = None) -> Dict[str, str]:
    """只读解析 .env（不写入 os.environ）。"""
    p = path or default_env_path()
    out: Dict[str, str] = {}
    if not os.path.isfile(p):
        return out
    try:
        with open(p, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    out[key] = value
    except OSError:
        return {}
    return out


def read_env_key(key: str, path: Optional[str] = None) -> str:
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return str(read_env_file_keys(path).get(key) or "").strip()


def upsert_env_key(key: str, value: str, path: Optional[str] = None) -> Dict[str, object]:
    """更新或删除 .env 中的 KEY=VALUE；立即同步 os.environ。"""
    k = str(key or "").strip()
    if not k:
        return {"ok": False, "reason": "empty key"}
    p = path or default_env_path()
    val = str(value or "").strip()
    lines: list[str] = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    replaced = False
    kept: list[str] = []
    prefix = f"{k}="
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            kept.append(line)
            continue
        line_key = stripped.split("=", 1)[0].strip()
        if line_key == k:
            replaced = True
            if val:
                kept.append(f"{k}={val}")
            continue
        kept.append(line)
    if val and not replaced:
        if kept and kept[-1].strip():
            kept.append("")
        kept.append(f"{k}={val}")
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    content = "\n".join(kept)
    if content:
        content += "\n"
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=parent or None, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, p)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return {"ok": False, "reason": str(exc), "path": p}
    if val:
        os.environ[k] = val
    else:
        os.environ.pop(k, None)
    return {"ok": True, "path": p, "key": k, "value": val, "replaced": replaced}


def persist_llm_model_to_env(model: str, path: Optional[str] = None) -> Dict[str, object]:
    """平台/Web 保存 LLM 模型名 → investment/.env 的 DASHSCOPE_MODEL。"""
    raw = str(model or "").strip()
    if not raw:
        out = upsert_env_key("DASHSCOPE_MODEL", "", path=path)
        os.environ.pop("DOUBAO_MODEL", None)
        return {**out, "cleared": True}
    if len(raw) > 128 or " " in raw:
        return {"ok": False, "reason": "invalid model name"}
    out = upsert_env_key("DASHSCOPE_MODEL", raw, path=path)
    os.environ.pop("DOUBAO_MODEL", None)
    return out
