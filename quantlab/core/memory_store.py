"""Compat shim — prefer ``core.platform.memory_store``."""

from core.platform.memory_store import *  # noqa: F401, F403
from core.platform.memory_store import (  # noqa: F401
    DEFAULT_LLM_MODEL,
    HORIZON_MAX,
    HORIZON_MIN,
    clamp_horizon_days,
    effective_preferences,
    normalize_llm_model,
    read_memory,
    resolve_llm_model_config,
    write_memory,
)
