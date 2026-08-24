"""基于 OOS 的 stance 阈值微调建议（P13.3）。

- 线上 ``stance_thresholds`` 为 ŷ% 量纲时：用组 β / 就地拟合模型扫 wait，再推 avoid/probe。
- 遗留 0–100 门槛表：仍走旧 ``min_score`` OOS（兼容）。
不自动写 ``signal_config``。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, List, Optional, Sequence

from core.signal.config import get_stance_thresholds, load_signal_config

STANCE_KEYS = ("avoid", "wait", "probe")
# 遗留 0–100：avoid≥10；ŷ%：avoid 通常 < 10
_HEURISTIC_AVOID_FLOOR = 10.0

# ŷ% 扫描网格与步长
_YHAT_WAIT_GRID = (-0.5, 0.0, 0.25, 0.5, 1.0, 1.5)
_YHAT_MAX_DELTA = 0.5
_YHAT_AVOID_GAP = 0.5
_YHAT_PROBE_GAP = 0.35
_YHAT_MIN_DELTA = 0.15  # 小于此视为无需改


def _is_predicted_stance_scale(thresholds: Dict[str, float]) -> bool:
    try:
        return float(thresholds.get("avoid", 0)) < _HEURISTIC_AVOID_FLOOR
    except (TypeError, ValueError):
        return True


def _fmt_pct(v: Any) -> str:
    try:
        return f"{round(float(v), 1)}"
    except (TypeError, ValueError):
        return "—"


def _trade_metrics(returns: Sequence[float]) -> Dict[str, Any]:
    rets = [float(r) for r in returns if r is not None]
    if not rets:
        return {
            "trade_count": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "total_return_pct": None,
        }
    wins = sum(1 for r in rets if r > 0)
    avg = sum(rets) / len(rets)
    equity = 1.0
    for r in rets:
        equity *= 1.0 + r / 100.0
    return {
        "trade_count": len(rets),
        "win_rate_pct": round(wins / len(rets) * 100.0, 1),
        "avg_return_pct": round(avg, 2),
        "total_return_pct": round((equity - 1.0) * 100.0, 2),
    }


def _hold_return_pct(bars: List[dict], entry_idx: int, hold_days: int) -> Optional[float]:
    if entry_idx < 0 or entry_idx + hold_days >= len(bars):
        return None
    entry = bars[entry_idx].get("close")
    exit_p = bars[entry_idx + hold_days].get("close")
    if not entry:
        return None
    try:
        return round((float(exit_p) / float(entry) - 1.0) * 100.0, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _fit_yhat_model_on_bars(
    bars: List[dict],
    *,
    end_index: int,
    horizon_days: int = 3,
) -> Any:
    """在 bars[:end_index] 上拟合 ReturnScoreModel；失败返回 None。"""
    from core.research.panel import collect_subscore_forward_panel
    from core.signal.return_score import fit_return_model_from_panel

    if end_index < 40 or len(bars) < 40:
        return None
    train = bars[: max(40, int(end_index))]
    try:
        xs, ys, _dates = collect_subscore_forward_panel(
            train,
            horizon_days=horizon_days,
            max_window=30,
        )
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in threshold_suggest.py", exc_info=True)
        return None
    model, _rep = fit_return_model_from_panel(
        xs,
        ys,
        horizon_days=horizon_days,
        min_samples=24,
    )
    return model


def _build_yhat_series(
    bars: List[dict],
    model: Any,
    *,
    horizon_days: int = 3,
    min_history: int = 12,
    max_window: int = 30,
) -> List[Dict[str, Any]]:
    """预计算每日 ŷ 与前瞻收益，供多档 wait 复用。"""
    from core.backtest.engine import _mock_quote_from_bars, _prepare_scoring_window
    from core.signal.scorer import score_bars

    n = len(bars or [])
    horizon_days = max(1, min(int(horizon_days or 3), 10))
    min_history = max(5, int(min_history or 12))
    max_window = max(10, int(max_window or 30))
    max_i = n - horizon_days - 1
    out: List[Dict[str, Any]] = []
    if max_i <= min_history or model is None:
        return out

    for i in range(min_history - 1, max_i):
        window, _src = _prepare_scoring_window(
            bars, i, max_window=max_window, data_mode="full"
        )
        quote = _mock_quote_from_bars(bars, i)
        scored = score_bars(window, horizon_days=horizon_days, quote=quote)
        if scored.get("hard_reject"):
            continue
        yhat = model.predict(scored.get("sub_scores") or {})
        if yhat is None:
            continue
        # next_open 近似：信号日 i，收益用 i→i+h 收盘（与研究口径一致）
        fwd = _hold_return_pct(bars, i, horizon_days)
        if fwd is None:
            continue
        out.append(
            {
                "index": i,
                "date": str((bars[i] or {}).get("date") or "")[:10],
                "yhat": float(yhat),
                "fwd_return_pct": float(fwd),
            }
        )
    return out


def _eval_wait_on_series(
    series: Sequence[Dict[str, Any]],
    wait: float,
    *,
    start_index: int,
    end_index: int,
    horizon_days: int,
) -> Dict[str, Any]:
    rets: List[float] = []
    i = 0
    n = len(series)
    while i < n:
        row = series[i]
        idx = int(row["index"])
        if idx < start_index:
            i += 1
            continue
        if idx >= end_index:
            break
        if float(row["yhat"]) >= float(wait):
            rets.append(float(row["fwd_return_pct"]))
            # 非重叠：跳过约 horizon 根
            target = idx + max(1, int(horizon_days))
            j = i + 1
            while j < n and int(series[j]["index"]) < target:
                j += 1
            i = j
        else:
            i += 1
    return _trade_metrics(rets)


def scan_yhat_wait_oos(
    bars: List[dict],
    *,
    model: Any = None,
    wait_grid: Sequence[float] = _YHAT_WAIT_GRID,
    horizon_days: int = 3,
    train_ratio: float = 0.6,
    valid_ratio: float = 0.2,
    min_test_trades: int = 3,
) -> Dict[str, Any]:
    """ŷ% wait 的 train/valid/test OOS 扫描。"""
    from core.research.split import time_series_split

    n = len(bars or [])
    if n < 50:
        return {"success": False, "error": f"日线不足（{n}）"}

    split = time_series_split(n, train_ratio=train_ratio, valid_ratio=valid_ratio)
    use_model = model
    model_source = "provided"
    if use_model is None:
        use_model = _fit_yhat_model_on_bars(
            bars, end_index=split.train_end, horizon_days=horizon_days
        )
        model_source = "fit_train"
    if use_model is None:
        return {"success": False, "error": "无 ŷ 模型（组 β 缺失且 train 拟合失败）"}

    series = _build_yhat_series(bars, use_model, horizon_days=horizon_days)
    if len(series) < 20:
        return {"success": False, "error": f"ŷ 序列过短（{len(series)}）", "model_source": model_source}

    grid = [float(x) for x in (wait_grid or _YHAT_WAIT_GRID)]
    scored_rows: List[dict] = []
    for w in grid:
        valid_m = _eval_wait_on_series(
            series,
            w,
            start_index=split.train_end,
            end_index=split.valid_end,
            horizon_days=horizon_days,
        )
        scored_rows.append(
            {
                "wait": w,
                "valid_win_rate_pct": valid_m.get("win_rate_pct"),
                "valid_trade_count": valid_m.get("trade_count"),
                "valid_total_return_pct": valid_m.get("total_return_pct"),
            }
        )

    # valid 上优先：有足够交易 → 胜率 → 累计收益
    def _key(r: dict) -> tuple:
        n_tr = int(r.get("valid_trade_count") or 0)
        wr = r.get("valid_win_rate_pct")
        tot = r.get("valid_total_return_pct")
        return (
            0 if n_tr >= min_test_trades else 1,
            -(float(wr) if wr is not None else -1e9),
            -(float(tot) if tot is not None else -1e9),
        )

    scored_rows.sort(key=_key)
    best = scored_rows[0]
    best_wait = float(best["wait"])
    test_m = _eval_wait_on_series(
        series,
        best_wait,
        start_index=split.valid_end,
        end_index=series[-1]["index"] + 1,
        horizon_days=horizon_days,
    )
    return {
        "success": True,
        "best_params": {"wait": best_wait, "horizon_days": horizon_days, "min_score": best_wait},
        "split": {
            "train_end": split.train_end,
            "valid_end": split.valid_end,
            "test_end": split.test_end,
        },
        "valid_top": scored_rows[:5],
        "test": {"metrics": test_m},
        "model_source": model_source,
        "series_count": len(series),
        "score_scale": "predicted_yhat",
        "note": "ŷ% wait OOS：valid 选参，test 一次性评估。",
    }


def _apply_yhat_suggestion(
    *,
    base: Dict[str, float],
    best_wait: float,
    test_metrics: Dict[str, Any],
    max_delta: float,
    min_test_trades: int,
) -> Dict[str, Any]:
    wait_cur = float(base["wait"])
    test_trades = int(test_metrics.get("trade_count") or 0)
    test_win = test_metrics.get("win_rate_pct")
    deltas = dict.fromkeys(STANCE_KEYS, 0.0)
    suggested = {k: round(float(base[k]), 2) for k in STANCE_KEYS}
    rationale: List[str] = []

    if test_trades < min_test_trades:
        rationale.append(
            f"ŷ OOS test 成交偏少（n={test_trades}），暂不改门槛；最优 wait={best_wait}"
        )
        return {
            "suggested_thresholds": suggested,
            "deltas": deltas,
            "rationale": rationale,
            "skipped_apply": True,
        }

    raw_delta = float(best_wait) - wait_cur
    capped = max(-max_delta, min(max_delta, raw_delta))
    if abs(capped) < _YHAT_MIN_DELTA:
        rationale.append(
            f"ŷ OOS 最优 wait={best_wait}，test 胜率={_fmt_pct(test_win)}%"
            f"（n={test_trades}）· 与当前接近，暂不调整"
        )
        return {
            "suggested_thresholds": suggested,
            "deltas": deltas,
            "rationale": rationale,
            "skipped_apply": True,
        }

    wait_new = round(wait_cur + capped, 2)
    avoid_new = round(wait_new - _YHAT_AVOID_GAP, 2)
    probe_new = round(wait_new + _YHAT_PROBE_GAP, 2)
    # 相对当前小步：avoid/probe 也按同向微调，但不超过 max_delta
    avoid_cap = round(
        max(
            float(base["avoid"]) - max_delta,
            min(float(base["avoid"]) + max_delta, avoid_new),
        ),
        2,
    )
    probe_cap = round(
        max(
            float(base["probe"]) - max_delta,
            min(float(base["probe"]) + max_delta, probe_new),
        ),
        2,
    )
    if avoid_cap >= wait_new:
        avoid_cap = round(wait_new - _YHAT_AVOID_GAP, 2)
    if probe_cap <= wait_new:
        probe_cap = round(wait_new + _YHAT_PROBE_GAP, 2)

    suggested["wait"] = wait_new
    suggested["avoid"] = avoid_cap
    suggested["probe"] = probe_cap
    deltas["wait"] = round(wait_new - wait_cur, 2)
    deltas["avoid"] = round(avoid_cap - float(base["avoid"]), 2)
    deltas["probe"] = round(probe_cap - float(base["probe"]), 2)
    rationale.append(
        f"ŷ OOS 最优 wait={best_wait}，test 胜率={_fmt_pct(test_win)}%"
        f"（n={test_trades}） → 建议 wait {wait_cur}→{wait_new}"
    )
    return {
        "suggested_thresholds": suggested,
        "deltas": deltas,
        "rationale": rationale,
        "skipped_apply": False,
    }


def suggest_stance_thresholds_from_oos(
    oos_result: Dict[str, Any],
    *,
    current_thresholds: Optional[Dict[str, float]] = None,
    max_delta: float = 3.0,
    min_test_trades: int = 3,
) -> Dict[str, Any]:
    """根据 OOS 结果给出阈值微调建议（不自动写配置）。

    - ``score_scale=predicted_yhat``：按 ŷ% wait 调整。
    - 否则视为旧 0–100 ``min_score``；若当前门槛已是 ŷ%，则跳过改门槛。
    """
    if not oos_result.get("success"):
        return {"success": False, "error": oos_result.get("error") or "OOS 扫描失败"}

    cfg = load_signal_config()
    base = dict(current_thresholds or get_stance_thresholds(cfg))
    if not base:
        return {"success": False, "error": "无 stance 阈值配置"}

    base_rounded = {k: round(float(base[k]), 2) for k in STANCE_KEYS}
    predicted_scale = _is_predicted_stance_scale(base_rounded)
    oos_scale = str(oos_result.get("score_scale") or "")
    test_metrics = ((oos_result.get("test") or {}).get("metrics") or {})
    test_trades = int(test_metrics.get("trade_count") or 0)
    test_win = test_metrics.get("win_rate_pct")
    best_params = oos_result.get("best_params") or {}

    # —— ŷ OOS 路径 ——
    if oos_scale == "predicted_yhat":
        best_wait = best_params.get("wait")
        if best_wait is None:
            best_wait = best_params.get("min_score")
        if best_wait is None:
            return {"success": False, "error": "ŷ OOS 缺少 best wait"}
        yhat_delta = float(max_delta) if max_delta else _YHAT_MAX_DELTA
        if yhat_delta >= 2.0:
            yhat_delta = _YHAT_MAX_DELTA
        applied = _apply_yhat_suggestion(
            base=base_rounded,
            best_wait=float(best_wait),
            test_metrics=test_metrics,
            max_delta=yhat_delta,
            min_test_trades=min_test_trades,
        )
        return {
            "success": True,
            "score_scale": "predicted",
            "skipped_apply": bool(applied.get("skipped_apply")),
            "current_thresholds": base_rounded,
            "suggested_thresholds": applied["suggested_thresholds"],
            "deltas": applied["deltas"],
            "oos": {
                "best_params": best_params,
                "test_metrics": test_metrics,
                "split": oos_result.get("split"),
                "score_scale": "predicted_yhat",
                "model_source": oos_result.get("model_source"),
            },
            "rationale": applied["rationale"]
            or ["ŷ OOS 与当前门槛大致匹配，暂不建议调整"],
            "params": {
                "max_delta": yhat_delta,
                "min_test_trades": min_test_trades,
                "wait_grid": list(_YHAT_WAIT_GRID),
            },
            "note": "ŷ% stance 建议仅供研究；人审后再改 signal_config.stance_thresholds。",
        }

    # —— 旧 0–100 扫到了 ŷ 门槛：跳过 ——
    if predicted_scale:
        best_min = best_params.get("min_score")
        rationale = [
            (
                f"OOS 参考（旧 0–100 分）：最优 min_score={best_min if best_min is not None else '—'}，"
                f"test 胜率={_fmt_pct(test_win)}%（n={test_trades}）· "
                "当前 stance 为 ŷ% 量纲，已跳过自动改门槛"
            )
        ]
        return {
            "success": True,
            "score_scale": "predicted",
            "skipped_apply": True,
            "current_thresholds": base_rounded,
            "suggested_thresholds": dict(base_rounded),
            "deltas": dict.fromkeys(STANCE_KEYS, 0.0),
            "oos": {
                "best_params": best_params,
                "test_metrics": test_metrics,
                "split": oos_result.get("split"),
                "score_scale": "heuristic_0_100",
            },
            "rationale": rationale,
            "params": {"max_delta": max_delta, "min_test_trades": min_test_trades},
            "note": (
                "OOS 仍扫规则分 0–100；线上 stance 用 ŷ%。"
                "请改用 ŷ OOS（研究池阈值）或直接改 stance_thresholds / min_predicted_score。"
            ),
        }

    # —— 遗留启发式门槛路径 ——
    rationale: List[str] = []
    deltas = dict.fromkeys(STANCE_KEYS, 0.0)
    suggested = {k: round(float(base[k]), 1) for k in STANCE_KEYS}
    best_min = best_params.get("min_score")

    if best_min is not None and test_trades >= min_test_trades:
        wait_cur = float(base["wait"])
        target_delta = float(best_min) - wait_cur
        target_delta = max(-max_delta, min(max_delta, target_delta))
        if abs(target_delta) >= 1.0:
            deltas["wait"] = round(target_delta, 1)
            suggested["wait"] = round(wait_cur + target_delta, 1)
            suggested["avoid"] = round(
                min(suggested["wait"] - 8.0, float(base["avoid"]) + target_delta * 0.5),
                1,
            )
            suggested["probe"] = round(
                max(suggested["wait"] + 10.0, float(base["probe"])),
                1,
            )
            rationale.append(
                f"OOS 最优 min_score={best_min}，test 胜率={_fmt_pct(test_win)}%"
                f"（n={test_trades}） → 建议 wait {wait_cur}→{suggested['wait']}"
            )
        elif test_win is not None and test_win < 45:
            deltas["wait"] = max_delta
            suggested["wait"] = round(float(base["wait"]) + max_delta, 1)
            suggested["probe"] = round(
                max(suggested["wait"] + 10.0, float(base["probe"])),
                1,
            )
            rationale.append(
                f"test 胜率偏低（{_fmt_pct(test_win)}%）→ 建议提高 wait +{max_delta}"
            )

    if suggested["avoid"] >= suggested["wait"]:
        suggested["avoid"] = round(suggested["wait"] - 8.0, 1)
    if suggested["probe"] <= suggested["wait"]:
        suggested["probe"] = round(suggested["wait"] + 10.0, 1)

    return {
        "success": True,
        "score_scale": "heuristic",
        "skipped_apply": False,
        "current_thresholds": {k: round(float(base[k]), 1) for k in STANCE_KEYS},
        "suggested_thresholds": suggested,
        "deltas": {k: round(deltas.get(k, 0.0), 1) for k in STANCE_KEYS},
        "oos": {
            "best_params": best_params,
            "test_metrics": test_metrics,
            "split": oos_result.get("split"),
            "score_scale": "heuristic_0_100",
        },
        "rationale": rationale or ["当前 OOS 指标与阈值大致匹配，暂不建议调整"],
        "params": {"max_delta": max_delta, "min_test_trades": min_test_trades},
        "note": "建议仅供研究；改 stance_thresholds 前须确认 test 样本外表现。",
    }


def format_threshold_config_diff(suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """生成可下载的 stance_thresholds diff（不自动写盘）。"""
    from datetime import datetime

    if not suggestion.get("success"):
        return {"success": False, "error": suggestion.get("error") or "无效阈值建议"}

    if suggestion.get("skipped_apply"):
        return {
            "success": True,
            "skipped_apply": True,
            "target_file": "data/signal_config.json",
            "patch": {},
            "changes": {},
            "deltas": suggestion.get("deltas") or {},
            "rationale": suggestion.get("rationale") or [],
            "params": suggestion.get("params") or {},
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "apply_note": "无门槛改动可合并（已跳过或与当前接近）。",
        }

    current = suggestion.get("current_thresholds") or {}
    suggested = suggestion.get("suggested_thresholds") or {}
    changes: Dict[str, dict] = {}
    min_abs = 0.1 if suggestion.get("score_scale") == "predicted" else 0.5
    for key in STANCE_KEYS:
        c = float(current.get(key, 0))
        s = float(suggested.get(key, c))
        delta = round(s - c, 2)
        if abs(delta) >= min_abs:
            changes[key] = {"from": round(c, 2), "to": round(s, 2), "delta": delta}

    return {
        "success": True,
        "skipped_apply": False,
        "target_file": "data/signal_config.json",
        "patch": {"stance_thresholds": suggested},
        "changes": changes,
        "deltas": suggestion.get("deltas") or {},
        "rationale": suggestion.get("rationale") or [],
        "params": suggestion.get("params") or {},
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "apply_note": "请手动合并 patch.stance_thresholds；须以 OOS test 指标为准。",
    }


def suggest_stance_thresholds_from_watching_oos(
    codes: List[str],
    *,
    lookback: int = 120,
    max_stocks: int = 5,
    max_delta: float = 3.0,
) -> Dict[str, Any]:
    """对 watching 多票 OOS：ŷ% 门槛用组 β/拟合扫 wait；遗留表仍扫 0–100。"""
    from core.backtest.engine import scan_signal_parameters_oos
    from core.data.facade import bars_and_source_research, get_quote

    if not codes:
        return {"success": False, "error": "候选列表为空"}

    cfg = load_signal_config()
    base = get_stance_thresholds(cfg)
    predicted_scale = _is_predicted_stance_scale(base)

    models_by_code: Dict[str, Any] = {}
    if predicted_scale:
        try:
            from core.signal.cluster.live import load_cluster_return_models_by_code

            models_by_code = load_cluster_return_models_by_code() or {}
        except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
            logger.debug("catch except Exception: in threshold_suggest.py", exc_info=True)
            models_by_code = {}

    best_waits: List[float] = []
    win_rates: List[float] = []
    per_stock: List[dict] = []
    failures: List[str] = []
    limit_n = max(1, min(int(max_stocks or 5), 10))

    for raw in codes[:limit_n]:
        quote = get_quote(str(raw))
        sym = quote.get("stock_code") if quote.get("success") else str(raw)
        sym = str(sym or raw).strip()
        bars, src = bars_and_source_research(raw, limit=lookback + 35)
        if not bars and quote.get("success"):
            bars, src = bars_and_source_research(sym, limit=lookback + 35)
        if not bars or len(bars) < 50:
            failures.append(str(raw))
            continue

        if predicted_scale:
            model = models_by_code.get(sym) or models_by_code.get(str(raw).strip())
            oos = scan_yhat_wait_oos(bars, model=model, horizon_days=3)
            if not oos.get("success"):
                failures.append(f"{sym}:{oos.get('error') or 'yhat_oos_fail'}")
                continue
            best_wait = (oos.get("best_params") or {}).get("wait")
            test_m = ((oos.get("test") or {}).get("metrics") or {})
            if best_wait is None:
                continue
            best_waits.append(float(best_wait))
            if test_m.get("win_rate_pct") is not None:
                win_rates.append(float(test_m["win_rate_pct"]))
            per_stock.append(
                {
                    "stock_code": sym,
                    "data_source": src,
                    "best_wait": best_wait,
                    "best_min_score": best_wait,  # 兼容旧字段名
                    "test_win_rate_pct": test_m.get("win_rate_pct"),
                    "test_trade_count": test_m.get("trade_count"),
                    "model_source": oos.get("model_source"),
                    "score_scale": "predicted_yhat",
                }
            )
        else:
            oos = scan_signal_parameters_oos(
                bars,
                min_scores=[45, 50, 55, 60, 65],
                horizon_days_list=[2, 3],
            )
            if not oos.get("success"):
                failures.append(sym)
                continue
            best_min = (oos.get("best_params") or {}).get("min_score")
            test_m = ((oos.get("test") or {}).get("metrics") or {})
            if best_min is None:
                continue
            best_waits.append(float(best_min))
            if test_m.get("win_rate_pct") is not None:
                win_rates.append(float(test_m["win_rate_pct"]))
            per_stock.append(
                {
                    "stock_code": sym,
                    "data_source": src,
                    "best_min_score": best_min,
                    "test_win_rate_pct": test_m.get("win_rate_pct"),
                    "test_trade_count": test_m.get("trade_count"),
                    "score_scale": "heuristic_0_100",
                }
            )

    if not best_waits:
        return {
            "success": False,
            "error": "watching OOS 无有效结果",
            "failures": failures,
        }

    best_waits.sort()
    median_best = best_waits[len(best_waits) // 2]
    avg_win = sum(win_rates) / len(win_rates) if win_rates else None
    if avg_win is not None:
        avg_win = round(float(avg_win), 1)

    if predicted_scale:
        synthetic_oos = {
            "success": True,
            "score_scale": "predicted_yhat",
            "best_params": {
                "wait": median_best,
                "min_score": median_best,
                "horizon_days": 3,
            },
            "test": {
                "metrics": {
                    "trade_count": max(3, len(best_waits) * 2),
                    "win_rate_pct": avg_win,
                }
            },
            "model_source": "watching_aggregate",
        }
        suggestion = suggest_stance_thresholds_from_oos(
            synthetic_oos,
            current_thresholds=base,
            max_delta=_YHAT_MAX_DELTA,
        )
        suggestion["watching_aggregate"] = {
            "stock_count": len(best_waits),
            "median_best_wait": median_best,
            "median_best_min_score": median_best,
            "best_waits": best_waits,
            "best_min_scores": best_waits,
            "per_stock": per_stock,
            "failures": failures,
            "score_scale": "predicted_yhat",
        }
    else:
        synthetic_oos = {
            "success": True,
            "score_scale": "heuristic_0_100",
            "best_params": {"min_score": median_best, "horizon_days": 3},
            "test": {
                "metrics": {
                    "trade_count": max(3, len(best_waits) * 2),
                    "win_rate_pct": avg_win,
                }
            },
        }
        suggestion = suggest_stance_thresholds_from_oos(
            synthetic_oos,
            current_thresholds=base,
            max_delta=max_delta,
        )
        suggestion["watching_aggregate"] = {
            "stock_count": len(best_waits),
            "median_best_min_score": median_best,
            "best_min_scores": best_waits,
            "per_stock": per_stock,
            "failures": failures,
            "score_scale": "heuristic_0_100",
        }

    suggestion["mode"] = "watching_oos_aggregate"
    return suggestion
