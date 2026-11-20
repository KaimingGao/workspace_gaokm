"""观察池容量与请求钳制常量。"""

from typing import Any, Optional

WATCHING_MIN_SIZE = 300
WATCHING_DEFAULT_SIZE = 300
WATCHING_MAX_SIZE = 1600
# ŷ_* 拟合请求钳制（观察池上限）；不垫 research_universe。分钟暖仓 / live 只读观察池。
MODEL_FIT_MAX_SIZE = 1600
# 与 watching.max_size 对齐，避免日报截断与满池历史回测不可比。
DAILY_PORTFOLIO_MAX_NAMES = WATCHING_MAX_SIZE


def clamp_watching_max_size(value: Any, default: int = WATCHING_DEFAULT_SIZE) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(WATCHING_MIN_SIZE, min(n, WATCHING_MAX_SIZE))


def watching_pool_limit(requested: Optional[int] = None, *, file_cap: bool = True) -> int:
    """研究/拟合/补齐用的观察池只数：min(请求, 文件 max_size, 硬顶)。

    ``file_cap=False`` 时不看观察池滑条，只受硬顶限制。显式成分名单走这条。
    """
    import logging

    logger = logging.getLogger(__name__)
    file_size = WATCHING_DEFAULT_SIZE
    try:
        from core.watching.repository import read_watching

        file_size = clamp_watching_max_size(
            (read_watching() or {}).get("max_size"),
            WATCHING_DEFAULT_SIZE,
        )
    except Exception:
        logger.debug("watching_pool_limit: read_watching failed", exc_info=True)
    hard = min(int(WATCHING_MAX_SIZE), int(MODEL_FIT_MAX_SIZE))
    if requested is None:
        n = file_size
    else:
        try:
            n = int(requested)
        except (TypeError, ValueError):
            n = file_size
    if not file_cap:
        return max(3, min(n, hard))
    return max(3, min(n, file_size, hard))
