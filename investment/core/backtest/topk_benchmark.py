"""TopK 回测相对基准（T6/T9）：默认观察池 A 档等权；也可指定指数，失败则回退池等权。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Tuple


POOL_BENCH_ALIASES = frozenset(
    {"pool", "pool_ew", "ew", "universe", "tier_a", "a", "pred_a"}
)
DEFAULT_POOL_BENCH_LABEL = "观察池A档等权"


def resolve_tier_a_benchmark_bars(
    stock_bars: Dict[str, List[dict]],
    *,
    lookback: int = 10,
    load_missing: bool = True,
) -> Tuple[Dict[str, List[dict]], Dict[str, Any]]:
    """超额基线：观察池可预测性 A 档等权买持。

    优先用已加载的 ``stock_bars`` 中的 A 档；缺失时可选补拉日线。
    无枢纽分档报告时回退为传入 ``stock_bars`` 全量（标签降为观察池等权）。
    """
    meta: Dict[str, Any] = {
        "ok": False,
        "source": "fallback_universe",
        "label": "观察池等权",
        "n_a": 0,
        "n_used": 0,
        "loaded_extra": 0,
    }
    bars_in = dict(stock_bars or {})
    try:
        from core.research.predictability_tiers import (
            load_predictability_tiers_last,
            tier_code_set,
        )

        tier_rep = load_predictability_tiers_last()
    except Exception:  # noqa: BLE001 — best-effort
        logger.debug("tier report load failed for A-bench", exc_info=True)
        tier_rep = None

    if not isinstance(tier_rep, dict) or not tier_rep.get("success"):
        meta["n_used"] = len(bars_in)
        meta["ok"] = bool(bars_in)
        meta["reason"] = "无枢纽分档；回退交易宇宙等权"
        return bars_in, meta

    a_codes = sorted(tier_code_set(tier_rep, ["A"]))
    meta["n_a"] = len(a_codes)
    meta["source"] = "predictability_tier_a"
    meta["label"] = DEFAULT_POOL_BENCH_LABEL
    if not a_codes:
        meta["reason"] = "分档无 A；回退交易宇宙等权"
        meta["label"] = "观察池等权"
        meta["n_used"] = len(bars_in)
        meta["ok"] = bool(bars_in)
        return bars_in, meta

    out: Dict[str, List[dict]] = {
        c: bars_in[c] for c in a_codes if c in bars_in and bars_in.get(c)
    }
    missing = [c for c in a_codes if c not in out]
    if missing and load_missing:
        try:
            from core.research.portfolio_bars import load_portfolio_stock_bars

            extra, _fail, _fund = load_portfolio_stock_bars(
                missing,
                lookback=max(5, int(lookback or 10)),
                fetch_fundamentals=False,
            )
            for c, bars in (extra or {}).items():
                if bars:
                    out[c] = bars
            meta["loaded_extra"] = sum(1 for c in missing if c in out)
        except Exception:  # noqa: BLE001
            logger.debug("load missing A-tier bars failed", exc_info=True)

    if not out:
        meta["reason"] = "A 档无日线；回退交易宇宙等权"
        meta["label"] = "观察池等权"
        meta["n_used"] = len(bars_in)
        meta["ok"] = bool(bars_in)
        return bars_in, meta

    meta["ok"] = True
    meta["n_used"] = len(out)
    meta["missing"] = [c for c in a_codes if c not in out]
    return out, meta


def _fetch_index_bars_bounded(
    code: str,
    *,
    limit: int = 120,
    timeout_sec: float = 15.0,
) -> List[dict]:
    """带超时拉指数日线；超时/失败返回 []，回退观察池等权（避免挂死在「挂基准…」）。

    不用 ``ThreadPoolExecutor``：``shutdown(wait=True)`` 会在超时后继续等 worker。
    """
    import threading

    try:
        from core.data.facade import get_index_bars
    except Exception:  # noqa: BLE001 — best-effort 降级
        logger.debug("get_index_bars import failed", exc_info=True)
        return []

    box: Dict[str, Any] = {"raw": None, "err": None}
    done = threading.Event()

    def _worker() -> None:
        try:
            box["raw"] = get_index_bars(str(code or "").strip(), limit=int(limit))
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc
            logger.debug("index bars fetch error: %s", exc, exc_info=True)
        finally:
            done.set()

    t = threading.Thread(
        target=_worker, daemon=True, name=f"topk-bench-index-{str(code)[:12]}"
    )
    t.start()
    if not done.wait(timeout=max(1.0, float(timeout_sec))):
        logger.warning(
            "index bars timeout after %.0fs code=%s · fallback pool EW",
            float(timeout_sec),
            code,
        )
        return []
    if box["err"] is not None:
        return []
    raw = box["raw"]
    if isinstance(raw, dict):
        return list(raw.get("bars") or [])
    if isinstance(raw, tuple):
        return list(raw[0] or [])
    if isinstance(raw, list):
        return list(raw)
    return []


def _period_return_from_bars(bars: List[dict]) -> Optional[float]:
    if not bars or len(bars) < 2:
        return None
    try:
        c0 = float(bars[0].get("close") or 0)
        c1 = float(bars[-1].get("close") or 0)
    except (TypeError, ValueError):
        return None
    if c0 <= 0 or c1 <= 0:
        return None
    return round((c1 / c0 - 1.0) * 100.0, 2)


def _close_by_date(bars: List[dict]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for b in bars or []:
        d = str(b.get("date") or "").strip()
        if not d:
            continue
        try:
            c = float(b.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[d] = c
    return out


def _pool_equal_hold_return(stock_bars: Dict[str, List[dict]]) -> Optional[float]:
    rets = []
    for bars in (stock_bars or {}).values():
        r = _period_return_from_bars(bars)
        if r is not None:
            rets.append(r)
    if not rets:
        return None
    return round(sum(rets) / len(rets), 2)


def _pool_return_from_equity_curve(curve: List[dict]) -> Optional[float]:
    """测试窗净值曲线末点相对起点 100 的累计收益%（等权买持）。"""
    if not curve or len(curve) < 2:
        return None
    try:
        e0 = float(curve[0].get("equity") or 0)
        e1 = float(curve[-1].get("equity") or 0)
    except (TypeError, ValueError):
        return None
    if e0 <= 0 or e1 <= 0:
        return None
    return round((e1 / e0 - 1.0) * 100.0, 2)


def _normalize_equity_curve(
    date_closes: Dict[str, float],
    dates: List[str],
) -> List[dict]:
    """将价格序列对齐到 dates，起点 100。"""
    pts: List[dict] = []
    base: Optional[float] = None
    for d in dates:
        c = date_closes.get(d)
        if c is None or c <= 0:
            continue
        if base is None:
            base = c
        pts.append({"date": d, "equity": round(100.0 * c / base, 2)})
    return pts


def _pool_ew_equity_curve(
    stock_bars: Dict[str, List[dict]],
    dates: List[str],
) -> List[dict]:
    maps = {code: _close_by_date(bars) for code, bars in (stock_bars or {}).items()}
    if not maps or not dates:
        return []
    start_date = None
    for d in dates:
        xs = [m[d] for m in maps.values() if d in m]
        if len(xs) >= 2:
            start_date = d
            break
    if not start_date:
        return []
    start_closes = {c: m[start_date] for c, m in maps.items() if start_date in m}
    pts = [{"date": start_date, "equity": 100.0}]
    for d in dates:
        if d == start_date:
            continue
        rets = []
        for code, c0 in start_closes.items():
            c1 = maps[code].get(d)
            if c1 is None or c0 <= 0:
                continue
            rets.append(c1 / c0)
        if len(rets) < 2:
            continue
        eq = 100.0 * (sum(rets) / len(rets))
        pts.append({"date": d, "equity": round(eq, 2)})
    return pts


def _align_period_excess(
    strat_curve: List[dict],
    bench_curve: List[dict],
) -> Tuple[List[float], List[dict]]:
    """在共同日期上算相邻点超额收益（百分点）。"""
    bmap = {
        str(p.get("date")): float(p.get("equity"))
        for p in (bench_curve or [])
        if p.get("date") is not None and p.get("equity") is not None
    }
    aligned_bench: List[dict] = []
    excesses: List[float] = []
    prev_s: Optional[float] = None
    prev_b: Optional[float] = None
    for p in strat_curve or []:
        d = str(p.get("date") or "")
        if d not in bmap:
            continue
        try:
            s = float(p.get("equity"))
            b = float(bmap[d])
        except (TypeError, ValueError):
            continue
        if s <= 0 or b <= 0:
            continue
        aligned_bench.append({"date": d, "equity": round(b, 2)})
        if prev_s is not None and prev_b is not None and prev_s > 0 and prev_b > 0:
            s_ret = (s / prev_s - 1.0) * 100.0
            b_ret = (b / prev_b - 1.0) * 100.0
            excesses.append(s_ret - b_ret)
        prev_s, prev_b = s, b
    return excesses, aligned_bench


def _ann_excess_pct(total_excess_pct: float, start_date: str, end_date: str) -> Optional[float]:
    try:
        from datetime import date

        d0 = date.fromisoformat(str(start_date)[:10])
        d1 = date.fromisoformat(str(end_date)[:10])
        days = max((d1 - d0).days, 1)
    except Exception:  # noqa: BLE001 — best-effort / 非阻塞分支降级
        logger.debug("exception caught in topk_benchmark.py line 142", exc_info=True)
        return None
    years = days / 365.25
    if years < 1e-6:
        return None
    try:
        factor = (1.0 + float(total_excess_pct) / 100.0) ** (1.0 / years) - 1.0
    except (OverflowError, ValueError):
        return None
    return round(factor * 100.0, 2)


def _ir_stats(excesses: List[float], *, horizon_days: int = 3) -> Dict[str, Any]:
    if len(excesses) < 2:
        return {"ir": None, "ann_ir": None, "period_count": len(excesses)}
    mean = sum(excesses) / len(excesses)
    var = sum((x - mean) ** 2 for x in excesses) / len(excesses)
    std = math.sqrt(var)
    ir = (mean / std) if std > 1e-12 else None
    h = max(1, int(horizon_days or 3))
    scale = math.sqrt(252.0 / h)
    ann_ir = round(ir * scale, 4) if ir is not None else None
    return {
        "ir": round(ir, 4) if ir is not None else None,
        "ann_ir": ann_ir,
        "period_excess_mean_pct": round(mean, 4),
        "period_excess_std_pct": round(std, 4),
        "period_count": len(excesses),
    }


def build_topk_benchmark_summary(
    result: Dict[str, Any],
    stock_bars: Dict[str, List[dict]],
    *,
    index_code: str = "000300",
    lookback: int = 120,
    force_pool: bool = False,
    pool_label: Optional[str] = None,
) -> Dict[str, Any]:
    """相对基准：index_code=pool / tier_a → 池等权（默认标签观察池A档等权）；否则优先指数。"""
    strat = ((result.get("metrics") or {}).get("total_return_pct"))
    params = result.get("params") or {}
    req = result.get("request") or {}
    horizon_days = int(params.get("horizon_days") or req.get("horizon_days") or 3)
    strat_curve = list(result.get("equity_curve") or [])
    strat_dates = [str(p.get("date")) for p in strat_curve if p.get("date")]

    code = str(index_code or "000300").strip()
    if code.lower() in POOL_BENCH_ALIASES:
        force_pool = True
        code = "000300"

    pool_lbl = str(pool_label).strip() if pool_label is not None else ""
    if not pool_lbl:
        pool_lbl = "观察池等权"

    out: Dict[str, Any] = {
        "ok": False,
        "index_code": None if force_pool else code,
        "benchmark_label": None,
        "benchmark_return_pct": None,
        "strategy_return_pct": strat,
        "excess_pct": None,
        "ann_excess_pct": None,
        "ir": None,
        "ann_ir": None,
        "equity_curve": [],
        "force_pool": bool(force_pool),
        "note": "",
    }
    if strat is None:
        out["reason"] = "无策略累计收益"
        return out

    index_bars: List[dict] = []
    if not force_pool:
        try:
            index_bars = _fetch_index_bars_bounded(
                code, limit=max(40, int(lookback) + 20), timeout_sec=15.0
            )
        except Exception as e:
            logger.exception("unexpected error in build_topk_benchmark_summary")
            out["index_error"] = str(e)
            index_bars = []

    bench_ret: Optional[float] = None
    bench_curve: List[dict] = []
    label: Optional[str] = None
    note = ""

    if index_bars and not force_pool:
        bench_ret = _period_return_from_bars(index_bars)
        if strat_dates:
            bench_curve = _normalize_equity_curve(_close_by_date(index_bars), strat_dates)
        label = f"指数{code}"
        note = "超额 = 策略累计 − 指数同期买持；曲线按策略调仓日对齐指数收盘。"

    if bench_ret is None or (strat_dates and len(bench_curve) < 2) or force_pool:
        pool_curve = _pool_ew_equity_curve(stock_bars, strat_dates) if strat_dates else []
        # 优先用策略测试窗对齐的等权净值；无曲线再退回各票 bars 首尾均值
        pool_bh = _pool_return_from_equity_curve(pool_curve)
        if pool_bh is None:
            pool_bh = _pool_equal_hold_return(stock_bars)
        if pool_bh is not None:
            bench_ret = pool_bh
            bench_curve = pool_curve
            label = pool_lbl
            if force_pool:
                note = (
                    f"基准={label}各票测试窗买持收益等权平均；"
                    "超额 = 策略累计 − 该基准。"
                )
            else:
                note = (
                    f"指数 {code} 不可用或无法对齐，回退为 {label}；"
                    "超额 = 策略 − 池等权（非指数）。"
                )

    if bench_ret is None:
        out["reason"] = "无可用基准"
        return out

    excess = round(float(strat) - float(bench_ret), 2)
    excesses, aligned = _align_period_excess(strat_curve, bench_curve)
    ir_pack = _ir_stats(excesses, horizon_days=horizon_days)
    ann_ex = None
    if strat_dates and len(strat_dates) >= 2:
        ann_ex = _ann_excess_pct(excess, strat_dates[0], strat_dates[-1])

    warn_abs_pos_excess_neg = bool(
        float(strat) > 0 and excess is not None and float(excess) < 0
    )

    out.update(
        {
            "ok": True,
            "benchmark_label": label,
            "benchmark_return_pct": bench_ret,
            "excess_pct": excess,
            "ann_excess_pct": ann_ex,
            "ir": ir_pack.get("ir"),
            "ann_ir": ir_pack.get("ann_ir"),
            "period_excess_mean_pct": ir_pack.get("period_excess_mean_pct"),
            "period_excess_std_pct": ir_pack.get("period_excess_std_pct"),
            "period_count": ir_pack.get("period_count"),
            "equity_curve": aligned if aligned else bench_curve,
            "warn_abs_pos_excess_neg": warn_abs_pos_excess_neg,
            "note": note
            + (
                " ⚠ 绝对收益为正但超额为负：赚的是 beta/池涨，非相对 alpha。"
                if warn_abs_pos_excess_neg
                else ""
            ),
        }
    )
    return out
