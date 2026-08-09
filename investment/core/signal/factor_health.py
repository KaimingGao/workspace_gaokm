"""X3 · 生产面因子健康：有源 / proxy / 稀疏 / 权重冲突。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# 无真数据 ingest、仅代理或常稀疏的因子
PROXY_OR_UNSOURCED = {
    "money_flow": {
        "status": "proxy",
        "note": "无净流入 ingest；默认 MFI 代理；权重须保持 0 除非人审有真源",
    },
    "alt_sentiment": {
        "status": "prior_only",
        "note": "无历史 news 面板；不进 ŷ；仅 sentiment.prior 旁路",
    },
    "llm_sentiment": {
        "status": "prior_only",
        "note": "Qwen LLM 舆情（研究轨）；不进 ŷ；仅作 alt_sentiment 研究对照",
    },
}


def assess_factor_health(*, config: Optional[dict] = None) -> Dict[str, Any]:
    """只读：对照 signal_config.weights 与因子数据源状态。"""
    if config is None:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:
            config = {}
    weights = dict((config or {}).get("weights") or {})
    rows: List[Dict[str, Any]] = []
    blockers: List[str] = []
    warnings: List[str] = []

    for key, meta in PROXY_OR_UNSOURCED.items():
        try:
            w = float(weights.get(key) or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        row = {
            "factor": key,
            "weight": w,
            "status": meta["status"],
            "note": meta["note"],
            "ok": True,
        }
        if meta["status"] == "proxy" and abs(w) > 1e-12:
            row["ok"] = False
            blockers.append(
                f"{key}: weight={w} 但无真数据源（proxy）；请归零或接入净流入后再启用"
            )
        if meta["status"] == "prior_only" and abs(w) > 1e-12:
            # include_in_score 硬闸另管；权重非 0 仍警告
            row["ok"] = False
            warnings.append(f"{key}: weight={w} 但契约为 prior_only，不应进生产 β")
        rows.append(row)

    # 指数依赖因子：live 已接 index；仅作健康说明
    for key in ("relative_strength", "idio_momentum"):
        try:
            w = float(weights.get(key) or 0.0)
        except (TypeError, ValueError):
            w = 0.0
        rows.append(
            {
                "factor": key,
                "weight": w,
                "status": "index_linked",
                "note": "依赖 live index_bars（X1）；无指数时降级/中性",
                "ok": True,
            }
        )

    ok = not blockers
    return {
        "ok": ok,
        "track": "X3",
        "rows": rows,
        "blockers": blockers,
        "warnings": warnings,
        "promote_blocked": bool(blockers),
        "note": "生产面因子健康 lite；sparse OLS 丢弃另见拟合报告",
    }


def guard_weights_for_promote(
    weights: Optional[dict],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """人审 promote / 写权前检查：money_flow 等非 0 且无真源时拦截（除非 force）。"""
    w = dict(weights or {})
    health = assess_factor_health(config={"weights": w})
    if health.get("promote_blocked") and not force:
        return {
            "ok": False,
            "blocked": True,
            "error": "; ".join(health.get("blockers") or ["factor_health_blocked"]),
            "factor_health": health,
        }
    return {
        "ok": True,
        "blocked": False,
        "factor_health": health,
        "forced": bool(force) and bool(health.get("promote_blocked")),
    }
