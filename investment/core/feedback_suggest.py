"""反馈半闭环：根据纸面/回测摘要提出 signal_config 补丁建议（不自动写盘）。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict, Optional

from core.paths import SIGNAL_CONFIG_PATH


def _load_config(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or SIGNAL_CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def suggest_config_feedback(
    *,
    paper_metrics: Optional[Dict[str, Any]] = None,
    backtest_metrics: Optional[Dict[str, Any]] = None,
    monitor_alerts: Optional[list] = None,
    config_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    启发式建议（人确认后再合并）：
    - 回撤过大 → 提高 min_score / 收紧 probe 阈值
    - 胜率偏低 → 小幅提高 momentum 权重提示（仅建议，不重算 IC）
    - 监控告警（N5）→ 写入 reasons，并按告警码收紧阈值（仍不写盘）
    """
    cfg = _load_config(config_path)
    patch: Dict[str, Any] = {}
    reasons: list = []

    try:
        from core.north_star import TTM_EVENT_IDEA, append_ttm_event

        append_ttm_event(TTM_EVENT_IDEA, ref="feedback_suggest")
    except Exception:
        pass

    bt = backtest_metrics or {}
    paper = paper_metrics or {}
    alerts = list(monitor_alerts or [])

    max_dd = bt.get("max_drawdown_pct")
    if max_dd is None:
        max_dd = paper.get("max_drawdown_pct")
    try:
        max_dd_f = float(max_dd) if max_dd is not None else None
    except (TypeError, ValueError):
        max_dd_f = None

    win_rate = bt.get("win_rate_pct")
    try:
        win_f = float(win_rate) if win_rate is not None else None
    except (TypeError, ValueError):
        win_f = None

    alert_codes = set()
    for a in alerts:
        if isinstance(a, dict):
            code = str(a.get("code") or "").strip()
            msg = str(a.get("message") or a.get("code") or "").strip()
            if code:
                alert_codes.add(code)
            if msg:
                reasons.append(f"监控告警：{msg}")
        elif a:
            reasons.append(f"监控告警：{a}")

    if "drawdown_limit" in alert_codes or "drawdown_target" in alert_codes:
        if max_dd_f is None:
            max_dd_f = 15.0 if "drawdown_limit" in alert_codes else 12.0

    if "ic_decay" in alert_codes and win_f is None:
        win_f = 40.0

    rank = dict(cfg.get("rank") or {})
    thresholds = dict(cfg.get("stance_thresholds") or {})
    weights = dict(cfg.get("weights") or {})

    if max_dd_f is not None and max_dd_f >= 12:
        new_min = min(75, int(rank.get("min_score") or 55) + 5)
        if new_min != rank.get("min_score"):
            rank["min_score"] = new_min
            patch["rank"] = rank
            reasons.append(f"最大回撤约 {max_dd_f}% ≥ 12%，建议提高 rank.min_score → {new_min}")
        probe = int(thresholds.get("probe") or 68)
        new_probe = min(80, probe + 3)
        if new_probe != probe:
            thresholds["probe"] = new_probe
            patch["stance_thresholds"] = thresholds
            reasons.append(f"同步收紧 stance_thresholds.probe → {new_probe}")

    if win_f is not None and win_f < 45 and "momentum" in weights:
        w = copy.deepcopy(weights)
        bump = 0.02
        w["momentum"] = round(float(w["momentum"]) + bump, 4)
        # 从 volume_price 匀一点
        if "volume_price" in w:
            w["volume_price"] = round(max(0.05, float(w["volume_price"]) - bump), 4)
        s = sum(float(x) for x in w.values()) or 1.0
        w = {k: round(float(v) / s, 4) for k, v in w.items()}
        patch["weights"] = w
        reasons.append(f"胜率约 {win_f}% < 45%，建议略增 momentum 权重（须样本外验证）")

    if not patch:
        reasons.append("指标未触发启发式规则，暂无配置补丁；可继续跑 IC/阈值建议。")

    return {
        "ok": True,
        "success": True,
        "auto_apply": False,
        "reasons": reasons,
        "patch": patch,
        "monitor_alerts": alerts,
        "note": (
            "仅生成建议补丁，不写盘。请人工审阅后合并到 signal_config.json，"
            "再经策略页 promote。市场有风险，不保证收益，不代客下单。"
        ),
        "config_path": config_path or SIGNAL_CONFIG_PATH,
    }
