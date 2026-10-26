"""轻量 TopK 权益曲线（权建议 OOS 专用）。

等权 · 硬 TopK · 无 tip/归因/涨跌停/止损影子。产出与门禁兼容的
``equity_curve`` / ``metrics`` / ``trades``，替代完整 ``topk_backtest`` 路径。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.backtest.engine import _mock_quote_from_bars, _trade_metrics

logger = logging.getLogger(__name__)


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _bars_by_date(bars: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    if not isinstance(bars, (list, tuple)):
        return out
    for b in bars:
        if not isinstance(b, dict):
            continue
        d = str(b.get("date") or "").strip()
        if d:
            out[d] = b
    return out


def _common_dates(stock_bars: Dict[str, List[dict]]) -> List[str]:
    keys: Optional[set] = None
    for bars in (stock_bars or {}).values():
        ds = set(_bars_by_date(bars).keys())
        keys = ds if keys is None else (keys & ds)
    return sorted(keys or [])


def _window_for_code(
    code: str,
    dates: List[str],
    date_maps: Dict[str, Dict[str, dict]],
    end_idx: int,
    max_window: int,
) -> List[dict]:
    start = max(0, end_idx - max_window + 1)
    dm = date_maps.get(code) or {}
    return [dm[d] for d in dates[start : end_idx + 1] if d in dm]


def _close_return_pct(
    date_maps: Dict[str, Dict[str, dict]],
    code: str,
    start_date: str,
    end_date: str,
) -> Optional[float]:
    dm = date_maps.get(code) or {}
    a = dm.get(start_date)
    b = dm.get(end_date)
    if not a or not b:
        return None
    try:
        pa = float(a.get("close") or 0)
        pb = float(b.get("close") or 0)
    except (TypeError, ValueError):
        return None
    if pa <= 0:
        return None
    return (pb / pa - 1.0) * 100.0


def _leg_forward_return(
    date_maps: Dict[str, Dict[str, dict]],
    code: str,
    entry_date: str,
    exit_date: str,
) -> Optional[float]:
    """次日开盘入 → 持有期末收盘出；缺开盘则用入场日收盘。"""
    dm = date_maps.get(code) or {}
    ent = dm.get(entry_date)
    ex = dm.get(exit_date)
    if not ent or not ex:
        return None
    try:
        entry_px = float(ent.get("open") or ent.get("close") or 0)
        exit_px = float(ex.get("close") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(entry_px) or not math.isfinite(exit_px):
        return None
    if entry_px <= 0 or exit_px <= 0:
        return None
    ret = (exit_px / entry_px - 1.0) * 100.0
    if not math.isfinite(ret):
        return None
    return ret


def oos_label_exit_index(signal_i: int, horizon_days: int) -> Optional[int]:
    """信号日下标 → 持有期末收盘的日期下标。下标不是整数时返回 None。"""
    try:
        return int(signal_i) + 1 + int(horizon_days)
    except (TypeError, ValueError):
        return None


def oos_label_ready(
    signal_i: int,
    decision_i: int,
    horizon_days: int,
    n_dates: int,
) -> bool:
    """平仓日已经落在决策日及以前，且仍在样本内。坏下标视为未就绪。"""
    exit_idx = oos_label_exit_index(signal_i, horizon_days)
    if exit_idx is None or exit_idx < 0:
        return False
    try:
        decision = int(decision_i)
        n = int(n_dates)
    except (TypeError, ValueError):
        return False
    return exit_idx < n and exit_idx <= decision


def run_oos_topk_curve(
    stock_bars: Dict[str, List[dict]],
    *,
    top_k: int = 3,
    horizon_days: int = 3,
    min_score: float = 55.0,
    min_history: int = 12,
    max_window: int = 30,
    rank_mode: str = "predicted_score",
    min_predicted_score: Optional[float] = None,
    allow_heuristic_baseline: bool = False,
    return_model_min_samples: int = 24,
    return_model_ridge_lambda: float = 0.0,
    neutralize: Optional[bool] = False,
    progress_cb: Optional[Callable[..., Any]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    **_ignored: Any,
) -> Dict[str, Any]:
    """OOS 门禁用轻量横截面 TopK 曲线。

    忽略完整回测的成本/涨跌停/权重模式等 kwargs（经 ``**_ignored`` 吞掉），
    保证 ``weight_oos_gate`` 共用 kwargs 可直接传入。
    """
    from core.signal.config import load_signal_config
    from core.signal.cross_section_batch import score_and_rank_watching, score_window_as_item
    from core.signal.return_score import (
        clamp_rank_mode,
        fit_return_model_from_panel,
        resolve_research_rank_mode,
    )

    if not stock_bars:
        return {"success": False, "error": "无标的日线"}

    cfg = load_signal_config()
    cfg = dict(cfg)
    scoring = dict(cfg.get("scoring") or {})
    scoring["skip_minute_io"] = True
    cfg["scoring"] = scoring
    ds = dict(cfg.get("dual_score") or {})
    ds["enable_minute_tau"] = False
    cfg["dual_score"] = ds

    resolved_rank_mode = (
        resolve_research_rank_mode(rank_mode)
        if allow_heuristic_baseline
        else clamp_rank_mode(rank_mode)
    )
    top_k = max(1, min(_as_int(top_k or 3, 3), 40))
    horizon_days = max(1, min(_as_int(horizon_days or 3, 3), 10))
    min_history = max(5, _as_int(min_history or 12, 12))
    max_window = max(min_history, _as_int(max_window or 30, 30))
    min_fit = max(8, _as_int(return_model_min_samples or 24, 24))
    try:
        ridge_lam = float(return_model_ridge_lambda or 0.0)
    except (TypeError, ValueError):
        ridge_lam = 0.0
    if not math.isfinite(ridge_lam) or ridge_lam < 0:
        ridge_lam = 0.0
    use_neutral = False if neutralize is None else bool(neutralize)

    date_maps = {code: _bars_by_date(bars) for code, bars in stock_bars.items()}
    dates = _common_dates(stock_bars)
    n = len(dates)
    need = min_history + horizon_days + 2
    if n < need:
        return {
            "success": False,
            "error": f"共同交易日不足（{n} 根，需要 {need}+）",
            "common_dates": n,
        }

    returns: List[float] = []
    trades: List[dict] = []
    equity_curve: List[dict] = []
    equity = 100.0
    pending_signals: List[dict] = []
    train_xs: List[Dict[str, Optional[float]]] = []
    train_ys: List[float] = []
    return_model = None
    rebalance_idx = 0

    i = min_history - 1
    equity_curve.append({"date": dates[i], "equity": equity, "return_pct": 0.0})
    last_signal_i = n - horizon_days - 2
    first_signal_i = i
    n_signal_days = max(0, last_signal_i - first_signal_i + 1)

    while i <= last_signal_i:
        if cancel_check is not None:
            try:
                if cancel_check():
                    return {"success": False, "error": "已取消"}
            except Exception:  # noqa: BLE001
                logger.debug("cancel_check raised", exc_info=True)

        signal_as_of = dates[i]
        day_n = i - first_signal_i + 1
        if progress_cb is not None and (day_n <= 1 or day_n % 4 == 0 or i >= last_signal_i):
            try:
                progress_cb(
                    f"{signal_as_of} · {day_n}/{n_signal_days} 日",
                    max(0, day_n),
                    max(1, n_signal_days),
                )
            except Exception:  # noqa: BLE001
                logger.debug("progress_cb raised", exc_info=True)

        entries: List[dict] = []
        for code, bars in stock_bars.items():
            window = _window_for_code(code, dates, date_maps, i, max_window)
            if len(window) < min_history:
                continue
            quote = _mock_quote_from_bars(window, len(window) - 1)
            item = score_window_as_item(
                code,
                window,
                horizon_days=horizon_days,
                quote=quote,
                config=cfg,
                fundamentals=None,
            )
            if item:
                entries.append(item)

        if resolved_rank_mode == "predicted_score":
            still_pending: List[dict] = []
            for p in pending_signals:
                if not isinstance(p, dict):
                    continue
                try:
                    j = int(p["signal_i"])
                except (TypeError, ValueError, KeyError):
                    continue
                # 训练目标 = 次日开盘入 → 持有期末收盘出（与实际 leg 收益对齐）
                # 平仓日未到不入库，避免用未来收盘拟合当天排序。
                exit_idx = oos_label_exit_index(j, horizon_days)
                if exit_idx is None or j < 0 or exit_idx < 0 or exit_idx >= n or j + 1 >= n:
                    continue
                if oos_label_ready(j, i, horizon_days, n):
                    try:
                        entry_date = dates[j + 1]
                        exit_date = dates[exit_idx]
                        y = _leg_forward_return(
                            date_maps,
                            str(p["code"]),
                            entry_date,
                            exit_date,
                        )
                    except Exception:  # noqa: BLE001 — 单条标签失败不中断曲线
                        logger.debug("oos label failed code=%s j=%s", p.get("code"), j, exc_info=True)
                        continue
                    subs = p.get("sub_scores") or {}
                    if (
                        y is not None
                        and math.isfinite(float(y))
                        and isinstance(subs, dict)
                        and subs
                    ):
                        train_xs.append(dict(subs))
                        train_ys.append(float(y))
                else:
                    still_pending.append(p)
            pending_signals = still_pending
            for item in entries:
                code = str(item.get("stock_code") or "").strip()
                if not code:
                    continue
                pending_signals.append(
                    {
                        "signal_i": i,
                        "signal_date": signal_as_of,
                        "code": code,
                        "sub_scores": dict(item.get("sub_scores") or {}),
                    }
                )
            if len(train_ys) >= min_fit:
                model, _fit_rep = fit_return_model_from_panel(
                    train_xs,
                    train_ys,
                    horizon_days=horizon_days,
                    ridge_lambda=ridge_lam,
                    fitted_as_of=signal_as_of,
                    min_samples=min_fit,
                )
                if model is not None:
                    return_model = model

        picks, _meta = score_and_rank_watching(
            entries,
            min_score=min_score,
            config=cfg,
            neutralize=use_neutral,
            rank_mode=resolved_rank_mode,
            return_model=return_model,
            return_models_by_code=None,
            min_predicted_score=min_predicted_score,
            allow_heuristic_baseline=allow_heuristic_baseline
            or resolved_rank_mode == "heuristic_score",
            apply_tau_buy_gate=False,
            exclude_oos_failed=False,
        )
        selected = list(picks[:top_k])
        rebalance_idx += 1
        if not selected:
            i += 1
            continue

        entry_date = dates[i + 1]
        exit_date = dates[i + 1 + horizon_days]
        leg_rets: List[float] = []
        legs: List[dict] = []
        for pick in selected:
            if not isinstance(pick, (list, tuple)) or len(pick) < 2:
                continue
            code, score = pick[0], pick[1]
            ret = _leg_forward_return(date_maps, str(code), entry_date, exit_date)
            if ret is None:
                continue
            leg_rets.append(float(ret))
            legs.append(
                {
                    "stock_code": str(code),
                    "score": score,
                    "return_pct": round(float(ret), 2),
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "weight_pct": 0.0,
                }
            )
        if not leg_rets:
            i += 1
            continue

        w = 100.0 / len(leg_rets)
        for leg in legs:
            leg["weight_pct"] = round(w, 4)
        port_ret = sum(leg_rets) / len(leg_rets)
        returns.append(port_ret)
        equity *= 1.0 + port_ret / 100.0
        trades.append(
            {
                "signal_date": signal_as_of,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "hold_days": horizon_days,
                "return_pct": round(port_ret, 2),
                "legs": legs,
                "top_k": len(legs),
                "weight_mode": "equal",
            }
        )
        equity_curve.append(
            {
                "date": exit_date,
                "equity": round(equity, 2),
                "return_pct": round(port_ret, 2),
            }
        )
        i += horizon_days

    metrics = _trade_metrics(returns, holding_days=horizon_days)
    return {
        "success": True,
        "strategy": "oos_topk_curve",
        "params": {
            "engine": "oos_topk_curve",
            "probe_lite": True,
            "top_k": top_k,
            "horizon_days": horizon_days,
            "rank_mode": resolved_rank_mode,
            "min_predicted_score": min_predicted_score,
            "weight_mode": "equal",
            "common_dates": n,
            "stock_count": len(stock_bars),
            "apply_costs": False,
            "apply_tau_buy_gate": False,
            "limit_filter": False,
        },
        "metrics": metrics,
        "trade_count": len(trades),
        "equity_curve": equity_curve,
        "trades": trades,
        "note": "轻量 OOS TopK 曲线（等权硬 TopK；≠ paper_replay / 完整 topk_research）。",
    }
