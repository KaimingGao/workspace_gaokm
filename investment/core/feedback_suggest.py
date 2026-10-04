"""反馈半闭环：根据纸面/回测摘要提出 signal_config 补丁建议（不自动写盘）。

生产选股门槛为 ŷ 滞回（scoring.*）；rank.min_score 字段已下线。
"""


import logging

logger = logging.getLogger(__name__)
import json
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
    - 回撤过大 → 提高 scoring.min_predicted_score / 收紧 stance probe（ŷ%）
    - ŷ IC 衰减 → 提示重拟合分组，不自动改 weights
    - 监控告警 → 写入 reasons（仍不写盘）
    """
    cfg = _load_config(config_path)
    patch: Dict[str, Any] = {}
    reasons: list = []

    try:
        from core.north_star import TTM_EVENT_IDEA, append_ttm_event

        append_ttm_event(TTM_EVENT_IDEA, ref="feedback_suggest")
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in feedback_suggest.py", exc_info=True)
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

    if ("ic_decay" in alert_codes or "yhat_ic_decay" in alert_codes) and win_f is None:
        win_f = 40.0

    scoring = dict(cfg.get("scoring") or {})
    thresholds = dict(cfg.get("stance_thresholds") or {})

    if max_dd_f is not None and max_dd_f >= 12:
        try:
            cur_buy = float(scoring.get("min_predicted_score") or 1.0)
        except (TypeError, ValueError):
            cur_buy = 1.0
        new_buy = round(min(5.0, cur_buy + 0.5), 2)
        if new_buy != cur_buy:
            scoring["min_predicted_score"] = new_buy
            scoring.setdefault("rank_mode", "predicted_score")
            if "min_hold_predicted_score" not in scoring:
                scoring["min_hold_predicted_score"] = -1.0
            patch["scoring"] = scoring
            reasons.append(
                f"最大回撤约 {max_dd_f}% ≥ 12%，建议提高 ŷ 买入门槛 "
                f"min_predicted_score → {new_buy}（策略中心人审写盘）"
            )
        try:
            probe = float(thresholds.get("probe") or 0.35)
        except (TypeError, ValueError):
            probe = 0.35
        # ŷ% stance：probe 常见 0.35；若误存 0–100 则跳过收紧
        if probe < 10:
            new_probe = round(min(2.0, probe + 0.1), 2)
            if new_probe != probe:
                thresholds["probe"] = new_probe
                patch["stance_thresholds"] = thresholds
                reasons.append(f"同步收紧 stance_thresholds.probe → {new_probe}%")

    if "yhat_ic_decay" in alert_codes or "ic_decay" in alert_codes:
        reasons.append(
            "因子/ŷ IC 衰减：请研究枢纽跑分组→对照重拟合；勿自动改 signal_config.weights"
        )

    if win_f is not None and win_f < 45:
        reasons.append(
            f"胜率约 {win_f}% 偏低：优先检查组 β OOS 与 ŷ 滞回，不建议盲加 momentum 权"
        )

    return {
        "success": True,
        "ok": True,
        "auto_apply": False,
        "patch": patch,
        "reasons": reasons,
        "note": "仅建议；合并须人审。选股门槛=scoring ŷ 滞回，不写 weights。",
        "signal_config_weights_touched": False,
    }
