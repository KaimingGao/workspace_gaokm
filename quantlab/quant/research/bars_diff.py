"""日线写入前后的差异。

刷新前先抽出观察池前复权的收盘和成交量，写完再比一次，摘要落到
``bars_refresh_diff.json``，研究枢纽日线区读这份摘要。

增量只补缺日时，重叠日应收盘不动（多半是「只补尾」）；价变 / 量纲成片出现，
说明走了整窗替换（强更、短仓或前复权重锚），旧价已被新序列盖掉。
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# 与前复权重叠日重锚同一量级：绝对 1 分，或相对 1e-4
_CLOSE_ABS = 0.01
_CLOSE_REL = 1e-4
# 成交量中位倍数离开这个区间，视为手 / 股不是同一单位
_VOL_LO = 1.0 / 1.5
_VOL_HI = 1.5
_TOP_N = 12

Series = Dict[str, Tuple[float, float]]


def _cn_bare(codes: Sequence[str]) -> List[str]:
    from core.market import resolve_market_code

    out: List[str] = []
    seen = set()
    for raw in codes or ():
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            market, bare = resolve_market_code(text)
        except Exception:  # noqa: BLE001
            market, bare = "CN", text
        bare = str(bare or "").strip()
        if market != "CN" or not (bare.isdigit() and len(bare) == 6):
            continue
        if bare in seen:
            continue
        seen.add(bare)
        out.append(bare)
    return out


def load_cn_qfq_series(codes: Sequence[str]) -> Optional[Dict[str, Series]]:
    """抽出 A 股前复权的 ``date → (close, volume)``。非 sqlite 仓返回 None。"""
    from core.store import bars_backend, get_store_dir

    if bars_backend() != "sqlite":
        return None
    bare = _cn_bare(codes)
    out: Dict[str, Series] = {code: {} for code in bare}
    if not bare:
        return out
    from core.store_bars_sqlite import get_read_conn

    conn = get_read_conn(get_store_dir())
    step = 200
    for i in range(0, len(bare), step):
        chunk = bare[i : i + step]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""
            SELECT code, date, close, volume
            FROM daily_bars
            WHERE market='CN' AND adjust_policy='qfq' AND code IN ({marks})
            """,
            tuple(chunk),
        )
        for row in rows:
            code = str(row["code"] or "")
            day = str(row["date"] or "")[:10]
            book = out.get(code)
            if book is None or len(day) < 10:
                continue
            try:
                close = float(row["close"])
                volume = float(row["volume"] or 0.0)
            except (TypeError, ValueError):
                continue
            book[day] = (close, volume)
    return out


def _close_moved(old: float, new: float) -> bool:
    if abs(new - old) > _CLOSE_ABS:
        return True
    if old != 0 and abs(new / old - 1.0) > _CLOSE_REL:
        return True
    return False


def _one(code: str, before: Series, after: Series) -> Dict[str, Any]:
    days = sorted(set(before) | set(after))
    overlap = [d for d in days if d in before and d in after]
    added = [d for d in days if d in after and d not in before]
    removed = [d for d in days if d in before and d not in after]
    max_abs = 0.0
    max_rel = 0.0
    price = False
    ratios: List[float] = []
    for day in overlap:
        old_c, old_v = before[day]
        new_c, new_v = after[day]
        delta = abs(new_c - old_c)
        if delta > max_abs:
            max_abs = delta
        if old_c != 0:
            rel = abs(new_c / old_c - 1.0)
            if rel > max_rel:
                max_rel = rel
        if _close_moved(old_c, new_c):
            price = True
        if old_v > 0 and new_v > 0:
            ratios.append(new_v / old_v)
    vol_ratio: Optional[float] = None
    volume = False
    if ratios:
        vol_ratio = float(statistics.median(ratios))
        volume = vol_ratio >= _VOL_HI or vol_ratio <= _VOL_LO
    if not before and after:
        tags = ["new"]
    elif before and not after:
        tags = ["gone"]
    else:
        tags = []
        if price:
            tags.append("price")
        if volume:
            tags.append("volume")
        if not price and not volume and (added or removed):
            tags.append("tail")
        if not tags and overlap:
            tags.append("unchanged")
    return {
        "code": code,
        "overlap_days": len(overlap),
        "added_days": len(added),
        "removed_days": len(removed),
        "max_close_abs": round(max_abs, 4),
        "max_close_rel": round(max_rel, 6),
        "vol_ratio": None if vol_ratio is None else round(vol_ratio, 4),
        "tags": tags,
    }


