"""做 T 规则配置（纸面 / 回测共用）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

# fill_mode:
#   trigger     — 按触发价成交（默认，偏保守）
#   mid         — 触发价与极值中点
#   optimistic  — 卖用 high、买用 low（上界，对照用）
#
# direction:
#   long_t  — 正 T（先卖后买）
#   reverse_t — 反 T（先买后卖，需现金）
#   auto    — 按跳空（今开 vs 昨收）强弱选择；不明则正 T
#   signal  — 开盘可用隔夜/昨收特征打分；低置信跳过（回测默认）
#
# path_mode: 日线不知盘中先后，用于抑制「方向定反还双触达虚盈」
#   dual_touch  — 旧行为：高低都触达就当两腿都成（偏乐观）
#   veto        — 用收盘在当日区间位置推断路径偏向；与开盘方向冲突则跳过（日线回测默认）
#   adverse     — 按对该方向不利的路径成交（正T假定先低后高，反T假定先高后低）
#   first_touch — 有分钟线时按时间序第一触达成交（更贴近真实；缺分钟则回退 veto）

DEFAULT_T0_RULES: Dict[str, Any] = {
    "enabled": True,
    "t0_ratio": 0.4,
    "sell_trigger_pct": 2.0,
    "buy_trigger_pct": 1.5,
    "must_cover_same_day": False,
    "lot_size": 100,
    "ref": "open",
    "fill_mode": "trigger",
    "direction": "auto",
    "auto_strong_pct": 0.5,
    "auto_weak_pct": 0.5,
    "dir_enter": 0.35,
    "w_gap": 0.45,
    "w_yclose_loc": 0.20,
    "w_mom3": 0.20,
    "w_gap_atr": 0.15,
    "path_mode": "dual_touch",
    "minute_period": "5",
    "min_range_pct": None,  # None → max(sell+buy)*0.6
    "use_atr": True,
    "atr_window": 14,
    "atr_sell_mult": 0.9,
    "atr_buy_mult": 0.7,
    "note": "日线代理；回测默认可启用 5m first_touch；非实盘。",
}


def load_t0_rules(override: Optional[dict] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_T0_RULES)
    if override:
        for k, v in override.items():
            if v is not None:
                cfg[k] = v
    cfg["t0_ratio"] = max(0.05, min(float(cfg.get("t0_ratio") or 0.4), 1.0))
    cfg["sell_trigger_pct"] = max(0.1, min(float(cfg.get("sell_trigger_pct") or 2.0), 20.0))
    cfg["buy_trigger_pct"] = max(0.1, min(float(cfg.get("buy_trigger_pct") or 1.5), 20.0))
    cfg["lot_size"] = max(1, int(cfg.get("lot_size") or 100))
    cfg["auto_strong_pct"] = max(0.0, min(float(cfg.get("auto_strong_pct") or 0.5), 5.0))
    cfg["auto_weak_pct"] = max(0.0, min(float(cfg.get("auto_weak_pct") or 0.5), 5.0))
    cfg["dir_enter"] = max(0.05, min(float(cfg.get("dir_enter") or 0.35), 1.0))
    for wk in ("w_gap", "w_yclose_loc", "w_mom3", "w_gap_atr"):
        cfg[wk] = max(0.0, min(float(cfg.get(wk) or 0.0), 1.0))
    # 归一化权重
    wsum = (
        float(cfg["w_gap"])
        + float(cfg["w_yclose_loc"])
        + float(cfg["w_mom3"])
        + float(cfg["w_gap_atr"])
    )
    if wsum <= 1e-9:
        cfg["w_gap"], cfg["w_yclose_loc"], cfg["w_mom3"], cfg["w_gap_atr"] = 0.45, 0.20, 0.20, 0.15
    else:
        cfg["w_gap"] = round(float(cfg["w_gap"]) / wsum, 4)
        cfg["w_yclose_loc"] = round(float(cfg["w_yclose_loc"]) / wsum, 4)
        cfg["w_mom3"] = round(float(cfg["w_mom3"]) / wsum, 4)
        cfg["w_gap_atr"] = round(float(cfg["w_gap_atr"]) / wsum, 4)
    fill = str(cfg.get("fill_mode") or "trigger").strip().lower()
    if fill not in {"trigger", "mid", "optimistic"}:
        fill = "trigger"
    cfg["fill_mode"] = fill
    direction = str(cfg.get("direction") or "auto").strip().lower()
    if direction in {"long", "正", "正t", "zheng"}:
        direction = "long_t"
    elif direction in {"reverse", "反", "反t", "fan", "short_t"}:
        direction = "reverse_t"
    elif direction in {"sig", "score", "factor"}:
        direction = "signal"
    if direction not in {"auto", "long_t", "reverse_t", "signal"}:
        direction = "auto"
    cfg["direction"] = direction
    path_mode = str(cfg.get("path_mode") or "dual_touch").strip().lower()
    if path_mode in {"dual", "any", "legacy", "optimistic_path"}:
        path_mode = "dual_touch"
    if path_mode in {"conservative", "worst"}:
        path_mode = "adverse"
    if path_mode in {"minute", "min", "5m", "first"}:
        path_mode = "first_touch"
    if path_mode not in {"dual_touch", "veto", "adverse", "first_touch"}:
        path_mode = "dual_touch"
    cfg["path_mode"] = path_mode
    period = str(cfg.get("minute_period") or "5").strip()
    if period not in {"1", "5", "15", "30", "60"}:
        period = "5"
    cfg["minute_period"] = period
    cfg["atr_window"] = max(3, min(int(cfg.get("atr_window") or 14), 60))
    cfg["atr_sell_mult"] = max(0.2, min(float(cfg.get("atr_sell_mult") or 0.9), 3.0))
    cfg["atr_buy_mult"] = max(0.2, min(float(cfg.get("atr_buy_mult") or 0.7), 3.0))
    if cfg.get("min_range_pct") is not None:
        cfg["min_range_pct"] = max(0.2, min(float(cfg["min_range_pct"]), 30.0))
    return cfg


def resolve_min_range_pct(cfg: dict) -> float:
    if cfg.get("min_range_pct") is not None:
        return float(cfg["min_range_pct"])
    # 相对触发阈值略宽一点即可；避免 ATR 抬升阈值后门禁二次收紧
    return round(
        max(0.8, (float(cfg["sell_trigger_pct"]) + float(cfg["buy_trigger_pct"])) * 0.45),
        4,
    )
