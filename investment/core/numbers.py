"""数值/时间解析工具（跨模块共用）。

P798+ 集中原分散在各模块的私有工具函数：
- to_float      （原 _to_float / _f 私有函数统一）
- date_key      （原 _date_key 私有函数统一）
- now_iso_utc   （原 _iso_now 私有函数统一）
- now_iso_local （原 _now_iso(seconds) 私有函数统一）
- calc_sma      （原 _calc_ma 私有函数统一）
"""


import logging

logger = logging.getLogger(__name__)
import math
from datetime import datetime, timezone
from typing import Any, Optional, Sequence


def to_float(v: Any) -> Optional[float]:
    """安全解析 float（拒 None / 异常 / NaN / ±inf）。

    吸收历史各模块 _to_float / _f 的差异：
    - 基础类型容错（int/float/str/bool/None）
    - NaN 拒绝（原 skills/common/* 版本）
    - ±inf 拒绝（原 core/score_ledger.py 版本）
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or math.isinf(f):
        return None
    return f


def date_key(raw: Any) -> str:
    """归一化日期键：接受 datetime / ISO / YYYY-MM-DD，输出 YYYY-MM-DD 或空串。

    原分散于：core/score_ledger.py · core/market_calendar.py · core/fundamentals_pit.py
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if "T" in s:
        s = s.split("T", 1)[0]
    return s


def now_iso_utc() -> str:
    """UTC ISO 8601 紧凑时间戳（带 Z 后缀）。

    原分散于：quant/research/cluster_pool_artifact.py · core/live_config_manifest.py
              core/signal/cluster_live.py · core/signal/cluster_pointer.py
              core/signal/cluster_live_audit.py
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso_local(*, timespec: str = "seconds") -> str:
    """本地时区 ISO 8601 时间戳（默认 seconds 精度）。

    注意：paper.py 的账本写入使用 milliseconds 精度，需显式传 timespec。

    原分散于：core/run_manifest.py · core/watching_store.py · core/strategy.py
              core/t0/rules.py · services/eval_service.py
    """
    return datetime.now().isoformat(timespec=timespec)


def calc_sma(closes: Sequence[float], period: int) -> Optional[float]:
    """简单移动均线（Simple Moving Average）。

    原分散于：core/signal/factors/ma_slope.py · core/signal/factors/technical_pattern.py
    """
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period
