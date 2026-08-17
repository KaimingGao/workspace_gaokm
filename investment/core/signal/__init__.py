"""评分领域包：信封 · 门禁 · SignalService。

兼容入口仍为 ``core.signal_service``（模块函数 → 默认 Service.as_dict()）。
实现仍在 ``score_stock`` / ``rank_*``；本包只收口出口。
惰性导出，避免 ``import core.signal`` 拉起打分全图。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SCALE_HEURISTIC",
    "SCALE_UNKNOWN",
    "SCALE_YHAT",
    "BookResult",
    "ResearchSignalService",
    "ScoreResult",
    "SignalService",
    "allows_production_yhat",
    "get_default_signal_service",
    "get_research_signal_service",
    "infer_score_scale",
    "metrics_snapshot",
    "reset_metrics",
    "set_default_signal_service",
    "set_research_signal_service",
]

_LAZY = {
    "SCALE_HEURISTIC": ("core.signal.gate", "SCALE_HEURISTIC"),
    "SCALE_UNKNOWN": ("core.signal.gate", "SCALE_UNKNOWN"),
    "SCALE_YHAT": ("core.signal.gate", "SCALE_YHAT"),
    "allows_production_yhat": ("core.signal.gate", "allows_production_yhat"),
    "infer_score_scale": ("core.signal.gate", "infer_score_scale"),
    "BookResult": ("core.signal.types", "BookResult"),
    "ScoreResult": ("core.signal.types", "ScoreResult"),
    "SignalService": ("core.signal.service", "SignalService"),
    "ResearchSignalService": ("core.signal.service", "ResearchSignalService"),
    "get_default_signal_service": ("core.signal.service", "get_default_signal_service"),
    "get_research_signal_service": ("core.signal.service", "get_research_signal_service"),
    "set_default_signal_service": ("core.signal.service", "set_default_signal_service"),
    "set_research_signal_service": ("core.signal.service", "set_research_signal_service"),
    "metrics_snapshot": ("core.signal.service", "metrics_snapshot"),
    "reset_metrics": ("core.signal.service", "reset_metrics"),
}


def __getattr__(name: str) -> Any:
    import importlib

    target = _LAZY.get(name)
    if target is not None:
        mod_name, attr = target
        mod = importlib.import_module(mod_name)
        val = getattr(mod, attr)
        globals()[name] = val
        return val
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name}") from exc


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
