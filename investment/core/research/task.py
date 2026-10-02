"""研究任务装配：模型头注册表 + 记录步骤。

一次运行 = 已登记的模型头 + 返回前的记录管道。
Web 仍可按头调用 QuantService.run_*_experiment；那些方法经
``records_experiment`` 走同一管道。``dispatch_research_task`` 只分发已登记的头。

不在这里加载任意类，也不把调仓 / 做 T 收成同一执行器。
"""

from __future__ import annotations

import inspect
import logging
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from core.experiment_tracker import canonical_model_type

logger = logging.getLogger(__name__)

# model_type -> QuantService 方法名
_HEADS: Dict[str, str] = {}

_SCALAR = (str, int, float, bool)


def list_research_heads() -> List[str]:
    return sorted(_HEADS)


def research_head_method(model_type: str) -> Optional[str]:
    key = canonical_model_type(model_type)
    return _HEADS.get(key)


def _scalar_config(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """只留下可放进实验 JSON 的标量配置，避免把 bars 写进记录。"""
    cfg: Dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "self":
            continue
        if value is None or isinstance(value, _SCALAR):
            cfg[key] = value
    return cfg


def describe_processor_windows(report: Dict[str, Any]) -> Dict[str, Any]:
    """标出研究套（训练窗）和执行套（全样本）各自的标准化窗口。

    样本外块若显式标明 model_role=live，视为用错了模型。
    未标明时，只要存在 return_model_research，就记为 research。
    """
    rpt = report or {}
    research = rpt.get("return_model_research")
    live = rpt.get("return_model")
    oos = rpt.get("oos") if isinstance(rpt.get("oos"), dict) else {}
    oos_role = oos.get("model_role")
    inferred = None
    if isinstance(research, dict) and oos:
        inferred = "research"
    role = oos_role or inferred
    ok = role != "live"
    return {
        "ok": ok,
        "research_window": "train" if isinstance(research, dict) else None,
        "live_window": "full" if isinstance(live, dict) else None,
        "oos_model_role": role,
        "fit_end": rpt.get("fit_end"),
        "eval_start": rpt.get("eval_start"),
        "error": None if ok else "oos_used_live_model",
    }


def research_zscore_leaks(train_mean: float, recorded_mean: float, *, tol: float = 1e-6) -> bool:
    """研究套 z-score 均值偏离训练窗均值时为 True。"""
    try:
        return abs(float(recorded_mean) - float(train_mean)) > float(tol)
    except (TypeError, ValueError):
        return True


def finish_research_run(
    model_type: str,
    config: Dict[str, Any],
    report: Dict[str, Any],
) -> Optional[str]:
    """记录步骤：把本次 fit 写入实验追踪器。已有 experiment_id 时不重复写。"""
    if not isinstance(report, dict):
        return None
    if report.get("cluster_retired"):
        return None
    existing = report.get("experiment_id")
    if existing:
        return str(existing)
    windows = describe_processor_windows(report)
    tags: Dict[str, str] = {}
    pit = report.get("pit_fundamentals")
    if pit is None:
        pit = report.get("fundamentals_pit")
    if pit is True:
        tags["fundamentals_pit"] = "true"
    elif pit is False:
        tags["fundamentals_pit"] = "false"
    from core import experiment_tracker as et

    eid = et.track_fit_report(
        model_type,
        dict(config or {}),
        report,
        tags=tags or None,
    )
    extra: Dict[str, Any] = {
        "research_window": windows.get("research_window"),
        "live_window": windows.get("live_window"),
        "oos_model_role": windows.get("oos_model_role"),
        "oos_guard_ok": bool(windows.get("ok")),
    }
    for key in ("fit_end", "eval_start"):
        if report.get(key) not in (None, ""):
            extra[key] = report.get(key)
    et.log_metrics(eid, {k: v for k, v in extra.items() if v is not None})
    report["experiment_id"] = eid
    report["processor_windows"] = windows
    return eid


def records_experiment(model_type: str) -> Callable:
    """装饰 QuantService.run_*_experiment：返回前写入实验记录。"""

    def deco(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            report = fn(self, *args, **kwargs)
            if isinstance(report, dict) and not report.get("cluster_retired"):
                try:
                    bound = inspect.signature(fn).bind(self, *args, **kwargs)
                    bound.apply_defaults()
                    cfg = _scalar_config(dict(bound.arguments))
                    finish_research_run(model_type, cfg, report)
                except Exception:
                    logger.exception("research record failed: %s", model_type)
            return report

        _HEADS[str(model_type)] = wrapper.__name__
        return wrapper

    return deco


def dispatch_research_task(service: Any, head: str, **params: Any) -> Dict[str, Any]:
    """只调用已登记的研究头。参数不匹配时返回错误，不抛到路由外。"""
    raw = str(head or "").strip()
    key = canonical_model_type(raw)
    name = _HEADS.get(key)
    if not name or not hasattr(service, name):
        return {
            "success": False,
            "error": f"unknown research head: {head}",
            "heads": list_research_heads(),
        }
    fn = getattr(service, name)
    try:
        out = fn(**params)
    except TypeError as exc:
        return {"success": False, "error": str(exc), "head": key}
    if isinstance(out, dict):
        return out
    return {"success": False, "error": "research head did not return a dict", "head": key}
