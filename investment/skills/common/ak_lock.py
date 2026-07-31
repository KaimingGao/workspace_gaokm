"""Serialize all AkShare calls.

AkShare (East Money) uses py_mini_racer. Concurrent MiniRacer init/eval
can FATAL the whole process (`address_pool_manager Check failed`), which
leaves the Web UI stuck on loading with no response.

Call ``install_akshare_lock()`` once at process start (also safe to call
lazily before first AkShare use).
"""

from __future__ import annotations

import threading
import types
from contextlib import contextmanager
from typing import Any, Callable, Iterator

_AKSHARE_LOCK = threading.RLock()
_INSTALLED = False


@contextmanager
def akshare_lock() -> Iterator[None]:
    with _AKSHARE_LOCK:
        yield


def _wrap_callable(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with _AKSHARE_LOCK:
            return fn(*args, **kwargs)

    wrapped.__name__ = getattr(fn, "__name__", name)
    wrapped.__doc__ = getattr(fn, "__doc__", None)
    return wrapped


def install_akshare_lock() -> bool:
    """Patch public callables on the akshare module so all API use is serialized."""
    global _INSTALLED
    with _AKSHARE_LOCK:
        if _INSTALLED:
            return True
        try:
            import akshare as ak
        except Exception:
            return False

        for name in dir(ak):
            if name.startswith("_"):
                continue
            try:
                obj = getattr(ak, name)
            except Exception:
                continue
            if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
                setattr(ak, name, _wrap_callable(obj, name))
            elif callable(obj) and not isinstance(obj, type):
                # module-level helpers / partial-like callables
                try:
                    setattr(ak, name, _wrap_callable(obj, name))
                except Exception:
                    pass
        _INSTALLED = True
        return True


def import_akshare():
    """Import akshare after ensuring the process-wide lock patch is installed."""
    install_akshare_lock()
    import akshare as ak

    return ak
