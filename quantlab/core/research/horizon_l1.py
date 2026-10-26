"""ŷ_τ* L1 契约：按钟 ``tc_HHMM`` stitch + 可选整头 ``hz_*`` 训练板。

对齐 ŷ_oo 的 fingerprint 命中路径：热路径优先读湖。
产品消费路径对 cold / 探测失败 / 实际命中过低 **fail-closed**
（``materialize_required``）；脚本未开只读时个别票仍可回退 5m。
"""

from __future__ import annotations

import gc
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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


def _pack_shorter_lookback(
    code: str,
    bars: Sequence[dict],
    *,
    family: str,
    spec_fp: Any,
    names: Optional[Sequence[str]],
    min_rows: int,
) -> Optional[Dict[str, Any]]:
    """精确指纹 miss 时，把物化长窗切成与当前日线重叠的后缀（含 ``M``）。"""
    from core.research.incremental_panel import try_slice_shorter_lookback

    try:
        sliced = try_slice_shorter_lookback(
            code,
            bars,
            family=family,
            spec_fp=spec_fp,
            names=names,
            min_rows=int(min_rows),
            with_m=True,
            allow_end_lag=True,
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "horizon shorter lookback failed code=%s family=%s",
            code,
            family,
            exc_info=True,
        )
        return None
    if not sliced or len(sliced) < 6:
        return None
    X, y_arr, got_names, dates_l, M, m_names = sliced
    if y_arr is None:
        return None
    return {
        "X": X,
        "y": y_arr,
        "names": [str(n) for n in (got_names or [])],
        "dates": [str(d)[:10] for d in (dates_l or [])],
        "M": M,
        "_meta": {"m_names": [str(n) for n in (m_names or [])]},
    }


_SHARED_STR: Dict[str, str] = {}


def share_str(value: Any) -> str:
    """日期 / 钟点只留一份，避免每行各持一个相同字符串。"""
    s = str(value or "")
    hit = _SHARED_STR.get(s)
    if hit is None:
        _SHARED_STR[s] = s
        return s
    return hit


def slim_price_meta(meta: Optional[dict], *, tau: str = "") -> Dict[str, Any]:
    """矩阵路径的行元数据：只要钟点和价。训练用的主题列在广度阶段写进 X。"""
    src = meta if isinstance(meta, dict) else {}
    out: Dict[str, Any] = {}
    clock = str(tau or src.get("tau") or "").strip()
    if clock:
        out["tau"] = share_str(clock)
    for key in META_PX_KEYS:
        raw = src.get(key)
        if raw is None:
            continue
        try:
            fv = float(raw)
        except (TypeError, ValueError):
            continue
        if fv == fv:
            out[key] = fv
    return out


def process_rss_bytes() -> int:
    """当前物理占用。读不到则 0。"""
    import sys

    if sys.platform == "darwin":
        rss = _darwin_phys_footprint()
        if rss > 0:
            return rss
    elif sys.platform.startswith("linux"):
        rss = _linux_rss_bytes()
        if rss > 0:
            return rss
    return _ps_rss_bytes()


def _darwin_phys_footprint() -> int:
    """当前 phys_footprint，和系统回收进程时看的是同一个数。"""
    try:
        import ctypes
        import ctypes.util
        import os

        class _RusageInfoV0(ctypes.Structure):
            _fields_ = [
                ("ri_uuid", ctypes.c_uint8 * 16),
                ("ri_user_time", ctypes.c_uint64),
                ("ri_system_time", ctypes.c_uint64),
                ("ri_pkg_idle_wkups", ctypes.c_uint64),
                ("ri_interrupt_wkups", ctypes.c_uint64),
                ("ri_pageins", ctypes.c_uint64),
                ("ri_wired_size", ctypes.c_uint64),
                ("ri_resident_size", ctypes.c_uint64),
                ("ri_phys_footprint", ctypes.c_uint64),
                ("ri_proc_start_abstime", ctypes.c_uint64),
                ("ri_proc_exit_abstime", ctypes.c_uint64),
                ("ri_child_user_time", ctypes.c_uint64),
                ("ri_child_system_time", ctypes.c_uint64),
                ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
                ("ri_child_interrupt_wkups", ctypes.c_uint64),
                ("ri_child_pageins", ctypes.c_uint64),
                ("ri_child_elapsed_abstime", ctypes.c_uint64),
                ("ri_diskio_bytesread", ctypes.c_uint64),
                ("ri_diskio_byteswritten", ctypes.c_uint64),
            ]

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        fn = libc.proc_pid_rusage
        fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        fn.restype = ctypes.c_int
        info = _RusageInfoV0()
        if fn(os.getpid(), 0, ctypes.byref(info)) != 0:
            return 0
        return int(info.ri_phys_footprint or info.ri_resident_size or 0)
    except Exception:  # noqa: BLE001
        return 0


