"""一键物化：可选 L1 → L2。"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from core.data.policy import BARS_DAILY_LOOKBACK
from core.research.materialize.catalog import (
    catalog_payload,
    normalize_clocks,
    normalize_families,
    suggested_tau_clocks,
)
from core.research.materialize.l1_jobs import materialize_l1, materialize_l1_tau_grid
from core.research.materialize.l2_jobs import materialize_l2
from core.watching.store import MODEL_FIT_MAX_SIZE, WATCHING_MAX_SIZE

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[int, int, str], None]]
CancelCb = Optional[Callable[[], bool]]


def _emit(cb: ProgressCb, cur: int, tot: int, msg: str) -> None:
    if not cb:
        return
    try:
        cb(int(cur), max(1, int(tot)), str(msg or ""))
    except Exception:  # noqa: BLE001
        logger.debug("materialize orchestrate progress failed", exc_info=True)


def resolve_materialize_codes(
    *,
    codes: Sequence[str] | None = None,
    watching_limit: int = WATCHING_MAX_SIZE,
) -> List[str]:
    if codes:
        out = []
        seen = set()
        for raw in codes:
            c = str(raw or "").strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out[: max(2, int(watching_limit or WATCHING_MAX_SIZE))]
    try:
        from core.research_universe import resolve_model_fit_codes

        resolved = resolve_model_fit_codes(watching_limit=watching_limit)
        return list(resolved.get("codes") or [])
    except Exception:  # noqa: BLE001
        logger.debug("resolve_model_fit_codes failed", exc_info=True)
    try:
        from core.watching.store import read_watching

        doc = read_watching() or {}
        raw_list = doc.get("watchlist") if isinstance(doc, dict) else None
        out: List[str] = []
        seen = set()
        for raw in raw_list or []:
            if isinstance(raw, dict):
                c = str(raw.get("stock_code") or raw.get("code") or "").strip()
            else:
                c = str(raw or "").strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out[: max(2, int(watching_limit or WATCHING_MAX_SIZE))]
    except Exception:  # noqa: BLE001
        return []


def _run_materialize(
    *,
    families: Sequence[str] | None = None,
    clocks: Sequence[str] | None = None,
    codes: Sequence[str] | None = None,
    dates: Sequence[str] | None = None,
    do_l1: bool = True,
    do_l2: bool = True,
    do_l1_tau_grid: bool = False,
    lookback: int = BARS_DAILY_LOOKBACK,
    l2_lookback_days: int = 60,
    watching_limit: int = WATCHING_MAX_SIZE,
    horizon_days: int = 1,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Dict[str, Any]:
    """独立物化入口。L1 票级 raw →（可选）τ 网格 L1 → L2 日/钟截面 μ/σ。"""
    t0 = time.perf_counter()
    fams = normalize_families(families) if do_l1 else []
    clks = normalize_clocks(clocks) if do_l2 else []
    # τ 网格：并入 suggested。做 T 回测要 open + 决策钟立方；纯 τ 轨也保留 open，
    # L2 在未指定 dates 时仍只盖本地 5m 够格日（见 l2_jobs clip_minute_store）。
    if do_l1_tau_grid and do_l2:
        sug = suggested_tau_clocks()
        if not do_l1:
            # UI 可能只传 open 占位；换成 open + τ 钟，避免只物化开盘、漏分钟钟
            has_tau = any(
                str(c).lower() not in ("open", "09:30", "0930") for c in clks
            )
            if not has_tau:
                clks = ["open", *list(sug)]
            else:
                if "open" not in clks and "09:30" not in clks:
                    clks = ["open", *clks]
        for c in sug:
            if c not in clks:
                clks.append(c)
    code_list = resolve_materialize_codes(
        codes=codes, watching_limit=watching_limit
    )
    out: Dict[str, Any] = {
        "success": False,
        "ok": False,
        "catalog": catalog_payload(),
        "codes_n": len(code_list),
        "do_l1": bool(do_l1),
        "do_l1_tau_grid": bool(do_l1_tau_grid),
        "do_l2": bool(do_l2),
        "l1": None,
        "l1_tau_grid": None,
        "l2": None,
        "elapsed_s": 0.0,
    }
    if len(code_list) < 2:
        out["error"] = "观察池/宇宙不足 2 只"
        return out

    steps = (1 if do_l1 else 0) + (1 if do_l1_tau_grid else 0) + (1 if do_l2 else 0)
    step = 0

    def _cancelled() -> bool:
        if not cancel_cb:
            return False
        try:
            return bool(cancel_cb())
        except Exception:  # noqa: BLE001
            return False

    tot_steps = max(1, steps)

    if do_l1:
        if _cancelled():
            out["error"] = "cancelled"
            return out
        _emit(progress_cb, step, tot_steps, "物化 L1…")

        def _pcb(c: int, t: int, m: str = "") -> None:
            # 子进度原样送出。Job 层按 planned 折成百分比，不把票数写成 total。
            _emit(progress_cb, c, t, m or "L1…")

        out["l1"] = materialize_l1(
            fams,
            code_list,
            lookback=int(lookback or BARS_DAILY_LOOKBACK),
            horizon_days=int(horizon_days or 1),
            progress_cb=_pcb,
            cancel_cb=cancel_cb,
        )
        step += 1
        _emit(progress_cb, step, tot_steps, "L1 完成")

    if do_l1_tau_grid:
        if _cancelled():
            out["error"] = "cancelled"
            out["elapsed_s"] = round(time.perf_counter() - t0, 2)
            return out
        _emit(progress_cb, step, tot_steps, "物化 L1 τ 网格…")

        def _pcb_tau(c: int, t: int, m: str = "") -> None:
            _emit(progress_cb, c, t, m or "L1 τ…")

        tau_clocks = [c for c in clks if str(c).lower() not in ("open", "09:30", "0930")]
        if not tau_clocks:
            tau_clocks = suggested_tau_clocks()
        out["l1_tau_grid"] = materialize_l1_tau_grid(
            code_list,
            lookback=int(lookback or BARS_DAILY_LOOKBACK),
            clocks=tau_clocks,
            progress_cb=_pcb_tau,
            cancel_cb=cancel_cb,
        )
        step += 1
        _emit(progress_cb, step, tot_steps, "L1 τ 完成")

    if do_l2:
        if _cancelled():
            out["error"] = "cancelled"
            out["elapsed_s"] = round(time.perf_counter() - t0, 2)
            return out
        _emit(progress_cb, step, tot_steps, "物化 L2…")

        def _pcb2(c: int, t: int, m: str = "") -> None:
            _emit(progress_cb, c, t, m or "L2…")

        out["l2"] = materialize_l2(
            code_list,
            dates=dates,
            clocks=clks,
            lookback_days=int(lookback or l2_lookback_days or 60),
            use_trading_days=not dates,
            progress_cb=_pcb2,
            cancel_cb=cancel_cb,
            require_horizon_names=bool(do_l1_tau_grid),
        )
        step += 1
        _emit(progress_cb, step, tot_steps, "L2 完成")

    l1_ok = (not do_l1) or bool((out.get("l1") or {}).get("ok"))
    tau_ok = (not do_l1_tau_grid) or bool((out.get("l1_tau_grid") or {}).get("ok"))
    l2_ok = (not do_l2) or bool((out.get("l2") or {}).get("ok"))
    out["ok"] = bool(l1_ok and tau_ok and l2_ok)
    out["success"] = out["ok"]
    out["elapsed_s"] = round(time.perf_counter() - t0, 2)
    if not out["ok"] and not out.get("error"):
        errs = []
        if do_l1 and not l1_ok:
            errs.append("l1")
        if do_l1_tau_grid and not tau_ok:
            errs.append("l1_tau_grid")
        if do_l2 and not l2_ok:
            errs.append("l2")
        out["error"] = "materialize failed: " + ",".join(errs)
    _emit(progress_cb, tot_steps, tot_steps, f"物化完成 · {out['elapsed_s']}s")
    logger.info(
        "materialize done ok=%s codes=%s l1=%s tau_grid=%s l2=%s elapsed=%.1fs",
        out["ok"],
        len(code_list),
        do_l1,
        do_l1_tau_grid,
        do_l2,
        out["elapsed_s"],
    )
    return out


def run_materialize(
    *,
    families: Sequence[str] | None = None,
    clocks: Sequence[str] | None = None,
    codes: Sequence[str] | None = None,
    dates: Sequence[str] | None = None,
    do_l1: bool = True,
    do_l2: bool = True,
    do_l1_tau_grid: bool = False,
    lookback: int = BARS_DAILY_LOOKBACK,
    l2_lookback_days: int = 60,
    watching_limit: int = WATCHING_MAX_SIZE,
    horizon_days: int = 1,
    progress_cb: ProgressCb = None,
    cancel_cb: CancelCb = None,
) -> Dict[str, Any]:
    """独立物化入口。写入方不受并发消费只读开关影响。"""
    from core.research.materialize.consumer import materialize_writer

    with materialize_writer():
        return _run_materialize(
            families=families,
            clocks=clocks,
            codes=codes,
            dates=dates,
            do_l1=do_l1,
            do_l2=do_l2,
            do_l1_tau_grid=do_l1_tau_grid,
            lookback=lookback,
            l2_lookback_days=l2_lookback_days,
            watching_limit=watching_limit,
            horizon_days=horizon_days,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )


__all__ = [
    "catalog_payload",
    "resolve_materialize_codes",
    "run_materialize",
    "MODEL_FIT_MAX_SIZE",
]
