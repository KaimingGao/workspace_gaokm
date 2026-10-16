"""Compat shim — prefer ``core.platform.schedule_jobs``."""

from core.platform.schedule_jobs import *  # noqa: F401, F403
from core.platform.schedule_jobs import (  # noqa: F401
    _minute_warmup_core,
    _resolve_warmup_codes,
    _write_last_run,
)