def _linux_rss_bytes() -> int:
    try:
        import os

        with open("/proc/self/statm", encoding="ascii") as handle:
            parts = handle.read().split()
        if len(parts) < 2:
            return 0
        return int(parts[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except Exception:  # noqa: BLE001
        return 0


def _ps_rss_bytes() -> int:
    try:
        import os
        import subprocess

        out = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            text=True,
        )
        return max(0, int(out.strip() or "0")) * 1024
    except Exception:  # noqa: BLE001
        return 0


def physical_ram_bytes() -> int:
    try:
        import os

        return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except Exception:  # noqa: BLE001
        return 8 * 1024**3


def release_allocator_cache() -> None:
    """丢掉拟合和回测留在进程里的缓存，再把空闲堆页还给系统。

    日立方、整段 5m、做 T 特征行都还挂在模块字典上。只 gc 的话这些页仍是脏的，
    分配器交不回去，下一头还没建面板就被内存保护停掉。
    """
    try:
        from core.research.day_cube import clear_memory

        clear_memory()
    except Exception:  # noqa: BLE001
        logger.debug("clear day cube memory failed", exc_info=True)
    try:
        from core.research.tau_panel import clear_minute_lag_cache

        clear_minute_lag_cache()
    except Exception:  # noqa: BLE001
        logger.debug("clear minute lag cache failed", exc_info=True)
    try:
        from core.t0.score_policy import clear_score_model_cache

        clear_score_model_cache()
    except Exception:  # noqa: BLE001
        logger.debug("clear score model cache failed", exc_info=True)
    try:
        from core.signal.minute_tau_feats import clear_peer_minute_bars

        clear_peer_minute_bars()
    except Exception:  # noqa: BLE001
        logger.debug("clear peer minute bars failed", exc_info=True)
    gc.collect()
    try:
        import sys

        if sys.platform == "darwin":
            _darwin_malloc_pressure_relief()
        elif sys.platform.startswith("linux"):
            _linux_malloc_trim()
    except Exception:  # noqa: BLE001
        logger.debug("release allocator cache failed", exc_info=True)


def _darwin_malloc_pressure_relief() -> None:
    import ctypes
    import ctypes.util

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    fn = getattr(libc, "malloc_zone_pressure_relief", None)
    if fn is None:
        return
    fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    fn.restype = ctypes.c_size_t
    fn(None, 0)


def _linux_malloc_trim() -> None:
    import ctypes

    libc = ctypes.CDLL(None)
    fn = getattr(libc, "malloc_trim", None)
    if fn is None:
        return
    fn.argtypes = [ctypes.c_size_t]
    fn.restype = ctypes.c_int
    fn(0)


def fit_memory_stop_reason(
    *,
    panel_bytes: int,
    rss_bytes: int = 0,
    ram_bytes: int = 0,
) -> str:
    """该停就返回给用户的原因；还能继续则空串。"""
    ram = int(ram_bytes or 0)
    rss = int(rss_bytes or 0)
    panels = max(0, int(panel_bytes or 0))
    if panels >= int(TAU_PANEL_BYTE_BUDGET):
        return (
            f"内存保护：面板数据已约 {panels / 1024**3:.1f} GB，"
            "已停止以免网页进程被系统杀掉。请缩小研究池，或把窗头终点提前。"
        )
    if ram > 0 and rss > 0:
        # 给系统和浏览器留一截，不把机器吃到被杀
        ceiling = min(int(ram * 0.62), ram - int(1.2 * 1024**3))
        if ceiling > 0 and rss >= ceiling:
            return (
                f"内存保护：进程已用约 {rss / 1024**3:.1f} GB / "
                f"机器 {ram / 1024**3:.0f} GB，已停止以免网页进程被系统杀掉。"
                "请缩小研究池，或把窗头终点提前。"
            )
    return ""


def confirm_fit_memory_stop(
    *,
    panel_bytes: int,
    rss_bytes: int,
    ram_bytes: int,
    reread_rss: Callable[[], int],
) -> str:
    """超水位时先归还空闲页再量一次。面板字节本身超预算则直接停。"""
    reason = fit_memory_stop_reason(
        panel_bytes=panel_bytes,
        rss_bytes=rss_bytes,
        ram_bytes=ram_bytes,
    )
    if not reason:
        return ""
    if int(panel_bytes or 0) >= int(TAU_PANEL_BYTE_BUDGET):
        return reason
    release_allocator_cache()
    try:
        rss2 = int(reread_rss() or 0)
    except Exception:  # noqa: BLE001
        logger.debug("reread rss failed", exc_info=True)
        return reason
    return fit_memory_stop_reason(
        panel_bytes=panel_bytes,
        rss_bytes=rss2,
        ram_bytes=ram_bytes,
    )


def panel_payload_bytes(panel: Optional[dict]) -> int:
    if not isinstance(panel, dict):
        return 0
    total = 0
    X = panel.get("X")
    if X is not None:
        try:
            total += int(np.asarray(X).nbytes)
        except Exception:  # noqa: BLE001
            pass
    M = panel.get("M")
    if M is not None:
        try:
            total += int(np.asarray(M).nbytes)
        except Exception:  # noqa: BLE001
            pass
    # 瘦 meta：按行粗算，避免为了记账再走一遍对象图
    n_meta = len(panel.get("metas") or [])
    if n_meta:
        total += n_meta * 240
    n_xs = len(panel.get("xs") or [])
    if n_xs and isinstance((panel.get("xs") or [None])[0], dict):
        total += n_xs * max(64, 48 * len(panel["xs"][0]))
    return total


def project_feature_block(
    X: np.ndarray,
    src_names: Sequence[str],
    keep_names: Sequence[str],
) -> Tuple[np.ndarray, List[str]]:
    """按列名抽出 ``keep_names``。缺列填 NaN，不建 dict 行。"""
    arr = np.asarray(X, dtype=np.float64)
    keep = [str(n) for n in keep_names if str(n)]
    if arr.ndim != 2 or not keep:
        return np.zeros((0, len(keep)), dtype=np.float64), keep
    index = {str(n): i for i, n in enumerate(src_names or [])}
    out = np.full((int(arr.shape[0]), len(keep)), np.nan, dtype=np.float64)
    for j, name in enumerate(keep):
        i = index.get(name)
        if i is None or i >= arr.shape[1]:
            continue
        out[:, j] = arr[:, i]
    return out, keep


def try_stitch_horizon_l1_panel(
    code: str,
    bars: Sequence[dict],
    clocks: Sequence[str],
    *,
    min_history: int = 12,
    include_alpha_tc: bool = True,
    include_alpha_th: bool = True,
    minute_bars: Optional[Sequence[dict]] = None,
    as_matrix: bool = False,
    project_names: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """按钟拼 ``tc_HHMM``；日线指纹命中则可不带 ``minute_bars``。

    物化长窗、拟合短窗时切重叠决策日（含 ``M``）。末日相差时只留双方都有的日期，
    这段 K 线必须仍是湖序列的真后缀。单钟损坏或列缺失则跳过该钟；命中钟数
    低于一半整票 miss。口径变了或无 ``price_tau*`` 仍 miss。
    命中时 metas 带 ``price_tau*``（若物化写入了 ``M``），供 enrich/relabel。
    ``as_matrix=True``：按钟竖叠 float64，可选 ``project_names`` 只留入模列，
    不把每一格展开成 dict（8GB 上全池 dict 会把进程打死）。
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
    from core.research.incremental_panel import _align_matrix_names

    fp_bars = horizon_tau_bars_fp(bars)
    xs_all: List[dict] = []
    ys_all: List[float] = []
    dates_all: List[str] = []
    metas_all: List[dict] = []
    clocks_req = 0
    clocks_hit = 0
    blocks: List[np.ndarray] = []
    for hm in clocks:
        clk = str(hm or "").strip()
        if not clk or clk.lower() in ("open", "09:30", "0930"):
            continue
        clocks_req += 1
        fp = horizon_tau_clock_fingerprint(
            bars,
            clk,
            min_history=min_history,
            include_alpha_tc=include_alpha_tc,
            include_alpha_th=include_alpha_th,
            bars_fp=fp_bars,
        )
        fam = family_for_tc(tau_hm=clk)

        def _spec(bars_fp: str, _clk: str = clk) -> str:
            return horizon_tau_clock_fingerprint(
                bars,
                _clk,
                min_history=min_history,
                include_alpha_tc=include_alpha_tc,
                include_alpha_th=include_alpha_th,
                bars_fp=bars_fp,
            )

        try:
            pack = lake.load_stock_panel(fam, code, fingerprint=fp)
        except Exception:  # noqa: BLE001
            logger.debug(
                "tau stitch load failed code=%s clock=%s", code, clk, exc_info=True
            )
            pack = None
        if not pack:
            pack = _pack_shorter_lookback(
                code,
                bars,
                family=fam,
                spec_fp=_spec,
                names=col_names,
                min_rows=2,
            )
        if not pack:
            logger.debug("tau stitch miss code=%s clock=%s", code, clk)
            continue
        got_names = [str(n) for n in (pack.get("names") or [])]
        try:
            X = np.asarray(pack["X"], dtype=np.float64)
        except (TypeError, ValueError):
            continue
        names = got_names or list(col_names)
        if got_names and got_names != list(col_names):
            aligned = _align_matrix_names(X, got_names, col_names)
            if aligned is None:
                logger.debug("tau stitch cols mismatch code=%s clock=%s", code, clk)
                continue
            X = aligned
            names = list(col_names)
        dates_l = [str(d)[:10] for d in (pack.get("dates") or [])]
        if X.ndim != 2 or len(dates_l) < 2:
            continue
        n = min(int(X.shape[0]), len(dates_l))
        if n < 2:
            continue
        rows = None if as_matrix else _matrix_rows_to_dicts(X, names)
        if as_matrix:
            blocks.append(np.ascontiguousarray(X[:n], dtype=np.float64))
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
            if rows is not None:
                xs_all.append(rows[i])
            ys_all.append(float(y_list[i]) if i < len(y_list) else float("nan"))
            src_meta = metas_clock[i] if i < len(metas_clock) else {}
            if as_matrix:
                dates_all.append(share_str(str(dates_l[i])[:10]))
                # 钟点以当前 family 为准；账本字段不进每行
                metas_all.append(slim_price_meta(src_meta, tau=clk))
            else:
                dates_all.append(dates_l[i])
                meta = dict(src_meta)
                meta["tau"] = clk
                meta["l1_family"] = fam
                meta["l1_hit"] = True
                metas_all.append(meta)
        clocks_hit += 1
    if clocks_req < 1 or clocks_hit < 1:
        return None
    if (float(clocks_hit) / float(clocks_req)) < float(TAU_STITCH_MIN_CLOCK_FRAC):
        logger.debug(
            "tau stitch clock coverage low code=%s hit=%s req=%s",
            code,
            clocks_hit,
            clocks_req,
        )
        return None
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
    x_names: List[str] = []
    X_out: Optional[np.ndarray] = None
    if as_matrix:
        if not blocks:
            return None
        X_cat = blocks[0] if len(blocks) == 1 else np.vstack(blocks)
        del blocks
        src_names = list(col_names)
        if project_names:
            X_out, x_names = project_feature_block(X_cat, src_names, project_names)
        else:
            X_out, x_names = np.asarray(X_cat, dtype=np.float64), src_names
        del X_cat
        xs_all = []
    out: Dict[str, Any] = {
        "code": code,
        "xs": xs_all,
        "ys": ys_all,
        "dates": dates_all,
        "metas": metas_all,
        "l1_stitched": True,
        "l1_clocks_hit": int(clocks_hit),
        "l1_clocks_req": int(clocks_req),
    }
    if X_out is not None:
        out["X"] = X_out
        out["x_names"] = x_names
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
    as_matrix: bool = False,
    project_names: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """读整头 ``hz_*`` 训练板（已 relabel 的 y）。"""
    from core.research import feature_lake as lake

    if not lake.enabled() or not code:
        return None
    fam = family_for_horizon(head)
    fp = horizon_head_fingerprint(
        bars, head, tau_grid, min_history=min_history
    )
    try:
        pack = lake.load_stock_panel(fam, code, fingerprint=fp)
    except Exception:  # noqa: BLE001
        logger.debug("hz load failed code=%s head=%s", code, head, exc_info=True)
        pack = None
    if not pack or pack.get("y") is None:
        def _head_spec(bars_fp: str) -> str:
            return horizon_head_fingerprint(
                bars, head, tau_grid, min_history=min_history, bars_fp=bars_fp
            )

        pack = _pack_shorter_lookback(
            code,
            bars,
            family=fam,
            spec_fp=_head_spec,
            names=feat_names,
            min_rows=4,
        )
    if not pack or pack.get("y") is None:
        return None
    X = np.asarray(pack["X"], dtype=np.float64)
    y = np.asarray(pack["y"], dtype=np.float64)
    dates = [str(d)[:10] for d in (pack.get("dates") or [])]
    names = [str(n) for n in (pack.get("names") or [])]
    if feat_names is not None:
        want = [str(n) for n in feat_names]
        if names and names != want:
            from core.research.incremental_panel import _align_matrix_names

            aligned = _align_matrix_names(X, names, want)
            if aligned is None:
                return None
            X = aligned
            names = want
        elif not names:
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
    n = min(int(X.shape[0]), len(y), len(dates), len(metas))
    if n < 4:
        return None
    if as_matrix:
        metas_out = [slim_price_meta(m) for m in metas[:n]]
        dates_out = [share_str(str(dates[i])[:10]) for i in range(n)]
    else:
        metas_out = metas[:n]
        dates_out = [str(dates[i])[:10] for i in range(n)]
    out_head: Dict[str, Any] = {
        "code": code,
        "ys": [float(y[i]) for i in range(n)],
        "dates": dates_out,
        "metas": metas_out,
        "l1_head_hit": True,
        "already_relabeled": True,
    }
    if as_matrix:
        keep = list(project_names or names)
        block, keep_names = project_feature_block(X[:n], names, keep)
        out_head["X"] = block
        out_head["x_names"] = keep_names
        out_head["xs"] = []
        return out_head
    xs = _matrix_rows_to_dicts(X[:n], names)
    out_head["xs"] = xs
    return out_head


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
    X_in = panel.get("X")
    matrix = X_in is not None and not xs
    if matrix:
        X_arr = np.asarray(X_in, dtype=np.float64)
        n = min(int(X_arr.shape[0]), len(ys), len(dates))
    else:
        X_arr = None
        n = min(len(xs), len(ys), len(dates))
    if n < 4 or not code:
        return False
    if matrix:
        names = [str(n0) for n0 in (panel.get("x_names") or feat_names or [])]
        X = np.asarray(X_arr[:n], dtype=np.float64)
        if feat_names:
            X, names = project_feature_block(X, names, feat_names)
    else:
        names = [str(n0) for n0 in (feat_names or [])]
        if not names:
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
# 单钟坏档跳过；命中钟数低于一半则整票不算 L1
TAU_STITCH_MIN_CLOCK_FRAC = 0.5
# 面板数据本身超过该字节数就停。800 只 × 18 钟的瘦矩阵大约 2.4GB，留到 3GB 再拦第二份拷贝
TAU_PANEL_BYTE_BUDGET = int(3.0 * 1024**3)


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
        error = "请先分钟线物化特征（L1/L2）：τ 网格 L1 覆盖探测失败（fail-closed，未开 5m 回退）"
    elif reason_s == "lake_disabled":
        error = "请先分钟线物化特征（L1/L2）：Feature Lake 未启用，无法 stitch τ 网格 L1"
    elif reason_s in ("low_hit", "early_abort"):
        pct = f"{hit_rate:.0%}" if hit_rate is not None else "?"
        error = (
            f"请先分钟线物化特征（L1/L2）：建面板 L1 命中仅 {pct}"
            f"（L1={l1_n} · 5m回退={rebuild}；指纹/价旁路不足）"
        )
    else:
        pct = f"{rate_f:.0%}" if rate_f is not None else "?"
        error = (
            f"请先分钟线物化特征（L1/L2）：τ 网格 L1 热覆盖约 {pct}"
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
            "Feature Lake 关闭或网格为空；产品拟合将阻断，请先分钟线物化特征（L1/L2）"
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
            "请先跑「分钟线物化特征（L1/L2）」（产品拟合将阻断）"
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
