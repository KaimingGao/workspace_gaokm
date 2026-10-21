"""涨跌份额恒等式：SUMP+SUMN≡1、VSUMP+VSUMN≡1。

截面去均值后 Z(SUMN)=−Z(SUMP)，两列进 Ridge（keep_all）会把不可识别分量
显示成 |β| 垄断；若打分误用 raw×β，更会把全体 ŷ 抬成常数偏置。

分层策略（非一刀切）：
- L1 / 算子（alpha_tc、alpha_oo 等）仍算全 SUMP/SUMN/SUMD（及 V 版），供树模型等使用
- Ridge / 线性截面拟合与预测：丢掉 SUMP/SUMN（及 V 对），只保留差分 SUMD/VSUMD
- 打分侧另拒 raw 份额误用与截面批塌缩
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# 窗长后缀：PSUMP5 / OSUMP5 / raw_atc_PSUMN20 / raw_aoo_OVSUMP10 …
_SUM_PAIR_RE = re.compile(
    r"^(?:raw_a(?:tc|oo|th)_)?(?P<body>[OP]?(?:V)?SUM)(?P<side>[PND])(?P<win>\d+)$",
    re.IGNORECASE,
)

_REDUNDANT_SIDES = frozenset({"P", "N"})

# 截面 z 后 E[ŷ]≈intercept；池内塌缩到远离 intercept 的常数 → 多半误喂 raw
_COLLAPSE_MIN_N = 8
_COLLAPSE_MAX_STD = 0.15
_COLLAPSE_MEAN_GAP = 1.5  # 百分点


def is_redundant_sum_share_name(name: str) -> bool:
    """是否为应剔除的 SUMP/SUMN（或 VSUMP/VSUMN）列名。"""
    m = _SUM_PAIR_RE.match(str(name or "").strip())
    if not m:
        return False
    return m.group("side").upper() in _REDUNDANT_SIDES


def finite_coef(beta: Any) -> Optional[float]:
    """入模系数：None / 非有限 → None。"""
    if beta is None:
        return None
    try:
        v = float(beta)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def drop_redundant_sum_share_names(
    names: Sequence[str],
) -> Tuple[List[str], List[str]]:
    """从入模名列表去掉 SUMP/SUMN（及 VSUM 对）；返回 (kept, dropped)。"""
    kept: List[str] = []
    dropped: List[str] = []
    for n in names:
        s = str(n or "")
        if is_redundant_sum_share_name(s):
            dropped.append(s)
        else:
            kept.append(s)
    return kept, dropped


def filter_predict_active_names(
    names: Iterable[str],
    coefs: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    """预测用 active：丢掉 SUMP/SUMN（旧模系数仍可能带），可选再要求 coef 有限。"""
    out: List[str] = []
    for n in names:
        s = str(n or "")
        if not s or is_redundant_sum_share_name(s):
            continue
        if coefs is not None and finite_coef(coefs.get(s)) is None:
            continue
        out.append(s)
    return out


def omit_redundant_from_coef_map(
    coef_map: Mapping[str, Any],
    excluded: Sequence[str],
) -> Dict[str, Optional[float]]:
    """写回 coefficients：恒等对只进 excluded，不占 β=None 槽（避免误扫/误拒）。"""
    out: Dict[str, Optional[float]] = {}
    for k, v in dict(coef_map).items():
        s = str(k)
        if is_redundant_sum_share_name(s):
            continue
        out[s] = finite_coef(v)
    for name in excluded:
        s = str(name or "")
        if not s or is_redundant_sum_share_name(s):
            continue
        out.setdefault(s, None)
    return out


def prep_meta_excluded(meta: Mapping[str, Any]) -> List[str]:
    """``_prepare_complete_*`` 各类剔除列合并（含恒等对）。"""
    return (
        list(meta.get("dropped_sparse") or [])
        + list(meta.get("dropped_constant") or [])
        + list(meta.get("dropped_for_coverage") or [])
        + list(meta.get("dropped_sum_identity") or [])
    )


def _pair_keys_from_name(name: str) -> Optional[Tuple[str, str, str]]:
    """由任一 SUMP/SUMN/SUMD 名还原 (P键, N键, D键)。"""
    raw = str(name or "").strip()
    m = _SUM_PAIR_RE.match(raw)
    if not m:
        return None
    body = m.group("body")
    win = m.group("win")
    prefix = raw[: m.start("body")]
    p_key = f"{prefix}{body}P{win}"
    n_key = f"{prefix}{body}N{win}"
    d_key = f"{prefix}{body}D{win}"
    return p_key, n_key, d_key


def _finite_float(row: Mapping[str, Any], key: str) -> Optional[float]:
    if key not in row:
        return None
    try:
        v = float(row.get(key))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _in_unit_interval(v: float, *, atol: float) -> bool:
    return -atol <= v <= 1.0 + atol


def coef_names_for_raw_identity(
    coefs: Optional[Mapping[str, Any]],
) -> List[str]:
    """参与 raw 份额检测的列：β 有限的入模名。

    剔除后 β 写成 None、或根本不在系数里的 SUMP/SUMN，仍会留在面板行上。
    那些列不入点积，不能当成「整行漏做截面 z」。
    """
    out: List[str] = []
    if not isinstance(coefs, Mapping):
        return out
    for name, beta in coefs.items():
        s = str(name or "")
        if not s or finite_coef(beta) is None:
            continue
        out.append(s)
    return out


def row_has_raw_sum_share_identity(
    row: Optional[Mapping[str, Any]],
    *,
    atol: float = 1e-3,
    relevant_names: Optional[Iterable[str]] = None,
) -> bool:
    """行内若存在未截面化的涨跌份额，视为 raw 误用。

    检测：
    1. SUMP+SUMN≈1 且两者∈[0,1]
    2. 仅有 P+D 或 N+D：raw 下 SUMD=2P−1=1−2N（截面 Z 后不成立）

    ``relevant_names`` 给定时，只认其中的份额列。面板仍会带上未入模的
    SUMP/SUMN；份额本身不在入模名里时，不能因 P+N≡1 拒掉整行预测。
    P+D / N+D 也只在份额列入模时检查，避免用残留 raw P 去对已经 z 过的 SUMD。
    """
    if not isinstance(row, Mapping) or not row:
        return False
    relevant: Optional[set] = None
    if relevant_names is not None:
        relevant = {str(n) for n in relevant_names if str(n or "")}

    def _share_in_scope(key: str) -> bool:
        return relevant is None or key in relevant

    seen: set = set()
    for key in row.keys():
        triple = _pair_keys_from_name(str(key))
        if triple is None:
            continue
        p_key, n_key, d_key = triple
        tag = (p_key, n_key)
        if tag in seen:
            continue
        seen.add(tag)
        pv = _finite_float(row, p_key)
        nv = _finite_float(row, n_key)
        dv = _finite_float(row, d_key)
        share_in_scope = _share_in_scope(p_key) or _share_in_scope(n_key)
        if (
            share_in_scope
            and pv is not None
            and nv is not None
            and _in_unit_interval(pv, atol=atol)
            and _in_unit_interval(nv, atol=atol)
            and abs(pv + nv - 1.0) <= atol
        ):
            return True
        # P+D：raw 时 D=2P−1；Z 后 Z_D≈2 Z_P 且 P 不再是份额。
        # 份额列未入模时不拿残留 raw P 对 SUMD（z 后 D≈0 且 P≈0.5 会误伤）。
        if (
            _share_in_scope(p_key)
            and pv is not None
            and dv is not None
            and _in_unit_interval(pv, atol=atol)
            and abs(dv - (2.0 * pv - 1.0)) <= atol
        ):
            return True
        if (
            _share_in_scope(n_key)
            and nv is not None
            and dv is not None
            and _in_unit_interval(nv, atol=atol)
            and abs(dv - (1.0 - 2.0 * nv)) <= atol
        ):
            return True
    return False


def cs_batch_preds_look_collapsed(
    preds: Sequence[Optional[float]],
    *,
    intercept: float = 0.0,
    min_n: int = _COLLAPSE_MIN_N,
    max_std: float = _COLLAPSE_MAX_STD,
    mean_gap: float = _COLLAPSE_MEAN_GAP,
) -> bool:
    """截面批预测是否塌缩成远离 intercept 的近常数（典型 raw×β 偏置）。"""
    vals = [float(p) for p in preds if p is not None and math.isfinite(float(p))]
    if len(vals) < max(2, int(min_n)):
        return False
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = math.sqrt(var)
    return std <= float(max_std) and abs(mean - float(intercept)) >= float(mean_gap)
