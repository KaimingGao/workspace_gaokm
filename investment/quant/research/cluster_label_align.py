"""重跑分组时对齐 cluster_id / G 标签到上一版 live 映射（减 ŷ 抖动）。

用 code 重叠最大化做旧组↔新组匹配（Hungarian；无 scipy 则贪心）。
未匹配的新组分配新 id（接在旧 max id 之后）。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


def _parse_g_label(lab: Any) -> Optional[int]:
    """'G3' / 'g3' → 2（0-based cluster_id）；失败 None。"""
    s = str(lab or "").strip()
    if not s:
        return None
    m = re.match(r"^[Gg](\d+)$", s)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return n - 1


def previous_code_cluster_ids(
    active: Optional[Dict[str, Any]],
) -> Dict[str, int]:
    """从 live / 产物 artifact 抽出 code → cluster_id（0-based）。"""
    out: Dict[str, int] = {}
    if not isinstance(active, dict):
        return out
    cmap = active.get("code_map") or {}
    if isinstance(cmap, dict):
        for code, meta in cmap.items():
            c = str(code or "").strip()
            if not c or not isinstance(meta, dict):
                continue
            cid = meta.get("cluster_id")
            try:
                if cid is not None and int(cid) >= 0:
                    out[c] = int(cid)
                    continue
            except (TypeError, ValueError):
                pass
            parsed = _parse_g_label(
                meta.get("cluster_label") or meta.get("label")
            )
            if parsed is not None:
                out[c] = int(parsed)
    # clusters[].members 补洞
    for cl in active.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        cid = cl.get("cluster_id")
        try:
            cid_i = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid_i = None
        if cid_i is None or cid_i < 0:
            parsed = _parse_g_label(cl.get("label") or cl.get("cluster_label"))
            if parsed is None:
                continue
            cid_i = int(parsed)
        for m in cl.get("members") or []:
            code = str(m or "").strip()
            if code and code not in out:
                out[code] = cid_i
    return out


def _overlap_matrix(
    codes: Sequence[str],
    new_labels: np.ndarray,
    prev: Dict[str, int],
) -> Tuple[np.ndarray, List[int], List[int], int]:
    """返回 (overlap[old_idx, new_idx], old_ids, new_ids, n_overlap_codes)。"""
    labs = np.asarray(new_labels, dtype=int)
    new_ids = sorted({int(v) for v in labs if int(v) >= 0})
    old_ids = sorted({int(v) for v in prev.values() if int(v) >= 0})
    if not new_ids or not old_ids:
        return np.zeros((0, 0), dtype=int), old_ids, new_ids, 0
    old_pos = {oid: i for i, oid in enumerate(old_ids)}
    new_pos = {nid: i for i, nid in enumerate(new_ids)}
    mat = np.zeros((len(old_ids), len(new_ids)), dtype=int)
    n_overlap = 0
    for i, code in enumerate(codes):
        c = str(code or "").strip()
        if not c or c not in prev:
            continue
        nid = int(labs[i]) if i < len(labs) else -1
        if nid < 0 or nid not in new_pos:
            continue
        oid = int(prev[c])
        if oid not in old_pos:
            continue
        mat[old_pos[oid], new_pos[nid]] += 1
        n_overlap += 1
    return mat, old_ids, new_ids, n_overlap


def _assign_maximize_overlap(overlap: np.ndarray) -> List[Tuple[int, int]]:
    """行=旧组下标，列=新组下标 → [(row, col), ...] 最大权匹配。"""
    if overlap.size == 0:
        return []
    try:
        from scipy.optimize import linear_sum_assignment  # type: ignore

        # 只在双方都有正重叠的子问题上求最优；全零列/行仍可被匹配但权 0
        cost = -overlap.astype(float)
        ri, cj = linear_sum_assignment(cost)
        pairs = []
        for r, c in zip(ri.tolist(), cj.tolist()):
            if int(overlap[int(r), int(c)]) > 0:
                pairs.append((int(r), int(c)))
        return pairs
    except Exception:
        pass
    # 贪心：按重叠从大到小取互不冲突对
    flat: List[Tuple[int, int, int]] = []
    for i in range(int(overlap.shape[0])):
        for j in range(int(overlap.shape[1])):
            v = int(overlap[i, j])
            if v > 0:
                flat.append((v, i, j))
    flat.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_r: set = set()
    used_c: set = set()
    pairs = []
    for v, i, j in flat:
        if i in used_r or j in used_c:
            continue
        pairs.append((i, j))
        used_r.add(i)
        used_c.add(j)
    return pairs


def align_cluster_labels(
    codes: Sequence[str],
    new_labels: Sequence[int],
    prev_code_to_cluster: Dict[str, int],
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """把 new_labels 对齐到 prev 的 cluster_id 空间。

    返回 (aligned_labels, diagnostics)。无重叠时原样返回（已 0..k-1）。
    """
    labs = np.asarray(new_labels, dtype=int).copy()
    n = int(labs.shape[0])
    empty = {
        "aligned": False,
        "reason": "no_previous",
        "n_codes": n,
        "n_overlap_codes": 0,
        "n_matched_clusters": 0,
        "mapping_new_to_old": {},
        "stability": None,
    }
    if n == 0 or not prev_code_to_cluster:
        return labs, empty

    overlap, old_ids, new_ids, n_overlap = _overlap_matrix(
        codes, labs, prev_code_to_cluster
    )
    if n_overlap < 2 or not old_ids or not new_ids:
        return labs, {
            **empty,
            "reason": "too_few_overlap",
            "n_overlap_codes": int(n_overlap),
            "n_prev_clusters": len(old_ids),
            "n_new_clusters": len(new_ids),
        }

    pairs = _assign_maximize_overlap(overlap)
    # new_id → old_id
    new_to_old: Dict[int, int] = {}
    matched_mass = 0
    for r, c in pairs:
        oid = int(old_ids[r])
        nid = int(new_ids[c])
        new_to_old[nid] = oid
        matched_mass += int(overlap[r, c])

    used_old = set(new_to_old.values())
    next_id = (max(old_ids) + 1) if old_ids else 0
    for nid in new_ids:
        if nid in new_to_old:
            continue
        while next_id in used_old:
            next_id += 1
        new_to_old[nid] = next_id
        used_old.add(next_id)
        next_id += 1

    aligned = np.array(
        [int(new_to_old.get(int(v), int(v))) if int(v) >= 0 else -1 for v in labs],
        dtype=int,
    )
    # 稳定度：重叠票中仍落在「匹配到的旧组」的占比
    stable = 0
    for i, code in enumerate(codes):
        c = str(code or "").strip()
        if not c or c not in prev_code_to_cluster:
            continue
        if int(aligned[i]) == int(prev_code_to_cluster[c]):
            stable += 1
    stability = round(float(stable) / float(n_overlap), 4) if n_overlap else None

    return aligned, {
        "aligned": True,
        "reason": None,
        "n_codes": n,
        "n_overlap_codes": int(n_overlap),
        "n_matched_clusters": int(len(pairs)),
        "n_prev_clusters": len(old_ids),
        "n_new_clusters": len(new_ids),
        "mapping_new_to_old": {str(k): int(v) for k, v in new_to_old.items()},
        "matched_mass": int(matched_mass),
        "stability": stability,
        "method": "hungarian_or_greedy",
        "note": "新组 id 对齐上一版 live；未匹配组分配新 id。G 标签随 cluster_id。",
    }


def pair_label_stability(
    codes: Sequence[str],
    labels_a: Sequence[int],
    labels_b: Sequence[int],
) -> Dict[str, Any]:
    """两套 labels 的重叠稳定度：把 B 对齐到 A 后，同票同组占比。"""
    labs_a = np.asarray(labels_a, dtype=int)
    labs_b = np.asarray(labels_b, dtype=int)
    n = min(len(codes), labs_a.shape[0], labs_b.shape[0])
    if n < 2:
        return {"stability": None, "n_overlap_codes": 0, "aligned": False}
    prev = {
        str(codes[i]).strip(): int(labs_a[i])
        for i in range(n)
        if str(codes[i] or "").strip() and int(labs_a[i]) >= 0
    }
    _aligned, diag = align_cluster_labels(list(codes)[:n], labs_b[:n], prev)
    return {
        "stability": diag.get("stability"),
        "n_overlap_codes": diag.get("n_overlap_codes"),
        "n_matched_clusters": diag.get("n_matched_clusters"),
        "aligned": bool(diag.get("aligned")),
        "reason": diag.get("reason"),
    }


def align_labels_with_active_live(
    codes: Sequence[str],
    new_labels: Sequence[int],
    *,
    active: Optional[Dict[str, Any]] = None,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """读取 active cluster weights（若未传入）并对齐 labels。"""
    art = active
    if art is None:
        try:
            from core.signal.cluster_live import load_active_cluster_weights

            art = load_active_cluster_weights()
        except Exception:
            art = None
    prev = previous_code_cluster_ids(art)
    return align_cluster_labels(codes, new_labels, prev)
