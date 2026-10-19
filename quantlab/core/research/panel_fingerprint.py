"""训练 / L1 共用的 bars 指纹与逻辑 head_key。

``PANEL_CACHE_SCHEMA`` 参与 ``make_fingerprint`` 字符串；变更会令 L1 全量 miss。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Optional, Sequence

# 特征注册 / collect_* 语义变更时人工 +1 → 全池 fingerprint miss
# v2：τc 改为矩阵+slim；v3：指纹覆盖窗内每一根价量
PANEL_CACHE_SCHEMA = 3


def _daily_bar_crumb(bar: dict) -> str:
    day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
    return (
        f"{day}|{bar.get('open')}|{bar.get('high')}|{bar.get('low')}|"
        f"{bar.get('close')}|{bar.get('volume')}"
    )


def _minute_bar_crumb(bar: dict) -> str:
    ts = str(bar.get("datetime") or bar.get("date") or "")[:19]
    return (
        f"{ts}|{bar.get('open')}|{bar.get('high')}|{bar.get('low')}|"
        f"{bar.get('close')}|{bar.get('volume')}"
    )


def bars_fingerprint(
    bars: Optional[Sequence[dict]],
    *,
    minute_bars: Optional[Sequence[dict]] = None,
) -> str:
    """窗内每一根价量；中间一根变化也会 miss。空序列为 empty。"""
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    parts = [_daily_bar_crumb(b) for b in seq] if seq else ["empty"]
    mb = [b for b in (minute_bars or []) if isinstance(b, dict)]
    if mb:
        parts.extend(_minute_bar_crumb(b) for b in mb)
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def make_fingerprint(*, bars_fp: str, **kwargs: Any) -> str:
    parts = [f"schema={PANEL_CACHE_SCHEMA}", f"bars={bars_fp}"]
    for key in sorted(kwargs):
        parts.append(f"{key}={kwargs[key]}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]


def head_relpath(head: str, *parts: str) -> str:
    """逻辑 head 路径（如 ``oo/h1_core0``）；调试 / 键标识用。"""
    safe_parts = []
    for p in parts:
        s = str(p).strip().replace(os.sep, "_").replace("/", "_").replace("..", "_")
        safe_parts.append(s or "_")
    head_s = str(head or "x").strip().replace(os.sep, "_") or "x"
    return os.path.join(head_s, *safe_parts) if safe_parts else head_s


def oo_head_key(*, horizon_days: int, ridge_core_only: bool) -> str:
    return head_relpath("oo", f"h{int(horizon_days)}_core{1 if ridge_core_only else 0}")


def co_head_key(*, min_history: int, include_alpha_oo: bool) -> str:
    return head_relpath(
        "co", f"mh{int(min_history)}_aoo{1 if include_alpha_oo else 0}"
    )


def _tau_cache_leaf(
    *,
    tau_hm: str,
    min_history: int,
    include_alpha_tc: bool,
    use_minute: bool,
) -> str:
    hm = "".join(ch for ch in str(tau_hm or "open") if ch.isalnum() or ch in "_-") or "open"
    return (
        f"hm_{hm}_mh{int(min_history)}_atc{1 if include_alpha_tc else 0}"
        f"_m{1 if use_minute else 0}"
    )


def tc_head_key(
    *,
    tau_hm: str,
    min_history: int,
    include_alpha_tc: bool,
    use_minute: bool,
) -> str:
    return head_relpath(
        "tc",
        _tau_cache_leaf(
            tau_hm=tau_hm,
            min_history=min_history,
            include_alpha_tc=include_alpha_tc,
            use_minute=use_minute,
        ),
    )


def tc_rank_head_key(
    *,
    tau_hm: str,
    min_history: int,
    include_alpha_tc: bool,
    use_minute: bool,
) -> str:
    """ŷ_τc_rank 逻辑键；与 ŷ_τc 同叶子名、不同 head，避免互相覆盖。"""
    return head_relpath(
        "tc_rank",
        _tau_cache_leaf(
            tau_hm=tau_hm,
            min_history=min_history,
            include_alpha_tc=include_alpha_tc,
            use_minute=use_minute,
        ),
    )
