"""实验追踪器（吸收 Qlib Recorder 思想，轻量版无 MLflow 依赖）。

记录模型训练实验的配置、指标、产物，支持查询与对比。

设计原则：
- 纯 JSON 存储，对齐 run_manifest 的 atomic_write_json 风格
- 不引入 MLflow 等重依赖
- 接口简洁：start → log_metrics/log_artifact → finish，或一步 log_experiment
- 支持按模型类型筛选、按指标排序对比

存储路径: ``data/experiments/<model_type>/<ts>_<fp>.json``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.numbers import now_iso_local as _now_iso
from core.paths import DATA_DIR

EXPERIMENTS_DIR = os.path.join(DATA_DIR, "experiments")

# 进行中的实验（进程内）
_active: Dict[str, Dict[str, Any]] = {}


def _fingerprint(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _ts_slug() -> str:
    return _now_iso().replace(":", "").replace("-", "").replace("T", "_").replace(" ", "_")[:19]


def _dir_for(model_type: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(model_type or "unknown"))
    return os.path.join(EXPERIMENTS_DIR, safe)


# --------------------------------------------------------------------------- #
# 核心接口
# --------------------------------------------------------------------------- #
def start_experiment(
    model_type: str,
    config: Dict[str, Any],
    *,
    experiment_id: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
) -> str:
    """开始一个实验，返回 experiment_id。

    Args:
        model_type: 模型类型，如 "tau_tree" / "oo_rank" / "ridge"
        config: 训练配置（超参、因子集、数据窗口、标签等）
        experiment_id: 自定义 ID；默认自动生成
        tags: 标签，如 {"dataset": "csi300", "author": "gaokm"}
    """
    exp_id = experiment_id or f"{model_type}_{_ts_slug()}_{_fingerprint(config)[:8]}"
    record = {
        "experiment_id": exp_id,
        "model_type": str(model_type),
        "status": "running",
        "config": dict(config or {}),
        "metrics": {},
        "artifacts": {},
        "tags": dict(tags or {}),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "duration_sec": None,
    }
    _active[exp_id] = record
    _persist(record)
    logger.info("experiment started: %s (%s)", exp_id, model_type)
    return exp_id


def log_metrics(experiment_id: str, metrics: Dict[str, Any]) -> None:
    """记录指标（增量更新，重复 key 覆盖）。"""
    rec = _active.get(experiment_id)
    if rec is None:
        rec = _load(experiment_id)
    if rec is None:
        logger.warning("experiment not found, cannot log metrics: %s", experiment_id)
        return
    rec.setdefault("metrics", {}).update(metrics or {})
    rec["updated_at"] = _now_iso()
    _persist(rec)
    _active[experiment_id] = rec


def log_artifact(experiment_id: str, name: str, path: str) -> None:
    """记录产物路径（模型文件、报告等）。"""
    rec = _active.get(experiment_id)
    if rec is None:
        rec = _load(experiment_id)
    if rec is None:
        logger.warning("experiment not found, cannot log artifact: %s", experiment_id)
        return
    rec.setdefault("artifacts", {})[name] = str(path)
    rec["updated_at"] = _now_iso()
    _persist(rec)
    _active[experiment_id] = rec


def finish_experiment(
    experiment_id: str,
    *,
    status: str = "completed",
    metrics: Optional[Dict[str, Any]] = None,
    artifacts: Optional[Dict[str, str]] = None,
    error: Optional[str] = None,
) -> None:
    """结束实验。"""
    rec = _active.get(experiment_id)
    if rec is None:
        rec = _load(experiment_id)
    if rec is None:
        logger.warning("experiment not found, cannot finish: %s", experiment_id)
        return
    if metrics:
        rec.setdefault("metrics", {}).update(metrics)
    if artifacts:
        rec.setdefault("artifacts", {}).update(artifacts)
    if error:
        rec["error"] = str(error)[:500]
    rec["status"] = status
    rec["updated_at"] = _now_iso()
    created = rec.get("created_at")
    if created:
        try:
            rec["duration_sec"] = round(time.time() - time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")), 2)
        except Exception:  # noqa: BLE001
            rec["duration_sec"] = None
    _persist(rec)
    _active.pop(experiment_id, None)
    logger.info("experiment %s: %s", experiment_id, status)


def log_experiment(
    model_type: str,
    config: Dict[str, Any],
    metrics: Dict[str, Any],
    *,
    artifacts: Optional[Dict[str, str]] = None,
    tags: Optional[Dict[str, str]] = None,
    status: str = "completed",
    error: Optional[str] = None,
) -> str:
    """一步到位：创建实验并记录指标+产物+结束。返回 experiment_id。"""
    exp_id = start_experiment(model_type, config, tags=tags)
    finish_experiment(exp_id, status=status, metrics=metrics, artifacts=artifacts, error=error)
    return exp_id


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def list_experiments(
    model_type: Optional[str] = None,
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """列出实验，按 created_at 倒序。"""
    results: List[Dict[str, Any]] = []
    if not os.path.isdir(EXPERIMENTS_DIR):
        return results
    dirs = [model_type] if model_type else os.listdir(EXPERIMENTS_DIR)
    for d in dirs:
        full = os.path.join(EXPERIMENTS_DIR, d)
        if not os.path.isdir(full):
            continue
        for fname in sorted(os.listdir(full), reverse=True):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(full, fname), encoding="utf-8") as f:
                    rec = json.load(f)
            except Exception:  # noqa: BLE001
                continue
            if status and rec.get("status") != status:
                continue
            results.append(rec)
            if len(results) >= limit:
                return results
    return results


def get_experiment(experiment_id: str) -> Optional[Dict[str, Any]]:
    """按 ID 获取实验详情。"""
    rec = _active.get(experiment_id)
    if rec is not None:
        return rec
    return _load(experiment_id)


def compare_experiments(
    experiment_ids: Sequence[str],
    metric_keys: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """对比多个实验的配置与指标。

    返回每行一个实验，包含 model_type / 关键配置 / 指定指标。
    """
    rows = []
    for eid in experiment_ids:
        rec = get_experiment(str(eid))
        if rec is None:
            rows.append({"experiment_id": eid, "error": "not found"})
            continue
        row = {
            "experiment_id": eid,
            "model_type": rec.get("model_type"),
            "status": rec.get("status"),
            "created_at": rec.get("created_at"),
            "duration_sec": rec.get("duration_sec"),
        }
        metrics = rec.get("metrics") or {}
        keys = metric_keys or list(metrics.keys())
        for k in keys:
            row[f"metric.{k}"] = metrics.get(k)
        rows.append(row)
    return rows


def best_experiment(
    model_type: str,
    metric_key: str,
    *,
    higher_is_better: bool = True,
    status: str = "completed",
) -> Optional[Dict[str, Any]]:
    """找出某模型类型下指定指标最优的实验。"""
    exps = list_experiments(model_type, status=status, limit=200)
    best = None
    best_val = None
    for rec in exps:
        val = (rec.get("metrics") or {}).get(metric_key)
        if val is None:
            continue
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if best is None or (higher_is_better and val > best_val) or (not higher_is_better and val < best_val):
            best = rec
            best_val = val
    return best


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def _persist(record: Dict[str, Any]) -> str:
    d = _dir_for(record.get("model_type", "unknown"))
    os.makedirs(d, exist_ok=True)
    eid = str(record.get("experiment_id") or "na")
    path = os.path.join(d, f"{eid}.json")
    atomic_write_json(path, record)
    return path


def _load(experiment_id: str) -> Optional[Dict[str, Any]]:
    if not os.path.isdir(EXPERIMENTS_DIR):
        return None
    for d in os.listdir(EXPERIMENTS_DIR):
        full = os.path.join(EXPERIMENTS_DIR, d)
        if not os.path.isdir(full):
            continue
        path = os.path.join(full, f"{experiment_id}.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:  # noqa: BLE001
                return None
    return None


# --------------------------------------------------------------------------- #
# 与 fit_*_report 便捷对接
# --------------------------------------------------------------------------- #
def track_fit_report(
    model_type: str,
    config: Dict[str, Any],
    report: Dict[str, Any],
    *,
    tags: Optional[Dict[str, str]] = None,
    artifacts: Optional[Dict[str, str]] = None,
) -> str:
    """把 fit_*_report 返回的报告自动转为实验记录。

    从 report 中提取常见指标（ic / rank_ic / oos_return / ir / mdd 等），
    自动判断 success 状态并记录。返回 experiment_id。

    用法::

        report = fit_tau_tree_report(bars, backend="lightgbm", ...)
        track_fit_report("tau_tree", {"backend": "lightgbm", ...}, report)
    """
    metrics: Dict[str, Any] = {}
    rpt = report or {}
    # 常见指标字段（兼容不同 fit 函数的返回结构）
    for key in ("ic", "rank_ic", "spearman_ic", "oos_return", "annualized_return",
                "ir", "information_ratio", "mdd", "max_drawdown", "sharpe",
                "hit_rate", "sample_count", "stock_count"):
        if key in rpt and rpt[key] is not None:
            metrics[key] = rpt[key]
    # 嵌套的 oos / test 指标
    for sub in ("oos", "test", "validation"):
        sub_obj = rpt.get(sub)
        if isinstance(sub_obj, dict):
            for k, v in sub_obj.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    metrics[f"{sub}_{k}"] = v
    status = "completed" if rpt.get("success", True) else "failed"
    error = None if status == "completed" else str(rpt.get("error") or "")[:500]
    return log_experiment(
        model_type,
        config,
        metrics,
        artifacts=artifacts,
        tags=tags,
        status=status,
        error=error,
    )