def diff_daily_series(
    before: Dict[str, Series],
    after: Dict[str, Series],
    *,
    mode: str,
    universe_count: int,
) -> Dict[str, Any]:
    """比较两份 ``code → date → (close, volume)``，给出计数和差异最大的几只。"""
    codes = sorted(set(before) | set(after))
    rows = [_one(code, before.get(code) or {}, after.get(code) or {}) for code in codes]
    price_n = sum(1 for row in rows if "price" in row["tags"])
    volume_n = sum(1 for row in rows if "volume" in row["tags"])
    tail_n = sum(1 for row in rows if "tail" in row["tags"])
    unchanged_n = sum(1 for row in rows if "unchanged" in row["tags"])
    new_n = sum(1 for row in rows if "new" in row["tags"])
    gone_n = sum(1 for row in rows if "gone" in row["tags"])

    def _rank(row: Dict[str, Any]) -> Tuple[int, float, float]:
        tags = row["tags"]
        if "price" in tags or "volume" in tags:
            band = 0
        elif "tail" in tags or "new" in tags or "gone" in tags:
            band = 1
        else:
            band = 2
        rel = float(row.get("max_close_rel") or 0.0)
        ratio = row.get("vol_ratio")
        vol_dist = 0.0
        if isinstance(ratio, (int, float)) and ratio > 0:
            vol_dist = abs(float(ratio) - 1.0)
        return (band, -rel, -vol_dist)

    interesting = [row for row in rows if "unchanged" not in row["tags"]]
    interesting.sort(key=_rank)
    label = "强更日 K" if str(mode) == "full" else "增量补齐"
    headline = (
        f"{label} · 比较 {len(rows)} 只"
        f" · 价变 {price_n} · 量纲 {volume_n}"
        f" · 只补尾 {tail_n} · 未改 {unchanged_n}"
    )
    notes: List[str] = []
    if price_n:
        notes.append("收盘成片移动时，旧仓多半是未复权，新仓是前复权。除权日之后两边会重新对齐。")
    if volume_n:
        notes.append("成交量比离开 1（常见约 100 或 0.01）是手和股接在一起。")
    if tail_n and not price_n and not volume_n:
        notes.append("重叠日的收盘和成交量没动，只多了末尾日期。历史段还是写入前的那一份。")
    if not notes and not interesting:
        notes.append("重叠日的收盘和成交量没有超过阈值的差异。")
    worst_rel = max((float(row["max_close_rel"] or 0.0) for row in rows), default=0.0)
    return {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "full" if str(mode) == "full" else "topup",
        "universe_count": int(universe_count or 0),
        "compared": len(rows),
        "price_count": price_n,
        "volume_count": volume_n,
        "tail_count": tail_n,
        "unchanged_count": unchanged_n,
        "new_count": new_n,
        "gone_count": gone_n,
        "worst_close_rel": round(worst_rel, 6),
        "headline": headline,
        "note": " ".join(notes),
        "rows": interesting[:_TOP_N],
    }


def write_bars_refresh_diff(diff: Dict[str, Any]) -> None:
    from core.io_atomic import atomic_write_json
    from core.paths import BARS_REFRESH_DIFF_PATH

    atomic_write_json(BARS_REFRESH_DIFF_PATH, diff)


def read_bars_refresh_diff() -> Optional[Dict[str, Any]]:
    from core.persistence.json_doc import read_json_file
    from core.paths import BARS_REFRESH_DIFF_PATH

    try:
        data = read_json_file(BARS_REFRESH_DIFF_PATH, default=None)
    except Exception:  # noqa: BLE001
        logger.debug("read bars refresh diff failed", exc_info=True)
        return None
    if not isinstance(data, dict) or not data.get("headline"):
        return None
    return data
