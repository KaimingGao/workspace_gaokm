"""AkShare 进程池：隔离 py_mini_racer 并发 FATAL，解锁批量并发取数。

背景
----
AkShare 底层 py_mini_racer 在同一进程内并发调用会 FATAL 整个进程
（address_pool_manager Check failed），故 core 有全局 RLock 串行化所有调用
（core.data.ak_lock）。这导致 80 票批量取数串行 → rebalance 卡顿。

方案
----
用 ProcessPoolExecutor 把 AkShare 调用隔离到子进程：
- 每个子进程独立 akshare + 独立 RLock，进程间无共享 mini_racer → 不会 FATAL
- 单个 worker FATAL 只影响该子进程，主进程与其他 worker 不受影响
- 单票查询仍走原进程内路径（history.fetch_daily_bars），避免序列化开销

Python 3.8 兼容
----------------
- 不使用 cancel_futures（3.9+）/ max_tasks_per_child（3.11+）
- shutdown(wait=False) 即可
- macOS 默认 spawn，显式 mp_context("spawn") 避免歧义

用法
----
    from adapters.market.ak_worker import batch_fetch_daily_bars

    results = batch_fetch_daily_bars("CN", ["600519", "000858"], limit=120)
    # → {"600519": [...], "000858": [...]}，失败 code 值为 []
"""


import atexit
import logging
import os
import threading
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_DEFAULT_WORKERS = max(1, int(os.environ.get("AK_POOL_WORKERS", "8") or "8"))
_POOL: Optional["AkPool"] = None
_POOL_LOCK = threading.Lock()
_SHUTTING_DOWN = False


# --------------------------------------------------------------------------- #
# worker 函数（顶层 → 可 pickle；子进程入口）
# 失败统一返回 []，避免单票异常拖垮整批
# --------------------------------------------------------------------------- #


def _worker_fetch_a_daily(code: str, limit: int, adjust: str = "qfq") -> List[dict]:
    try:
        from adapters.market.history import fetch_a_daily_bars

        packed = fetch_a_daily_bars(code, limit=limit, adjust=adjust)
        if isinstance(packed, tuple):
            return list(packed[0] or [])
        return list(packed or [])
    except Exception:
        logger.warning("ak_worker a_daily %s failed", code, exc_info=True)
        return []


def _worker_fetch_hk_daily(code: str, limit: int, adjust: str = "qfq") -> List[dict]:
    # adjust 不适用于港股，保留参数以统一调用签名
    try:
        from adapters.market.history import fetch_hk_daily_bars

        return fetch_hk_daily_bars(code, limit=limit)
    except Exception:
        logger.warning("ak_worker hk_daily %s failed", code, exc_info=True)
        return []


def _worker_fetch_us_daily(code: str, limit: int, adjust: str = "qfq") -> List[dict]:
    # adjust 不适用于美股，保留参数以统一调用签名
    try:
        from adapters.market.history import fetch_us_daily_bars

        return fetch_us_daily_bars(code, limit=limit)
    except Exception:
        logger.warning("ak_worker us_daily %s failed", code, exc_info=True)
        return []


def _pick_worker(market: str) -> Callable[..., List[dict]]:
    """按市场选 worker 函数。未知市场回退 A 股 worker。"""
    m = (market or "").upper()
    if m == "HK":
        return _worker_fetch_hk_daily
    if m == "US":
        return _worker_fetch_us_daily
    return _worker_fetch_a_daily


# --------------------------------------------------------------------------- #
# 进程池封装
# --------------------------------------------------------------------------- #


def _new_executor(max_workers: int) -> ProcessPoolExecutor:
    import multiprocessing as mp

    return ProcessPoolExecutor(
        max_workers=max_workers,
        mp_context=mp.get_context("spawn"),
    )


