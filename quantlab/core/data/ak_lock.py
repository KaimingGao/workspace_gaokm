"""Serialize all AkShare calls.

AkShare (East Money) uses py_mini_racer. Concurrent MiniRacer init/eval
can FATAL the whole process (`address_pool_manager Check failed`), which
leaves the Web UI stuck on loading with no response.

Call ``install_akshare_lock()`` once at process start (also safe to call
lazily before first AkShare use).

AkShare 进程内串行锁，canonical location。

获取锁带超时：某次远端挂死后，后续调用不再无限等锁（日线/分钟 Job 会「卡住」）。
挂死线程仍可能占着锁；超时后本进程内后续 AkShare 会快速失败，需重启 Web 清残线程。
"""

import logging
import os
import threading
import types
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

_AKSHARE_LOCK = threading.RLock()
_INSTALLED = False

# 等锁上限（秒）；``INVESTMENT_AK_LOCK_TIMEOUT_SEC=0`` 关闭（恢复无限等）
_DEFAULT_LOCK_TIMEOUT_SEC = 90.0


def _lock_acquire_timeout_sec() -> Optional[float]:
    raw = os.environ.get("INVESTMENT_AK_LOCK_TIMEOUT_SEC", str(_DEFAULT_LOCK_TIMEOUT_SEC))
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float(_DEFAULT_LOCK_TIMEOUT_SEC)
    if v <= 0:
        return None
    return max(1.0, min(v, 600.0))


def _acquire_ak_lock(*, timeout_sec: Optional[float] = None) -> None:
    lim = _lock_acquire_timeout_sec() if timeout_sec is None else timeout_sec
    if lim is None:
        _AKSHARE_LOCK.acquire()
        return
    if not _AKSHARE_LOCK.acquire(timeout=float(lim)):
        raise TimeoutError(f"akshare lock busy after {lim:.0f}s")


@contextmanager
def akshare_lock(*, timeout_sec: Optional[float] = None) -> Iterator[None]:
    _acquire_ak_lock(timeout_sec=timeout_sec)
    try:
        yield
    finally:
        _AKSHARE_LOCK.release()


def _wrap_callable(fn: Callable[..., Any], name: str) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with akshare_lock():
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
            logger.exception('unexpected error in install_akshare_lock')
            return False

        for name in dir(ak):
            if name.startswith("_"):
                continue
            try:
                obj = getattr(ak, name)
            except Exception:
                logger.exception('unexpected error in install_akshare_lock')
                continue
            if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
                setattr(ak, name, _wrap_callable(obj, name))
            elif callable(obj) and not isinstance(obj, type):
                try:
                    setattr(ak, name, _wrap_callable(obj, name))
                except Exception:
                    logger.exception('unexpected error in install_akshare_lock')
        _INSTALLED = True
        return True


def import_akshare():
    """Import akshare after ensuring the process-wide lock patch is installed."""
    install_akshare_lock()
    import akshare as ak

    return ak
