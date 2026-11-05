"""账户风控限额单一真源（C 轨 C1 / R-S1）。

DEFAULT 与 ``get_strategy_spec`` 缺键填空同值；产品 Spec 显式 risk 优先。
禁止各调用点私自 ``or 5`` / ``or 25`` / ``or 40``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional, TypedDict

logger = logging.getLogger(__name__)

DEFAULT_RISK_LIMITS: Dict[str, Any] = {
    "max_drawdown_pct": 20.0,
    "max_position_pct": 25.0,
    "max_sector_pct": 40.0,
    "max_positions": 20,
}

# 解析后硬夹紧，防脏 JSON / 手改 rules 拖垮调仓
_MAX_POSITIONS_CAP = 500
_PCT_LO = 0.01
_PCT_HI = 100.0


class RiskLimits(TypedDict):
    """resolve_account_risk_limits 返回形状（轻量类型，无行为）。"""

    max_drawdown_pct: float
    target_drawdown_pct: float
    max_position_pct: float
    max_sector_pct: float
    max_positions: int
    weight_mode: str


def pct_to_fraction(raw: Any, *, default: float) -> float:
    """仓位比例换算：``>1`` 视为百分数（如 20 → 0.20），否则当比例（0.25）。

    Spec.risk / paper.rules 的 ``max_position_pct`` 多为百分数；
    ``position_pct`` / kelly 内部用 0–1 比例。结果夹紧到 ``[0, 1]``。
    """
    try:
        if raw is None or raw == "":
            out = float(default)
        else:
            val = float(raw)
            if val != val or val in (float("inf"), float("-inf")):
                out = float(default)
            elif val < 0:
                out = float(default)
            elif val > 1.0:
                out = val / 100.0
            else:
                out = val
    except (TypeError, ValueError):
        out = float(default)
    if out != out or out in (float("inf"), float("-inf")):
        out = float(default)
    return max(0.0, min(1.0, out))


def _as_float(raw: Any, default: float) -> float:
    try:
        if raw is None or raw == "":
            return float(default)
        val = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if val != val or val in (float("inf"), float("-inf")):
        return float(default)
    return val


def _as_int(raw: Any, default: int) -> int:
    try:
        if raw is None or raw == "":
            return int(default)
        # 拒绝 bool（bool 是 int 子类，True→1 易误伤）
        if isinstance(raw, bool):
            return int(default)
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _clamp_pct(val: float, default: float) -> float:
    if val != val or val <= 0:
        return float(default)
    return max(_PCT_LO, min(_PCT_HI, float(val)))


def _clamp_positions(n: int, default: int) -> int:
    if n < 1:
        return max(1, int(default))
    return min(int(n), _MAX_POSITIONS_CAP)


def fill_risk_defaults(
    risk: Optional[Mapping[str, Any]] = None,
    *,
    paper_rules: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """补全 risk dict 缺键（供 ``get_strategy_spec`` 与 resolve 共用）。"""
    out: Dict[str, Any] = dict(risk or {})
    pr = paper_rules or {}
    if out.get("max_drawdown_pct") is None:
        out["max_drawdown_pct"] = DEFAULT_RISK_LIMITS["max_drawdown_pct"]
    if out.get("max_position_pct") is None:
        out["max_position_pct"] = DEFAULT_RISK_LIMITS["max_position_pct"]
    if out.get("max_sector_pct") is None:
        out["max_sector_pct"] = DEFAULT_RISK_LIMITS["max_sector_pct"]
    if out.get("max_positions") is None:
        out["max_positions"] = _as_int(
            pr.get("max_positions"),
            int(DEFAULT_RISK_LIMITS["max_positions"]),
        )
    if out.get("weight_mode") is None:
        out["weight_mode"] = str(pr.get("weight_mode") or "score_budget")
    return out


def resolve_account_risk_limits(
    *,
    paper: Optional[Mapping[str, Any]] = None,
    strategy_id: Optional[str] = None,
    risk: Optional[Mapping[str, Any]] = None,
) -> RiskLimits:
    """解析账户风控限额。

    优先级（后者覆盖前者）：DEFAULT ← Spec.risk ← paper.rules ← 显式 ``risk``。
    返回值经类型清洗与范围夹紧，调用方可安全直接使用。
    """
    if paper is not None and not isinstance(paper, Mapping):
        logger.warning(
            "resolve_account_risk_limits: paper is not a mapping (%s); ignoring",
            type(paper).__name__,
        )
        paper = {}
    paper = paper or {}
    sid = strategy_id or paper.get("strategy_id") or None
    raw_rules = paper.get("rules")
    paper_rules = raw_rules if isinstance(raw_rules, dict) else {}
    if raw_rules is not None and not isinstance(raw_rules, dict):
        logger.warning(
            "resolve_account_risk_limits: paper.rules type=%s; ignored",
            type(raw_rules).__name__,
        )

    merged: Dict[str, Any] = dict(DEFAULT_RISK_LIMITS)
    spec_paper_rules: Dict[str, Any] = {}

    try:
        from core.strategy import get_strategy_spec

        key = str(sid).strip() if sid else None
        if key == "":
            key = None
        spec = get_strategy_spec(key) if key else get_strategy_spec()
        for k, v in (spec.get("risk") or {}).items():
            if v is not None:
                merged[k] = v
        spec_paper_rules = dict(spec.get("paper_rules") or {})
    except KeyError:
        logger.warning(
            "resolve_account_risk_limits: unknown strategy %r; using DEFAULT",
            sid,
        )
    except Exception:  # noqa: BLE001 — Spec 失败则保留 DEFAULT
        logger.warning(
            "resolve_account_risk_limits: get_strategy_spec failed (sid=%r); using DEFAULT",
            sid,
            exc_info=True,
        )

    src_rules = paper_rules or spec_paper_rules
    if isinstance(src_rules, dict):
        for k in (
            "max_drawdown_pct",
            "max_position_pct",
            "max_sector_pct",
            "max_positions",
            "weight_mode",
        ):
            if src_rules.get(k) is not None:
                merged[k] = src_rules[k]

    if risk is not None:
        if not isinstance(risk, Mapping):
            logger.warning(
                "resolve_account_risk_limits: risk is not a mapping (%s); ignored",
                type(risk).__name__,
            )
        else:
            for k, v in dict(risk).items():
                if v is not None:
                    merged[k] = v

    filled = fill_risk_defaults(
        merged,
        paper_rules=src_rules if isinstance(src_rules, dict) else {},
    )

    dd_default = float(DEFAULT_RISK_LIMITS["max_drawdown_pct"])
    pos_default = float(DEFAULT_RISK_LIMITS["max_position_pct"])
    sec_default = float(DEFAULT_RISK_LIMITS["max_sector_pct"])
    n_default = int(DEFAULT_RISK_LIMITS["max_positions"])

    max_dd = _clamp_pct(_as_float(filled.get("max_drawdown_pct"), dd_default), dd_default)
    target_raw = filled.get("target_drawdown_pct")
    target_dd = (
        _clamp_pct(_as_float(target_raw, max_dd * 0.6), max_dd * 0.6)
        if target_raw is not None
        else max_dd * 0.6
    )
    # target 不应高于 max_dd
    target_dd = min(target_dd, max_dd)

    max_pos_pct = _clamp_pct(
        _as_float(filled.get("max_position_pct"), pos_default),
        pos_default,
    )
    max_sector_pct = _clamp_pct(
        _as_float(filled.get("max_sector_pct"), sec_default),
        sec_default,
    )
    max_positions = _clamp_positions(
        _as_int(filled.get("max_positions"), n_default),
        n_default,
    )

    weight_mode = str(filled.get("weight_mode") or "score_budget").strip() or "score_budget"

    return {
        "max_drawdown_pct": max_dd,
        "target_drawdown_pct": float(target_dd),
        "max_position_pct": max_pos_pct,
        "max_sector_pct": max_sector_pct,
        "max_positions": max_positions,
        "weight_mode": weight_mode,
    }
