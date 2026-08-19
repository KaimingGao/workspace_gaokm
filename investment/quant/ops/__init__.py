import logging

logger = logging.getLogger(__name__)
from quant.ops.daily_presets import DAILY_PRESETS, list_daily_presets, resolve_daily_preset

__all__ = [
    "DAILY_PRESETS",
    "list_daily_presets",
    "resolve_daily_preset",
    "build_daily_health",
]


def __getattr__(name: str):
    if name == "build_daily_health":
        from quant.ops.daily_health import build_daily_health

        return build_daily_health
    if name == "build_eval_routing_map":
        from quant.ops.eval_routing_map import build_eval_routing_map

        return build_eval_routing_map
    if name == "build_quant_package_info":
        from quant.ops.package_info import build_quant_package_info

        return build_quant_package_info
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
