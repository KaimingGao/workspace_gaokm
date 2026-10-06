"""过热标注：从 factors / bars 写 tip / 对照列，不拦纸面开加。

生产打分默认不 hard_reject 掐死入簿（见 score_stock.mom3_hard_reject=False）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, MutableMapping, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_OVERHEAT_REJECT",
    "annotate_item_overheat",
    "evaluate_overheat_gate",
    "metrics_from_item",
    "resolve_hard_reject_cfg",
]


DEFAULT_OVERHEAT_REJECT: Dict[str, Any] = {
    "mom3_gain_max_pct": 15.0,
    "mom3_loss_min_pct": -12.0,
    "mom5_gain_max_pct": 10.0,
    "day_gain_max_pct": 6.0,
    "soft_reject": False,
    "soft_scale_yhat": False,
}


def resolve_hard_reject_cfg(config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_OVERHEAT_REJECT)
    if not config:
        try:
            from core.signal.config import load_signal_config

            config = load_signal_config()
        except Exception:  # noqa: BLE001
            logger.debug("load_signal_config failed in overheat gate", exc_info=True)
            return cfg
    hr = (config or {}).get("hard_reject") or {}
    if isinstance(hr, Mapping):
        for k, v in hr.items():
            if v is not None:
                cfg[k] = v
    cfg.pop("paper_buy_enforce", None)
    return cfg


def metrics_from_item(item: Optional[Mapping[str, Any]]) -> Dict[str, Optional[float]]:
    """从 ranking / score 行读取 mom3/mom5/当日涨幅。"""
    row = dict(item or {})
    fac = row.get("factors") if isinstance(row.get("factors"), Mapping) else {}
    oh = row.get("overheat") if isinstance(row.get("overheat"), Mapping) else {}

    def _f(*keys: str) -> Optional[float]:
        for src in (oh, fac, row):
            if not isinstance(src, Mapping):
                continue
            for k in keys:
                if src.get(k) is None:
                    continue
                try:
                    return float(src[k])
                except (TypeError, ValueError):
                    continue
        return None

    day = _f("day_gain_pct", "last_change", "change")
    return {
        "mom3": _f("momentum_3d", "mom3_pct", "mom3"),
        "mom5": _f("momentum_5d", "mom5_pct", "mom5"),
        "day_gain": day,
    }


def evaluate_overheat_gate(
    *,
    mom3: Optional[float] = None,
    mom5: Optional[float] = None,
    day_gain: Optional[float] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """判定是否触发过热/追高闸。"""
    from core.signal.factors.overheat import (
        overheat_raw,
        overheat_scale_from_raw,
        score_from_raw,
    )

    hr = resolve_hard_reject_cfg(config)
    gain3 = float(hr.get("mom3_gain_max_pct", 15.0))
    loss3 = float(hr.get("mom3_loss_min_pct", -12.0))
    gain5 = float(hr.get("mom5_gain_max_pct", 10.0))
    day_max = float(hr.get("day_gain_max_pct", 6.0))

    reasons: list[str] = []
    hit = False
    if mom3 is not None and mom3 >= gain3:
        hit = True
        reasons.append(f"近3日涨幅过大({mom3:.1f}%≥{gain3:g}%)")
    if mom3 is not None and mom3 <= loss3:
        hit = True
        reasons.append(f"近3日跌幅过大({mom3:.1f}%≤{loss3:g}%)")
    if mom5 is not None and mom5 >= gain5:
        hit = True
        reasons.append(f"近5日涨幅过大({mom5:.1f}%≥{gain5:g}%)")
    if day_gain is not None and day_gain >= day_max:
        hit = True
        reasons.append(f"当日涨幅过大({day_gain:.1f}%≥{day_max:g}%)")

    raw = overheat_raw(mom3, mom5, day_gain)
    scale = overheat_scale_from_raw(raw)
    level = "none"
    if raw is not None:
        if raw >= 10:
            level = "extreme"
        elif raw >= 8:
            level = "hot"
        elif raw >= 4:
            level = "warm"

    return {
        "hit": hit,
        "reasons": reasons,
        "reason": "；".join(reasons) if reasons else "",
        "overheat_raw_pct": None if raw is None else round(float(raw), 3),
        "overheat_scale": round(float(scale), 3),
        "overheat_score": round(float(score_from_raw(raw)), 1),
        "level": level,
        "mom3": None if mom3 is None else round(float(mom3), 2),
        "mom5": None if mom5 is None else round(float(mom5), 2),
        "day_gain": None if day_gain is None else round(float(day_gain), 2),
        "soft_reject": bool(hr.get("soft_reject", False)),
        "soft_scale_yhat": bool(hr.get("soft_scale_yhat", False)),
    }


def annotate_item_overheat(
    item: MutableMapping[str, Any],
    *,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """就地标注过热 meta（不改生产 hard_reject，不拦纸面开/加）。"""
    m = metrics_from_item(item)
    gate = evaluate_overheat_gate(
        mom3=m.get("mom3"),
        mom5=m.get("mom5"),
        day_gain=m.get("day_gain"),
        config=config,
    )
    item["overheat"] = {
        "raw_pct": gate["overheat_raw_pct"],
        "scale": gate["overheat_scale"],
        "score": gate["overheat_score"],
        "level": gate["level"],
        "hit": gate["hit"],
        "reason": gate["reason"] or None,
    }
    item["overheat_scale"] = gate["overheat_scale"]
    item["mom_chase_risk"] = bool(gate["hit"])

    fac = item.get("factors")
    if not isinstance(fac, dict):
        fac = {}
        item["factors"] = fac
    if gate.get("mom5") is not None and fac.get("momentum_5d") is None:
        fac["momentum_5d"] = gate["mom5"]
    if gate.get("mom3") is not None and fac.get("momentum_3d") is None:
        fac["momentum_3d"] = gate["mom3"]
    if gate.get("day_gain") is not None and fac.get("day_gain_pct") is None:
        fac["day_gain_pct"] = gate["day_gain"]
    fac["overheat_raw_pct"] = gate["overheat_raw_pct"]
    fac["overheat_scale"] = gate["overheat_scale"]

    item.pop("paper_hard_reject", None)
    item.pop("paper_reject_reason", None)

    # ŷ tip：默认只写对照列；soft_scale_yhat 才改主 ŷ
    y = item.get("predicted_score")
    if y is None:
        y = item.get("predicted_score_oo")
    if y is None:
        y = item.get("y_oo")
    if y is None:
        y = item.get("predicted_score_eod")
    try:
        y_f = float(y) if y is not None else None
    except (TypeError, ValueError):
        y_f = None
    if y_f is not None and gate["overheat_scale"] < 1.0:
        scaled = round(y_f * float(gate["overheat_scale"]), 6)
        item["predicted_score_overheat_scaled"] = scaled
        if gate["soft_scale_yhat"] and gate["hit"]:
            item["predicted_score_before_overheat"] = y_f
            item["predicted_score"] = scaled
            if item.get("predicted_score_oo") is not None:
                item["predicted_score_oo"] = scaled
    return gate
