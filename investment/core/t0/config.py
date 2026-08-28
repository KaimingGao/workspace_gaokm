"""做 T 规则配置（纸面 / 回测共用）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional


def coerce_cfg_bool(val: Any, default: bool = True) -> bool:
    """解析纸面/请求里的 bool；避免 ``bool(\"false\")`` 误判为 True。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in {"false", "0", "no", "off", "none", ""}:
        return False
    if s in {"true", "1", "yes", "on"}:
        return True
    return default


# t0_pm_degrade_{long|reverse}: HH:MM 之后禁新开（端点不含；已开未平则第二腿中点追价，否则 eod/放弃）
# t0_pm_degrade:  legacy，等同 reverse 侧
# t0_pm_chase_interval_min_{long|reverse}: 起算后每隔 N 分钟再中点一次（1–60）
# t0_pm_chase_interval_min: legacy，等同 reverse 侧
# must_cover_same_day_{long|reverse}: 正T默认关；反T默认开
# sell|buy_trigger_pct_*：leg1 相对 ref；leg2 相对第一腿成交价（正T买回 / 反T卖旧）
#
# fill_mode:
#   trigger     — 按触发价成交（默认，偏保守）
#   mid         — 触发价与极值中点
#   optimistic  — 卖用 high、买用 low（上界，对照用）
# 正/反可分侧：sell|buy_trigger_pct_{long|reverse}、min_range_pct_*、fill_mode_*（缺省回退共用键）
#
# direction:
#   dual_y  — y_trade 资格 · y_τ 主方向 · y_eod 目标价同向回升 · y_on 回补
#   y_tau_map（dual_y 子选项）— scalp|trend|fixed_long|fixed_reverse
#   （long_t / reverse_t / auto / signal 已下线；仅单测可经 load_t0_rules 显式传入）
#
# path_mode: 仅 first_touch（分钟时间序第一触达）。日线 dual_touch/veto/adverse 已删除。

DEFAULT_T0_RULES: Dict[str, Any] = {
    "enabled": True,
    "t0_ratio": 1.0,
    "sell_trigger_pct": 1.0,
    "buy_trigger_pct": 1.0,
    "sell_trigger_pct_long": 1.0,
    "buy_trigger_pct_long": 5.0,
    "sell_trigger_pct_reverse": 5.0,
    "buy_trigger_pct_reverse": 1.0,
    "must_cover_same_day": True,
    "must_cover_same_day_long": False,
    "must_cover_same_day_reverse": True,
    "lot_size": 100,
    "ref": "open",
    "fill_mode": "trigger",
    "fill_mode_long": "trigger",
    "fill_mode_reverse": "trigger",
    "direction": "dual_y",
    "auto_strong_pct": 0.5,
    "auto_weak_pct": 0.5,
    "dir_enter": 0.1,
    "w_gap": 0.45,
    "w_yclose_loc": 0.20,
    "w_mom3": 0.20,
    "w_gap_atr": 0.15,
    "path_mode": "first_touch",
    "minute_period": "5",
    "min_range_pct": 0.1,
    "min_range_pct_long": 1.0,
    "min_range_pct_reverse": 1.0,
    "use_atr": False,
    "atr_window": 14,
    "atr_sell_mult": 0.9,
    "atr_buy_mult": 0.7,
    # dual_y 阈值（百分比点）：*_enter 入场下限；*_strong 超强须与 τ 同号
    "y_trade_enter": 0.01,
    "y_trade_strong": 2.0,
    "y_tau_enter": 0.01,
    # 正T（−τ）/ 反T（+τ）分侧入场；缺省与 y_tau_enter 同
    "y_tau_enter_long": 0.01,
    "y_tau_enter_reverse": 0.01,
    "y_path_enter": 0.01,
    "y_path_enter_long": 0.01,
    "y_path_enter_reverse": 0.01,
    "y_eod_enter": 0.01,
    "y_eod_strong": 2.0,
    "y_eod_prior": 0.01,
    "y_on_risk": 0.01,
    "y_on_allow": 0.01,
    "y_block_tau_nowcast_sign": True,
    "y_tau_nowcast_sign_eps": 0.05,
    "y_nc_enter": 0.01,
    "y_nc_strong": 2.0,
    "y_tau_map": "trend",
    "y_use_path": True,
    "y_path_required": False,
    "y_gap_tier_mode": "skip_opposite",
    "y_gap_tier_pct": 1.0,
    "y_nowcast_oc_gate": False,
    "y_path_abandon_enabled": True,
    "y_path_abandon_bars": 12,
    # 第一腿段向确认：正T须从前缀 high 回落；反T须从前缀 low 弹起（%）
    # 确认后粘滞开闸（首次 entry_ready 起后续根可触价）——正确语义；
    # 禁止恢复「同根确认∧触价」死锁：那会系统性漏成交、虚高回测。
    "y_prefix_segment_enabled": True,
    "y_prefix_segment_enabled_long": True,
    "y_prefix_segment_enabled_reverse": True,
    "y_prefix_pullback_pct_long": 0.5,
    "y_prefix_bounce_pct_reverse": 0.5,
    "y_ratio_boost_cap": 2.0,
    "y_ratio_cut": 0.60,
    "y_ratio_tau_boost_cap": 1.15,
    "y_ratio_eod_align_boost": 1.10,
    # |y_τ| 刚过入场线时额外压低目标价（乘 y_ratio_cut）
    "y_ratio_tau_soft_band": 0.20,
    # 午后闸：到点后禁新开；已开未平则第二腿中点追价（正/反可分侧起算时刻）
    "t0_pm_degrade": "13:00",
    "t0_pm_degrade_long": "15:00",
    "t0_pm_degrade_reverse": "13:00",
    "t0_pm_chase_interval_min": 10,
    "t0_pm_chase_interval_min_long": 10,
    "t0_pm_chase_interval_min_reverse": 10,
    # dual_y 分数来源：compute=开盘信息集即时算（默认）；live_book/ledger 仅兜底或对照
    "y_score_source": "compute",
    "note": "A股T+1底仓做T；仅5m first_touch（已删除日线模拟）；非实盘。",
}


def _migrate_dual_y_gate_keys(cfg: dict, override_keys: Optional[set] = None) -> None:
    """统一 dual_y 闸命名：*_enter 入场、*_strong 强闸；旧键只读迁移。"""
    keys = override_keys or set()
    if "y_trade_enter" in keys:
        pass
    elif "y_trade_floor" in keys and cfg.get("y_trade_floor") is not None and cfg.get("y_trade_floor") != "":
        cfg["y_trade_enter"] = cfg["y_trade_floor"]
    elif cfg.get("y_trade_enter") is None or cfg.get("y_trade_enter") == "":
        legacy = cfg.get("y_trade_floor")
        if legacy is not None and legacy != "":
            cfg["y_trade_enter"] = legacy
    for new_key, old_key in (
        ("y_trade_strong", "y_trade_tau_sign_gate"),
        ("y_eod_strong", "y_eod_tau_sign_gate"),
        ("y_nc_strong", "y_nowcast_enter"),
    ):
        if new_key in keys:
            continue
        if old_key in keys and cfg.get(old_key) is not None and cfg.get(old_key) != "":
            cfg[new_key] = cfg[old_key]
        elif cfg.get(new_key) is None or cfg.get(new_key) == "":
            legacy = cfg.get(old_key)
            if legacy is not None and legacy != "":
                cfg[new_key] = legacy


def _sync_dual_y_gate_legacy_aliases(cfg: dict) -> None:
    """写出旧键别名，避免未升级的 overlay / 外部脚本读不到。"""
    if cfg.get("y_trade_enter") is not None:
        cfg["y_trade_floor"] = cfg["y_trade_enter"]
    if cfg.get("y_trade_strong") is not None:
        cfg["y_trade_tau_sign_gate"] = cfg["y_trade_strong"]
    if cfg.get("y_eod_strong") is not None:
        cfg["y_eod_tau_sign_gate"] = cfg["y_eod_strong"]
    if cfg.get("y_nc_strong") is not None:
        cfg["y_nowcast_enter"] = cfg["y_nc_strong"]


def load_t0_rules(override: Optional[dict] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_T0_RULES)
    override_keys = set(override.keys()) if override else set()
    if override:
        for k, v in override.items():
            if v is not None:
                cfg[k] = v
    cfg["t0_ratio"] = max(0.05, min(float(cfg.get("t0_ratio") or 1.0), 1.0))
    # 纸面/回测生效路径在 core.execution.resolve 再强制为 1.0
    cfg["sell_trigger_pct"] = max(0.1, min(float(cfg.get("sell_trigger_pct") or 1.0), 20.0))
    cfg["buy_trigger_pct"] = max(0.1, min(float(cfg.get("buy_trigger_pct") or 1.0), 20.0))
    for side_trig in (
        "sell_trigger_pct_long",
        "buy_trigger_pct_long",
        "sell_trigger_pct_reverse",
        "buy_trigger_pct_reverse",
    ):
        base_k = "sell_trigger_pct" if side_trig.startswith("sell_") else "buy_trigger_pct"
        raw_trig = cfg.get(side_trig)
        if raw_trig is None or raw_trig == "":
            cfg[side_trig] = float(cfg[base_k])
        else:
            try:
                cfg[side_trig] = max(0.1, min(float(raw_trig), 20.0))
            except (TypeError, ValueError):
                cfg[side_trig] = float(cfg[base_k])
    cfg["lot_size"] = max(1, int(cfg.get("lot_size") or 100))
    cfg["auto_strong_pct"] = max(0.0, min(float(cfg.get("auto_strong_pct") or 0.5), 5.0))
    cfg["auto_weak_pct"] = max(0.0, min(float(cfg.get("auto_weak_pct") or 0.5), 5.0))
    cfg["dir_enter"] = max(0.05, min(float(cfg.get("dir_enter") or 0.1), 1.0))
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
    for fill_side in ("fill_mode_long", "fill_mode_reverse"):
        if fill_side not in override_keys or cfg.get(fill_side) is None or cfg.get(fill_side) == "":
            cfg[fill_side] = fill
        else:
            fs = str(cfg.get(fill_side) or "").strip().lower()
            if fs not in {"trigger", "mid", "optimistic"}:
                fs = fill
            cfg[fill_side] = fs
    cfg["enabled"] = coerce_cfg_bool(cfg.get("enabled"), True)
    cfg["must_cover_same_day_long"] = coerce_cfg_bool(
        cfg.get("must_cover_same_day_long"), False
    )
    # 反T侧：显式侧向键优先；否则若只写了 legacy must_cover_same_day，用其覆盖默认 True
    # （旧逻辑用 coerce(default_reverse=True, legacy) 会吞掉 legacy=False）
    if "must_cover_same_day_reverse" in override_keys and cfg.get(
        "must_cover_same_day_reverse"
    ) is not None:
        cfg["must_cover_same_day_reverse"] = coerce_cfg_bool(
            cfg.get("must_cover_same_day_reverse"), True
        )
    elif "must_cover_same_day" in override_keys and cfg.get("must_cover_same_day") is not None:
        cfg["must_cover_same_day_reverse"] = coerce_cfg_bool(
            cfg.get("must_cover_same_day"), True
        )
    else:
        cfg["must_cover_same_day_reverse"] = coerce_cfg_bool(
            cfg.get("must_cover_same_day_reverse"), True
        )
    # legacy 键与反T侧对齐（旧读端）
    cfg["must_cover_same_day"] = cfg["must_cover_same_day_reverse"]
    cfg["use_atr"] = coerce_cfg_bool(cfg.get("use_atr"), False)
    direction = str(cfg.get("direction") or "auto").strip().lower()
    if direction in {"long", "正", "正t", "zheng"}:
        direction = "long_t"
    elif direction in {"reverse", "反", "反t", "fan", "short_t"}:
        direction = "reverse_t"
    elif direction in {"sig", "score", "factor"}:
        direction = "signal"
    elif direction in {"dual", "yhat", "dual_score", "multi_y"}:
        direction = "dual_y"
    if direction not in {"auto", "long_t", "reverse_t", "signal", "dual_y"}:
        direction = "dual_y"
    cfg["direction"] = direction
    # override_keys 已在函数开头定义
    _migrate_dual_y_gate_keys(cfg, override_keys)
    for yk, lo, hi, default in (
        ("y_eod_prior", 0.01, 5.0, 0.01),
        ("y_eod_enter", 0.01, 5.0, 0.01),
        ("y_eod_strong", 0.05, 5.0, 2.0),
        ("y_trade_enter", 0.01, 5.0, 0.01),
        ("y_trade_strong", 0.05, 5.0, 2.0),
        ("y_tau_enter", 0.01, 5.0, 0.01),
        ("y_tau_enter_long", 0.01, 5.0, 0.01),
        ("y_tau_enter_reverse", 0.01, 5.0, 0.01),
        ("y_on_risk", 0.01, 10.0, 0.01),
        ("y_on_allow", 0.01, 10.0, 0.01),
        ("y_ratio_boost_cap", 1.0, 2.0, 2.0),
        ("y_ratio_cut", 0.2, 1.0, 0.60),
        ("y_ratio_tau_boost_cap", 1.0, 1.5, 1.15),
        ("y_ratio_eod_align_boost", 1.0, 1.5, 1.10),
        ("y_ratio_tau_soft_band", 0.0, 2.0, 0.20),
        ("y_tau_nowcast_sign_eps", 0.0, 1.0, 0.05),
        ("y_nc_enter", 0.01, 10.0, 0.01),
        ("y_nc_strong", 0.05, 10.0, 2.0),
        ("y_path_enter", 0.01, 5.0, 0.01),
        ("y_path_enter_long", 0.01, 5.0, 0.01),
        ("y_path_enter_reverse", 0.01, 5.0, 0.01),
        ("y_gap_tier_pct", 0.3, 8.0, 1.0),
    ):
        try:
            # 旧键 y_trade_tau_sign_eps → y_tau_nowcast_sign_eps
            raw = cfg.get(yk)
            if (raw is None or raw == "") and yk == "y_tau_nowcast_sign_eps":
                raw = cfg.get("y_trade_tau_sign_eps")
            val = float(default if raw is None or raw == "" else raw)
        except (TypeError, ValueError):
            val = float(default)
        cfg[yk] = max(lo, min(val, hi))
    # 旧双闸：y_tau_enter_strong 并入入场；输出同步以免旧前端读到更低门槛
    try:
        strong_legacy = cfg.get("y_tau_enter_strong")
        if strong_legacy is not None and strong_legacy != "":
            strong_f = float(strong_legacy)
            if strong_f > float(cfg["y_tau_enter"]):
                cfg["y_tau_enter"] = max(0.01, min(strong_f, 5.0))
    except (TypeError, ValueError):
        pass
    cfg["y_tau_enter_strong"] = float(cfg["y_tau_enter"])
    # 侧向门槛：未显式覆盖时跟随 y_tau_enter / y_path_enter（兼容旧纸面）
    for side_key, base_key in (
        ("y_tau_enter_long", "y_tau_enter"),
        ("y_tau_enter_reverse", "y_tau_enter"),
        ("y_path_enter_long", "y_path_enter"),
        ("y_path_enter_reverse", "y_path_enter"),
    ):
        if side_key not in override_keys or cfg.get(side_key) is None or cfg.get(side_key) == "":
            cfg[side_key] = float(cfg[base_key])
        else:
            try:
                cfg[side_key] = max(0.01, min(float(cfg[side_key]), 5.0))
            except (TypeError, ValueError):
                cfg[side_key] = float(cfg[base_key])
    from core.t0.score_policy import normalize_y_trade_enter

    cfg["y_trade_enter"] = normalize_y_trade_enter(cfg.get("y_trade_enter"))
    _sync_dual_y_gate_legacy_aliases(cfg)
    # 异号闸：新键 τ↔nowcast；旧 y_block_trade_tau_sign 迁移一次
    if "y_block_tau_nowcast_sign" in cfg:
        cfg["y_block_tau_nowcast_sign"] = coerce_cfg_bool(cfg.get("y_block_tau_nowcast_sign"))
    elif "y_block_trade_tau_sign" in cfg:
        cfg["y_block_tau_nowcast_sign"] = coerce_cfg_bool(cfg.get("y_block_trade_tau_sign"))
    else:
        cfg["y_block_tau_nowcast_sign"] = True
    cfg.pop("y_block_trade_tau_sign", None)
    cfg.pop("y_trade_tau_sign_eps", None)
    cfg["y_use_path"] = coerce_cfg_bool(cfg.get("y_use_path"), True)
    cfg["y_path_required"] = coerce_cfg_bool(cfg.get("y_path_required"), False)
    cfg["y_nowcast_oc_gate"] = coerce_cfg_bool(cfg.get("y_nowcast_oc_gate"), False)
    cfg["y_path_abandon_enabled"] = bool(cfg.get("y_path_abandon_enabled", True))
    try:
        cfg["y_path_abandon_bars"] = max(2, min(int(cfg.get("y_path_abandon_bars") or 12), 48))
    except (TypeError, ValueError):
        cfg["y_path_abandon_bars"] = 12
    for ab_side in ("y_path_abandon_bars_long", "y_path_abandon_bars_reverse"):
        if ab_side not in override_keys:
            cfg[ab_side] = int(cfg["y_path_abandon_bars"])
            continue
        try:
            raw_ab = cfg.get(ab_side)
            if raw_ab is None or raw_ab == "":
                raw_ab = cfg["y_path_abandon_bars"]
            cfg[ab_side] = max(2, min(int(raw_ab), 48))
        except (TypeError, ValueError):
            cfg[ab_side] = int(cfg["y_path_abandon_bars"])
    cfg["y_prefix_segment_enabled"] = bool(cfg.get("y_prefix_segment_enabled", True))
    cfg["y_prefix_segment_enabled_long"] = bool(
        cfg.get("y_prefix_segment_enabled_long", cfg.get("y_prefix_segment_enabled", True))
    )
    cfg["y_prefix_segment_enabled_reverse"] = bool(
        cfg.get("y_prefix_segment_enabled_reverse", cfg.get("y_prefix_segment_enabled", True))
    )
    for seg_key, seg_default in (
        ("y_prefix_pullback_pct_long", 0.5),
        ("y_prefix_bounce_pct_reverse", 0.5),
    ):
        try:
            raw_seg = cfg.get(seg_key)
            val_seg = float(seg_default if raw_seg is None or raw_seg == "" else raw_seg)
        except (TypeError, ValueError):
            val_seg = float(seg_default)
        cfg[seg_key] = max(0.0, min(val_seg, 2.0))
    gap_mode = str(cfg.get("y_gap_tier_mode") or "skip_opposite").strip().lower()
    if gap_mode not in {"off", "none", "false", "0", "skip_opposite", "revert"}:
        gap_mode = "skip_opposite"
    cfg["y_gap_tier_mode"] = gap_mode
    cfg.pop("y_block_conflict", None)  # 已下线：eod↔τ / y_check 冲突跳过
    # 丢弃已下线的不利/时间止损字段（旧账户 overlay 可能残留）
    for _dead in (
        "t0_adverse_stop_pct",
        "t0_adverse_stop_atr_mult",
        "t0_time_stop",
        "t0_time_stop_underwater_only",
    ):
        cfg.pop(_dead, None)
    def _norm_pm_degrade(raw: Any, default: str) -> str:
        if raw is None:
            return default
        s = str(raw).strip()
        if s in {"", "0", "off", "none", "-"}:
            return ""
        return s

    pm_legacy = cfg.get("t0_pm_degrade")
    for side_key, default in (
        ("t0_pm_degrade_long", "15:00"),
        ("t0_pm_degrade_reverse", "13:00"),
    ):
        if side_key not in override_keys or cfg.get(side_key) is None:
            if side_key == "t0_pm_degrade_reverse" and pm_legacy is not None:
                cfg[side_key] = _norm_pm_degrade(pm_legacy, default)
            else:
                cfg[side_key] = _norm_pm_degrade(cfg.get(side_key), default)
        else:
            cfg[side_key] = _norm_pm_degrade(cfg.get(side_key), default)
    cfg["t0_pm_degrade"] = cfg["t0_pm_degrade_reverse"]
    legacy_iv = cfg.get("t0_pm_chase_interval_min")
    for iv_key in ("t0_pm_chase_interval_min_long", "t0_pm_chase_interval_min_reverse"):
        if iv_key not in override_keys or cfg.get(iv_key) is None or cfg.get(iv_key) == "":
            if iv_key == "t0_pm_chase_interval_min_reverse" and legacy_iv is not None:
                raw_iv = legacy_iv
            else:
                raw_iv = cfg.get(iv_key)
        else:
            raw_iv = cfg.get(iv_key)
        try:
            iv = int(raw_iv if raw_iv is not None and raw_iv != "" else 10)
        except (TypeError, ValueError):
            iv = 10
        cfg[iv_key] = max(1, min(iv, 60))
    cfg["t0_pm_chase_interval_min"] = cfg["t0_pm_chase_interval_min_reverse"]
    from core.t0.score_policy import normalize_y_tau_map

    cfg["y_tau_map"] = normalize_y_tau_map(cfg.get("y_tau_map"))
    y_src = str(cfg.get("y_score_source") or "compute").strip().lower()
    if y_src in {"book", "cluster", "cluster_book"}:
        y_src = "live_book"
    elif y_src in {"ledger", "score_ledger", "freeze"}:
        y_src = "ledger"
    elif y_src in {"compute", "pit", "live", "realtime", "on_the_fly"}:
        y_src = "compute"
    else:
        y_src = "compute"
    cfg["y_score_source"] = y_src
    path_mode = str(cfg.get("path_mode") or "first_touch").strip().lower()
    if path_mode in {"minute", "min", "5m", "first", "dual_touch", "dual", "any", "veto", "adverse", "conservative", "worst"}:
        path_mode = "first_touch"
    if path_mode != "first_touch":
        path_mode = "first_touch"
    cfg["path_mode"] = path_mode
    period = str(cfg.get("minute_period") or "5").strip()
    if period not in {"1", "5", "15", "30", "60"}:
        period = "5"
    cfg["minute_period"] = period
    cfg["atr_window"] = max(3, min(int(cfg.get("atr_window") or 14), 60))
    cfg["atr_sell_mult"] = max(0.2, min(float(cfg.get("atr_sell_mult") or 0.9), 3.0))
    cfg["atr_buy_mult"] = max(0.2, min(float(cfg.get("atr_buy_mult") or 0.7), 3.0))
    if cfg.get("min_range_pct") is not None:
        cfg["min_range_pct"] = max(0.1, min(float(cfg["min_range_pct"]), 30.0))
    for mr_side, base_mr in (
        ("min_range_pct_long", "min_range_pct"),
        ("min_range_pct_reverse", "min_range_pct"),
    ):
        raw_mr = cfg.get(mr_side)
        if raw_mr is None or raw_mr == "":
            cfg[mr_side] = cfg.get(base_mr)
        else:
            try:
                cfg[mr_side] = max(0.1, min(float(raw_mr), 30.0))
            except (TypeError, ValueError):
                cfg[mr_side] = cfg.get(base_mr)
    return cfg


def apply_side_exec_params(cfg: dict, direction: Optional[str]) -> Dict[str, Any]:
    """按正/反 T 覆盖执行参数：卖/买触发、振幅下限、成交模式。

    定向前用共用键；定方向后调用本函数再缩放触发价 / 跑路径。
    """
    out = dict(cfg or {})
    if direction not in {"long_t", "reverse_t"}:
        return out
    suf = "_reverse" if direction == "reverse_t" else "_long"
    for base in ("sell_trigger_pct", "buy_trigger_pct"):
        sk = f"{base}{suf}"
        raw = out.get(sk)
        if raw is not None and raw != "":
            try:
                out[base] = max(0.1, min(float(raw), 20.0))
            except (TypeError, ValueError):
                pass
    mr = out.get(f"min_range_pct{suf}")
    if mr is not None and mr != "":
        try:
            out["min_range_pct"] = max(0.1, min(float(mr), 30.0))
        except (TypeError, ValueError):
            pass
    fm = out.get(f"fill_mode{suf}")
    if fm is not None and str(fm).strip():
        fs = str(fm).strip().lower()
        if fs in {"trigger", "mid", "optimistic"}:
            out["fill_mode"] = fs
    mc = out.get(f"must_cover_same_day{suf}")
    if mc is not None:
        default_mc = direction == "reverse_t"
        out["must_cover_same_day"] = coerce_cfg_bool(mc, default_mc)
    pm = out.get(f"t0_pm_degrade{suf}")
    if pm is not None:
        s = str(pm).strip()
        out["t0_pm_degrade"] = "" if s in {"", "0", "off", "none", "-"} else s
    elif not out.get("t0_pm_degrade"):
        out["t0_pm_degrade"] = "15:00" if direction == "long_t" else "13:00"
    iv = out.get(f"t0_pm_chase_interval_min{suf}")
    if iv is not None and iv != "":
        try:
            out["t0_pm_chase_interval_min"] = max(1, min(int(iv), 60))
        except (TypeError, ValueError):
            pass
    elif not out.get("t0_pm_chase_interval_min"):
        side_iv = out.get(f"t0_pm_chase_interval_min{suf}")
        try:
            raw_iv = side_iv if side_iv is not None and side_iv != "" else 10
            out["t0_pm_chase_interval_min"] = max(1, min(int(raw_iv), 60))
        except (TypeError, ValueError):
            out["t0_pm_chase_interval_min"] = 10
    return out


def resolve_path_abandon_bars(cfg: dict, direction: Optional[str] = None) -> int:
    """前缀放弃根数；优先侧向键，否则共用 ``y_path_abandon_bars``。"""
    d = str(direction or "").strip().lower()
    side_key = None
    if d == "long_t":
        side_key = "y_path_abandon_bars_long"
    elif d == "reverse_t":
        side_key = "y_path_abandon_bars_reverse"
    raw = None
    if side_key is not None and (cfg or {}).get(side_key) is not None:
        raw = (cfg or {}).get(side_key)
    else:
        raw = (cfg or {}).get("y_path_abandon_bars")
    try:
        return max(2, min(int(raw or 12), 48))
    except (TypeError, ValueError):
        return 12


def resolve_min_range_pct(cfg: dict) -> float:
    if cfg.get("min_range_pct") is not None:
        return float(cfg["min_range_pct"])
    # 相对触发阈值略宽；回测/纸面「自动」不宜高于 1.0%，否则横盘股整日跳过
    return round(
        max(0.5, (float(cfg["sell_trigger_pct"]) + float(cfg["buy_trigger_pct"])) * 0.35),
        4,
    )


# 盘中 Worker：与 5m K 线对齐的轮询与分钟缓存 TTL
T0_INTRADAY_TICK_SEC = 300.0
T0_INTRADAY_MINUTE_CACHE_HOURS = 5.0 / 60.0
T0_INTRADAY_MINUTE_LOOKBACK_DAYS = 5
