"""训练 / L1 共用的 bars 指纹。

``PANEL_CACHE_SCHEMA`` 参与 ``make_fingerprint`` 字符串；变更会令 L1 全量 miss。
旧 ``head_key`` / panel 磁盘缓存路径已删除；只留指纹字符串。
"""

from __future__ import annotations

import hashlib
from typing import Any, List, Optional, Sequence

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


def find_bars_suffix_start(
    bars: Optional[Sequence[dict]],
    old_fp: str,
    *,
    hint_lens: Optional[Sequence[int]] = None,
) -> Optional[int]:
    """若 ``old_fp`` 等于 ``bars[i:]`` 的指纹且 ``i>0``，返回 ``i``（前面多出来的历史根数）。

    先试 ``hint_lens`` / 常见 lookback，再全扫。分钟路径由调用方跳过历史扩展。
    """
    target = str(old_fp or "").strip()
    if not target:
        return None
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    n = len(seq)
    if n < 3:
        return None
    crumbs = [_daily_bar_crumb(b) for b in seq]

    def _fp_from(i: int) -> str:
        if i >= n:
            return hashlib.sha1(b"empty").hexdigest()[:16]
        return hashlib.sha1("|".join(crumbs[i:]).encode("utf-8")).hexdigest()[:16]

    tried = set()
    candidates: List[int] = []
    for raw in hint_lens or ():
        try:
            L = int(raw)
        except (TypeError, ValueError):
            continue
        if L > 0:
            candidates.append(L)
    candidates.extend(
        [
            60,
            61,
            62,
            63,
            80,
            120,
            250,
            500,
            750,
            999,
            1000,
            1001,
            n - 1,
        ]
    )
    for L in candidates:
        if L < 2 or L >= n or L in tried:
            continue
        tried.add(L)
        i = n - L
        if i > 0 and _fp_from(i) == target:
            return i
    for i in range(1, n - 1):
        L = n - i
        if L in tried:
            continue
        if _fp_from(i) == target:
            return i
    return None


def make_fingerprint(*, bars_fp: str, **kwargs: Any) -> str:
    # ``ridge_core_only`` remains the on-disk fingerprint field name (L1 lake wire).
    # Callers should pass ``aoo_core_only=...``; we rewrite to the wire key before hash.
    if "aoo_core_only" in kwargs and "ridge_core_only" not in kwargs:
        kwargs = dict(kwargs)
        kwargs["ridge_core_only"] = kwargs.pop("aoo_core_only")
    parts = [f"schema={PANEL_CACHE_SCHEMA}", f"bars={bars_fp}"]
    for key in sorted(kwargs):
        parts.append(f"{key}={kwargs[key]}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]
