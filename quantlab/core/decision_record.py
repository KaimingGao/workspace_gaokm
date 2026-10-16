"""Compat shim — prefer ``core.platform.decision_record``."""

from core.platform.decision_record import *  # noqa: F401, F403
from core.platform.decision_record import (  # noqa: F401
    append_decision,
    build_decision_record,
    clear_decisions,
    list_decisions,
    record_from_advice,
)
