"""Arm·T 拟合曲线：步损失和每个 epoch 的 Holdout。只记显示，不选权重。"""

from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CURVE_ENV = "YHAT_FIT_CURVE"
_MAX_STEPS = 800
# 只留图上的轴。步损失、学习率和梯度在 fit.log 里。
_STEP_FIELDS = ("step", "avg")
_EPOCH_FIELDS = ("ep", "rank_ic", "ndcg30", "mse")


def finite_num(value: Any, *, ndigits: int = 6) -> Optional[float]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return round(num, int(ndigits))


def append_fit_curve(row: Dict[str, Any], path: Optional[str] = None) -> None:
    """追加一行。未设置路径时不写。坏行不影响训练。"""
    dest = path if path is not None else os.environ.get(CURVE_ENV, "").strip()
    if not dest or not isinstance(row, dict) or not row.get("k"):
        return
    try:
        with open(dest, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        logger.debug("fit curve append failed path=%s", dest, exc_info=True)


def reset_fit_curve(path: str) -> None:
    """新一轮拟合开始时清空，避免上一轮的点留在运行台上。"""
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8"):
            pass
    except OSError:
        logger.exception("fit curve reset failed path=%s", path)


def load_fit_curve(path: str) -> Optional[Dict[str, Any]]:
    """读曲线。没有点时返回 None。步点过多时均匀抽稀，并保留最后一点。"""
    if not path or not os.path.isfile(path):
        return None
    steps: List[Dict[str, Any]] = []
    epochs: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                row = _parse_line(line)
                if not row:
                    continue
                kind = row.get("k")
                if kind == "s":
                    point = _point(row, _STEP_FIELDS)
                    if point and "step" in point:
                        steps.append(point)
                elif kind == "e":
                    point = _point(row, _EPOCH_FIELDS)
                    if point and "ep" in point:
                        epochs.append(point)
    except OSError:
        logger.debug("fit curve load failed path=%s", path, exc_info=True)
        return None
    if not steps and not epochs:
        return None
    return {"steps": _thin(steps, _MAX_STEPS), "epochs": epochs}


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    text = str(line or "").strip()
    if not text:
        return None
    try:
        row = json.loads(text)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _point(row: Dict[str, Any], fields: tuple) -> Optional[Dict[str, Any]]:
    out: Dict[str, Any] = {}
    for key in fields:
        if key not in row:
            continue
        if key in ("step", "ep"):
            try:
                out[key] = int(row[key])
            except (TypeError, ValueError):
                continue
            continue
        num = finite_num(row[key])
        if num is not None:
            out[key] = num
    return out or None


def _thin(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    count = len(rows)
    keep = int(limit)
    if count <= keep or keep < 2:
        return rows if keep >= count else rows[-1:]
    picked: List[Dict[str, Any]] = []
    last_idx = -1
    for slot in range(keep):
        idx = int(round(slot * (count - 1) / float(keep - 1)))
        if idx == last_idx:
            continue
        picked.append(rows[idx])
        last_idx = idx
    if picked[-1] is not rows[-1]:
        picked.append(rows[-1])
    return picked
