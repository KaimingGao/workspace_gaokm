"""滚动验证（研究枢纽）：A 档 walk-forward 相对分闸门，不写模型。

宇宙 = 观察池 ∩ 可预测性 A 档（读 ``predictability_tiers_last``），缩小内存与算量。
OO：ŷ_oo>y_min 且 oo_rank 相对分 > rank_score_min，按相对分降序最多取 top_n
τc：ŷ_τc>y_min 且 τc_rank 相对分 > rank_score_min（开盘钟），按相对分降序最多取 top_n
有仓日收益 = 入选等权均值；命中 = 等权收益 > 0。

默认 lookback=500、holdout=10、folds=3、y_min=0.2、rank_score_min=0.3、top_n=10。
API / Job wire：``roll-gate``；落盘 ``roll_gate_last.json`` / ``jobs/roll_gate.json``。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK = 500
DEFAULT_HOLDOUT = 10
DEFAULT_FOLDS = 3
DEFAULT_Y_MIN = 0.2  # 百分点
DEFAULT_RANK_SCORE_MIN = 0.3  # oo_rank / τc_rank 相对分
DEFAULT_TOP_N = 10  # 过闸后按相对分最多取 N
DEFAULT_RIDGE_LAMBDA = 1.0

ProgressCb = Optional[Callable[[int, int, str], None]]

_progress_lock = threading.Lock()
_progress: Dict[str, Any] = {
    "status": "idle",
    "message": "",
    "fold": 0,
    "folds": 0,
    "partial": None,
}


def progress_snapshot() -> Dict[str, Any]:
    with _progress_lock:
        return dict(_progress)


def _set_progress(**kwargs: Any) -> None:
    with _progress_lock:
        _progress.update(kwargs)


def last_path() -> str:
    """规范落盘路径（写入只用此路径）。"""
    from core.paths import ROLL_GATE_LAST_PATH

    return ROLL_GATE_LAST_PATH


def resolved_last_path() -> str:
    """可读路径：优先规范文件，否则旧 ``roll_gate_top1_last.json``。"""
    from core.paths import ROLL_GATE_LAST_LEGACY_PATH

    path = last_path()
    if os.path.isfile(path):
        return path
    if os.path.isfile(ROLL_GATE_LAST_LEGACY_PATH):
        return ROLL_GATE_LAST_LEGACY_PATH
    return path


def load_last() -> Optional[Dict[str, Any]]:
    path = resolved_last_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _atomic_write(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix="roll_gate_", suffix=".json.tmp", dir=os.path.dirname(path) or "."
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def attach_saved_at(doc: Dict[str, Any], path: str) -> Dict[str, Any]:
    """无完成/写入时刻时，用结果文件 mtime 补 ``saved_at``（不改盘）。"""
    if not isinstance(doc, dict):
        return doc
    if doc.get("finished_at") or doc.get("updated_at"):
        return doc
    if not path or not os.path.isfile(path):
        return doc
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return doc
    out = dict(doc)
    out["saved_at"] = datetime.fromtimestamp(mt, tz=timezone.utc).isoformat()
    return out


def persist_last(doc: Dict[str, Any]) -> str:
    path = last_path()
    payload = dict(doc) if isinstance(doc, dict) else {"success": False}
    try:
        from core.signal.score_display import json_safe

        safe = json_safe(payload)
        if isinstance(safe, dict):
            payload = safe
    except Exception:  # noqa: BLE001
        pass
    _atomic_write(path, payload)
    return path


def _tier_a_codes() -> List[str]:
    """观察池 ∩ 可预测性 A 档。无分档报告或空 A → 空列表。"""
    from core.research.predictability_tiers import (
        load_predictability_tiers_last,
        tier_code_set,
    )
    from core.watching.store import read_watching

    uni = read_watching()
    watch = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
    if not watch:
        return []
    report = load_predictability_tiers_last()
    a_set = tier_code_set(report, ["A"])
    if not a_set:
        return []
    return [c for c in watch if c in a_set]


def _load_tier_a_pool(lookback: int, codes: Optional[Sequence[str]] = None) -> List[dict]:
    """只拉 A 档日线，避免全池进内存。"""
    from core.data.facade import get_bars_batch

    watch = [str(c).strip() for c in (codes if codes is not None else _tier_a_codes()) if str(c).strip()]
    if not watch:
        return []
    packs = get_bars_batch(watch, limit=int(lookback))
    out: List[dict] = []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        code = str(pack.get("stock_code") or pack.get("code") or "").strip()
        bars = list(pack.get("bars") or [])
        if not code or len(bars) < 40:
            continue
        out.append({"code": code, "bars": bars})
    return out


def _train_cutoff(uniq: Sequence[str], eval_start: str, *, embargo: int = 1) -> str:
    days = [str(d)[:10] for d in uniq if len(str(d)[:10]) >= 10]
    i = days.index(eval_start)
    cut = i - max(0, int(embargo))
    if cut <= 0:
        return ""
    return days[cut - 1]


def _rolling_train_dates(uniq: Sequence[str], fit_end: str, lookback: int) -> Set[str]:
    days = [str(d)[:10] for d in uniq if len(str(d)[:10]) >= 10]
    if fit_end not in days:
        return set()
    j = days.index(fit_end)
    start = max(0, j - int(lookback) + 1)
    return set(days[start : j + 1])


def _stack_days(
    days: Sequence[dict],
) -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    xs: List[np.ndarray] = []
    ys: List[float] = []
    dates: List[str] = []
    names: Optional[List[str]] = None
    for day in days:
        x = day.get("X")
        if not isinstance(x, np.ndarray) or x.ndim != 2:
            continue
        n = int(x.shape[0])
        nm = [str(n_) for n_ in (day.get("names") or [])]
        yrow = list(day.get("ys") or [])
        date = str(day.get("date") or "")[:10]
        if n == 0 or len(nm) != x.shape[1] or len(yrow) != n or len(date) < 10:
            continue
        if names is None:
            names = nm
        elif nm != names:
            continue
        xs.append(np.asarray(x, dtype=np.float64))
        for y in yrow:
            try:
                ys.append(float(y))
            except (TypeError, ValueError):
                ys.append(float("nan"))
        dates.extend([date] * n)
    if not xs or not names:
        return (
            np.zeros((0, 0), dtype=np.float64),
            np.zeros(0, dtype=np.float64),
            [],
            [],
        )
    return np.vstack(xs), np.asarray(ys, dtype=np.float64), names, dates


def _fit_tc_lgb(
    days: Sequence[dict],
    train_dates: Set[str],
    *,
    fit_end: str,
) -> Optional[Any]:
    """ŷ_τc 收益头：内存 LightGBM（与生产同 backend；滚动闸不写盘）。"""
    from core.research.tc_lgb import fit_tc_lgb_matrix

    use = [d for d in days if str(d.get("date") or "")[:10] in train_dates]
    if len(use) < 20:
        return None
    x, y, names, dates = _stack_days(use)
    if x.shape[0] < 80 or not names:
        return None
    model, report = fit_tc_lgb_matrix(
        x,
        y,
        names,
        min_samples=80,
        fitted_as_of=fit_end,
        row_dates=dates,
        cross_section_zscore=True,
        clock="open",
    )
    if model is None or not (isinstance(report, dict) and report.get("success")):
        return None
    return model


def _fit_oo_lgb(
    days: Sequence[dict],
    train_dates: Set[str],
    *,
    fit_end: str,
) -> Optional[Any]:
    """ŷ_oo 收益头：内存 LightGBM（与生产同 backend）。"""
    from core.research.oo_lgb import fit_oo_lgb_matrix

    use = [d for d in days if str(d.get("date") or "")[:10] in train_dates]
    if len(use) < 20:
        return None
    x, y, names, dates = _stack_days(use)
    if x.shape[0] < 80 or not names:
        return None
    model, report = fit_oo_lgb_matrix(
        x,
        y,
        names,
        horizon_days=1,
        min_samples=80,
        fitted_as_of=fit_end,
        row_dates=dates,
        cross_section_zscore=True,
    )
    if model is None or not (isinstance(report, dict) and report.get("success")):
        return None
    return model


def _fit_rank_lambda(
    days: Sequence[dict],
    train_dates: Set[str],
) -> Optional[dict]:
    from core.research.oo_rank_lambdarank import _collect_feature_names, fit_lambdarank

    use = [d for d in days if str(d.get("date") or "")[:10] in train_dates]
    if len(use) < 20:
        return None
    names = _collect_feature_names(use)
    if not names:
        return None
    fit = fit_lambdarank(
        use,
        feature_names=names,
        cross_section_zscore=True,
        train_codes=None,
    )
    if not isinstance(fit, dict) or not fit.get("success"):
        return None
    return fit


def _pick_gated_top_n(
    day: dict,
    *,
    y_preds: Sequence[Any],
    rank_preds: Sequence[Any],
    y_min: float,
    rank_score_min: float = DEFAULT_RANK_SCORE_MIN,
    top_n: int = DEFAULT_TOP_N,
) -> Dict[str, Any]:
    """过 ŷ 与相对分闸后，按相对分降序最多取 top_n；等权收益，否则空闸。"""
    codes = [str(c) for c in (day.get("codes") or [])]
    ys = list(day.get("ys") or [])
    n = len(codes)
    if n == 0 or len(ys) != n or len(y_preds) != n or len(rank_preds) != n:
        return {"empty": True, "reason": "bad_day"}
    rows: List[Tuple[str, float, float, float]] = []
    for code, p_y, p_rk, y in zip(codes, y_preds, rank_preds, ys):
        try:
            y_hat = float(p_y)
            score = float(p_rk)
            lab = float(y)
        except (TypeError, ValueError):
            continue
        if y_hat != y_hat or score != score or lab != lab:
            continue
        rows.append((code, y_hat, score, lab))
    if not rows:
        return {"empty": True, "reason": "no_rows"}
    rows.sort(key=lambda t: (-t[2], t[0]))
    score_thr = float(rank_score_min)
    y_thr = float(y_min)
    k = max(1, int(top_n or DEFAULT_TOP_N))
    passed = [
        (code, y_hat, score, lab)
        for code, y_hat, score, lab in rows
        if score > score_thr and y_hat > y_thr
    ]
    if not passed:
        code, y_hat, score, _lab = rows[0]
        reason = "rank_score" if score <= score_thr else "y_gate"
        return {
            "empty": True,
            "reason": reason,
            "code": code,
            "y_hat": y_hat,
            "rank_score": score,
            "n_picks": 0,
        }
    picked = passed[:k]
    labs = [lab for _c, _y, _s, lab in picked]
    mean_lab = sum(labs) / len(labs)
    top_code, top_y, top_score, _ = picked[0]
    return {
        "empty": False,
        "code": top_code,
        "y_hat": top_y,
        "rank_score": top_score,
        "label": mean_lab,
        "hit": 1 if mean_lab > 1e-12 else 0,
        "n_picks": len(picked),
        "codes": [c for c, _y, _s, _lab in picked],
    }


def _arm_stats(day_rows: Sequence[dict]) -> Dict[str, Any]:
    active = [r for r in day_rows if not r.get("empty")]
    empty = len(day_rows) - len(active)
    if not active:
        return {
            "hits": 0,
            "n": 0,
            "empty": empty,
            "hit_pct": None,
            "mean_ret": None,
            "days": list(day_rows),
        }
    hits = sum(int(r.get("hit") or 0) for r in active)
    n = len(active)
    rets: List[float] = []
    for r in active:
        try:
            v = float(r.get("label"))
        except (TypeError, ValueError):
            continue
        if v == v and abs(v) != float("inf"):
            rets.append(v)
    mean_ret = (sum(rets) / len(rets)) if rets else None
    return {
        "hits": hits,
        "n": n,
        "empty": empty,
        "hit_pct": round(100.0 * hits / n, 2),
        "mean_ret": round(mean_ret, 4) if mean_ret is not None else None,
        "days": list(day_rows),
    }


def _sum_arm(folds: Sequence[dict], key: str) -> Dict[str, Any]:
    hits = n = empty = total_days = 0
    ret_sum = 0.0
    ret_n = 0
    for fold in folds:
        arm = fold.get(key) if isinstance(fold.get(key), dict) else {}
        hits += int(arm.get("hits") or 0)
        n += int(arm.get("n") or 0)
        empty += int(arm.get("empty") or 0)
        total_days += int(arm.get("n") or 0) + int(arm.get("empty") or 0)
        if arm.get("n") and arm.get("mean_ret") is not None:
            try:
                mr = float(arm["mean_ret"])
            except (TypeError, ValueError):
                continue
            if mr == mr and abs(mr) != float("inf"):
                ret_sum += mr * int(arm["n"])
                ret_n += int(arm["n"])
    out: Dict[str, Any] = {
        "hits": hits,
        "n": n,
        "empty": empty,
        "total_days": total_days,
        "hit_pct": round(100.0 * hits / n, 2) if n else None,
        "mean_ret": round(ret_sum / ret_n, 4) if ret_n else None,
    }
    return out


def run_roll_gate(
    *,
    lookback: int = DEFAULT_LOOKBACK,
    holdout: int = DEFAULT_HOLDOUT,
    folds: int = DEFAULT_FOLDS,
    y_min: float = DEFAULT_Y_MIN,
    rank_score_min: float = DEFAULT_RANK_SCORE_MIN,
    top_n: int = DEFAULT_TOP_N,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    progress_cb: ProgressCb = None,
    persist: bool = True,
    job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """同步跑完；``progress_cb(cur, tot, msg)``；可选落盘 last.json。

    ``job_id`` 写入进度与 last，供 UI 区分「本轮」与上一轮落盘，避免运行中误读旧结果。
    """
    from core.research.oo_lgb import predict_oo_lgb_matrix
    from core.research.oo_rank_lambdarank import _predict_oo_rank_matrix
    from core.research.rank_day_panels import build_rank_day_panels
    from core.research.tc_lgb import predict_tc_lgb_matrix

    del ridge_lambda  # ŷ_τc 已切 LightGBM；形参保留兼容旧请求体

    lb = max(100, min(1000, int(lookback or DEFAULT_LOOKBACK)))
    hold_n = max(1, min(30, int(holdout or DEFAULT_HOLDOUT)))
    n_folds = max(1, min(20, int(folds or DEFAULT_FOLDS)))
    try:
        y_min = float(y_min if y_min is not None else DEFAULT_Y_MIN)
    except (TypeError, ValueError):
        y_min = float(DEFAULT_Y_MIN)
    if y_min != y_min:  # NaN
        y_min = float(DEFAULT_Y_MIN)
    try:
        rank_score_min = float(
            rank_score_min
            if rank_score_min is not None
            else DEFAULT_RANK_SCORE_MIN
        )
    except (TypeError, ValueError):
        rank_score_min = float(DEFAULT_RANK_SCORE_MIN)
    if rank_score_min != rank_score_min:  # NaN
        rank_score_min = float(DEFAULT_RANK_SCORE_MIN)
    try:
        top_n = int(top_n if top_n is not None else DEFAULT_TOP_N)
    except (TypeError, ValueError):
        top_n = int(DEFAULT_TOP_N)
    top_n = max(1, min(100, top_n))
    run_id = str(job_id or "").strip() or None
    need = lb + hold_n * n_folds + 40

    def _emit(cur: int, tot: int, msg: str) -> None:
        patch: Dict[str, Any] = {
            "status": "running",
            "message": str(msg or ""),
            "fold": int(cur),
            "folds": int(tot),
        }
        if run_id:
            patch["job_id"] = run_id
        _set_progress(**patch)
        if progress_cb:
            try:
                progress_cb(int(cur), max(1, int(tot)), str(msg or ""))
            except Exception:  # noqa: BLE001
                logger.debug("roll_gate progress_cb failed", exc_info=True)

    _emit(0, n_folds, "拉 A 档日线…")
    a_codes = _tier_a_codes()
    if not a_codes:
        out = {
            "success": False,
            "error": "无 A 档股票：请先在数据中心跑观察池分档",
        }
        _set_progress(status="failed", message=out["error"])
        return out
    stock_bars = _load_tier_a_pool(need, a_codes)
    if len(stock_bars) < 20:
        out = {
            "success": False,
            "error": f"A 档有效标的不足 n={len(stock_bars)}（候选 {len(a_codes)}）",
        }
        _set_progress(status="failed", message=out["error"])
        return out

    _emit(0, n_folds, "建 oo 日面板…")
    oo_days = build_rank_day_panels(
        stock_bars, family="oo_rank", horizon_days=1, excess_mode="none"
    )
    _emit(0, n_folds, "建 τc 日面板…")
    tc_days = build_rank_day_panels(
        stock_bars,
        family="tc",
        head="y_tc",
        cache_head="tc_roll_gate",
    )
    oo_by = {str(d.get("date") or "")[:10]: d for d in oo_days}
    tc_by = {str(d.get("date") or "")[:10]: d for d in tc_days}
    common = sorted(set(oo_by) & set(tc_by))
    need_test = hold_n * n_folds
    if len(common) < lb + need_test + 2:
        out = {
            "success": False,
            "error": f"可对齐日不足 common={len(common)} need≈{lb + need_test + 2}",
        }
        _set_progress(status="failed", message=out["error"])
        return out

    test_days = common[-need_test:]
    cal = list(common)
    fold_rows: List[dict] = []

    for fi in range(n_folds):
        seg = test_days[fi * hold_n : (fi + 1) * hold_n]
        if not seg:
            continue
        eval_start = seg[0]
        try:
            fit_end = _train_cutoff(cal, eval_start, embargo=1)
        except ValueError:
            continue
        if not fit_end:
            continue
        train_dates = _rolling_train_dates(cal, fit_end, lb)
        if len(train_dates) < max(60, lb // 2):
            continue

        t0 = time.time()
        _emit(
            fi + 1,
            n_folds,
            f"fold {fi + 1}/{n_folds} 拟合 oo LightGBM fit_end={fit_end}…",
        )
        oo_m = _fit_oo_lgb(oo_days, train_dates, fit_end=fit_end)
        _emit(
            fi + 1,
            n_folds,
            f"fold {fi + 1}/{n_folds} 拟合 oo LambdaRank…",
        )
        oo_rk = _fit_rank_lambda(oo_days, train_dates)
        _emit(
            fi + 1,
            n_folds,
            f"fold {fi + 1}/{n_folds} 拟合 τc LightGBM…",
        )
        tc_m = _fit_tc_lgb(tc_days, train_dates, fit_end=fit_end)
        _emit(
            fi + 1,
            n_folds,
            f"fold {fi + 1}/{n_folds} 拟合 τc LambdaRank…",
        )
        tc_rk = _fit_rank_lambda(tc_days, train_dates)
        if oo_m is None or oo_rk is None or tc_m is None or tc_rk is None:
            fold_rows.append(
                {
                    "fold": fi + 1,
                    "eval_start": seg[0],
                    "eval_end": seg[-1],
                    "fit_end": fit_end,
                    "ok": False,
                    "error": "fit_failed",
                    "oo": _arm_stats([]),
                    "tc": _arm_stats([]),
                }
            )
            continue

        _emit(fi + 1, n_folds, f"fold {fi + 1}/{n_folds} 打分…")
        oo_day_rows: List[dict] = []
        tc_day_rows: List[dict] = []
        for date in seg:
            od = oo_by.get(date)
            td = tc_by.get(date)
            base = {"date": date, "fold": fi + 1}
            if od is None:
                oo_day_rows.append({**base, "empty": True, "reason": "no_day"})
            else:
                xx = od.get("X")
                names = list(od.get("names") or [])
                n = int(xx.shape[0]) if isinstance(xx, np.ndarray) else 0
                if n <= 0:
                    oo_day_rows.append({**base, "empty": True, "reason": "bad_day"})
                else:
                    y_preds = predict_oo_lgb_matrix(
                        oo_m, xx, names, list(range(n)), row_dates=[date] * n
                    )
                    rk_preds = _predict_oo_rank_matrix(oo_rk, od)
                    pick = _pick_gated_top_n(
                        od,
                        y_preds=y_preds,
                        rank_preds=rk_preds,
                        y_min=y_min,
                        rank_score_min=rank_score_min,
                        top_n=top_n,
                    )
                    oo_day_rows.append({**base, **pick})

            if td is None:
                tc_day_rows.append({**base, "empty": True, "reason": "no_day"})
            else:
                xx = td.get("X")
                names = list(td.get("names") or [])
                n = int(xx.shape[0]) if isinstance(xx, np.ndarray) else 0
                if n <= 0:
                    tc_day_rows.append({**base, "empty": True, "reason": "bad_day"})
                else:
                    y_preds = predict_tc_lgb_matrix(
                        tc_m, xx, names, list(range(n)), row_dates=[date] * n
                    )
                    rk_preds = _predict_oo_rank_matrix(tc_rk, td)
                    pick = _pick_gated_top_n(
                        td,
                        y_preds=y_preds,
                        rank_preds=rk_preds,
                        y_min=y_min,
                        rank_score_min=rank_score_min,
                        top_n=top_n,
                    )
                    tc_day_rows.append({**base, **pick})

        fold_pack = {
            "fold": fi + 1,
            "eval_start": seg[0],
            "eval_end": seg[-1],
            "fit_end": fit_end,
            "ok": True,
            "elapsed_s": round(time.time() - t0, 1),
            "oo": _arm_stats(oo_day_rows),
            "tc": _arm_stats(tc_day_rows),
        }
        fold_rows.append(fold_pack)
        partial: Dict[str, Any] = {
            "success": True,
            "partial": True,
            "params": {
                "lookback": lb,
                "holdout": hold_n,
                "folds": n_folds,
                "y_min": y_min,
                "rank_score_min": rank_score_min,
                "top_n": top_n,
                "universe": "tier_a",
            },
            "window": {
                "start": test_days[0],
                "end": test_days[-1],
                "n_pool": len(stock_bars),
                "n_tier_a": len(a_codes),
            },
            "folds": list(fold_rows),
            "summary": {
                "oo": _sum_arm(fold_rows, "oo"),
                "tc": _sum_arm(fold_rows, "tc"),
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if run_id:
            partial["job_id"] = run_id
        prog_patch: Dict[str, Any] = {"partial": partial}
        if run_id:
            prog_patch["job_id"] = run_id
        _set_progress(**prog_patch)
        if persist:
            try:
                persist_last(partial)
            except Exception:  # noqa: BLE001
                logger.debug("roll_gate partial persist failed", exc_info=True)

    ok_folds = [f for f in fold_rows if f.get("ok") is True]
    if not fold_rows or not ok_folds:
        err = "无成功折（全部拟合失败或评测窗为空）"
        out: Dict[str, Any] = {
            "success": False,
            "error": err,
            "partial": bool(fold_rows),
            "params": {
                "lookback": lb,
                "holdout": hold_n,
                "folds": n_folds,
                "y_min": y_min,
                "rank_score_min": rank_score_min,
                "top_n": top_n,
                "universe": "tier_a",
            },
            "window": {
                "n_pool": len(stock_bars),
                "n_tier_a": len(a_codes),
            },
            "folds": fold_rows,
            "summary": {
                "oo": _sum_arm(fold_rows, "oo"),
                "tc": _sum_arm(fold_rows, "tc"),
            },
        }
        if run_id:
            out["job_id"] = run_id
        _set_progress(status="failed", message=err, fold=n_folds, folds=n_folds)
        return out

    summary = {
        "oo": _sum_arm(fold_rows, "oo"),
        "tc": _sum_arm(fold_rows, "tc"),
    }
    doc: Dict[str, Any] = {
        "success": True,
        "partial": False,
        "params": {
            "lookback": lb,
            "holdout": hold_n,
            "folds": n_folds,
            "y_min": y_min,
            "rank_score_min": rank_score_min,
            "top_n": top_n,
            "universe": "tier_a",
            "embargo": 1,
            "oo_rank_backend": "lambdarank",
            "tc_rank_backend": "lambdarank",
            "tc_clock": "open",
        },
        "window": {
            "start": test_days[0] if test_days else None,
            "end": test_days[-1] if test_days else None,
            "n_test_days": len(test_days),
            "n_pool": len(stock_bars),
            "n_tier_a": len(a_codes),
        },
        "folds": fold_rows,
        "summary": summary,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "note": "A 档宇宙；每折重训；空仓不计命中分母；不写模型/不入库",
    }
    if run_id:
        doc["job_id"] = run_id
    if persist:
        persist_last(doc)
    _set_progress(
        status="done",
        message="完成",
        fold=n_folds,
        folds=n_folds,
        partial=doc,
    )
    return doc


def start_roll_gate_job(
    *,
    lookback: int = DEFAULT_LOOKBACK,
    holdout: int = DEFAULT_HOLDOUT,
    folds: int = DEFAULT_FOLDS,
    y_min: float = DEFAULT_Y_MIN,
    rank_score_min: float = DEFAULT_RANK_SCORE_MIN,
    top_n: int = DEFAULT_TOP_N,
) -> Dict[str, Any]:
    """后台开槽跑；返回 job 快照。"""
    from core.job_progress import roll_gate_job

    roll_gate_job.reclaim_if_stale()
    n_folds = max(1, min(20, int(folds or DEFAULT_FOLDS)))
    job_id, started_or_running = roll_gate_job.try_start(
        kind="roll-gate",
        total=n_folds,
        message="排队中…",
    )
    if job_id is None:
        return {
            "ok": True,
            "success": True,
            "background": True,
            "reused": True,
            "job": started_or_running or roll_gate_job.get(),
        }

    # 新开槽：清掉上一轮内存 partial，避免 UI 把旧折表当成当前进度
    _set_progress(
        status="running",
        message="排队中…",
        fold=0,
        folds=n_folds,
        partial=None,
        job_id=job_id,
    )

    def _worker() -> None:
        stop_hb = threading.Event()

        def _hb() -> None:
            while not stop_hb.wait(30.0):
                try:
                    roll_gate_job.touch(job_id=job_id)
                except Exception:  # noqa: BLE001
                    break

        def _pcb(cur: int, tot: int, msg: str) -> None:
            try:
                pct = 100.0 * float(cur) / float(max(1, tot))
                roll_gate_job.update(
                    current=int(cur),
                    total=int(tot),
                    pct=pct,
                    message=str(msg or ""),
                    job_id=job_id,
                )
            except Exception:  # noqa: BLE001
                logger.debug("roll_gate job update failed", exc_info=True)

        threading.Thread(
            target=_hb, name="roll-gate-hb", daemon=True
        ).start()
        try:
            result = run_roll_gate(
                lookback=lookback,
                holdout=holdout,
                folds=folds,
                y_min=y_min,
                rank_score_min=rank_score_min,
                top_n=top_n,
                progress_cb=_pcb,
                persist=True,
                job_id=job_id,
            )
            if result.get("success"):
                try:
                    roll_gate_job.update(
                        message="滚动验证完成", job_id=job_id
                    )
                except Exception:  # noqa: BLE001
                    pass
                roll_gate_job.finish(result=result, job_id=job_id)
            else:
                roll_gate_job.finish(
                    error=str(result.get("error") or "failed"),
                    job_id=job_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("roll_gate job failed")
            try:
                roll_gate_job.finish(error=str(exc), job_id=job_id)
            except Exception:  # noqa: BLE001
                pass
            _set_progress(status="failed", message=str(exc))
        finally:
            stop_hb.set()

    threading.Thread(target=_worker, name="roll-gate", daemon=True).start()
    return {
        "ok": True,
        "success": True,
        "background": True,
        "reused": False,
        "job": roll_gate_job.get(),
    }
