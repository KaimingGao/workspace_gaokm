"""特征 z-score。

``feature_zscore`` 仍表示入模前做了标准化。``zscore_scope=cross_section`` 时
按决策日截面算 μ/σ（当天各股票），不把整段训练窗的均值和标准差套到每一天。
没有日期时仍可用训练窗列向 μ/σ（旧路径）。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

DEFAULT_FEATURE_ZSCORE = True
ZSCORE_SCOPE_CROSS_SECTION = "cross_section"
# 排序头截面 z 恒用整观察池（与主头一致）。旧落盘 ``tier_a`` 仅作训练宇宙兼容读。
ZSCORE_UNIVERSE_POOL = "pool"
ZSCORE_UNIVERSE_TIER_A = "tier_a"  # 历史别名；打分侧不再缩 z
# 排序头训练/损失宇宙（可与 z 宇宙分离）
TRAIN_UNIVERSE_POOL = "pool"
TRAIN_UNIVERSE_TIER_A = "tier_a"


def resolve_feature_zscore(
    *sources: Any,
    feature_zscore: Optional[bool] = None,
    default: bool = DEFAULT_FEATURE_ZSCORE,
) -> bool:
    """是否启用特征 z-score。显式参数优先于落盘字段。"""
    if feature_zscore is not None:
        return bool(feature_zscore)
    for src in sources:
        if not isinstance(src, Mapping):
            continue
        if "feature_zscore" in src and src["feature_zscore"] is not None:
            return bool(src["feature_zscore"])
        hp = src.get("hyperparams")
        if (
            isinstance(hp, Mapping)
            and "feature_zscore" in hp
            and hp["feature_zscore"] is not None
        ):
            return bool(hp["feature_zscore"])
    return bool(default)


def annotate_feature_zscore(dest: dict, enabled: bool) -> dict:
    """在报告 / hyperparams / 模型 dict 上写入 ``feature_zscore``。"""
    dest["feature_zscore"] = bool(enabled)
    return dest


def normalize_score_codes(score_codes: Optional[Sequence[str]]) -> Optional[set]:
    """None 表示全员打分。名单非空时只给这些代码出 ŷ 和名次。

    主头与排序头：截面 z 都用调用方传入的整批原始特征，不随这份名单缩小。
    """
    if score_codes is None:
        return None
    return {str(c).strip() for c in score_codes if str(c or "").strip()}


def normalize_zscore_universe(raw: Any) -> str:
    """截面 z 宇宙。现行恒为 ``pool``；读到旧 ``tier_a`` 仍归并成 pool（打分不再缩 z）。"""
    _ = raw
    return ZSCORE_UNIVERSE_POOL


def normalize_train_universe(raw: Any) -> str:
    """训练/损失宇宙：``pool`` 或 ``tier_a``。"""
    s = str(raw or "").strip().lower().replace("-", "_")
    if s in ("tier_a", "tier", "a", "tiera"):
        return TRAIN_UNIVERSE_TIER_A
    return TRAIN_UNIVERSE_POOL


def is_tier_a_zscore_universe(*sources: Any) -> bool:
    """兼容旧名：现表示「训练宇宙是否 A」，不再表示缩 z。

    优先读 ``train_universe``；否则回落旧 ``zscore_universe=tier_a``。
    """
    return is_tier_a_train_universe(*sources)


def is_tier_a_train_universe(*sources: Any) -> bool:
    """任一来源标记训练宇宙为 A 档。"""
    for src in sources:
        if src is None:
            continue
        if isinstance(src, Mapping):
            if "train_universe" in src:
                if normalize_train_universe(src.get("train_universe")) == (
                    TRAIN_UNIVERSE_TIER_A
                ):
                    return True
                continue
            # 旧落盘：zscore_universe=tier_a 曾绑 z+训；现只当训练 A 审计
            if normalize_train_universe(src.get("zscore_universe")) == (
                TRAIN_UNIVERSE_TIER_A
            ):
                return True
            hp = src.get("hyperparams")
            if isinstance(hp, Mapping):
                if "train_universe" in hp:
                    if normalize_train_universe(hp.get("train_universe")) == (
                        TRAIN_UNIVERSE_TIER_A
                    ):
                        return True
                elif normalize_train_universe(hp.get("zscore_universe")) == (
                    TRAIN_UNIVERSE_TIER_A
                ):
                    return True
            inner = src.get("return_model")
            if isinstance(inner, Mapping) and inner is not src:
                if is_tier_a_train_universe(inner):
                    return True
        else:
            tu = getattr(src, "train_universe", None)
            if tu is not None:
                if normalize_train_universe(tu) == TRAIN_UNIVERSE_TIER_A:
                    return True
            elif normalize_train_universe(
                getattr(src, "zscore_universe", None)
            ) == TRAIN_UNIVERSE_TIER_A:
                return True
    return False


def stamp_zscore_universe(
    dest: dict,
    universe: Any = ZSCORE_UNIVERSE_POOL,
    *,
    tier_codes: Optional[Sequence[str]] = None,
    tier_meta: Optional[Mapping[str, Any]] = None,
) -> dict:
    """写入排序头截面 z 宇宙（恒 ``pool``）。``tier_*`` 留给 train 戳。"""
    _ = universe
    dest["zscore_universe"] = ZSCORE_UNIVERSE_POOL
    dest.pop("zscore_universe_tiers", None)
    return dest


def stamp_train_universe(
    dest: dict,
    universe: Any,
    *,
    tier_codes: Optional[Sequence[str]] = None,
    tier_meta: Optional[Mapping[str, Any]] = None,
) -> dict:
    """写入排序头训练宇宙；``tier_a`` 时带冻结 A 名单。"""
    uni = normalize_train_universe(universe)
    dest["train_universe"] = uni
    # 截面 z 与训练解耦后恒 pool
    dest["zscore_universe"] = ZSCORE_UNIVERSE_POOL
    dest.pop("zscore_universe_tiers", None)
    if uni == TRAIN_UNIVERSE_TIER_A:
        dest["train_universe_tiers"] = ["A"]
        codes = sorted(
            {str(c).strip() for c in (tier_codes or []) if str(c or "").strip()}
        )
        if codes:
            dest["tier_codes"] = codes
            dest["n_tier_codes"] = len(codes)
        if isinstance(tier_meta, Mapping) and tier_meta:
            for k in (
                "tier_as_of",
                "tier_as_of_last",
                "holdout_n",
                "tier_n",
                "tier_fingerprint",
            ):
                if k in tier_meta and tier_meta.get(k) is not None:
                    dest[k] = tier_meta.get(k)
    else:
        dest.pop("train_universe_tiers", None)
    return dest


def is_cross_section_zscore(doc: Any) -> bool:
    """模型是否按当天截面标准化（推理不再套冻住的训练窗 μ/σ）。"""
    if doc is None:
        return False
    if not isinstance(doc, Mapping):
        return str(getattr(doc, "zscore_scope", "") or "") == ZSCORE_SCOPE_CROSS_SECTION
    if str(doc.get("zscore_scope") or "") == ZSCORE_SCOPE_CROSS_SECTION:
        return True
    hp = doc.get("hyperparams")
    if isinstance(hp, Mapping) and str(hp.get("zscore_scope") or "") == ZSCORE_SCOPE_CROSS_SECTION:
        return True
    inner = doc.get("return_model")
    if isinstance(inner, Mapping) and inner is not doc:
        return is_cross_section_zscore(inner)
    return False


def stamp_cross_section_zscore(dest: dict) -> dict:
    """标记截面标准化，并清掉训练窗 μ/σ，避免推理再套一层。"""
    dest["feature_zscore"] = True
    dest["zscore_scope"] = ZSCORE_SCOPE_CROSS_SECTION
    dest["zscore_means"] = {}
    dest["zscore_stds"] = {}
    return dest


def normalize_cs_group_key(group: Any) -> str:
    """截面分组键：纯日期 → ``YYYY-MM-DD``；``date|τ`` 保留钟（PIT，禁止截成仅日期）。"""
    s = str(group or "").strip()
    if not s:
        return ""
    if "|" in s:
        left, _, right = s.partition("|")
        d = str(left or "")[:10]
        clock = str(right or "").strip()
        if len(d) >= 10 and clock:
            if clock.lower() in ("open", "09:30", "0930"):
                return d
            return f"{d}|{clock[:5] if ':' in clock else clock}"
        return d
    return s[:10]


def cs_group_key(date: Any, tau: Any = None) -> str:
    """``(date, τ)`` → 分组键；无钟或开盘钟 → 仅日期。"""
    d = str(date or "")[:10]
    t = str(tau or "").strip()
    if not t or t.lower() in ("open", "09:30", "0930"):
        return d
    if ":" in t:
        t = t[:5]
    return normalize_cs_group_key(f"{d}|{t}")


def dates_for_complete_rows(
    row_dates: Optional[Sequence[Any]],
    complete_idx: Sequence[Any],
) -> Optional[List[str]]:
    """完整行在原矩阵中的下标 → 决策日。对不齐则放弃截面标准化。"""
    if not row_dates or not complete_idx:
        return None
    try:
        idxs = [int(i) for i in complete_idx]
    except (TypeError, ValueError):
        return None
    if not idxs or max(idxs) >= len(row_dates) or min(idxs) < 0:
        return None
    return [normalize_cs_group_key(row_dates[i]) for i in idxs]


def cross_section_zscore_matrix(
    x: Any,
    groups: Sequence[Any],
    *,
    min_names: int = 2,
    eps: float = 1e-12,
) -> Any:
    """按组对每列做总体 z-score。组可为交易日或 ``date|τ``（PIT）。

    组内有效样本少于 ``min_names``，或标准差接近 0 时，有效值记 0（落在当天均值上）。
    """
    import numpy as np

    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 2:
        return arr
    out = np.array(arr, copy=True)
    n, p = out.shape
    if n == 0 or p == 0 or len(groups) != n:
        return out
    buckets: Dict[str, List[int]] = {}
    for i, g in enumerate(groups):
        buckets.setdefault(normalize_cs_group_key(g), []).append(i)
    need = max(1, int(min_names))
    for idxs in buckets.values():
        ix = np.asarray(idxs, dtype=np.int64)
        for j in range(p):
            col = out[ix, j]
            finite = np.isfinite(col)
            m = int(finite.sum())
            if m < need:
                if m:
                    out[ix[finite], j] = 0.0
                continue
            vals = col[finite]
            mu = float(vals.mean())
            var = float(np.mean((vals - mu) ** 2))
            if (not math.isfinite(var)) or var <= eps:
                out[ix[finite], j] = 0.0
                continue
            sd = math.sqrt(var)
            if sd < 1e-12:
                out[ix[finite], j] = 0.0
            else:
                out[ix[finite], j] = (vals - mu) / sd
    return out


# 截面 z 后把值同步到裸名/canon，避免后续 stamp_*（裸名优先）冲掉 z-score。
_Z_ALIAS_PREFIXES = ("z_τh_", "z_τc_", "z_co_", "z_oo_")


def mirror_z_alias_value(row: Dict[str, Any], name: str, val: Any) -> None:
    """把截面 z 写到 ``name``，并同步裸名 / 已有 canon，防后续 stamp 冲掉。"""
    row[name] = val
    for p in _Z_ALIAS_PREFIXES:
        if name.startswith(p):
            bare = name[len(p) :]
            if bare:
                row[bare] = val
            return
    # 裸名入模：若行里已有任一 canon，一并写上
    for p in _Z_ALIAS_PREFIXES:
        canon = f"{p}{name}"
        if canon in row:
            row[canon] = val


def cross_section_zscore_dicts(
    rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    groups: Optional[Sequence[Any]] = None,
) -> List[dict]:
    """对一组特征 dict 做截面 z-score。缺测键保持缺测。``groups`` 缺省时视为同一天。"""
    import numpy as np

    keys = [str(n) for n in names]
    n = len(rows)
    if groups is None:
        groups = ["_"] * n
    mat = np.full((n, len(keys)), np.nan, dtype=np.float64)
    for i, row in enumerate(rows):
        src = row if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            v = src.get(name)
            if v is None or v == "":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                mat[i, j] = fv
    z = cross_section_zscore_matrix(mat, groups)
    out: List[dict] = []
    for i, row in enumerate(rows):
        src = dict(row) if isinstance(row, Mapping) else {}
        for j, name in enumerate(keys):
            val = float(z[i, j])
            if math.isfinite(val):
                mirror_z_alias_value(src, name, val)
        out.append(src)
    return out
