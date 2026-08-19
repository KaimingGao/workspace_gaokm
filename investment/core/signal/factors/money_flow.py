"""资金流因子（V2.1）：默认 OHLCV MFI 代理；可传入 money_flow 真值快照。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Tuple


def _typical_price(bar: dict) -> Optional[float]:
    try:
        h = float(bar.get("high") if bar.get("high") is not None else bar["close"])
        l = float(bar.get("low") if bar.get("low") is not None else bar["close"])
        c = float(bar["close"])
    except (TypeError, ValueError, KeyError):
        return None
    return (h + l + c) / 3.0


def _mfi_proxy(bars: List[dict], window: int = 14) -> Optional[float]:
    if not bars or len(bars) < window + 1:
        return None
    pos = 0.0
    neg = 0.0
    prev_tp = None
    for bar in bars[-(window + 1) :]:
        tp = _typical_price(bar)
        vol = float(bar.get("volume") or 0.0)
        if tp is None or vol <= 0:
            prev_tp = tp
            continue
        raw_mf = tp * vol
        if prev_tp is not None:
            if tp > prev_tp:
                pos += raw_mf
            elif tp < prev_tp:
                neg += raw_mf
        prev_tp = tp
    if pos + neg <= 0:
        return None
    if neg <= 0:
        return 100.0
    ratio = pos / neg
    return 100.0 - (100.0 / (1.0 + ratio))


def score_money_flow(
    bars: List[dict],
    *,
    money_flow: Optional[dict] = None,
    **_kw,
) -> Tuple[float, Dict[str, Any]]:
    """
    优先用 money_flow.net_inflow（真资金流口）；否则 MFI 代理。
    缺数据 → 不进 ŷ（omit）。
    """
    mf = money_flow or {}
    net = mf.get("net_inflow")
    if net is not None:
        try:
            net_f = float(net)
        except (TypeError, ValueError):
            net_f = None
        if net_f is not None:
            # 相对近窗成交额归一，避免绝对金额近二元化 + 大小盘不可比
            from core.bar_fields import bar_amount

            amts = [bar_amount(b) for b in (bars or [])[-10:]]
            amts = [a for a in amts if a and a > 0]
            denom = (sum(amts) / len(amts)) if amts else None
            if denom and denom > 0:
                ratio = net_f / denom
                # ratio 约在 ±数个百分点量级；用 tanh 软饱和
                scaled = math.tanh(ratio * 8.0)
                score = 50.0 + 35.0 * scaled
            else:
                # 无成交额锚时退化为符号档（仍避免 900 元即触顶）
                if net_f > 0:
                    score = 62.0
                elif net_f < 0:
                    score = 38.0
                else:
                    score = 50.0
            return float(round(max(20.0, min(85.0, score)), 1)), {
                "money_flow_source": "net_inflow",
                "money_flow_net_inflow": round(net_f, 4),
                "money_flow_vs_amount": (
                    round(net_f / denom, 6) if denom and denom > 0 else None
                ),
                "money_flow_mfi": None,
                "omit_sub_score": False,
            }

    mfi = _mfi_proxy(bars or [])
    if mfi is None:
        return 50.0, {
            "money_flow_source": "none",
            "money_flow_mfi": None,
            "ok": False,
            "omit_sub_score": True,
        }

    # MFI 中间区友好，极端超买/超卖降分
    if 45 <= mfi <= 65:
        score = 62.0 + (mfi - 45) * 0.4
    elif 30 <= mfi < 45:
        score = 48.0 + (mfi - 30) * 0.5
    elif 65 < mfi <= 80:
        score = 58.0 - (mfi - 65) * 0.4
    elif mfi < 30:
        score = 38.0
    else:
        score = 40.0

    return float(round(score, 1)), {
        "money_flow_source": "mfi_proxy",
        "money_flow_mfi": round(mfi, 2),
        "ok": True,
        "omit_sub_score": False,
    }
