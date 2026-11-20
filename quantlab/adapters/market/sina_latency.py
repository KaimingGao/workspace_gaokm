"""新浪日线单次响应时长。

子进程打完 ``stock_zh_a_daily`` 后写一条样本。数据中心读最近若干次，
把接口耗时和等名额的排队分开，避免把整批墙钟当成新浪变慢。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from core.file_lock import path_lock
from core.paths import DATA_DIR

_WINDOW = 60
_OUTCOMES = ("ok", "empty", "timeout", "error", "slot_wait")


def sina_daily_latency_path() -> str:
    raw = os.environ.get("QUANTLAB_SINA_DAILY_LATENCY_PATH", "").strip()
    if raw:
        return raw
    return os.path.join(DATA_DIR, "run", "sina_daily_latency.json")


def _blank() -> Dict[str, Any]:
    return {
        "n": 0,
        "window": _WINDOW,
        "ok": 0,
        "empty": 0,
        "timeout": 0,
        "error": 0,
        "slot_wait": 0,
        "http_p50_s": None,
        "http_p95_s": None,
        "http_max_s": None,
        "wait_p50_s": None,
        "wait_p95_s": None,
        "last": None,
    }


def _load(path: str) -> List[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("samples") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _save(path: str, samples: List[dict]) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"samples": samples[-_WINDOW:]}, fh)
    os.replace(tmp, path)


def _num(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val < 0:
        return None
    return round(val, 3)


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 3)
    rank = (len(xs) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return round(xs[lo] * (1.0 - frac) + xs[hi] * frac, 3)


def record_sina_daily_sample(
    *,
    http_s: Optional[float],
    wait_s: Optional[float],
    outcome: str,
) -> None:
    """追加一条。测试进程默认不写正式文件，除非显式指定路径。"""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get(
        "QUANTLAB_SINA_DAILY_LATENCY_PATH"
    ):
        return
    label = str(outcome or "error")
    if label not in _OUTCOMES:
        label = "error"
    row = {
        "ts": round(time.time(), 3),
        "http_s": _num(http_s),
        "wait_s": _num(wait_s),
        "outcome": label,
    }
    path = sina_daily_latency_path()
    try:
        with path_lock(path, timeout_sec=2.0):
            samples = _load(path)
            samples.append(row)
            _save(path, samples)
    except Exception:
        return


def sina_daily_latency_snapshot() -> Dict[str, Any]:
    path = sina_daily_latency_path()
    try:
        with path_lock(path, timeout_sec=1.0):
            samples = _load(path)[-_WINDOW:]
    except Exception:
        return _blank()
    out = _blank()
    out["n"] = len(samples)
    http_vals: List[float] = []
    wait_vals: List[float] = []
    for row in samples:
        label = str(row.get("outcome") or "")
        if label in _OUTCOMES:
            out[label] = int(out[label]) + 1
        else:
            out["error"] = int(out["error"]) + 1
        http_s = _num(row.get("http_s"))
        wait_s = _num(row.get("wait_s"))
        if http_s is not None:
            http_vals.append(http_s)
        if wait_s is not None:
            wait_vals.append(wait_s)
    out["http_p50_s"] = _percentile(http_vals, 0.50)
    out["http_p95_s"] = _percentile(http_vals, 0.95)
    out["http_max_s"] = round(max(http_vals), 3) if http_vals else None
    out["wait_p50_s"] = _percentile(wait_vals, 0.50)
    out["wait_p95_s"] = _percentile(wait_vals, 0.95)
    out["last"] = samples[-1] if samples else None
    return out
