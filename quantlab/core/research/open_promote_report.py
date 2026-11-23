"""会话日 open：临时立方晋升终态后的对照摘要。

日线物化会先删掉当天 open 立方再重建。对照副本放在 ``data/run/open_promote``，
不进 DayCube 回收。摘要给交易执行的 09:30 复现；没有早盘分数时不补 ŷ。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 对照副本只服务近端复盘；过期目录可删
_PROMOTE_KEEP_DAYS = 30

_OPEN_NAME_EXACT = {
    "gap_pct",
    "gap_atr",
    "gap_vs_sector",
    "yclose_loc",
}
_OPEN_NAME_PARTS = (
    "GAP",
    "OPEN0",
    "OPEN_IN",
    "ABOVE_YHIGH",
    "BELOW_YLOW",
    "NEAR_UPLIMIT",
    "NEAR_DNLIMIT",
    "YCLOSE_LOC",
)


def _promote_root() -> str:
    from core.paths import DATA_DIR

    return os.path.join(DATA_DIR, "run", "open_promote")


def _day_dir(day: str) -> str:
    return os.path.join(_promote_root(), str(day)[:10])


def _is_z_name(name: str) -> bool:
    text = str(name or "")
    return text.startswith("z_") or text.startswith("zτ")


def is_open_column(name: str) -> bool:
    """用到今天开盘价的列。截面 z 列另计，不进今开中位差。"""
    text = str(name or "")
    if _is_z_name(text):
        low = text.lower()
        return "gap" in low or "yclose" in low or "open" in low
    if text in _OPEN_NAME_EXACT:
        return True
    return any(part in text for part in _OPEN_NAME_PARTS)


def is_hist_column(name: str) -> bool:
    """只用 T−1 及更早的原始列。截面 z 随成员变，不进历史中位差。"""
    text = str(name or "")
    if not text or _is_z_name(text):
        return False
    return not is_open_column(text)


def _session_day() -> str:
    try:
        from core.signal.session_pit import oo_cycle_date

        day = str(oo_cycle_date() or "")[:10]
    except Exception:  # noqa: BLE001
        logger.debug("open promote session day failed", exc_info=True)
        return ""
    return day if len(day) >= 10 else ""


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _cube_matrix(path: str) -> Optional[Tuple[List[str], List[str], np.ndarray]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with np.load(path, allow_pickle=False) as blob:
            codes = [str(c) for c in blob["codes"].tolist()]
            names = [str(n) for n in blob["names"].tolist()]
            matrix = np.asarray(blob["X_raw"], dtype=np.float64)
    except Exception:  # noqa: BLE001
        logger.debug("open promote cube read failed %s", path, exc_info=True)
        return None
    if matrix.ndim != 2:
        return None
    return codes, names, matrix


def _paired(
    codes_a: Sequence[str],
    names_a: Sequence[str],
    matrix_a: np.ndarray,
    codes_b: Sequence[str],
    names_b: Sequence[str],
    matrix_b: np.ndarray,
    name: str,
    shared: Sequence[str],
) -> Optional[np.ndarray]:
    if name not in names_a or name not in names_b:
        return None
    ja = list(names_a).index(name)
    jb = list(names_b).index(name)
    ia = {str(c): i for i, c in enumerate(codes_a)}
    ib = {str(c): i for i, c in enumerate(codes_b)}
    vals: List[float] = []
    for code in shared:
        i = ia.get(code)
        k = ib.get(code)
        if i is None or k is None:
            continue
        if i >= matrix_a.shape[0] or k >= matrix_b.shape[0]:
            continue
        if ja >= matrix_a.shape[1] or jb >= matrix_b.shape[1]:
            continue
        left = float(matrix_a[i, ja])
        right = float(matrix_b[k, jb])
        if left == left and right == right:
            vals.append(abs(left - right))
    if len(vals) < 2:
        return None
    return np.asarray(vals, dtype=np.float64)


def _percentile(vals: np.ndarray, q: float) -> float:
    if vals.size == 0:
        return 0.0
    return float(np.percentile(vals, q))


def compare_open_matrices(
    before_codes: Sequence[str],
    before_names: Sequence[str],
    before_x: np.ndarray,
    after_codes: Sequence[str],
    after_names: Sequence[str],
    after_x: np.ndarray,
) -> Dict[str, Any]:
    """临时立方对终态立方。今开差用 gap_pct（百分点 → bp）。"""
    b_codes = [str(c) for c in before_codes if str(c)]
    a_codes = [str(c) for c in after_codes if str(c)]
    b_set = set(b_codes)
    a_set = set(a_codes)
    shared = [c for c in b_codes if c in a_set]
    out: Dict[str, Any] = {
        "only_morning": len(b_set - a_set),
        "only_final": len(a_set - b_set),
        "n_shared": len(shared),
    }
    gap = _paired(
        b_codes, before_names, before_x, a_codes, after_names, after_x, "gap_pct", shared
    )
    scale = 100.0  # gap_pct 是百分点，1 百分点 = 100bp
    if gap is None:
        gap = _paired(
            b_codes,
            before_names,
            before_x,
            a_codes,
            after_names,
            after_x,
            "raw_aoo_GAP",
            shared,
        )
        scale = 10000.0  # 收益小数
    if gap is not None:
        bp = gap * scale
        out["open_median_bp"] = round(_percentile(bp, 50), 4)
        out["open_p95_bp"] = round(_percentile(bp, 95), 4)
    hist_medians: List[float] = []
    names = [n for n in before_names if n in set(after_names) and is_hist_column(n)]
    for name in names:
        paired = _paired(
            b_codes, before_names, before_x, a_codes, after_names, after_x, name, shared
        )
        if paired is None:
            continue
        hist_medians.append(float(np.median(paired)))
    if hist_medians:
        out["hist_median"] = float(np.median(np.asarray(hist_medians, dtype=np.float64)))
    return out


def _fmt_bp(value: float) -> str:
    num = abs(float(value))
    if num < 0.05:
        return "0bp"
    if num < 10:
        text = f"{num:.1f}".rstrip("0").rstrip(".")
        return f"{text}bp"
    return f"{num:.0f}bp"


def _fmt_pct(value: float) -> str:
    num = abs(float(value))
    if num < 1:
        return f"{num:.2f}%"
    return f"{num:.1f}%"


def _fmt_hist(value: float) -> str:
    num = abs(float(value))
    if num < 1e-8:
        return "0"
    return f"{num:.3g}"


def format_open_promote_line(summary: Optional[Dict[str, Any]]) -> str:
    """09:30 复现上的一段。缺的段不补 0。"""
    doc = summary if isinstance(summary, dict) else {}
    bits: List[str] = []
    if doc.get("open_median_bp") is not None:
        text = f"今开列中位差 {_fmt_bp(float(doc['open_median_bp']))}"
        p95 = doc.get("open_p95_bp")
        med = float(doc["open_median_bp"])
        if p95 is not None and float(p95) >= 1 and float(p95) > med + 0.05:
            text += f"（95% {_fmt_bp(float(p95))}）"
        bits.append(text)
    if doc.get("hist_median") is not None:
        bits.append(f"历史列中位差 {_fmt_hist(float(doc['hist_median']))}")
    if doc.get("only_morning") is not None and doc.get("only_final") is not None:
        morning = int(doc["only_morning"])
        final = int(doc["only_final"])
        if morning == 0 and final == 0:
            bits.append("股票集合相同")
        else:
            bits.append(f"只在早盘 {morning}")
            bits.append(f"只在终态 {final}")
    if doc.get("y_mae_pct") is not None:
        bits.append(f"ŷ 平均差 {_fmt_pct(float(doc['y_mae_pct']))}")
    if doc.get("rank_corr") is not None:
        bits.append(f"名次相关 {float(doc['rank_corr']):.2f}")
    if doc.get("threshold_cross") is not None:
        bits.append(f"跨门槛 {int(doc['threshold_cross'])}")
    return " · ".join(bits)


def _needs_hold(meta: Optional[Dict[str, Any]]) -> bool:
    from core.research.pit_session import meta_needs_lake_rebuild

    return bool(meta) and meta_needs_lake_rebuild(meta)


def _copy_morning_ranks(day: str) -> None:
    dest = os.path.join(_day_dir(day), "morning_ranks.json")
    if os.path.isfile(dest):
        return
    try:
        from core.paper.rebalance.watching_matrix import _live_rank_snapshot_path
    except Exception:  # noqa: BLE001
        logger.debug("open promote rank path failed", exc_info=True)
        return
    src = _live_rank_snapshot_path()
    snap = _read_json(src)
    if not snap or str(snap.get("as_of") or "")[:10] != str(day)[:10]:
        return
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
    except OSError:
        logger.debug("open promote copy ranks failed", exc_info=True)


def hold_provisional_opens(day: Optional[str] = None) -> int:
    """把仍是临时/旧口径的会话日 open 立方抄到 run 目录。已有副本不覆盖。"""
    day_s = str(day or _session_day() or "")[:10]
    if len(day_s) < 10:
        return 0
    # L1 已留过则 L2 再调直接返回，避免扫全库哈希
    day_root = _day_dir(day_s)
    if os.path.isdir(day_root):
        try:
            prior = sum(
                1
                for uh in os.listdir(day_root)
                if uh
                and not uh.startswith(".")
                and os.path.isfile(os.path.join(day_root, uh, "before.npz"))
            )
        except OSError:
            prior = 0
        if prior:
            return prior
    try:
        from core.research.day_cube import _root
        from core.research.feature_registry import FEATURE_SCHEMA
    except Exception:  # noqa: BLE001
        logger.debug("open promote hold imports failed", exc_info=True)
        return 0
    schema_root = os.path.join(_root(), f"v{FEATURE_SCHEMA}")
    if not os.path.isdir(schema_root):
        return 0
    kept = 0
    try:
        hashes = os.listdir(schema_root)
    except OSError:
        return 0
    for uh in hashes:
        if not uh or uh.startswith("."):
            continue
        src_dir = os.path.join(schema_root, uh, "open")
        meta_path = os.path.join(src_dir, f"{day_s}.meta.json")
        npz_path = os.path.join(src_dir, f"{day_s}.npz")
        if not os.path.isfile(meta_path) or not os.path.isfile(npz_path):
            continue
        meta = _read_json(meta_path)
        if not _needs_hold(meta):
            continue
        dest_dir = os.path.join(_day_dir(day_s), uh)
        dest_npz = os.path.join(dest_dir, "before.npz")
        if os.path.isfile(dest_npz):
            kept += 1
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copyfile(npz_path, dest_npz)
            shutil.copyfile(meta_path, os.path.join(dest_dir, "before.meta.json"))
        except OSError:
            logger.warning("open promote hold failed day=%s uh=%s", day_s, uh, exc_info=True)
            continue
        kept += 1
    if kept:
        _copy_morning_ranks(day_s)
    return kept


def _hash_for_codes(codes: Sequence[str]) -> str:
    from core.research.feature_registry import universe_hash

    listed = [str(c).strip() for c in codes if str(c or "").strip()]
    return universe_hash(listed)


def _summary_path(day: str, uh: str) -> str:
    return os.path.join(_day_dir(day), uh, "summary.json")


def _save_summary(day: str, uh: str, summary: Dict[str, Any]) -> Dict[str, Any]:
    summary = dict(summary)
    summary["day"] = str(day)[:10]
    summary["universe_hash"] = uh
    summary["line"] = format_open_promote_line(summary)
    _write_json(_summary_path(day, uh), summary)
    return summary


def finish_open_promote(day: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """用留下的临时立方和盘上终态立方写特征摘要。没有副本则不动。"""
    day_s = str(day or _session_day() or "")[:10]
    if len(day_s) < 10:
        return None
    folder = _day_dir(day_s)
    if not os.path.isdir(folder):
        return None
    try:
        from core.research.day_cube import _root
        from core.research.feature_registry import FEATURE_SCHEMA
    except Exception:  # noqa: BLE001
        logger.debug("open promote finish imports failed", exc_info=True)
        return None
    schema_root = os.path.join(_root(), f"v{FEATURE_SCHEMA}")
    written: Optional[Dict[str, Any]] = None
    try:
        hashes = os.listdir(folder)
    except OSError:
        return None
    for uh in hashes:
        before = os.path.join(folder, uh, "before.npz")
        if not os.path.isfile(before):
            continue
        after = os.path.join(schema_root, uh, "open", f"{day_s}.npz")
        left = _cube_matrix(before)
        right = _cube_matrix(after)
        if left is None or right is None:
            continue
        try:
            from core.research.pit_session import meta_is_final_open

            meta = _read_json(os.path.join(schema_root, uh, "open", f"{day_s}.meta.json"))
            if meta is not None and not meta_is_final_open(meta):
                continue
        except Exception:  # noqa: BLE001
            logger.debug("open promote final check failed", exc_info=True)
        summary = compare_open_matrices(*left, *right)
        prev = _read_json(_summary_path(day_s, uh)) or {}
        for key in ("y_mae_pct", "rank_corr", "threshold_cross"):
            if prev.get(key) is not None and summary.get(key) is None:
                summary[key] = prev[key]
        written = _save_summary(day_s, uh, summary)
    try:
        prune_old_open_promotes(keep_days=_PROMOTE_KEEP_DAYS, keep_day=day_s)
    except Exception:  # noqa: BLE001
        logger.debug("open promote prune failed", exc_info=True)
    return written


def prune_old_open_promotes(
    *,
    keep_days: int = _PROMOTE_KEEP_DAYS,
    keep_day: Optional[str] = None,
) -> int:
    """删掉过期的 ``open_promote/{day}/``；``keep_day`` 当天始终保留。"""
    root = _promote_root()
    if not os.path.isdir(root):
        return 0
    try:
        days = int(keep_days)
    except (TypeError, ValueError):
        days = _PROMOTE_KEEP_DAYS
    if days < 1:
        days = _PROMOTE_KEEP_DAYS
    keep = str(keep_day or "")[:10]
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        day = str(name or "")[:10]
        if len(day) < 10 or day == keep:
            continue
        if day >= cutoff:
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError:
            logger.debug("open promote rmtree failed day=%s", day, exc_info=True)
    return removed


def _iter_summaries(day: str) -> List[Tuple[str, Dict[str, Any]]]:
    folder = _day_dir(day)
    if not os.path.isdir(folder):
        return []
    found: List[Tuple[str, Dict[str, Any]]] = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for uh in names:
        doc = _read_json(_summary_path(day, uh))
        if doc:
            found.append((uh, doc))
    return found


def summary_for_codes(day: str, codes: Sequence[str]) -> Optional[Dict[str, Any]]:
    day_s = str(day or "")[:10]
    if len(day_s) < 10:
        return None
    found = _iter_summaries(day_s)
    if not found:
        return None
    if codes:
        uh = _hash_for_codes(codes)
        for key, doc in found:
            if key == uh:
                return doc
    if len(found) == 1:
        return found[0][1]
    return None


def _score_of(row: Dict[str, Any]) -> Optional[float]:
    for key in ("y_oo_fuse", "y_oo"):
        try:
            val = row.get(key)
            if val is None or val == "":
                continue
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num == num:
            return num
    return None


def _as_fraction(values: Sequence[float]) -> bool:
    """fuse 门槛是收益小数。ŷ 若已经是百分点，中位绝对值通常大于 0.5。"""
    if not values:
        return True
    med = float(np.median(np.abs(np.asarray(values, dtype=np.float64))))
    return med < 0.5


def _corr(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    a = np.asarray(left, dtype=np.float64)
    b = np.asarray(right, dtype=np.float64)
    if a.size < 3:
        return None
    a = a - float(a.mean())
    b = b - float(b.mean())
    den = float(np.sqrt(np.sum(a * a) * np.sum(b * b)))
    if den < 1e-12:
        return None
    return float(np.sum(a * b) / den)


def score_delta(
    morning_by_code: Dict[str, Any],
    final_items: Sequence[dict],
    *,
    fuse_enter: float = 0.0,
) -> Dict[str, Any]:
    """早盘快照对终态出分。分数和名次都缺时返回空。"""
    morning = morning_by_code if isinstance(morning_by_code, dict) else {}
    final: Dict[str, Dict[str, Any]] = {}
    for it in final_items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if code:
            final[code] = it
    y_m: List[float] = []
    y_f: List[float] = []
    rank_m: List[float] = []
    rank_f: List[float] = []
    cross = 0
    paired_y = 0
    try:
        thresh = float(fuse_enter or 0.0)
    except (TypeError, ValueError):
        thresh = 0.0
    raw_scores: List[float] = []
    for code, row in morning.items():
        if not isinstance(row, dict) or code not in final:
            continue
        left = _score_of(row)
        right = _score_of(final[code])
        if left is not None and right is not None:
            raw_scores.append(left)
            raw_scores.append(right)
    fraction = _as_fraction(raw_scores)
    gate = thresh if fraction else thresh * 100.0
    for code, row in morning.items():
        if not isinstance(row, dict) or code not in final:
            continue
        left = _score_of(row)
        right = _score_of(final[code])
        if left is not None and right is not None:
            y_m.append(left)
            y_f.append(right)
            paired_y += 1
            if (left > gate) != (right > gate):
                cross += 1
        try:
            rm = row.get("y_oo_rank")
            rf = final[code].get("y_oo_rank")
            if rm is not None and rf is not None:
                rank_m.append(float(rm))
                rank_f.append(float(rf))
        except (TypeError, ValueError):
            continue
    out: Dict[str, Any] = {}
    if paired_y >= 2:
        diff = np.abs(np.asarray(y_m) - np.asarray(y_f))
        mae = float(diff.mean())
        out["y_mae_pct"] = round(mae * (100.0 if fraction else 1.0), 4)
        out["threshold_cross"] = int(cross)
    corr = _corr(rank_m, rank_f)
    if corr is not None:
        out["rank_corr"] = round(corr, 4)
    return out


def apply_score_delta(
    day: str,
    codes: Sequence[str],
    items: Sequence[dict],
    fuse_enter: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """09:30 复现时，用早盘名次快照补上 ŷ / 名次 / 跨门槛。"""
    day_s = str(day or "")[:10]
    snap = _read_json(os.path.join(_day_dir(day_s), "morning_ranks.json"))
    if not snap:
        return summary_for_codes(day_s, codes)
    by_code = snap.get("by_code") if isinstance(snap.get("by_code"), dict) else {}
    if not by_code:
        return summary_for_codes(day_s, codes)
    fp = str(snap.get("universe_fp") or "")
    if fp and codes:
        try:
            from core.paper.rebalance.watching_matrix import live_rank_universe_fp

            if live_rank_universe_fp(codes) != fp:
                return summary_for_codes(day_s, codes)
        except Exception:  # noqa: BLE001
            logger.debug("open promote universe fp failed", exc_info=True)
    uh = _hash_for_codes(codes) if codes else ""
    found = _iter_summaries(day_s)
    target = uh if any(key == uh for key, _doc in found) else (found[0][0] if len(found) == 1 else "")
    if not target:
        return None
    base = _read_json(_summary_path(day_s, target)) or {}
    extra = score_delta(by_code, items, fuse_enter=fuse_enter)
    if not extra:
        return base or None
    base.update(extra)
    return _save_summary(day_s, target, base)


def promote_line_for_codes(day: str, codes: Sequence[str]) -> str:
    return format_open_promote_line(summary_for_codes(day, codes))


__all__ = [
    "apply_score_delta",
    "compare_open_matrices",
    "finish_open_promote",
    "format_open_promote_line",
    "hold_provisional_opens",
    "is_hist_column",
    "is_open_column",
    "promote_line_for_codes",
    "prune_old_open_promotes",
    "score_delta",
    "summary_for_codes",
]
