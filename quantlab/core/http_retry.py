"""出站 HTTP 轻量重试 / 退避（无第三方依赖）。

用于行情与公开源：瞬时超时、连接错误可重试；4xx（除 429）不重试。
"""

import random
import time
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

# 默认可重试的异常类型名（避免强依赖 requests）
_RETRY_NAME_FRAGMENTS = (
    "timeout",
    "timed out",
    "connection",
    "temporarily unavailable",
    "503",
    "502",
    "429",
    "reset by peer",
    "remote end closed",
    "aborted",
    "disconnected",
)


def _is_retryable(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    msg = str(exc or "").lower()
    if "timeout" in name or "connection" in name:
        return True
    return any(frag in msg for frag in _RETRY_NAME_FRAGMENTS)


def call_with_retry(
    fn: Callable[..., T],
    *args: Any,
    retries: int = 2,
    base_delay_sec: float = 0.4,
    max_delay_sec: float = 4.0,
    retryable: Optional[Callable[[BaseException], bool]] = None,
    **kwargs: Any,
) -> T:
    """执行 ``fn``；失败时指数退避重试 ``retries`` 次（总尝试 = 1+retries）。"""
    check = retryable or _is_retryable
    attempts = max(0, int(retries)) + 1
    last: Optional[BaseException] = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except BaseException as e:
            last = e
            if i >= attempts - 1 or not check(e):
                raise
            delay = min(max_delay_sec, base_delay_sec * (2**i))
            delay *= 0.8 + random.random() * 0.4
            time.sleep(delay)
    assert last is not None
    raise last


def requests_get_with_retry(url: str, *, retries: int = 2, **kwargs: Any) -> Any:
    """``requests.get`` 包装：注入默认 timeout，瞬时失败退避重试。"""
    import requests

    kwargs.setdefault("timeout", 10)

    def _once():
        resp = requests.get(url, **kwargs)
        # 429 / 5xx 视为可重试
        if resp.status_code == 429 or resp.status_code >= 500:
            raise requests.HTTPError(
                f"{resp.status_code} for url: {url}", response=resp
            )
        return resp

    return call_with_retry(_once, retries=retries)
