"""每日任务 preset（Web / CLI / cron 共用）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

DAILY_PRESETS: Dict[str, Dict[str, Any]] = {
    "advisor": {
        "label": "投顾日常",
        "description": "纸面观察池 + 离线 golden checklist（工作日收盘后推荐）",
        "paper_run": True,
        "paper_buy": False,
        "eval_mock": True,
        "eval_agent": False,
        "quant_report": False,
        "watching_refresh": False,
        "cross_section": False,
        "sync_paper_watchlist": False,
        "paper_rebalance": False,
        "export_quant_report": False,
        "portfolio_neutral_compare": False,
    },
    "quant": {
        "label": "量化研究",
        "description": "刷新 watching、横截面、量化日报（组ŷ主叙事 + 中性化对照）并导出 Markdown/HTML",
        "paper_run": False,
        "paper_buy": False,
        "eval_mock": False,
        "eval_agent": False,
        "quant_report": True,
        "watching_refresh": True,
        "cross_section": True,
        "sync_paper_watchlist": True,
        "paper_rebalance": False,
        "export_quant_report": True,
        "portfolio_neutral_compare": True,
    },
    "full": {
        "label": "全量日常",
        "description": "投顾 + 量化（含中性化对照，不含纸面调仓与 Agent 回归）",
        "paper_run": True,
        "paper_buy": False,
        "eval_mock": True,
        "eval_agent": False,
        "quant_report": True,
        "watching_refresh": True,
        "cross_section": True,
        "sync_paper_watchlist": True,
        "paper_rebalance": False,
        "export_quant_report": True,
        "portfolio_neutral_compare": True,
    },
    "quant_paper": {
        "label": "量化 + 纸面调仓",
        "description": "quant 全流程 + 中性化对照 + 按纸面 max_positions 做横截面 TopK 调仓（显式 opt-in，非实盘；会卖出非 Top 持仓）",
        "paper_run": False,
        "paper_buy": False,
        "eval_mock": False,
        "eval_agent": False,
        "quant_report": True,
        "watching_refresh": True,
        "cross_section": True,
        "sync_paper_watchlist": True,
        "paper_rebalance": True,
        "export_quant_report": True,
        "portfolio_neutral_compare": True,
    },
}

_BOOL_KEYS = (
    "paper_run",
    "paper_buy",
    "eval_mock",
    "eval_agent",
    "quant_report",
    "watching_refresh",
    "cross_section",
    "sync_paper_watchlist",
    "paper_rebalance",
    "export_quant_report",
    "portfolio_neutral_compare",
)


def list_daily_presets() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name, cfg in DAILY_PRESETS.items():
        out.append(
            {
                "name": name,
                "label": cfg.get("label") or name,
                "description": cfg.get("description") or "",
                "flags": {k: bool(cfg.get(k)) for k in _BOOL_KEYS},
            }
        )
    return out


def resolve_daily_preset(
    preset: Optional[str] = None,
    *,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """合并 preset 与显式 overrides；显式 True/False 覆盖 preset。"""
    base: Dict[str, Any] = {k: False for k in _BOOL_KEYS}
    preset_name = (preset or "").strip().lower() or None
    if preset_name:
        if preset_name not in DAILY_PRESETS:
            raise ValueError(f"未知 preset: {preset!r}，可选: {', '.join(DAILY_PRESETS)}")
        for key in _BOOL_KEYS:
            base[key] = bool(DAILY_PRESETS[preset_name].get(key))

    for key in _BOOL_KEYS:
        if overrides and key in overrides and overrides[key] is not None:
            base[key] = bool(overrides[key])

    return {
        "preset": preset_name,
        "flags": deepcopy(base),
    }
