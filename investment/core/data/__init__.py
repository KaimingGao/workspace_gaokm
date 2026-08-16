"""数据层领域包：信封 · Ports · MarketDataService。

兼容入口仍为 ``core.data_service``（模块函数 → 默认 Service.as_dict()）。
惰性导出，避免 ``import core.data`` 过早拉起 service→ports 全图。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DEFAULT_ADJUST_POLICY",
    "BarsResult",
    "DataEnvelope",
    "MarketDataService",
    "MarketPorts",
    "PitMeta",
    "QualityMeta",
    "ResearchDataService",
    "allows_production_score",
    "default_ports",
    "get_default_service",
    "get_research_service",
    "infer_adjust",
    "normalize_adjust_policy",
    "metrics_snapshot",
    "reset_metrics",
    "set_default_service",
    "set_research_service",
]

_LAZY = {
    "DEFAULT_ADJUST_POLICY": ("core.data.gate", "DEFAULT_ADJUST_POLICY"),
    "allows_production_score": ("core.data.gate", "allows_production_score"),
    "infer_adjust": ("core.data.gate", "infer_adjust"),
    "normalize_adjust_policy": ("core.data.gate", "normalize_adjust_policy"),
    "MarketPorts": ("core.data.ports", "MarketPorts"),
    "default_ports": ("core.data.ports", "default_ports"),
    "BarsResult": ("core.data.types", "BarsResult"),
    "DataEnvelope": ("core.data.types", "DataEnvelope"),
    "PitMeta": ("core.data.types", "PitMeta"),
    "QualityMeta": ("core.data.types", "QualityMeta"),
    "MarketDataService": ("core.data.service", "MarketDataService"),
    "ResearchDataService": ("core.data.service", "ResearchDataService"),
    "get_default_service": ("core.data.service", "get_default_service"),
    "get_research_service": ("core.data.service", "get_research_service"),
    "metrics_snapshot": ("core.data.service", "metrics_snapshot"),
    "reset_metrics": ("core.data.service", "reset_metrics"),
    "set_default_service": ("core.data.service", "set_default_service"),
    "set_research_service": ("core.data.service", "set_research_service"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = target
    import importlib

    mod = importlib.import_module(mod_name)
    val = getattr(mod, attr)
    globals()[name] = val
    return val


def __dir__() -> list:
    return sorted(set(globals()) | set(__all__))
