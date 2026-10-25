"""ŷ_τ* L1 契约：按钟 ``tc_HHMM`` stitch + 可选整头 ``hz_*`` 训练板。

对齐 ŷ_oo 的 fingerprint 命中路径：热路径优先读湖。
产品消费路径对 cold / 探测失败 / 实际命中过低 **fail-closed**
（``materialize_required``）；脚本未开只读时个别票仍可回退 5m。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 与 materialize / stitch 共用的 meta 价列（进 npz ``M``，不进特征 ``X`` / L2）
META_PRICE_TAU = "price_tau"
META_PX_KEYS = (
    "price_tau",
    "price_tau30",
    "price_tau45",
    "price_tau60",
    "price_tau75",
    "price_tau90",
)
# fingerprint：日线 bars_fp（不含 5m），旧含分钟的 L1 会 miss → 需重物化
FP_BARS_SCOPE = "daily"


def family_for_horizon(head: str) -> str:
    """整头训练板 family：``hz_t30`` … ``hz_t90``。"""
    h = str(head or "").strip().lower()
    if h.startswith("τ"):
        h = "t" + h[1:]
    if not re.fullmatch(r"t(30|45|60|75|90)", h or ""):
        h = "t30"
    return f"hz_{h}"


def list_hz_l1_families() -> List[str]:
    """固定五头 + 盘上已有 ``hz_*``（失效扫全）。"""
    fams = {family_for_horizon(h) for h in ("t30", "t45", "t60", "t75", "t90")}
    try:
        import os

        from core.research.feature_lake import _root
        from core.research.feature_registry import FEATURE_SCHEMA

        root = os.path.join(_root(), f"v{FEATURE_SCHEMA}")
        if os.path.isdir(root):
            for name in os.listdir(root):
                if str(name).startswith("hz_"):
                    fams.add(str(name))
    except Exception:  # noqa: BLE001
        logger.debug("list_hz_l1_families scan failed", exc_info=True)
    return sorted(fams)


def _matrix_rows_to_dicts(X: Any, names: Sequence[str]) -> List[dict]:
    """``X`` 行 → dict（避免依赖 ``tc_ridge_compact`` 造成环引用）。"""
    out: List[dict] = []
    arr = np.asarray(X, dtype=np.float64)
    if arr.ndim != 2:
        return out
    for i in range(int(arr.shape[0])):
        row: Dict[str, Any] = {}
        for j, name in enumerate(names):
            if j >= arr.shape[1]:
                break
            v = float(arr[i, j])
            if v == v:  # finite
                row[str(name)] = v
        out.append(row)
    return out


def horizon_tau_bars_fp(bars: Sequence[dict]) -> str:
    """τ 网格 L1 用日线指纹（不吃 5m），便于拟合跳过读分钟。"""
    from core.research.panel_fingerprint import bars_fingerprint

    return bars_fingerprint(list(bars or []))


def horizon_tau_clock_fingerprint(
    bars: Sequence[dict],
    clock: str,
    *,
    min_history: int,
    include_alpha_tc: bool = True,
    include_alpha_th: bool = True,
    bars_fp: Optional[str] = None,
) -> str:
    from core.research.panel_fingerprint import make_fingerprint

    fp_bars = bars_fp or horizon_tau_bars_fp(bars)
    return make_fingerprint(
        bars_fp=fp_bars,
        tau_hm=str(clock),
        min_history=int(min_history),
        include_alpha_tc=int(bool(include_alpha_tc)),
        include_alpha_th=int(bool(include_alpha_th)),
        use_minute=1,
        bars_fp_scope=FP_BARS_SCOPE,
    )


def horizon_head_fingerprint(
    bars: Sequence[dict],
    head: str,
    tau_grid: Sequence[str],
    *,
    min_history: int = 12,
    bars_fp: Optional[str] = None,
) -> str:
    from core.research.panel_fingerprint import make_fingerprint

    grid = ",".join(str(c).strip() for c in (tau_grid or []) if str(c).strip())
    return make_fingerprint(
        bars_fp=bars_fp or horizon_tau_bars_fp(bars),
        head=str(head or "").strip().lower(),
        tau_grid=grid,
        min_history=int(min_history),
        feature_pack="alpha_τh",
        bars_fp_scope=FP_BARS_SCOPE,
    )


def _tau_hm_to_num(hm: str) -> float:
    digits = "".join(ch for ch in str(hm or "") if ch.isdigit())
    if len(digits) >= 4:
        return float(digits[:4])
    if len(digits) == 3:
        return float(digits)
    return float("nan")


def _num_to_tau_hm(v: Any) -> str:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    s = f"{n:04d}"
    return f"{s[:2]}:{s[2:]}"


def meta_price_matrix_from_rows(
    metas: Sequence[dict],
    clocks_hint: Optional[Sequence[str]] = None,
) -> Tuple[np.ndarray, List[str]]:
    """从 collect metas 抽 ``M`` 矩阵：tau_hm 数值 + price_tau*。"""
    names = ["tau_hm", *META_PX_KEYS]
    n = len(metas or [])
    M = np.full((n, len(names)), np.nan, dtype=np.float64)
    for i, meta in enumerate(metas or []):
        m = meta if isinstance(meta, dict) else {}
        tau = str(m.get("tau") or m.get("tau_hm") or "").strip()
        if not tau and clocks_hint:
            tau = str(clocks_hint[0] or "")
        M[i, 0] = _tau_hm_to_num(tau)
        for j, key in enumerate(META_PX_KEYS, start=1):
            raw = m.get(key)
            if raw is None:
                continue
            try:
                fv = float(raw)
            except (TypeError, ValueError):
                continue
            if np.isfinite(fv):
                M[i, j] = fv
    return M, names


def metas_from_price_matrix(
    M: Optional[np.ndarray],
    m_names: Sequence[str],
    *,
    dates: Sequence[str],
    code: str,
    default_tau: str = "",
) -> List[dict]:
    """``M`` → metas（供 relabel 无 5m）。"""
    n = len(dates or [])
    out: List[dict] = []
    arr = None if M is None else np.asarray(M, dtype=np.float64)
    name_i = {str(n): i for i, n in enumerate(m_names or [])}
    for i in range(n):
        day = str(dates[i] or "")[:10]
        meta: Dict[str, Any] = {
            "date": day,
            "stock_code": code,
            "l1_hit": True,
        }
        if arr is not None and arr.ndim == 2 and i < arr.shape[0]:
            row = arr[i]
            j_tau = name_i.get("tau_hm")
            if j_tau is not None and j_tau < row.size and np.isfinite(row[j_tau]):
                meta["tau"] = _num_to_tau_hm(row[j_tau])
            for key in META_PX_KEYS:
                j = name_i.get(key)
                if j is None or j >= row.size:
                    continue
                if np.isfinite(row[j]):
                    meta[key] = float(row[j])
        if not meta.get("tau") and default_tau:
            meta["tau"] = str(default_tau)
        out.append(meta)
    return out


def try_stitch_horizon_l1_panel(
    code: str,
    bars: Sequence[dict],
    clocks: Sequence[str],
    *,
    min_history: int = 12,
    include_alpha_tc: bool = True,
    include_alpha_th: bool = True,
    minute_bars: Optional[Sequence[dict]] = None,
) -> Optional[Dict[str, Any]]:
    """按钟拼 ``tc_HHMM``；日线指纹命中则可不带 ``minute_bars``。

    命中时 metas 带 ``price_tau*``（若物化写入了 ``M``），供 enrich/relabel。
    """
    if not code or not clocks or len(bars) < int(min_history) + 2:
        return None
    try:
        from core.research import feature_lake as lake
        from core.research.lake_bridge import family_for_tc
        from core.research.panel_matrix import _tc_matrix_seed_names
        from core.research.materialize.horizon_names import horizon_l1_feature_names
        from core.yhat.keys import TAU_Z_FEATURES
    except Exception:  # noqa: BLE001
        return None
    if not lake.enabled():
        return None

    seed = horizon_l1_feature_names() if include_alpha_th else list(TAU_Z_FEATURES)
    col_names = _tc_matrix_seed_names(
        seed,
        include_alpha_tc=bool(include_alpha_tc),
        include_alpha_th=bool(include_alpha_th),
    )
    fp_bars = horizon_tau_bars_fp(bars)
    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    for hm in clocks:
        clk = str(hm or "").strip()
        if not clk or clk.lower() in ("open", "09:30", "0930"):
            continue
        fp = horizon_tau_clock_fingerprint(
            bars,
            clk,
            min_history=min_history,
            include_alpha_tc=include_alpha_tc,
            include_alpha_th=include_alpha_th,
            bars_fp=fp_bars,
        )
        fam = family_for_tc(tau_hm=clk)
        try:
            pack = lake.load_stock_panel(fam, code, fingerprint=fp)
        except Exception:  # noqa: BLE001
            return None
        if not pack:
            return None
        got_names = [str(n) for n in (pack.get("names") or [])]
        if got_names and got_names != list(col_names):
            return None
        X = np.asarray(pack["X"], dtype=np.float64)
        dates_l = [str(d)[:10] for d in (pack.get("dates") or [])]
        names = got_names or list(col_names)
        if X.ndim != 2 or len(dates_l) < 2:
            return None
        rows = _matrix_rows_to_dicts(X, names)
        n = min(len(rows), len(dates_l))
        if n < 2:
            return None
        y_arr = pack.get("y")
        y_list: List[float] = []
        if y_arr is not None:
            try:
                y_list = [float(v) for v in np.asarray(y_arr).reshape(-1).tolist()]
            except Exception:  # noqa: BLE001
                y_list = []
        M = pack.get("M")
        m_names = list((pack.get("_meta") or {}).get("m_names") or [])
        metas_clock = metas_from_price_matrix(
            M if M is not None else None,
            m_names,
            dates=dates_l[:n],
            code=code,
            default_tau=clk,
        )
        for i in range(n):
            xs_all.append(rows[i])
            ys_all.append(float(y_list[i]) if i < len(y_list) else float("nan"))
            dates_all.append(dates_l[i])
            meta = dict(metas_clock[i]) if i < len(metas_clock) else {}
            meta["tau"] = clk  # 按钟 stitch：钟点以当前 family 为准
            meta["l1_family"] = fam
            meta["l1_hit"] = True
            metas_all.append(meta)
    if len(ys_all) < 4:
        return None
    # 无 M/price_tau* 的旧 L1 不能免读 5m → miss，逼重物化或回退
    priced = 0
    for m in metas_all:
        if m.get(META_PRICE_TAU) is None:
            continue
        if any(m.get(k) is not None for k in META_PX_KEYS[1:]):
            priced += 1
    if priced < 4 and not minute_bars:
        return None
    out: Dict[str, Any] = {
        "code": code,
        "xs": xs_all,
        "ys": ys_all,
        "dates": dates_all,
        "metas": metas_all,
        "l1_stitched": True,
    }
    if minute_bars:
        out["minute_bars"] = list(minute_bars)
    return out


def try_load_horizon_head_panel(
    head: str,
    code: str,
    bars: Sequence[dict],
    tau_grid: Sequence[str],
    *,
    min_history: int = 12,
    feat_names: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """读整头 ``hz_*`` 训练板（已 relabel 的 y）。"""
    from core.research import feature_lake as lake

    if not lake.enabled() or not code:
        return None
    fam = family_for_horizon(head)
    fp = horizon_head_fingerprint(
        bars, head, tau_grid, min_history=min_history
    )
    pack = lake.load_stock_panel(fam, code, fingerprint=fp)
    if not pack or pack.get("y") is None:
        return None
    X = np.asarray(pack["X"], dtype=np.float64)
    y = np.asarray(pack["y"], dtype=np.float64)
    dates = [str(d)[:10] for d in (pack.get("dates") or [])]
    names = [str(n) for n in (pack.get("names") or [])]
    if feat_names is not None:
        want = [str(n) for n in feat_names]
        if names and names != want:
            return None
        if not names:
            names = want
    if X.ndim != 2 or X.shape[0] != len(dates) or y.shape[0] != len(dates):
        return None
    M = pack.get("M")
    m_names = list((pack.get("_meta") or {}).get("m_names") or [])
    metas = metas_from_price_matrix(
        M if M is not None else None,
        m_names,
        dates=dates,
        code=code,
    )
    xs = _matrix_rows_to_dicts(X, names)
    n = min(len(xs), len(y), len(dates), len(metas))
    if n < 4:
        return None
    return {
        "code": code,
        "xs": xs[:n],
        "ys": [float(y[i]) for i in range(n)],
        "dates": [str(dates[i])[:10] for i in range(n)],
        "metas": metas[:n],
        "l1_head_hit": True,
        "already_relabeled": True,
    }


def sync_horizon_head_panel(
    head: str,
    code: str,
    panel: Dict[str, Any],
    bars: Sequence[dict],
    tau_grid: Sequence[str],
    *,
    min_history: int = 12,
    feat_names: Optional[Sequence[str]] = None,
) -> bool:
    """物化/拟合 write-through：把 enrich 后的单票面板写入 ``hz_*``。"""
    from core.research.lake_bridge import sync_panel_to_lake
    from core.research.incremental_panel import stem_extra_meta

    xs = list(panel.get("xs") or [])
    ys = list(panel.get("ys") or [])
    dates = [str(d)[:10] for d in (panel.get("dates") or [])]
    metas = list(panel.get("metas") or [])
    n = min(len(xs), len(ys), len(dates))
    if n < 4 or not code:
        return False
    names = [str(n0) for n0 in (feat_names or [])]
    if not names:
        # 并集列名
        seen = set()
        for row in xs[:n]:
            if not isinstance(row, dict):
                continue
            for k in row:
                if k not in seen:
                    seen.add(k)
                    names.append(str(k))
    from core.research.panel_matrix import _dict_rows_to_matrix

    X = _dict_rows_to_matrix(
        [xs[i] if isinstance(xs[i], dict) else {} for i in range(n)],
        names,
    )
    M, m_names = meta_price_matrix_from_rows(metas[:n])
    fp = horizon_head_fingerprint(
        bars, head, tau_grid, min_history=min_history
    )
    extra = stem_extra_meta(bars)
    extra["m_names"] = m_names
    extra["head"] = str(head)
    return bool(
        sync_panel_to_lake(
            family_for_horizon(head),
            code,
            X=X,
            dates=dates[:n],
            names=names,
            fingerprint=fp,
            y=np.asarray(ys[:n], dtype=np.float64),
            extra_meta=extra,
            M=M,
            m_names=m_names,
        )
    )


# 与 ŷ_oo 覆盖闸对齐：≤ 半仓价旁路视为冷；建面板实际命中同门槛
TAU_L1_COLD_RATE = 0.5
TAU_L1_MIN_HIT_RATE = TAU_L1_COLD_RATE
# 产品路径：连续啃 5m 达到该票数且命中率过低 → 提前阻断（防指纹热探测误放行）
TAU_L1_EARLY_ABORT_MIN = 24


def tau_l1_coverage_blocks_fit(cov: Optional[Dict[str, Any]]) -> bool:
    """产品拟合是否应因探测结果阻断（fail-closed）。

    - 探测异常 / 空结果 / ``probe_ok=False``
    - ``cold`` 或 Feature Lake 关闭
    """
    if not isinstance(cov, dict) or not cov:
        return True
    if cov.get("probe_ok") is False:
        return True
    if cov.get("lake_enabled") is False:
        return True
    if bool(cov.get("cold")):
        return True
    return False


def tau_l1_hit_rate_too_low(
    *,
    l1_head_hit: int,
    l1_stitch_hit: int,
    rebuild_5m: int,
    min_attempted: int = 1,
    min_rate: float = TAU_L1_MIN_HIT_RATE,
) -> bool:
    """建面板实际 L1 命中是否过低（含指纹 miss 导致的大面积 5m）。"""
    l1_n = max(0, int(l1_head_hit)) + max(0, int(l1_stitch_hit))
    rebuild = max(0, int(rebuild_5m))
    attempted = l1_n + rebuild
    if attempted < max(1, int(min_attempted)):
        return False
    return (float(l1_n) / float(attempted)) <= float(min_rate)


def materialize_required_tau_l1_payload(
    *,
    tau_grid: Sequence[str],
    task: str = "",
    cov: Optional[Dict[str, Any]] = None,
    reason: str = "cold",
    l1_head_hit: int = 0,
    l1_stitch_hit: int = 0,
    rebuild_5m: int = 0,
) -> Dict[str, Any]:
    """ŷ_τ* 缺可用 τ 网格 L1 时的 ``MaterializeRequired`` 载荷。"""
    grid = [str(c).strip() for c in (tau_grid or []) if str(c or "").strip()]
    cov_d = dict(cov or {})
    rate = cov_d.get("rate")
    try:
        rate_f = float(rate) if rate is not None else None
    except (TypeError, ValueError):
        rate_f = None
    l1_n = max(0, int(l1_head_hit)) + max(0, int(l1_stitch_hit))
    rebuild = max(0, int(rebuild_5m))
    attempted = l1_n + rebuild
    hit_rate = (float(l1_n) / float(attempted)) if attempted else None
    reason_s = str(reason or "cold").strip() or "cold"
    if reason_s == "probe_failed":
        error = "请先分钟线物化 L1/L2：τ 网格 L1 覆盖探测失败（fail-closed，未开 5m 回退）"
    elif reason_s == "lake_disabled":
        error = "请先分钟线物化 L1/L2：Feature Lake 未启用，无法 stitch τ 网格 L1"
    elif reason_s in ("low_hit", "early_abort"):
        pct = f"{hit_rate:.0%}" if hit_rate is not None else "?"
        error = (
            f"请先分钟线物化 L1/L2：建面板 L1 命中仅 {pct}"
            f"（L1={l1_n} · 5m回退={rebuild}；指纹/价旁路不足）"
        )
    else:
        pct = f"{rate_f:.0%}" if rate_f is not None else "?"
        error = (
            f"请先分钟线物化 L1/L2：τ 网格 L1 热覆盖约 {pct}"
            "（缺 M/price_tau* 或分片不足，无法免读 5m）"
        )
    return {
        "error_code": "materialize_required",
        "error": error,
        "hint": "分钟线物化",
        "layer": "l1",
        "families": ["tc"],
        "clocks": list(grid),
        "task": str(task or ""),
        "reason": reason_s,
        "l1_tau_coverage": cov_d,
        "probe_hint": str(cov_d.get("hint") or "").strip(),
        "l1_head_hit": int(l1_head_hit),
        "l1_stitch_hit": int(l1_stitch_hit),
        "rebuild_5m": int(rebuild_5m),
        "l1_hit_rate": hit_rate,
        "request": {
            "do_l1": False,
            "do_l2": True,
            "do_l1_tau_grid": True,
            "families": [],
            "clocks": list(grid),
        },
    }


def probe_tau_grid_l1_coverage(
    codes: Sequence[str],
    tau_grid: Sequence[str],
    *,
    sample_n: int = 40,
) -> Dict[str, Any]:
    """拟合前轻量探测：τ 网格 L1 是否具备 stitch 条件（有分片 + ``m_names``/price_tau）。

    不校验日线 fingerprint（那步交给热路径）；只读 meta，避免开 npz。
    返回 ``{rate, present, with_meta, n_probed, n_slots, hint, cold, probe_ok}``。
    """
    from core.research.feature_lake import enabled, read_stock_meta
    from core.research.lake_bridge import family_for_tc

    code_list = [str(c).strip() for c in (codes or []) if str(c or "").strip()]
    clocks = [
        str(c).strip()
        for c in (tau_grid or [])
        if str(c or "").strip()
        and str(c).strip().lower() not in ("open", "09:30", "0930")
    ]
    lake_on = bool(enabled())
    out: Dict[str, Any] = {
        "rate": 0.0,
        "present": 0,
        "with_meta": 0,
        "n_probed": 0,
        "n_slots": 0,
        "n_codes": len(code_list),
        "n_clocks": len(clocks),
        "hint": "",
        "cold": True,
        "lake_enabled": lake_on,
        "probe_ok": True,
    }
    if not lake_on or not code_list or not clocks:
        out["hint"] = (
            "Feature Lake 关闭或网格为空；产品拟合将阻断，请先分钟线物化 L1/L2"
        )
        return out
    cap = max(1, min(int(sample_n or 40), len(code_list)))
    # 均匀抽样，避免只看池头
    if cap >= len(code_list):
        sample = code_list
    else:
        step = max(1, len(code_list) // cap)
        sample = [code_list[i] for i in range(0, len(code_list), step)][:cap]
    present = 0
    with_meta = 0
    slots = 0
    for code in sample:
        for clock in clocks:
            slots += 1
            fam = family_for_tc(tau_hm=clock)
            try:
                meta = read_stock_meta(fam, code)
            except Exception:  # noqa: BLE001
                meta = None
            if not meta:
                continue
            present += 1
            m_names = [str(n) for n in (meta.get("m_names") or [])]
            if META_PRICE_TAU in m_names or any(
                k in m_names for k in META_PX_KEYS
            ):
                with_meta += 1
    rate = float(with_meta) / float(max(1, slots))
    cold = rate <= float(TAU_L1_COLD_RATE)
    hint = ""
    if cold:
        hint = (
            f"τ 网格 L1 热覆盖约 {rate:.0%}（抽样 {len(sample)}×{len(clocks)}）；"
            "请先跑「分钟线物化 L1/L2」（产品拟合将阻断）"
        )
    elif rate < 0.9:
        hint = (
            f"τ 网格 L1 部分就绪 {rate:.0%}；未命中票将回退 5m，"
            "命中过低时产品路径会中止"
        )
    else:
        hint = f"τ 网格 L1 就绪约 {rate:.0%}（可免读 5m）"
    out.update(
        {
            "rate": rate,
            "present": present,
            "with_meta": with_meta,
            "n_probed": len(sample),
            "n_slots": slots,
            "hint": hint,
            "cold": cold,
            "probe_ok": True,
        }
    )
    return out
