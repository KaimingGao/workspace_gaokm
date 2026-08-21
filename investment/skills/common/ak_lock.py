"""Backward-compatible shim. Canonical location: core.data.ak_lock"""
from core.data.ak_lock import (  # noqa: F401
    akshare_lock,
    import_akshare,
    install_akshare_lock,
)
