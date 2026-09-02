"""做 T 规则配置（纸面 / 回测共用）。"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

# 成交明细 API 样本上限（与 web ``T0_TRADE_TABLE_MAX_ROWS`` 对齐）
T0_TRADE_DAYS_SAMPLE_UI_LIMIT = 500


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


def t0_dir_label(direction: Optional[str]) -> str:
    """内部枚举 → 中文：buy_then_sell=正T，sell_then_buy=反T。"""
    d = str(direction or "").strip().lower()
    if d == "buy_then_sell":
        return "正T"
    if d == "sell_then_buy":
        return "反T"
    return d or "—"


# 方向键：*_buy_then_sell = 正T，*_sell_then_buy = 反T
# t0_pm_degrade_*：HH:MM 之后禁新开（端点不含；已开未平则第二腿中点追价）
# t0_pm_degrade：legacy，等同正T侧
# must_cover_same_day_*：反T默认不强制回补；正T默认同日卖旧
# 第二腿：τ 出场价闸（正T卖 / 反T买）；相对 leg1 的 % 触发已下线
#
# fill_mode: trigger | mid | optimistic；可分侧 fill_mode_*
#
# direction:
#   dual_y  — y_trade 资格 · y_τ 主方向 · y_eod 目标价同向回升 · y_on 回补
#   y_tau_map — trend|fixed_sell_then_buy|fixed_buy_then_sell
#   （sell_then_buy / buy_then_sell / auto / signal 仅单测可显式传入）
#
# path_mode: 仅 first_touch（分钟时间序第一触达）。

# 已废弃命名（旧 long_t/reverse_t 与 *_long/*_reverse）；加载时丢弃，不迁移取值
_DEAD_T0_KEYS = (
    "buy_trigger_pct_long",
    "sell_trigger_pct_reverse",
    "sell_trigger_pct_long",  # 旧 leg1 误键
    "buy_trigger_pct_reverse",  # 旧 leg1 误键
    "buy_trigger_pct_buy_then_sell",  # 旧正T相对开盘低吸；第一腿改确认根收盘
    "sell_trigger_pct_sell_then_buy",  # 旧反T相对开盘冲高卖
    "must_cover_same_day_long",
    "must_cover_same_day_reverse",
    "fill_mode_long",
    "fill_mode_reverse",
    "min_range_pct_long",
    "min_range_pct_reverse",
    "y_tau_enter_long",
    "y_tau_enter_reverse",
    "y_path_enter_long",
    "y_path_enter_reverse",
    "y_path_abandon_bars_long",
    "y_path_abandon_bars_reverse",
    "y_prefix_segment_enabled_long",
    "y_prefix_segment_enabled_reverse",
    "y_prefix_upbar_ratio_reverse",
    "y_prefix_downbar_ratio_long",
    "t0_pm_degrade_long",
    "t0_pm_degrade_reverse",
    "t0_pm_chase_interval_min_long",
    "t0_pm_chase_interval_min_reverse",
    "y_n_break_high_reverse",
    "y_n_shape_gate_reverse",
    "y_prefix_n_rise_pct_reverse",
    "y_prefix_pullback_pct_long",
    "y_prefix_bounce_pct_reverse",
    # 已写死 / Web 无控件：残留 overlay 丢弃
    "y_gap_tier_skip_low_open_reverse",
    "y_tau_nowcast_sign_eps",
    "y_trade_tau_sign_eps",
    "y_ratio_tau_boost_cap",
    "y_ratio_eod_align_boost",
    "y_ratio_tau_soft_band",
    "t0_leg1_hunt_pct_buy_then_sell",
    "t0_leg1_hunt_pct_sell_then_buy",
    # 已改为 τ 入场价闸
    "y_prefix_vs_path_skip",
    "y_prefix_vs_path_mult",
    # 固定前缀：只按后半阴阳占比，不再叠最少命中根数
    "y_prefix_min_half_hits",
    # 第二腿已改 τ 出场价闸；相对 leg1 的 % 触发下线
    "sell_trigger_pct",
    "buy_trigger_pct",
    "buy_trigger_pct_sell_then_buy",
    "sell_trigger_pct_buy_then_sell",
    # v5 已钉死：both 确认 ∩ 环境闸 ∩ 半贪心滚仓；旧回退开关丢弃
    "t0_leg_confirm_mode",
    "t0_env_gate_enabled",
    "t0_slots_roll_unused",
    "y_prefix_segment_enabled",
    "y_prefix_segment_enabled_sell_then_buy",
    "y_prefix_segment_enabled_buy_then_sell",
)


def _migrate_prefix_vs_path_to_tau_entry(cfg: dict) -> None:
    """旧「前缀振幅 vs |path|」→ τ 入场价闸（仅当新键未显式给出时）。"""
    if not isinstance(cfg, dict):
        return
    if "y_tau_entry_price_mult" not in cfg and "y_prefix_vs_path_mult" in cfg:
        cfg["y_tau_entry_price_mult"] = cfg.get("y_prefix_vs_path_mult")
    if "y_tau_entry_price_skip" not in cfg and "y_prefix_vs_path_skip" in cfg:
        cfg["y_tau_entry_price_skip"] = cfg.get("y_prefix_vs_path_skip")
    legacy_skip = cfg.get("y_tau_entry_price_skip")
    legacy_mult = cfg.get("y_tau_entry_price_mult")
    for side in ("buy_then_sell", "sell_then_buy"):
        sk = f"y_tau_entry_price_skip_{side}"
        mk = f"y_tau_entry_price_mult_{side}"
        if sk not in cfg and legacy_skip is not None:
            cfg[sk] = legacy_skip
        if mk not in cfg and legacy_mult is not None:
            cfg[mk] = legacy_mult


def drop_dead_t0_keys(cfg: dict) -> None:
    """就地丢弃已废弃做 T 键（含旧 long/reverse 命名）。"""
    if not isinstance(cfg, dict):
        return
    _migrate_prefix_vs_path_to_tau_entry(cfg)
    for k in _DEAD_T0_KEYS:
        cfg.pop(k, None)


def normalize_t0_direction(raw: Any, *, default: str = "dual_y") -> str:
    """统一方向枚举：buy_then_sell / sell_then_buy / dual_y / auto / signal。"""
    direction = str(raw or default).strip().lower()
    if direction in {"反", "反t", "sell_then_buy"}:
        return "sell_then_buy"
    if direction in {"正", "正t", "buy_then_sell"}:
        return "buy_then_sell"
    if direction in {"sig", "score", "factor", "signal"}:
        return "signal"
    if direction in {"dual", "yhat", "dual_score", "multi_y", "dual_y"}:
        return "dual_y"
    if direction in {"auto", "buy_then_sell", "sell_then_buy"}:
        return direction
    return default


DEFAULT_T0_RULES: Dict[str, Any] = {
    "enabled": True,
    "t0_ratio": 1.0,
    "must_cover_same_day": True,
    "must_cover_same_day_sell_then_buy": False,
    "must_cover_same_day_buy_then_sell": True,
    "lot_size": 100,
    "ref": "open",
    "fill_mode": "trigger",
    "fill_mode_sell_then_buy": "trigger",
    "fill_mode_buy_then_sell": "trigger",
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
    "min_range_pct": 0.0,
    "min_range_pct_sell_then_buy": 0.0,
    "min_range_pct_buy_then_sell": 0.0,
    "use_atr": False,
    "atr_window": 14,
    "atr_sell_mult": 0.9,
    "atr_buy_mult": 0.7,
    # dual_y 阈值（百分比点）：*_enter 入场下限；*_strong 超强须与 τ 同号
    "y_trade_enter": 0.01,
    "y_trade_strong": 0.2,
    "y_tau_enter": 0.01,
    # 反T / 正T 分侧入场；缺省与 y_tau_enter 同
    "y_tau_enter_sell_then_buy": 0.01,
    "y_tau_enter_buy_then_sell": 0.01,
    "y_path_enter": 0.01,
    "y_path_enter_sell_then_buy": 0.01,
    "y_path_enter_buy_then_sell": 0.01,
    "y_eod_enter": 0.01,
    "y_eod_strong": 0.2,
    "y_eod_prior": 0.01,
    "y_on_risk": 0.01,
    "y_on_allow": 0.01,
    "y_block_tau_nowcast_sign": True,
    "y_nc_enter": 0.01,
    "y_nc_strong": 0.2,
    "y_tau_map": "trend",
    "y_use_path": True,
    "y_path_required": False,
    "y_gap_tier_mode": "skip_opposite",
    "y_gap_tier_pct": 1.0,
    "y_nowcast_oc_gate": False,
    "y_path_abandon_enabled": True,
    "y_path_abandon_bars": 6,
    # 正/反T：固定前缀 N=abandon_bars；齐窗一次判定后半阴阳占比（不滚动探极值）
    # 正T：后半 close>open ≥upbar_ratio；反T：后半 close<open ≥downbar_ratio
    "y_prefix_upbar_ratio_buy_then_sell": 0.2,
    "y_prefix_downbar_ratio_sell_then_buy": 0.2,
    # v5 选腿：阴阳占比 ∩ 复合确认（开盘锚偏离+动量）；环境闸常开；半贪心滚仓常开
    # 偏离默认 0.3：0.8 在低波动票上几乎 0 成交；0=不要求偏离（仍要动量）
    "t0_confirm_dev_pct": 0.3,
    "t0_confirm_mom_bars": 2,
    "t0_confirm_vol_mult": 0.0,  # 0=不看量；>0 则末根≥均量×倍数
    "t0_env_min_range_pct": 0.5,
    "t0_env_min_path_abs": 0.08,
    "t0_env_one_sided_tau_abs": 2.0,
    "t0_env_one_sided_path_abs": 2.0,
    # 缺 ŷ_τ 时禁止开第一腿（τ 闸开则硬跳过，不等午后追价）
    "y_tau_require_for_leg1": True,
    # 确认根第一腿：正T买价<open×(1+ŷ_τ×裕度)；反T卖价>同式（有符号ŷ_τ，分侧可调）
    "y_tau_entry_price_skip": True,
    "y_tau_entry_price_mult": 0.5,
    "y_tau_entry_price_skip_buy_then_sell": True,
    "y_tau_entry_price_mult_buy_then_sell": 0.5,
    "y_tau_entry_price_skip_sell_then_buy": True,
    "y_tau_entry_price_mult_sell_then_buy": 0.5,
    # 价闸动幅：bound=open×(1+(clamp(ŷ_τ×裕度,min,max)+price_bias)/100)；价偏代数可正可负
    "y_tau_entry_price_bias": 0.5,
    "y_tau_entry_price_bias_buy_then_sell": 0.5,
    "y_tau_entry_price_bias_sell_then_buy": -0.5,
    "y_tau_entry_price_move_min": -100.0,
    "y_tau_entry_price_move_max": 100.0,
    "y_tau_exit_price_skip": True,
    "y_tau_exit_price_mult": 1.0,
    "y_tau_exit_price_skip_buy_then_sell": True,
    "y_tau_exit_price_mult_buy_then_sell": 1.0,
    "y_tau_exit_price_skip_sell_then_buy": True,
    "y_tau_exit_price_mult_sell_then_buy": 1.0,
    "y_tau_exit_price_bias": 1.0,
    "y_tau_exit_price_bias_buy_then_sell": 1.0,
    "y_tau_exit_price_bias_sell_then_buy": -1.0,
    "y_tau_exit_price_move_min": -100.0,
    "y_tau_exit_price_move_max": 100.0,
    "y_ratio_boost_cap": 2.0,
    "y_ratio_cut": 0.60,
    # 午后闸：到点后禁新开；已开未平则第二腿中点追价（与止损并存：止损管亏、追价管软卖）
    "t0_pm_degrade": "14:00",
    "t0_pm_degrade_sell_then_buy": "13:00",
    "t0_pm_degrade_buy_then_sell": "14:00",
    # 午后追价：默认不超过/不低于 leg1 成交价（即使 must_cover=False）
    "t0_pm_chase_cap_leg1_sell_then_buy": True,
    "t0_pm_chase_cap_leg1_buy_then_sell": True,
    "t0_pm_chase_interval_min": 5,
    "t0_pm_chase_interval_min_sell_then_buy": 5,
    "t0_pm_chase_interval_min_buy_then_sell": 5,
    # 正/反T第二腿止损（相对第一腿成交价）；0=关；默认延迟2根+收盘确认
    "t0_stop_pct_buy_then_sell": 1.2,
    "t0_stop_pct_sell_then_buy": 1.2,
    "t0_stop_arm_bars": 2,
    "t0_stop_on_close": True,
    # dual_y 分数来源：compute=开盘信息集即时算（默认）；live_book/ledger 仅兜底或对照
    "y_score_source": "compute",
    # 多轮独立做T：10:00–11:30 四轮各 15%；确认根收盘开第一腿；午后仅 leg2 追价/EOD
    # 不开 09:30。四轮共用一套买卖/止损配方；空轮仓位滚入后续（半贪心，常开）
    "t0_slots_enabled": True,
    "t0_slots": None,
    "t0_slots_max_rounds": 4,
    "note": "A股T+1底仓做T；仅5m first_touch（已删除日线模拟）；非实盘。",
}

# 第一腿最晚：11:30 确认根 = 第 24 根 5m（09:35 起计）；午后不再新开 leg1
T0_LAST_LEG1_HM = "11:30"
T0_LAST_LEG1_PREFIX_BARS = 24

# 10:00–11:30 四轮；不做 09:30 开盘轮、不做 13:00/14:00 午后新开
DEFAULT_T0_SLOTS: tuple = (
    {"id": "s1", "hm": "10:00", "prefix_bars": 6, "ratio": 0.15},
    {"id": "s2", "hm": "10:30", "prefix_bars": 12, "ratio": 0.15},
    {"id": "s3", "hm": "11:00", "prefix_bars": 18, "ratio": 0.15},
    {"id": "s4", "hm": "11:30", "prefix_bars": 24, "ratio": 0.15},
)
# 研究枢纽 τ / path 网格仍含 13:00/14:00；做 T 执行钟仅 10:00–11:30
DEFAULT_T0_SLOT_CLOCKS: tuple = tuple(
    str(s.get("hm") or "").strip() for s in DEFAULT_T0_SLOTS if str(s.get("hm") or "").strip()
)
# 纸面曾落盘的默认五轮（含 09:30 开盘）；加载时迁到现行四轮
_LEGACY_OPEN_FIVE_CLOCKS: tuple = ("09:30", "10:00", "10:30", "11:00", "11:30")
# 曾短暂默认六轮（含午后 leg1）；加载时迁到现行四轮
_LEGACY_SIX_CLOCKS: tuple = ("10:00", "10:30", "11:00", "11:30", "13:00", "14:00")


def _slot_allows_leg1(*, hm: str, prefix_bars: int) -> bool:
    """多轮做 T：超过 11:30 / 第 24 根前缀的槽位不允许开第一腿。"""
    try:
        n = int(prefix_bars or 0)
    except (TypeError, ValueError):
        n = 0
    if n > T0_LAST_LEG1_PREFIX_BARS:
        return False
    clock = str(hm or "").strip()[:5]
    if clock and clock > T0_LAST_LEG1_HM:
        return False
    return True


def normalize_t0_slots(raw: Any) -> list:
    """规范化槽位列表；空/缺省 → 默认四轮（10:00–11:30）。

    槽位只保留时钟 / 前缀根数 / 仓位切分。第二腿触发、止损、成交、dual_y 闸
    一律走账户级 ``load_t0_rules``（正/反分侧，不按槽位分套）。
    超过 ``T0_LAST_LEG1_*`` 的午后槽位会被丢弃（午后只做 leg2 追价/EOD）。
    """
    if raw is None:
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    if isinstance(raw, str) and not raw.strip():
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    if not isinstance(raw, (list, tuple)):
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    out: list = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or f"s{i + 1}").strip() or f"s{i + 1}"
        hm = str(item.get("hm") or "").strip()[:5]
        try:
            prefix_bars = int(item.get("prefix_bars") if item.get("prefix_bars") is not None else 0)
        except (TypeError, ValueError):
            prefix_bars = 0
        prefix_bars = max(0, min(prefix_bars, T0_LAST_LEG1_PREFIX_BARS))
        if not _slot_allows_leg1(hm=hm, prefix_bars=prefix_bars):
            continue
        try:
            ratio = float(item.get("ratio") if item.get("ratio") is not None else 0.15)
        except (TypeError, ValueError):
            ratio = 0.15
        ratio = max(0.05, min(ratio, 1.0))
        out.append({"id": sid, "hm": hm, "prefix_bars": prefix_bars, "ratio": ratio})
    clocks = tuple(str(s.get("hm") or "").strip() for s in out)
    if clocks in (_LEGACY_OPEN_FIVE_CLOCKS, _LEGACY_SIX_CLOCKS):
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    return out or [dict(s) for s in DEFAULT_T0_SLOTS]


def t0_slots_enabled(cfg: Optional[dict]) -> bool:
    """是否走多轮独立槽位（空列表视为关）。"""
    if not coerce_cfg_bool((cfg or {}).get("t0_slots_enabled"), True):
        return False
    slots = (cfg or {}).get("t0_slots")
    if isinstance(slots, (list, tuple)) and len(slots) == 0:
        return False
    return True


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
        ov = dict(override)
        _migrate_prefix_vs_path_to_tau_entry(ov)
        override_keys = set(ov.keys())
        for k, v in ov.items():
            if v is not None:
                cfg[k] = v
    drop_dead_t0_keys(cfg)
    cfg["t0_ratio"] = max(0.05, min(float(cfg.get("t0_ratio") or 1.0), 1.0))
    # 纸面/回测生效路径在 core.execution.resolve 再强制为 1.0
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
    for fill_side in ("fill_mode_sell_then_buy", "fill_mode_buy_then_sell"):
        if fill_side not in override_keys or cfg.get(fill_side) is None or cfg.get(fill_side) == "":
            cfg[fill_side] = fill
        else:
            fs = str(cfg.get(fill_side) or "").strip().lower()
            if fs not in {"trigger", "mid", "optimistic"}:
                fs = fill
            cfg[fill_side] = fs
    cfg["enabled"] = coerce_cfg_bool(cfg.get("enabled"), True)
    cfg["must_cover_same_day_sell_then_buy"] = coerce_cfg_bool(
        cfg.get("must_cover_same_day_sell_then_buy"), False
    )
    # 正T侧：显式侧向键优先；否则若只写了 legacy must_cover_same_day，用其覆盖默认 True
    # （旧逻辑用 coerce(default_reverse=True, legacy) 会吞掉 legacy=False）
    if "must_cover_same_day_buy_then_sell" in override_keys and cfg.get(
        "must_cover_same_day_buy_then_sell"
    ) is not None:
        cfg["must_cover_same_day_buy_then_sell"] = coerce_cfg_bool(
            cfg.get("must_cover_same_day_buy_then_sell"), True
        )
    elif "must_cover_same_day" in override_keys and cfg.get("must_cover_same_day") is not None:
        cfg["must_cover_same_day_buy_then_sell"] = coerce_cfg_bool(
            cfg.get("must_cover_same_day"), True
        )
    else:
        cfg["must_cover_same_day_buy_then_sell"] = coerce_cfg_bool(
            cfg.get("must_cover_same_day_buy_then_sell"), True
        )
    # legacy 键与正T侧对齐（旧读端，T+1 规则决定当日卖旧仓可行性）
    cfg["must_cover_same_day"] = cfg["must_cover_same_day_buy_then_sell"]
    cfg["use_atr"] = coerce_cfg_bool(cfg.get("use_atr"), False)
    cfg["direction"] = normalize_t0_direction(cfg.get("direction"), default="dual_y")
    # override_keys 已在函数开头定义
    _migrate_dual_y_gate_keys(cfg, override_keys)
    for yk, lo, hi, default in (
        ("y_eod_prior", 0.01, 5.0, 0.01),
        ("y_eod_enter", 0.01, 5.0, 0.01),
        ("y_eod_strong", 0.05, 5.0, 0.2),
        ("y_trade_enter", 0.01, 5.0, 0.01),
        ("y_trade_strong", 0.05, 5.0, 0.2),
        ("y_tau_enter", 0.01, 5.0, 0.01),
        ("y_tau_enter_sell_then_buy", 0.01, 5.0, 0.01),
        ("y_tau_enter_buy_then_sell", 0.01, 5.0, 0.01),
        ("y_on_risk", 0.01, 10.0, 0.01),
        ("y_on_allow", 0.01, 10.0, 0.01),
        ("y_ratio_boost_cap", 1.0, 2.0, 2.0),
        ("y_ratio_cut", 0.2, 1.0, 0.60),
        ("y_nc_enter", 0.01, 10.0, 0.01),
        ("y_nc_strong", 0.05, 10.0, 0.2),
        ("y_path_enter", 0.01, 5.0, 0.01),
        ("y_path_enter_sell_then_buy", 0.01, 5.0, 0.01),
        ("y_path_enter_buy_then_sell", 0.01, 5.0, 0.01),
        ("y_gap_tier_pct", 0.3, 8.0, 1.0),
    ):
        try:
            raw = cfg.get(yk)
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
        ("y_tau_enter_sell_then_buy", "y_tau_enter"),
        ("y_tau_enter_buy_then_sell", "y_tau_enter"),
        ("y_path_enter_sell_then_buy", "y_path_enter"),
        ("y_path_enter_buy_then_sell", "y_path_enter"),
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
    cfg["y_use_path"] = coerce_cfg_bool(cfg.get("y_use_path"), True)
    cfg["y_path_required"] = coerce_cfg_bool(cfg.get("y_path_required"), False)
    cfg["y_nowcast_oc_gate"] = coerce_cfg_bool(cfg.get("y_nowcast_oc_gate"), False)
    cfg["y_path_abandon_enabled"] = bool(cfg.get("y_path_abandon_enabled", True))
    try:
        cfg["y_path_abandon_bars"] = max(2, min(int(cfg.get("y_path_abandon_bars") or 6), 48))
    except (TypeError, ValueError):
        cfg["y_path_abandon_bars"] = 6
    for ab_side in ("y_path_abandon_bars_sell_then_buy", "y_path_abandon_bars_buy_then_sell"):
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
    try:
        raw_up = cfg.get("y_prefix_upbar_ratio_buy_then_sell")
        up_ratio = float(0.2 if raw_up is None or raw_up == "" else raw_up)
    except (TypeError, ValueError):
        up_ratio = 0.2
    cfg["y_prefix_upbar_ratio_buy_then_sell"] = max(0.0, min(up_ratio, 1.0))
    try:
        raw_dn = cfg.get("y_prefix_downbar_ratio_sell_then_buy")
        dn_ratio = float(0.2 if raw_dn is None or raw_dn == "" else raw_dn)
    except (TypeError, ValueError):
        dn_ratio = 0.2
    cfg["y_prefix_downbar_ratio_sell_then_buy"] = max(0.0, min(dn_ratio, 1.0))
    try:
        raw_dev = cfg.get("t0_confirm_dev_pct")
        cfg["t0_confirm_dev_pct"] = max(
            0.0, min(float(0.3 if raw_dev is None or raw_dev == "" else raw_dev), 20.0)
        )
    except (TypeError, ValueError):
        cfg["t0_confirm_dev_pct"] = 0.3
    try:
        raw_mom = cfg.get("t0_confirm_mom_bars")
        cfg["t0_confirm_mom_bars"] = max(
            1, min(int(2 if raw_mom is None or raw_mom == "" else raw_mom), 12)
        )
    except (TypeError, ValueError):
        cfg["t0_confirm_mom_bars"] = 2
    try:
        raw_vol = cfg.get("t0_confirm_vol_mult")
        cfg["t0_confirm_vol_mult"] = max(
            0.0, min(float(0.0 if raw_vol is None or raw_vol == "" else raw_vol), 20.0)
        )
    except (TypeError, ValueError):
        cfg["t0_confirm_vol_mult"] = 0.0
    try:
        raw_range = cfg.get("t0_env_min_range_pct")
        cfg["t0_env_min_range_pct"] = max(
            0.0,
            min(float(0.5 if raw_range is None or raw_range == "" else raw_range), 30.0),
        )
    except (TypeError, ValueError):
        cfg["t0_env_min_range_pct"] = 0.5
    try:
        raw_path = cfg.get("t0_env_min_path_abs")
        cfg["t0_env_min_path_abs"] = max(
            0.0,
            min(float(0.08 if raw_path is None or raw_path == "" else raw_path), 50.0),
        )
    except (TypeError, ValueError):
        cfg["t0_env_min_path_abs"] = 0.08
    try:
        raw_ot = cfg.get("t0_env_one_sided_tau_abs")
        cfg["t0_env_one_sided_tau_abs"] = max(
            0.0,
            min(float(2.0 if raw_ot is None or raw_ot == "" else raw_ot), 50.0),
        )
    except (TypeError, ValueError):
        cfg["t0_env_one_sided_tau_abs"] = 2.0
    try:
        raw_op = cfg.get("t0_env_one_sided_path_abs")
        cfg["t0_env_one_sided_path_abs"] = max(
            0.0,
            min(float(2.0 if raw_op is None or raw_op == "" else raw_op), 50.0),
        )
    except (TypeError, ValueError):
        cfg["t0_env_one_sided_path_abs"] = 2.0
    legacy_skip = coerce_cfg_bool(cfg.get("y_tau_entry_price_skip"), True)
    try:
        raw_legacy_mult = cfg.get("y_tau_entry_price_mult")
        legacy_mult = float(
            0.5 if raw_legacy_mult is None or raw_legacy_mult == "" else raw_legacy_mult
        )
    except (TypeError, ValueError):
        legacy_mult = 0.5
    legacy_mult = max(0.5, min(legacy_mult, 5.0))
    for side in ("buy_then_sell", "sell_then_buy"):
        sk = f"y_tau_entry_price_skip_{side}"
        mk = f"y_tau_entry_price_mult_{side}"
        if sk not in override_keys or cfg.get(sk) is None:
            if cfg.get(sk) is None:
                cfg[sk] = legacy_skip
        cfg[sk] = coerce_cfg_bool(cfg.get(sk), True)
        try:
            raw_side_mult = cfg.get(mk)
            if raw_side_mult is None or raw_side_mult == "":
                raw_side_mult = legacy_mult
            side_mult = float(raw_side_mult)
        except (TypeError, ValueError):
            side_mult = legacy_mult
        cfg[mk] = max(0.5, min(side_mult, 5.0))
    cfg["y_tau_entry_price_skip"] = cfg["y_tau_entry_price_skip_buy_then_sell"]
    cfg["y_tau_entry_price_mult"] = cfg["y_tau_entry_price_mult_buy_then_sell"]
    try:
        raw_legacy_entry_bias = cfg.get("y_tau_entry_price_bias")
        legacy_entry_bias = float(
            0.5
            if raw_legacy_entry_bias is None or raw_legacy_entry_bias == ""
            else raw_legacy_entry_bias
        )
    except (TypeError, ValueError):
        legacy_entry_bias = 0.5
    legacy_entry_bias = max(-50.0, min(legacy_entry_bias, 50.0))
    try:
        legacy_entry_move_min = float(cfg.get("y_tau_entry_price_move_min") or -100.0)
    except (TypeError, ValueError):
        legacy_entry_move_min = -100.0
    try:
        legacy_entry_move_max = float(cfg.get("y_tau_entry_price_move_max") or 100.0)
    except (TypeError, ValueError):
        legacy_entry_move_max = 100.0
    legacy_entry_move_min = max(-100.0, min(legacy_entry_move_min, 100.0))
    legacy_entry_move_max = max(-100.0, min(legacy_entry_move_max, 100.0))
    if legacy_entry_move_min > legacy_entry_move_max:
        legacy_entry_move_min, legacy_entry_move_max = (
            legacy_entry_move_max,
            legacy_entry_move_min,
        )
    for side in ("buy_then_sell", "sell_then_buy"):
        bk = f"y_tau_entry_price_bias_{side}"
        mnk = f"y_tau_entry_price_move_min_{side}"
        mxk = f"y_tau_entry_price_move_max_{side}"
        try:
            raw_bias = cfg.get(bk)
            if raw_bias is None or raw_bias == "":
                raw_bias = legacy_entry_bias
            side_bias = float(raw_bias)
        except (TypeError, ValueError):
            side_bias = legacy_entry_bias
        cfg[bk] = max(-50.0, min(side_bias, 50.0))
        try:
            raw_min = cfg.get(mnk)
            if raw_min is None or raw_min == "":
                raw_min = legacy_entry_move_min
            side_min = float(raw_min)
        except (TypeError, ValueError):
            side_min = legacy_entry_move_min
        try:
            raw_max = cfg.get(mxk)
            if raw_max is None or raw_max == "":
                raw_max = legacy_entry_move_max
            side_max = float(raw_max)
        except (TypeError, ValueError):
            side_max = legacy_entry_move_max
        side_min = max(-100.0, min(side_min, 100.0))
        side_max = max(-100.0, min(side_max, 100.0))
        if side_min > side_max:
            side_min, side_max = side_max, side_min
        cfg[mnk] = side_min
        cfg[mxk] = side_max
    cfg["y_tau_entry_price_bias"] = cfg["y_tau_entry_price_bias_buy_then_sell"]
    cfg["y_tau_entry_price_move_min"] = cfg["y_tau_entry_price_move_min_buy_then_sell"]
    cfg["y_tau_entry_price_move_max"] = cfg["y_tau_entry_price_move_max_buy_then_sell"]
    legacy_exit_skip = coerce_cfg_bool(cfg.get("y_tau_exit_price_skip"), True)
    try:
        raw_legacy_exit_mult = cfg.get("y_tau_exit_price_mult")
        legacy_exit_mult = float(
            1.0
            if raw_legacy_exit_mult is None or raw_legacy_exit_mult == ""
            else raw_legacy_exit_mult
        )
    except (TypeError, ValueError):
        legacy_exit_mult = 1.0
    legacy_exit_mult = max(0.5, min(legacy_exit_mult, 5.0))
    for side in ("buy_then_sell", "sell_then_buy"):
        sk = f"y_tau_exit_price_skip_{side}"
        mk = f"y_tau_exit_price_mult_{side}"
        if sk not in override_keys or cfg.get(sk) is None:
            if cfg.get(sk) is None:
                cfg[sk] = legacy_exit_skip
        cfg[sk] = coerce_cfg_bool(cfg.get(sk), True)
        try:
            raw_side_mult = cfg.get(mk)
            if raw_side_mult is None or raw_side_mult == "":
                raw_side_mult = legacy_exit_mult
            side_mult = float(raw_side_mult)
        except (TypeError, ValueError):
            side_mult = legacy_exit_mult
        cfg[mk] = max(0.5, min(side_mult, 5.0))
    cfg["y_tau_exit_price_skip"] = cfg["y_tau_exit_price_skip_buy_then_sell"]
    cfg["y_tau_exit_price_mult"] = cfg["y_tau_exit_price_mult_buy_then_sell"]
    try:
        raw_legacy_exit_bias = cfg.get("y_tau_exit_price_bias")
        legacy_exit_bias = float(
            1.0
            if raw_legacy_exit_bias is None or raw_legacy_exit_bias == ""
            else raw_legacy_exit_bias
        )
    except (TypeError, ValueError):
        legacy_exit_bias = 1.0
    legacy_exit_bias = max(-50.0, min(legacy_exit_bias, 50.0))
    try:
        legacy_exit_move_min = float(cfg.get("y_tau_exit_price_move_min") or -100.0)
    except (TypeError, ValueError):
        legacy_exit_move_min = -100.0
    try:
        legacy_exit_move_max = float(cfg.get("y_tau_exit_price_move_max") or 100.0)
    except (TypeError, ValueError):
        legacy_exit_move_max = 100.0
    legacy_exit_move_min = max(-100.0, min(legacy_exit_move_min, 100.0))
    legacy_exit_move_max = max(-100.0, min(legacy_exit_move_max, 100.0))
    if legacy_exit_move_min > legacy_exit_move_max:
        legacy_exit_move_min, legacy_exit_move_max = (
            legacy_exit_move_max,
            legacy_exit_move_min,
        )
    for side in ("buy_then_sell", "sell_then_buy"):
        bk = f"y_tau_exit_price_bias_{side}"
        mnk = f"y_tau_exit_price_move_min_{side}"
        mxk = f"y_tau_exit_price_move_max_{side}"
        try:
            raw_bias = cfg.get(bk)
            if raw_bias is None or raw_bias == "":
                raw_bias = legacy_exit_bias
            side_bias = float(raw_bias)
        except (TypeError, ValueError):
            side_bias = legacy_exit_bias
        cfg[bk] = max(-50.0, min(side_bias, 50.0))
        try:
            raw_min = cfg.get(mnk)
            if raw_min is None or raw_min == "":
                raw_min = legacy_exit_move_min
            side_min = float(raw_min)
        except (TypeError, ValueError):
            side_min = legacy_exit_move_min
        try:
            raw_max = cfg.get(mxk)
            if raw_max is None or raw_max == "":
                raw_max = legacy_exit_move_max
            side_max = float(raw_max)
        except (TypeError, ValueError):
            side_max = legacy_exit_move_max
        side_min = max(-100.0, min(side_min, 100.0))
        side_max = max(-100.0, min(side_max, 100.0))
        if side_min > side_max:
            side_min, side_max = side_max, side_min
        cfg[mnk] = side_min
        cfg[mxk] = side_max
    cfg["y_tau_exit_price_bias"] = cfg["y_tau_exit_price_bias_buy_then_sell"]
    cfg["y_tau_exit_price_move_min"] = cfg["y_tau_exit_price_move_min_buy_then_sell"]
    cfg["y_tau_exit_price_move_max"] = cfg["y_tau_exit_price_move_max_buy_then_sell"]
    gap_mode = str(cfg.get("y_gap_tier_mode") or "skip_opposite").strip().lower()
    if gap_mode not in {"off", "none", "false", "0", "skip_opposite", "revert"}:
        gap_mode = "skip_opposite"
    cfg["y_gap_tier_mode"] = gap_mode
    # 主仓 breakglass 回注的有效 τ 门槛（可选）
    try:
        eff = cfg.get("y_tau_enter_effective")
        if eff is not None and eff != "":
            cfg["y_tau_enter_effective"] = max(0.0, min(float(eff), 5.0))
        else:
            cfg.pop("y_tau_enter_effective", None)
    except (TypeError, ValueError):
        cfg.pop("y_tau_enter_effective", None)
    cfg.pop("y_block_conflict", None)  # 已下线：eod↔τ / y_check 冲突跳过
    # 丢弃已下线的不利/时间止损字段（旧账户 overlay 可能残留）
    for _dead in (
        "t0_adverse_stop_pct",
        "t0_adverse_stop_atr_mult",
        "t0_time_stop",
        "t0_time_stop_underwater_only",
    ):
        cfg.pop(_dead, None)
    drop_dead_t0_keys(cfg)  # 含旧 long/reverse、y_prefix_min_half_hits 等
    def _norm_pm_degrade(raw: Any, default: str) -> str:
        if raw is None:
            return default
        s = str(raw).strip()
        if s in {"", "0", "off", "none", "-"}:
            return ""
        return s

    pm_legacy = cfg.get("t0_pm_degrade")
    for side_key, default in (
        ("t0_pm_degrade_sell_then_buy", "13:00"),
        ("t0_pm_degrade_buy_then_sell", "14:00"),
    ):
        if side_key not in override_keys or cfg.get(side_key) is None:
            if side_key == "t0_pm_degrade_buy_then_sell" and pm_legacy is not None:
                cfg[side_key] = _norm_pm_degrade(pm_legacy, default)
            else:
                cfg[side_key] = _norm_pm_degrade(cfg.get(side_key), default)
        else:
            cfg[side_key] = _norm_pm_degrade(cfg.get(side_key), default)
    cfg["t0_pm_degrade"] = cfg["t0_pm_degrade_buy_then_sell"]
    legacy_iv = cfg.get("t0_pm_chase_interval_min")
    for iv_key in ("t0_pm_chase_interval_min_sell_then_buy", "t0_pm_chase_interval_min_buy_then_sell"):
        if iv_key not in override_keys or cfg.get(iv_key) is None or cfg.get(iv_key) == "":
            if iv_key == "t0_pm_chase_interval_min_buy_then_sell" and legacy_iv is not None:
                raw_iv = legacy_iv
            else:
                raw_iv = cfg.get(iv_key)
        else:
            raw_iv = cfg.get(iv_key)
        try:
            iv = int(raw_iv if raw_iv is not None and raw_iv != "" else 5)
        except (TypeError, ValueError):
            iv = 5
        cfg[iv_key] = max(1, min(iv, 60))
    cfg["t0_pm_chase_interval_min"] = cfg["t0_pm_chase_interval_min_buy_then_sell"]
    # 正T跌破止损
    try:
        raw_stop = cfg.get("t0_stop_pct_buy_then_sell")
        if raw_stop is None or raw_stop == "":
            stop_pct = 1.2
        else:
            stop_pct = float(raw_stop)
    except (TypeError, ValueError):
        stop_pct = 1.2
    cfg["t0_stop_pct_buy_then_sell"] = max(0.0, min(stop_pct, 20.0))
    try:
        raw_stop_stb = cfg.get("t0_stop_pct_sell_then_buy")
        if raw_stop_stb is None or raw_stop_stb == "":
            stop_pct_stb = 1.2
        else:
            stop_pct_stb = float(raw_stop_stb)
    except (TypeError, ValueError):
        stop_pct_stb = 1.2
    cfg["t0_stop_pct_sell_then_buy"] = max(0.0, min(stop_pct_stb, 20.0))
    try:
        raw_arm = cfg.get("t0_stop_arm_bars")
        arm_bars = int(2 if raw_arm is None or raw_arm == "" else raw_arm)
    except (TypeError, ValueError):
        arm_bars = 2
    cfg["t0_stop_arm_bars"] = max(0, min(arm_bars, 48))
    cfg["t0_stop_on_close"] = coerce_cfg_bool(cfg.get("t0_stop_on_close"), True)
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
        # 0=振幅下限门禁关（Web 已下线）；兼容旧盘仍可读字段
        cfg["min_range_pct"] = max(0.0, min(float(cfg["min_range_pct"]), 30.0))
    for mr_side, base_mr in (
        ("min_range_pct_sell_then_buy", "min_range_pct"),
        ("min_range_pct_buy_then_sell", "min_range_pct"),
    ):
        raw_mr = cfg.get(mr_side)
        if raw_mr is None or raw_mr == "":
            cfg[mr_side] = cfg.get(base_mr)
        else:
            try:
                cfg[mr_side] = max(0.0, min(float(raw_mr), 30.0))
            except (TypeError, ValueError):
                cfg[mr_side] = cfg.get(base_mr)
    cfg["t0_slots_enabled"] = coerce_cfg_bool(cfg.get("t0_slots_enabled"), True)
    raw_max_rounds = cfg.get("t0_slots_max_rounds")
    if raw_max_rounds is None or raw_max_rounds == "":
        cfg["t0_slots_max_rounds"] = 4
    else:
        try:
            cfg["t0_slots_max_rounds"] = max(0, min(int(raw_max_rounds), 16))
        except (TypeError, ValueError):
            cfg["t0_slots_max_rounds"] = 4
    if isinstance(cfg.get("t0_slots"), (list, tuple)) and len(cfg.get("t0_slots") or []) == 0:
        cfg["t0_slots"] = []
    else:
        cfg["t0_slots"] = normalize_t0_slots(cfg.get("t0_slots"))
    return cfg


def apply_side_exec_params(cfg: dict, direction: Optional[str]) -> Dict[str, Any]:
    """按正/反 T 覆盖执行参数：成交模式、回补、午后闸等（第二腿仅 τ 出场价闸）。"""
    out = dict(cfg or {})
    if direction not in {"sell_then_buy", "buy_then_sell"}:
        return out
    suf = "_buy_then_sell" if direction == "buy_then_sell" else "_sell_then_buy"
    mr = out.get(f"min_range_pct{suf}")
    if mr is not None and mr != "":
        try:
            out["min_range_pct"] = max(0.0, min(float(mr), 30.0))
        except (TypeError, ValueError):
            pass
    fm = out.get(f"fill_mode{suf}")
    if fm is not None and str(fm).strip():
        fs = str(fm).strip().lower()
        if fs in {"trigger", "mid", "optimistic"}:
            out["fill_mode"] = fs
    mc = out.get(f"must_cover_same_day{suf}")
    if mc is not None:
        default_mc = direction == "buy_then_sell"
        out["must_cover_same_day"] = coerce_cfg_bool(mc, default_mc)
    pm = out.get(f"t0_pm_degrade{suf}")
    if pm is not None:
        s = str(pm).strip()
        out["t0_pm_degrade"] = "" if s in {"", "0", "off", "none", "-"} else s
    elif "t0_pm_degrade" not in out or out.get("t0_pm_degrade") is None:
        # 缺侧向键时回落默认：正T 14:00 / 反T 13:00
        out["t0_pm_degrade"] = "13:00" if direction == "sell_then_buy" else "14:00"
    iv = out.get(f"t0_pm_chase_interval_min{suf}")
    if iv is not None and iv != "":
        try:
            out["t0_pm_chase_interval_min"] = max(1, min(int(iv), 60))
        except (TypeError, ValueError):
            pass
    elif not out.get("t0_pm_chase_interval_min"):
        side_iv = out.get(f"t0_pm_chase_interval_min{suf}")
        try:
            raw_iv = side_iv if side_iv is not None and side_iv != "" else 5
            out["t0_pm_chase_interval_min"] = max(1, min(int(raw_iv), 60))
        except (TypeError, ValueError):
            out["t0_pm_chase_interval_min"] = 5
    return out


def resolve_path_abandon_bars(cfg: dict, direction: Optional[str] = None) -> int:
    """前缀放弃根数；优先侧向键，否则共用 ``y_path_abandon_bars``。"""
    d = str(direction or "").strip().lower()
    side_key = None
    if d == "sell_then_buy":
        side_key = "y_path_abandon_bars_sell_then_buy"
    elif d == "buy_then_sell":
        side_key = "y_path_abandon_bars_buy_then_sell"
    raw = None
    if side_key is not None and (cfg or {}).get(side_key) is not None:
        raw = (cfg or {}).get(side_key)
    else:
        raw = (cfg or {}).get("y_path_abandon_bars")
    try:
        return max(2, min(int(raw or 6), 48))
    except (TypeError, ValueError):
        return 6


def resolve_min_range_pct(cfg: dict) -> float:
    """兼容旧调用；振幅下限门禁已下线，恒返回 0。"""
    _ = cfg
    return 0.0


# 盘中 Worker：与 5m K 线对齐的轮询与分钟缓存 TTL
T0_INTRADAY_TICK_SEC = 300.0
T0_INTRADAY_MINUTE_CACHE_HOURS = 5.0 / 60.0
T0_INTRADAY_MINUTE_LOOKBACK_DAYS = 5
