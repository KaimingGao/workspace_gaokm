"""Persist last product / T0 backtest so /replay and /follow can restore after refresh."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from core.io_atomic import atomic_write_json
from core.paths import LAST_PORTFOLIO_BACKTEST_PATH, LAST_T0_BACKTEST_PATH

logger = logging.getLogger(__name__)

# 历史回测页只画 KPI / 净值 / 成交账 / 分票贡献；其余研究块不落盘。
_KEEP_KEYS = (
    "success",
    "metrics",
    "equity_curve",
    "equity_curve_tail",
    "benchmark",
    "sim_trades",
    "stock_contrib",
    "request",
    "params",
    "universe",
    "alpha_beta_legs",
    "loaded_stocks",
    "cost_model",
    "engine",
)


def _slim_universe(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    fails = raw.get("load_failures") or []
    if not isinstance(fails, list):
        fails = []
    return {
        "source": raw.get("source"),
        "candidate_count": raw.get("candidate_count"),
        "loaded_count": raw.get("loaded_count"),
        "note": raw.get("note"),
        "load_failures": fails[:20],
    }


def _slim_result(result: Dict[str, Any]) -> Dict[str, Any]:
    slim: Dict[str, Any] = {}
    for key in _KEEP_KEYS:
        if key not in result:
            continue
        val = result[key]
        if key == "universe":
            slim[key] = _slim_universe(val)
        elif key == "loaded_stocks" and isinstance(val, list):
            slim[key] = [str(c) for c in val[:200]]
        else:
            slim[key] = val
    if slim.get("success") is None:
        slim["success"] = True
    return slim


def save_last_portfolio_backtest(
    result: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> str:
    """成功产品回测后落盘，供 /replay 刷新恢复。"""
    p = path or LAST_PORTFOLIO_BACKTEST_PATH
    from core.signal.score_display import json_safe

    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "result": json_safe(_slim_result(result or {})),
    }
    atomic_write_json(p, payload)
    return p


def load_last_portfolio_backtest(path: Optional[str] = None) -> Dict[str, Any]:
    return _load_snapshot(path or LAST_PORTFOLIO_BACKTEST_PATH)


# 做 T 回测页只画指标 / 图 / 成交样本；逐票全日 days 不落盘。
_T0_DROP_KEYS = ("results",)
_T0_DAY_DROP_KEYS = ("minute_bars", "hist_bars", "_t0_score_snap")
_T0_REWRITE_IF_LARGER = 1_500_000


def _is_t0_feat_bulk_key(key: Any) -> bool:
    """features / 规格 / 系数：体积大，展开 tip 不依赖；仅成交触发根保留对照。"""
    s = str(key or "")
    return (
        s.startswith("score_formula")
        or s.startswith("features_")
        or s.startswith("y_spec")
        or s
        in (
            "factor_coefficients",
            "active_features",
            "zscore_means",
            "zscore_stds",
        )
    )


def _is_t0_tip_bulk_key(key: Any) -> bool:
    s = str(key or "")
    return s.startswith("formula_terms") or _is_t0_feat_bulk_key(s)


def _slim_t0_scan_row(row: Any) -> Optional[dict]:
    """扫描行保留 formula_terms_*，展开日期明细各钟 tip 才有组成。

    features / y_spec / 重复的 score_formula 只留 leg1，控制 last_t0 体积。
    """
    if not isinstance(row, dict):
        return None
    keep_feat = bool(row.get("leg1"))
    out = {
        k: v
        for k, v in row.items()
        if v is not None and (keep_feat or not _is_t0_feat_bulk_key(k))
    }
    return out or None


def _strip_t0_tip_bulk(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_t0_tip_bulk(v)
            for k, v in obj.items()
            if not _is_t0_tip_bulk_key(k)
        }
    if isinstance(obj, list):
        return [_strip_t0_tip_bulk(x) for x in obj]
    return obj


def _slim_t0_day(day: Any) -> Any:
    if not isinstance(day, dict):
        return day
    out = {k: v for k, v in day.items() if k not in _T0_DAY_DROP_KEYS}
    scan = out.pop("close_band_scan", None)
    out = _strip_t0_tip_bulk(out)
    if isinstance(scan, list):
        out["close_band_scan"] = [r for r in (_slim_t0_scan_row(x) for x in scan) if r]
    return out


def _slim_t0_result(result: Dict[str, Any]) -> Dict[str, Any]:
    slim = {k: v for k, v in (result or {}).items() if k not in _T0_DROP_KEYS}
    sample = slim.get("trade_days_sample")
    days = slim.get("days")
    if (
        isinstance(sample, list)
        and isinstance(days, list)
        and len(days) > len(sample) + 20
    ):
        slim["days"] = sample
    if isinstance(slim.get("days"), list):
        slim["days"] = [_slim_t0_day(d) for d in slim["days"]]
        slim.pop("trade_days_sample", None)
    elif isinstance(sample, list):
        slim["trade_days_sample"] = [_slim_t0_day(d) for d in sample]
    if slim.get("success") is None:
        slim["success"] = True
    return slim


def save_last_t0_backtest(
    result: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> str:
    """成功做 T 回测后落盘，供 /follow 刷新恢复。"""
    p = path or LAST_T0_BACKTEST_PATH
    from core.signal.score_display import json_safe

    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "result": json_safe(_slim_t0_result(result or {})),
    }
    atomic_write_json(p, payload)
    return p


def load_last_t0_backtest(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or LAST_T0_BACKTEST_PATH
    pack = _load_snapshot(p)
    result = pack.get("result")
    if not isinstance(result, dict):
        return pack
    slim = _slim_t0_result(result)
    pack["result"] = slim
    try:
        raw_size = os.path.getsize(p) if os.path.isfile(p) else 0
    except OSError:
        raw_size = 0
    if raw_size > _T0_REWRITE_IF_LARGER:
        try:
            from core.signal.score_display import json_safe

            atomic_write_json(
                p,
                {
                    "saved_at": pack.get("saved_at")
                    or datetime.now().isoformat(timespec="seconds"),
                    "result": json_safe(slim),
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("rewrite slim t0 snapshot failed", exc_info=True)
    return pack


def _load_snapshot(path: str) -> Dict[str, Any]:
    p = path
    if not os.path.isfile(p):
        return {"ok": False, "empty": True, "result": None, "path": p}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "ok": False,
            "empty": True,
            "error": str(e),
            "result": None,
            "path": p,
        }
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or not result.get("success"):
        return {"ok": False, "empty": True, "result": None, "path": p}
    return {
        "ok": True,
        "empty": False,
        "path": p,
        "saved_at": data.get("saved_at"),
        "result": result,
    }
