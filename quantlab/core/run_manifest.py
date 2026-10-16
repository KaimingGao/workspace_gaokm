"""Compat shim — prefer ``core.platform.run_manifest``."""

from core.platform.run_manifest import *  # noqa: F401, F403
from core.platform.run_manifest import (  # noqa: F401
    MANIFEST_DIR,
    build_run_manifest,
    write_run_manifest,
)
