"""长期偏好 Memory（短对话仍由 Agent messages / session 承担）。

``effective_preferences`` 供研究枢纽 / 回测等读取已钳制默认值；
``horizon_days`` 驱动 IC · OLS · Top-K 请求默认（页内可临时覆盖）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import json
import os
import time
from typing import Any, Dict, Optional

from core.paths import MEMORY_PATH
from core.io_atomic import atomic_write_json

# 与 quant schemas（IC/OLS/回测）对齐：1～10 交易日
HORIZON_MIN = 1
HORIZON_MAX = 10
_RISK_STYLES = frozenset({"conservative", "balanced", "aggressive"})
DEFAULT_LLM_MODEL = "qwen-plus"
_LLM_MODEL_MAX_LEN = 128

_DEFAULT: Dict[str, Any] = {
    "version": 1,
    "preferences": {
        "risk_style": "balanced",  # conservative | balanced | aggressive
        "horizon_days": 3,
        "focus_markets": ["CN"],
        "llm_model": "",
        "notes": "",
    },
    "updated_at": None,
}


def _env_llm_model() -> str:
    from core.env import read_env_key

    for name in ("DASHSCOPE_MODEL", "DOUBAO_MODEL"):
        raw = read_env_key(name)
        if raw:
            return raw
    return ""


def normalize_llm_model(value: Any, *, default: str = DEFAULT_LLM_MODEL) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    if len(raw) > _LLM_MODEL_MAX_LEN or " " in raw:
        return default
    return raw


def resolve_llm_model_config(
    *, explicit: Optional[str] = None, path: Optional[str] = None
) -> Dict[str, str]:
    """生效 LLM 模型：构造参数 > 环境变量 > memory.json > 默认。"""
    if explicit is not None and str(explicit).strip():
        model = normalize_llm_model(explicit)
        return {"llm_model": model, "llm_model_source": "arg", "llm_model_saved": ""}
    env_model = _env_llm_model()
    mem = read_memory(path)
    saved = str((mem.get("preferences") or {}).get("llm_model") or "").strip()
    if env_model:
        return {
            "llm_model": normalize_llm_model(env_model),
            "llm_model_source": "env",
            "llm_model_saved": env_model,
        }
    if saved:
        return {
            "llm_model": normalize_llm_model(saved),
            "llm_model_source": "memory",
            "llm_model_saved": saved,
        }
    return {
        "llm_model": DEFAULT_LLM_MODEL,
        "llm_model_source": "default",
        "llm_model_saved": "",
    }


def clamp_horizon_days(value: Any, *, default: int = 3) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(HORIZON_MIN, min(HORIZON_MAX, n))


def effective_preferences(path: Optional[str] = None) -> Dict[str, Any]:
    """只读生效偏好（已钳制），供研究/回测默认值。"""
    mem = read_memory(path)
    prefs = dict(mem.get("preferences") or {})
    risk = str(prefs.get("risk_style") or "balanced").strip().lower()
    if risk not in _RISK_STYLES:
        risk = "balanced"
    saved_model = str(prefs.get("llm_model") or "").strip()
    llm_cfg = resolve_llm_model_config(path=path)
    return {
        "risk_style": risk,
        "horizon_days": clamp_horizon_days(prefs.get("horizon_days"), default=3),
        "focus_markets": list(prefs.get("focus_markets") or ["CN"]),
        "llm_model": llm_cfg["llm_model"],
        "llm_model_saved": llm_cfg.get("llm_model_saved") or saved_model or "",
        "llm_model_source": llm_cfg["llm_model_source"],
        "notes": str(prefs.get("notes") or ""),
        "memory_exists": bool(mem.get("exists")),
        "updated_at": mem.get("updated_at"),
    }


def _ensure_parent(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def read_memory(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or MEMORY_PATH
    if not os.path.isfile(p):
        out = json.loads(json.dumps(_DEFAULT))
        out["path"] = p
        out["exists"] = False
        return out
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
    prefs = data.get("preferences") if isinstance(data.get("preferences"), dict) else {}
    merged = json.loads(json.dumps(_DEFAULT))
    merged["preferences"].update(prefs)
    merged["updated_at"] = data.get("updated_at")
    merged["path"] = p
    merged["exists"] = True
    return merged


def write_memory(preferences: Dict[str, Any], path: Optional[str] = None) -> Dict[str, Any]:
    p = path or MEMORY_PATH
    current = read_memory(p)
    prefs = dict(current.get("preferences") or {})
    for k, v in (preferences or {}).items():
        if k == "horizon_days":
            prefs[k] = clamp_horizon_days(v, default=int(prefs.get("horizon_days") or 3))
        elif k == "risk_style":
            rs = str(v or "balanced").strip().lower()
            prefs[k] = rs if rs in _RISK_STYLES else "balanced"
        elif k == "llm_model":
            from core.env import persist_llm_model_to_env

            raw = str(v or "").strip()
            env_out = persist_llm_model_to_env(raw)
            if not env_out.get("ok") and raw:
                prefs[k] = str(prefs.get("llm_model") or "")
            else:
                prefs[k] = ""
        else:
            prefs[k] = v
    payload = {
        "version": 1,
        "preferences": prefs,
        "updated_at": time.time(),
    }
    _ensure_parent(p)
    atomic_write_json(p, payload)
    out = read_memory(p)
    out["ok"] = True
    out["effective"] = effective_preferences(p)
    return out