class AkPool:
    """进程池懒加载单例；BrokenProcessPool 自动重建。

    线程安全：submit/map 可被多线程调用；内部用 Lock 保护 executor 生命周期。
    """

    def __init__(self, max_workers: int = _DEFAULT_WORKERS):
        self._max_workers = max(1, int(max_workers))
        self._executor: Optional[ProcessPoolExecutor] = None
        self._lock = threading.Lock()

    def _ensure(self) -> ProcessPoolExecutor:
        if self._executor is not None:
            return self._executor
        with self._lock:
            if self._executor is not None:
                return self._executor
            logger.info("ak_worker init pool workers=%d", self._max_workers)
            self._executor = _new_executor(self._max_workers)
            return self._executor

    def _reset(self) -> None:
        with self._lock:
            if self._executor is None:
                return
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                logger.warning("ak_worker reset shutdown failed", exc_info=True)
            self._executor = None

    def submit(self, fn: Callable, *args: Any, **kwargs: Any) -> Future:
        """提交任务；遇 BrokenProcessPool 自动重建并重试一次。"""
        if _SHUTTING_DOWN:
            raise RuntimeError("ak_worker pool is shutting down")
        for attempt in (0, 1):
            ex = self._ensure()
            try:
                return ex.submit(fn, *args, **kwargs)
            except BrokenProcessPool:
                logger.warning(
                    "ak_worker pool broken (attempt %d), recreating", attempt + 1
                )
                self._reset()
            except Exception:
                logger.warning("ak_worker submit failed", exc_info=True)
                raise
        # 两次都坏：最后一次兜底（不再捕获，让调用方感知）
        return self._ensure().submit(fn, *args, **kwargs)

    def map(
        self,
        fn: Callable,
        items: Sequence[Any],
        *,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> List[Any]:
        """有序并发映射；fn(item, **kwargs) 逐项执行，单项异常返回 None。"""
        items = list(items or [])
        if not items:
            return []
        ex = self._ensure()
        futures = []
        for item in items:
            try:
                futures.append(ex.submit(fn, item, **kwargs))
            except BrokenProcessPool:
                logger.warning("ak_worker map submit broken, recreating")
                self._reset()
                ex = self._ensure()
                futures.append(ex.submit(fn, item, **kwargs))
        results: List[Any] = []
        for i, fut in enumerate(futures):
            try:
                results.append(fut.result(timeout=timeout))
            except Exception:
                logger.warning("ak_worker map item[%s] failed", items[i], exc_info=True)
                results.append(None)
        return results

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is None:
                return
            logger.info("ak_worker shutdown pool")
            try:
                # 3.8: 无 cancel_futures；wait=False 不阻塞退出
                self._executor.shutdown(wait=False)
            except TypeError:
                self._executor.shutdown()
            except Exception:
                logger.warning("ak_worker shutdown failed", exc_info=True)
            self._executor = None


# --------------------------------------------------------------------------- #
# 模块单例 + 生命周期
# --------------------------------------------------------------------------- #


def get_pool() -> AkPool:
    """获取全局进程池单例（懒加载）。"""
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = AkPool(max_workers=_DEFAULT_WORKERS)
    return _POOL


def shutdown_pool() -> None:
    """显式关闭进程池（atexit 会自动调用）。"""
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    if _POOL is not None:
        _POOL.shutdown()


def reap_orphaned_pool_workers(*, dry_run: bool = False) -> int:
    """清理父进程已死、仍挂在 launchd 下的 quantlab multiprocessing 孤儿。

    Web / 单测被 SIGKILL 后，spawn worker 与 resource_tracker 常变成 ppid=1，
    每组约 8 个 × ~30MB，叠多层可达数 GB 交换区。启动时收一遍即可。
    """
    import signal
    import sys

    my_pid = os.getpid()
    root_hint = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    killed = 0
    try:
        import subprocess

        out = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            errors="replace",
        )
    except Exception:  # noqa: BLE001
        logger.debug("reap orphan ps failed", exc_info=True)
        return 0

    targets: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        if pid == my_pid or ppid != 1:
            continue
        if "quantlab" not in cmd and root_hint not in cmd:
            continue
        if "multiprocessing.spawn" not in cmd and "resource_tracker" not in cmd:
            continue
        if sys.executable and sys.executable not in cmd and "python" not in cmd.lower():
            # 允许 .venv/bin/python* 路径
            if "/python" not in cmd:
                continue
        targets.append(pid)

    for pid in targets:
        if dry_run:
            killed += 1
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except ProcessLookupError:
            continue
        except PermissionError:
            logger.debug("reap orphan deny pid=%s", pid)
        except Exception:  # noqa: BLE001
            logger.debug("reap orphan kill pid=%s failed", pid, exc_info=True)
    if killed:
        logger.info(
            "reaped orphan multiprocessing workers count=%s dry_run=%s",
            killed,
            dry_run,
        )
    return killed


atexit.register(shutdown_pool)


# --------------------------------------------------------------------------- #
# 批量便捷接口
# --------------------------------------------------------------------------- #


def batch_fetch_daily_bars(
    market: str,
    codes: Sequence[str],
    *,
    limit: int = 120,
    adjust: str = "qfq",
    timeout: Optional[float] = None,
) -> Dict[str, List[dict]]:
    """批量日线：按市场分发到进程池。

    Parameters
    ----------
    market : "CN" / "HK" / "US"
    codes : 裸代码列表（600519 / 00700 / AAPL）
    limit : 每只拉取的日线根数
    adjust : 复权策略（仅 CN 生效，HK/US 忽略）
    timeout : 单票超时秒数；None 表示不限制

    Returns
    -------
    {code: bars}，失败的 code 值为 []（不抛异常，不拖垮整批）
    """
    codes = list(codes or [])
    if not codes:
        return {}
    worker = _pick_worker(market)
    pool = get_pool()
    futures = {
        code: pool.submit(worker, code, limit, adjust) for code in codes
    }
    out: Dict[str, List[dict]] = {}
    for code, fut in futures.items():
        try:
            out[code] = fut.result(timeout=timeout) or []
        except Exception:
            logger.warning(
                "batch_fetch_daily_bars %s failed", code, exc_info=True
            )
            out[code] = []
    return out
