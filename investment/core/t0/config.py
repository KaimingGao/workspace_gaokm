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
# must_cover_same_day_*：正T/反T默认均强制当日回补
# 第二腿：τ 出场价闸（正T卖 / 反T买）；相对 leg1 的 % 触发已下线
#
# fill_mode: trigger | mid | optimistic；可分侧 fill_mode_*
#
# direction:
#   dual_y  — 旗舰选腿：ŷ_oc 破带定方向与 C_τ 目标价；ŷ_τw 门槛
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
    # 前缀振幅 vs |path|、τ 入场价闸：均已下线（第一腿=确认根收盘）
    "y_prefix_vs_path_skip",
    "y_prefix_vs_path_mult",
    "y_tau_require_for_leg1",
    "y_tau_entry_price_mult",
    "y_tau_entry_price_mult_buy_then_sell",
    "y_tau_entry_price_mult_sell_then_buy",
    "y_tau_entry_price_skip",
    "y_tau_entry_price_skip_buy_then_sell",
    "y_tau_entry_price_skip_sell_then_buy",
    "y_tau_entry_price_bias",
    "y_tau_entry_price_bias_buy_then_sell",
    "y_tau_entry_price_bias_sell_then_buy",
    "y_tau_entry_price_move_min",
    "y_tau_entry_price_move_max",
    "y_tau_entry_price_move_min_buy_then_sell",
    "y_tau_entry_price_move_max_buy_then_sell",
    "y_tau_entry_price_move_min_sell_then_buy",
    "y_tau_entry_price_move_max_sell_then_buy",
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
    # v6：收盘带宽选腿；前缀阴阳/复合确认/环境闸选腿下线
    "t0_confirm_dev_pct",
    "t0_confirm_mom_bars",
    "t0_confirm_vol_mult",
    "t0_env_min_range_pct",
    "t0_env_min_path_abs",
    "t0_env_one_sided_tau_abs",
    "t0_env_one_sided_path_abs",
    "y_prefix_upbar_ratio_buy_then_sell",
    "y_prefix_downbar_ratio_sell_then_buy",
    "y_path_abandon_enabled",
    "y_path_abandon_bars",
    "y_path_abandon_bars_buy_then_sell",
    "y_path_abandon_bars_sell_then_buy",
    # score 先验平移带宽已下线，旧 overlay 丢弃
    "y_tau_leg1_prior_band_floor",
    "y_tau_leg1_prior",
    "y_tau_leg1_prior_mode",
    "y_tau_leg1_prior_risk",
    "y_tau_leg1_prior_shift_scale",
    # 表单已下线：超额 r 入场闸（误标 R̂_τ，实际是 |C/C_τ−1|）
    "r_tau_enter",
    "r_tau_enter_alt",
    # v6 收盘带宽：dual_y 选向 / 振幅下限 / ATR / 缺口档 / trade·eod 强闸已不参与开腿
    "min_range_pct",
    "min_range_pct_sell_then_buy",
    "min_range_pct_buy_then_sell",
    "y_tau_map",
    # ŷ_τc 租用已由 ŷ_τ30/60/90 覆盖
    "y_tc_strong",
    "y_τc_strong",
    "y_tc_enter",
    "y_τc_enter",
    "y_tc_enter_alt",
    "y_τc_enter_alt",
    "y_tc_validate",
    "y_τc_validate",
    # ŷ_τ30/45/60/75/90 个股旁路已下线：只进 ŷ_τw 投票
    "y_t30_strong",
    "y_τ30_strong",
    "y_t45_strong",
    "y_τ45_strong",
    "y_t60_strong",
    "y_τ60_strong",
    "y_t75_strong",
    "y_τ75_strong",
    "y_t90_strong",
    "y_τ90_strong",
    # ŷ_τ30/45/60/75/90 个股入场已下线：不考虑单独阈值
    "y_t30_enter",
    "y_τ30_enter",
    "y_t30_enter_alt",
    "y_τ30_enter_alt",
    "y_t45_enter",
    "y_τ45_enter",
    "y_t45_enter_alt",
    "y_τ45_enter_alt",
    "y_t60_enter",
    "y_τ60_enter",
    "y_t60_enter_alt",
    "y_τ60_enter_alt",
    "y_t75_enter",
    "y_τ75_enter",
    "y_t75_enter_alt",
    "y_τ75_enter_alt",
    "y_t90_enter",
    "y_τ90_enter",
    "y_t90_enter_alt",
    "y_τ90_enter_alt",
    # 门槛1/2 |ŷ_oc| / |y_hl| 入场已下线
    "y_hl_strong",
    "y_hl_required",
    "y_enter_enabled",
    "y_enter_alt_enabled",
    "y_tau_enter",
    "y_tau_enter_strong",
    "y_tau_enter_sell_then_buy",
    "y_tau_enter_buy_then_sell",
    "y_tau_enter_alt",
    "y_hl_enter",
    "y_hl_enter_sell_then_buy",
    "y_hl_enter_buy_then_sell",
    "y_hl_enter_alt",
    # 旧 y_path_* 闸键：load 时迁到 y_hl_* 后再丢
    "y_path_enter",
    "y_path_enter_sell_then_buy",
    "y_path_enter_buy_then_sell",
    "y_path_enter_alt",
    "y_path_strong",
    "y_use_path",
    "y_use_hl",
    "y_path_required",
    # ŷ_cx / ŷ_tpd 验证后暂不用于调仓与做 T
    "y_complexity_max",
    "y_cx_max",
    "y_tpd_max",
    "y_complexity_max_alt",
    "y_tpd_max_alt",
    "y_gap_tier_mode",
    "y_gap_tier_pct",
    "y_trade_strong",
    "y_eod_strong",
    "y_eod_enter",
    "y_eod_prior",
    "y_trade_tau_sign_gate",
    "y_eod_tau_sign_gate",
    "y_block_tau_nowcast_sign",
    "y_block_trade_tau_sign",
    "y_nowcast_oc_gate",
    "y_nc_enter",
    "y_nc_strong",
    "y_nowcast_enter",
    "use_atr",
    "atr_window",
    "atr_sell_mult",
    "atr_buy_mult",
    "dir_enter",
    "auto_strong_pct",
    "auto_weak_pct",
    "w_gap",
    "w_yclose_loc",
    "w_mom3",
    "w_gap_atr",
    # 阴阳门槛 / 前序 ŷ_τw 确认 / 收贴端已下线
    "t0_bar_oc_gate",
    "t0_leg1_close_extreme",
    "t0_ytw_prefix_confirm",
    "t0_ytw_prefix_lookback",
    "t0_ytw_prefix_min_hit_pct",
    # 正/反 T 分侧 Y_τw入场已下线，表单共用 y_tw_enter
    "y_tw_enter_buy_then_sell",
    "y_tw_enter_sell_then_buy",
    "y_τw_enter_buy_then_sell",
    "y_τw_enter_sell_then_buy",
    "y_ratio_boost_cap",
    "y_ratio_cut",
    "y_block_conflict",
    # 自极值回吐已下线，改锁赢%
    "t0_giveback_pct_buy_then_sell",
    "t0_giveback_pct_sell_then_buy",
    "t0_giveback_arm_pct",
)


_Y_PATH_TO_HL = (
    ("y_path_enter", "y_hl_enter"),
    ("y_path_enter_sell_then_buy", "y_hl_enter_sell_then_buy"),
    ("y_path_enter_buy_then_sell", "y_hl_enter_buy_then_sell"),
    ("y_path_enter_alt", "y_hl_enter_alt"),
    ("y_path_strong", "y_hl_strong"),
    ("y_path_required", "y_hl_required"),
)


def migrate_y_path_keys_to_hl(cfg: dict, override_keys: Optional[set] = None) -> None:
    """旧 overlay ``y_path_*`` → ``y_hl_*``。新键已显式覆盖时不抢。"""
    if not isinstance(cfg, dict):
        return
    keys = override_keys
    for old_key, new_key in _Y_PATH_TO_HL:
        explicit_new = (
            keys is not None and new_key in keys and cfg.get(new_key) not in (None, "")
        )
        if explicit_new:
            cfg.pop(old_key, None)
            continue
        if cfg.get(old_key) not in (None, ""):
            cfg[new_key] = cfg[old_key]
            if keys is not None:
                keys.add(new_key)
        cfg.pop(old_key, None)


def drop_dead_t0_keys(cfg: dict) -> None:
    """就地丢弃已废弃做 T 键（含旧 long/reverse 命名）。"""
    if not isinstance(cfg, dict):
        return
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
    "must_cover_same_day_sell_then_buy": True,
    "must_cover_same_day_buy_then_sell": True,
    "lot_size": 100,
    "ref": "open",
    "fill_mode": "trigger",
    "fill_mode_sell_then_buy": "trigger",
    "fill_mode_buy_then_sell": "trigger",
    "direction": "dual_y",
    "path_mode": "first_touch",
    "minute_period": "5",
    # dual_y 阈值（百分比点）：*_enter 入场下限（v6 入场闸 / 回补）
    "y_trade_enter": 0.01,
    "y_tw_enter": 2.0,  # 正T：ŷ_τw>=此票；反T：ŷ_τw<=−此票。0=允许 0 票
    "y_τw_enter": 2.0,
    "y_tw_strong": 2.0,  # 过入场后 |ŷ_τw|>=此票用强股数，否则入场股数
    "y_τw_strong": 2.0,
    "y_tw_enter_shares": 2000,  # 过入场未过强：本轮股数
    "y_τw_enter_shares": 2000,
    "y_tw_strong_shares": 4000,  # 过强：本轮股数（≥入场股数）
    "y_τw_strong_shares": 4000,
    "y_tw_vote_margin": 5.0,  # |p_up−mid|≤此百分点不给 ŷ_τw 投票
    "y_τw_vote_margin": 5.0,
    "y_tw_midpoint": 47.0,  # ŷ_τ30/45/60/75/90 共用中位点%
    "y_τw_midpoint": 47.0,
    "t0_y_oc_target_scale": 10.0,  # C_τ = O×(1+clip(ŷ_oc×scale, l, u)/100)
    "t0_y_oc_l": -3.0,
    "t0_y_oc_u": 3.0,
    "t0_close_band_delta_pct": 0.5,  # 破带带宽 δ%
    "fusion_w_τc": 0.5,  # residual 融合：ŷ_τc 权
    "fusion_w_tc": 0.5,
    "residual_w_oc": 0.5,  # residual 融合：remaining(ŷ_oc) 权
    "residual_w_mode": "fixed",  # fixed | inv_var
    "y_on_risk": 0.01,
    "y_on_allow": 0.01,
    # 日/分昨收错位门禁（|P_d/P_m−1| 超阈跳过）；开盘差已下线（0=关）
    "t0_price_space_gate": True,
    "t0_price_space_max_dev_pct": 0.0,
    "t0_price_space_prev_dev_pct": 5.0,
    "t0_round_ratio": 0.4,
    "t0_max_position_pct": 1.0,
    # 第一腿：确认根收盘；τ 入场价闸已下线
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
    # 午后闸：到点后禁新开；已开未平则第二腿中点追价（与止损并存：止损管亏、追价管软卖）
    "t0_pm_degrade": "13:00",
    "t0_pm_degrade_sell_then_buy": "13:00",
    "t0_pm_degrade_buy_then_sell": "13:00",
    # 午后追价：默认不超过/不低于 leg1 成交价（即使 must_cover=False）
    "t0_pm_chase_cap_leg1_sell_then_buy": True,
    "t0_pm_chase_cap_leg1_buy_then_sell": True,
    "t0_pm_chase_interval_min": 5,
    "t0_pm_chase_interval_min_sell_then_buy": 5,
    "t0_pm_chase_interval_min_buy_then_sell": 5,
    # 正/反T第二腿止损（相对第一腿成交价）；0=关；默认延迟1根+收盘确认
    "t0_stop_pct_buy_then_sell": 1.2,
    "t0_stop_pct_sell_then_buy": 1.2,
    "t0_stop_arm_bars": 1,
    "t0_stop_on_close": True,
    # 锁赢：收益达到此%则提前第二腿（0=关）；与止损方向相反
    "t0_lock_win_pct_buy_then_sell": 2.0,
    "t0_lock_win_pct_sell_then_buy": 2.0,
    # dual_y 分数来源：compute=开盘信息集即时算（默认）；live_book/ledger 仅兜底或对照
    "y_score_source": "compute",
    # v6：多轮（每轮 40% 至满仓）；11:00 后不开 leg1；午后仅 leg2
    "t0_slots_enabled": True,
    "t0_slots": None,
    "t0_slots_max_rounds": 5,
    "note": "A股T+1底仓做T；旗舰选腿=ŷ_oc破带+ŷ_τw入场；非实盘。",
}

# 第一腿最晚：11:00（约第 18 根 5m，09:35 起计）；其后只收第二腿
T0_LAST_LEG1_HM = "11:00"
T0_LAST_LEG1_PREFIX_BARS = 18

# 兼容旧 UI/测试的槽位壳：v6 实际按破带动态开轮，不再钉死确认钟
DEFAULT_T0_SLOTS: tuple = (
    {"id": "r1", "hm": "11:00", "prefix_bars": 18, "ratio": 0.20},
    {"id": "r2", "hm": "11:00", "prefix_bars": 18, "ratio": 0.20},
    {"id": "r3", "hm": "11:00", "prefix_bars": 18, "ratio": 0.20},
    {"id": "r4", "hm": "11:00", "prefix_bars": 18, "ratio": 0.20},
    {"id": "r5", "hm": "11:00", "prefix_bars": 18, "ratio": 0.20},
)
DEFAULT_T0_SLOT_CLOCKS: tuple = ("11:00",)


def _am_5m_clocks_until(last_hm: str = T0_LAST_LEG1_HM) -> tuple:
    """09:35 起每 5 分钟至 ``last_hm``（含），供分槽画像对齐拟合 by_tau。"""
    out: list = []
    h, m = 9, 35
    last = str(last_hm or T0_LAST_LEG1_HM).strip()[:5] or T0_LAST_LEG1_HM
    while True:
        hm = f"{h:02d}:{m:02d}"
        if hm > last:
            break
        out.append(hm)
        m += 5
        if m >= 60:
            h += 1
            m -= 60
        if h > 15:
            break
    return tuple(out)


# v6 扫描网格：每根 5m 因果 ŷ vs 全日标签（不是破带开轮才入样）
T0_PORTRAIT_SLOT_CLOCKS: tuple = _am_5m_clocks_until(T0_LAST_LEG1_HM)
# 日级「预估命中」用首根 5m，避免跳过日被 11:00 前缀垫高
T0_PORTRAIT_DAY_HM = "09:35"
_LEGACY_OPEN_FIVE_CLOCKS: tuple = ("09:30", "10:00", "10:30", "11:00", "11:30")
_LEGACY_SIX_CLOCKS: tuple = ("10:00", "10:30", "11:00", "11:30", "13:00", "14:00")
_LEGACY_V5_FOUR_CLOCKS: tuple = ("10:00", "10:30", "11:00", "11:30")


def _slot_allows_leg1(*, hm: str, prefix_bars: int) -> bool:
    """超过 11:00 / 第 18 根前缀不允许开第一腿。"""
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
    """规范化槽位列表；v6 默认五轮×20% 壳（执行走收盘带宽扫描）。

    旧四轮/五轮/六轮时钟落盘一律迁到 v6 默认壳。
    """
    if raw is None:
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    if isinstance(raw, str) and not raw.strip():
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    if not isinstance(raw, (list, tuple)):
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    raw_clocks = tuple(
        str(item.get("hm") or "").strip()[:5]
        for item in raw
        if isinstance(item, dict)
    )
    if raw_clocks in (_LEGACY_OPEN_FIVE_CLOCKS, _LEGACY_SIX_CLOCKS, _LEGACY_V5_FOUR_CLOCKS):
        return [dict(s) for s in DEFAULT_T0_SLOTS]
    out: list = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or f"r{i + 1}").strip() or f"r{i + 1}"
        hm = str(item.get("hm") or "").strip()[:5]
        try:
            prefix_bars = int(item.get("prefix_bars") if item.get("prefix_bars") is not None else 0)
        except (TypeError, ValueError):
            prefix_bars = 0
        prefix_bars = max(0, min(prefix_bars, T0_LAST_LEG1_PREFIX_BARS))
        if not _slot_allows_leg1(hm=hm, prefix_bars=prefix_bars):
            continue
        try:
            ratio = float(item.get("ratio") if item.get("ratio") is not None else 0.20)
        except (TypeError, ValueError):
            ratio = 0.20
        ratio = max(0.05, min(ratio, 1.0))
        out.append({"id": sid, "hm": hm, "prefix_bars": prefix_bars, "ratio": ratio})
    return out or [dict(s) for s in DEFAULT_T0_SLOTS]


def t0_slots_enabled(cfg: Optional[dict]) -> bool:
    """v6：恒走多轮收盘带宽壳；``t0_slots_max_rounds=0`` 仅禁开 leg1。"""
    _ = cfg
    return True


def _migrate_y_tw_enter(cfg: dict, override_keys: Optional[set] = None) -> None:
    """对齐 y_tw_enter / y_τw_enter 与 y_tw_strong / y_τw_strong 别名。"""
    _ = override_keys
    if cfg.get("y_tw_enter") in (None, "") and cfg.get("y_τw_enter") not in (None, ""):
        cfg["y_tw_enter"] = cfg.get("y_τw_enter")
    cfg["y_τw_enter"] = cfg.get("y_tw_enter")
    if cfg.get("y_tw_strong") in (None, "") and cfg.get("y_τw_strong") not in (None, ""):
        cfg["y_tw_strong"] = cfg.get("y_τw_strong")
    if cfg.get("y_τw_strong") in (None, "") and cfg.get("y_tw_strong") not in (None, ""):
        cfg["y_τw_strong"] = cfg.get("y_tw_strong")


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
    _migrate_y_tw_enter(cfg, keys)


def _sync_dual_y_gate_legacy_aliases(cfg: dict) -> None:
    """写出旧键别名，避免未升级的 overlay / 外部脚本读不到。"""
    if cfg.get("y_trade_enter") is not None:
        cfg["y_trade_floor"] = cfg["y_trade_enter"]


def load_t0_rules(override: Optional[dict] = None) -> Dict[str, Any]:
    cfg = dict(DEFAULT_T0_RULES)
    override_keys = set(override.keys()) if override else set()
    if override:
        ov = dict(override)
        override_keys = set(ov.keys())
        for k, v in ov.items():
            if v is not None:
                cfg[k] = v
    migrate_y_path_keys_to_hl(cfg, override_keys)
    drop_dead_t0_keys(cfg)
    cfg["t0_ratio"] = max(0.05, min(float(cfg.get("t0_ratio") or 1.0), 1.0))
    # 纸面/回测生效路径在 core.execution.resolve 再强制为 1.0
    cfg["lot_size"] = max(1, int(cfg.get("lot_size") or 100))
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
        cfg.get("must_cover_same_day_sell_then_buy"), True
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
    cfg["direction"] = normalize_t0_direction(cfg.get("direction"), default="dual_y")
    # override_keys 已在函数开头定义
    _migrate_dual_y_gate_keys(cfg, override_keys)
    for yk, lo, hi, default in (
        ("y_trade_enter", 0.01, 5.0, 0.01),
        ("y_on_risk", 0.01, 10.0, 0.01),
        ("y_on_allow", 0.01, 10.0, 0.01),
        ("y_tw_enter", 0.0, 5.0, 2.0),
        ("y_τw_enter", 0.0, 5.0, 2.0),
        ("y_tw_strong", 0.0, 5.0, 2.0),
        ("y_τw_strong", 0.0, 5.0, 2.0),
        ("y_tw_vote_margin", 0.0, 20.0, 5.0),
        ("y_τw_vote_margin", 0.0, 20.0, 5.0),
        ("y_tw_midpoint", 1.0, 99.0, 47.0),
        ("y_τw_midpoint", 1.0, 99.0, 47.0),
        ("t0_y_oc_target_scale", 0.0, 100.0, 10.0),
        ("t0_y_oc_l", -20.0, 20.0, -3.0),
        ("t0_y_oc_u", -20.0, 20.0, 3.0),
        ("t0_close_band_delta_pct", 0.0, 10.0, 0.5),
        ("fusion_w_τc", 0.0, 1.0, 0.5),
        ("fusion_w_tc", 0.0, 1.0, 0.5),
        ("residual_w_oc", 0.0, 1.0, 0.5),
    ):
        try:
            raw = cfg.get(yk)
            val = float(default if raw is None or raw == "" else raw)
        except (TypeError, ValueError):
            val = float(default)
        cfg[yk] = max(lo, min(val, hi))
    mode = str(cfg.get("residual_w_mode") or "fixed").strip().lower()
    cfg["residual_w_mode"] = (
        "inv_var"
        if mode in ("inv_var", "inverse_var", "inverse_variance", "oos", "variance")
        else "fixed"
    )
    def _w_or_default(raw: Any, default: float = 0.5) -> float:
        try:
            if raw is None or raw == "":
                return float(default)
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    if "fusion_w_τc" in override_keys and cfg.get("fusion_w_τc") is not None:
        cfg["fusion_w_tc"] = float(cfg["fusion_w_τc"])
    elif "fusion_w_tc" in override_keys:
        cfg["fusion_w_τc"] = _w_or_default(cfg.get("fusion_w_tc"))
    else:
        cfg["fusion_w_τc"] = _w_or_default(cfg.get("fusion_w_tc"), _w_or_default(cfg.get("fusion_w_τc")))
        cfg["fusion_w_tc"] = float(cfg["fusion_w_τc"])
    from core.t0.score_policy import normalize_y_trade_enter

    cfg["y_trade_enter"] = normalize_y_trade_enter(cfg.get("y_trade_enter"))
    _sync_dual_y_gate_legacy_aliases(cfg)
    if cfg.get("y_tw_enter") in (None, "") and cfg.get("y_τw_enter") not in (None, ""):
        cfg["y_tw_enter"] = cfg.get("y_τw_enter")
    cfg["y_τw_enter"] = cfg.get("y_tw_enter")
    if cfg.get("y_tw_midpoint") in (None, "") and cfg.get("y_τw_midpoint") not in (None, ""):
        cfg["y_tw_midpoint"] = cfg.get("y_τw_midpoint")
    cfg["y_τw_midpoint"] = cfg.get("y_tw_midpoint")
    try:
        lo = float(cfg.get("t0_y_oc_l") if cfg.get("t0_y_oc_l") not in (None, "") else -3.0)
        hi = float(cfg.get("t0_y_oc_u") if cfg.get("t0_y_oc_u") not in (None, "") else 3.0)
    except (TypeError, ValueError):
        lo, hi = -3.0, 3.0
    if lo > hi:
        cfg["t0_y_oc_l"], cfg["t0_y_oc_u"] = hi, lo
    shared_enter = float(cfg.get("y_tw_enter") or 0.0)
    if cfg.get("y_tw_strong") in (None, "") and cfg.get("y_τw_strong") not in (None, ""):
        cfg["y_tw_strong"] = cfg.get("y_τw_strong")
    strong_explicit = any(
        k in override_keys and cfg.get(k) not in (None, "")
        for k in ("y_tw_strong", "y_τw_strong")
    )
    if not strong_explicit:
        cfg["y_tw_strong"] = shared_enter
    else:
        try:
            strong = float(
                cfg.get("y_tw_strong") if cfg.get("y_tw_strong") not in (None, "") else 2.0
            )
        except (TypeError, ValueError):
            strong = 2.0
        cfg["y_tw_strong"] = max(0.0, min(strong, 5.0))
    cfg["y_τw_strong"] = cfg["y_tw_strong"]
    if (
        "y_tw_vote_margin" not in override_keys
        or cfg.get("y_tw_vote_margin") in (None, "")
    ) and cfg.get("y_τw_vote_margin") not in (None, ""):
        cfg["y_tw_vote_margin"] = cfg.get("y_τw_vote_margin")
    cfg["y_τw_vote_margin"] = cfg.get("y_tw_vote_margin")
    from core.research.horizon_prob import migrate_horizon_gate_cfg

    migrate_horizon_gate_cfg(cfg)
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
    # 主仓 breakglass 回注的有效 τ 门槛（可选）
    try:
        eff = cfg.get("y_tau_enter_effective")
        if eff is not None and eff != "":
            cfg["y_tau_enter_effective"] = max(0.0, min(float(eff), 100.0))
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
        ("t0_pm_degrade_buy_then_sell", "13:00"),
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
        arm_bars = int(1 if raw_arm is None or raw_arm == "" else raw_arm)
    except (TypeError, ValueError):
        arm_bars = 1
    cfg["t0_stop_arm_bars"] = max(0, min(arm_bars, 48))
    cfg["t0_stop_on_close"] = True

    def _norm_lock_win(raw: Any, default: float) -> float:
        try:
            if raw is None or raw == "":
                return default
            return max(0.0, min(float(raw), 20.0))
        except (TypeError, ValueError):
            return default

    cfg["t0_lock_win_pct_buy_then_sell"] = _norm_lock_win(
        cfg.get("t0_lock_win_pct_buy_then_sell"), 2.0
    )
    cfg["t0_lock_win_pct_sell_then_buy"] = _norm_lock_win(
        cfg.get("t0_lock_win_pct_sell_then_buy"), 2.0
    )
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
    cfg["t0_slots_enabled"] = True
    raw_max_rounds = cfg.get("t0_slots_max_rounds")
    if raw_max_rounds is None or raw_max_rounds == "":
        cfg["t0_slots_max_rounds"] = 5
    else:
        try:
            cfg["t0_slots_max_rounds"] = max(0, min(int(raw_max_rounds), 16))
        except (TypeError, ValueError):
            cfg["t0_slots_max_rounds"] = 5

    cfg["t0_price_space_gate"] = bool(cfg.get("t0_price_space_gate", True))
    try:
        raw_ps = cfg.get("t0_price_space_max_dev_pct")
        ps_dev = 0.0 if raw_ps is None or raw_ps == "" else float(raw_ps)
    except (TypeError, ValueError):
        ps_dev = 0.0
    cfg["t0_price_space_max_dev_pct"] = max(0.0, min(ps_dev, 5.0))
    try:
        raw_prev = cfg.get("t0_price_space_prev_dev_pct")
        ps_prev = 5.0 if raw_prev is None or raw_prev == "" else float(raw_prev)
    except (TypeError, ValueError):
        ps_prev = 5.0
    cfg["t0_price_space_prev_dev_pct"] = max(0.0, min(ps_prev, 5.0))
    try:
        round_ratio = float(cfg.get("t0_round_ratio") or 0.4)
    except (TypeError, ValueError):
        round_ratio = 0.4
    cfg["t0_round_ratio"] = max(0.05, min(round_ratio, 1.0))
    try:
        max_pos = float(cfg.get("t0_max_position_pct") or 1.0)
    except (TypeError, ValueError):
        max_pos = 1.0
    cfg["t0_max_position_pct"] = max(0.05, min(max_pos, 1.0))

    def _norm_ytw_shares(raw: Any, default: int) -> int:
        if raw is None or raw == "":
            return int(default)
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            v = int(default)
        if v <= 0:
            return 0
        v = max(100, min(v, 100000))
        return (v // 100) * 100

    if cfg.get("y_tw_enter_shares") in (None, "") and cfg.get("y_τw_enter_shares") not in (
        None,
        "",
    ):
        cfg["y_tw_enter_shares"] = cfg.get("y_τw_enter_shares")
    cfg["y_tw_enter_shares"] = _norm_ytw_shares(cfg.get("y_tw_enter_shares"), 2000)
    cfg["y_τw_enter_shares"] = cfg["y_tw_enter_shares"]
    if cfg.get("y_tw_strong_shares") in (None, "") and cfg.get("y_τw_strong_shares") not in (
        None,
        "",
    ):
        cfg["y_tw_strong_shares"] = cfg.get("y_τw_strong_shares")
    strong_sh = _norm_ytw_shares(cfg.get("y_tw_strong_shares"), 4000)
    if cfg["y_tw_enter_shares"] > 0:
        strong_sh = max(cfg["y_tw_enter_shares"], strong_sh)
    cfg["y_tw_strong_shares"] = strong_sh
    cfg["y_τw_strong_shares"] = cfg["y_tw_strong_shares"]
    if isinstance(cfg.get("t0_slots"), (list, tuple)) and len(cfg.get("t0_slots") or []) == 0:
        cfg["t0_slots"] = []
    else:
        cfg["t0_slots"] = normalize_t0_slots(cfg.get("t0_slots"))
    return cfg


def t0_backtest_virtual_shares(cfg: Optional[dict] = None, fallback: Any = 1000) -> int:
    """回测虚拟底仓：配了入场/强股数时 = 最大轮数 × 强股数，否则用 fallback。

    每轮绝对股数若等于底仓，满仓上限 100% 会在第 1 轮用尽，最大轮数无法铺开。
    """
    cfg_d = cfg if isinstance(cfg, dict) else {}

    def _lot(raw: Any) -> int:
        try:
            v = int(float(0 if raw in (None, "") else raw))
        except (TypeError, ValueError):
            v = 0
        if v <= 0:
            return 0
        v = max(100, min(v, 100000))
        return (v // 100) * 100

    enter = _lot(
        cfg_d.get("y_tw_enter_shares")
        if cfg_d.get("y_tw_enter_shares") not in (None, "")
        else cfg_d.get("y_τw_enter_shares")
    )
    strong = _lot(
        cfg_d.get("y_tw_strong_shares")
        if cfg_d.get("y_tw_strong_shares") not in (None, "")
        else cfg_d.get("y_τw_strong_shares")
    )
    if enter <= 0 and strong <= 0:
        try:
            fb = int(float(fallback if fallback not in (None, "") else 1000))
        except (TypeError, ValueError):
            fb = 1000
        return max(100, min(fb, 100000))
    per = max(enter, strong)
    raw_rounds = cfg_d.get("t0_slots_max_rounds")
    try:
        rounds = int(5 if raw_rounds in (None, "") else raw_rounds)
    except (TypeError, ValueError):
        rounds = 5
    rounds = max(1, min(rounds, 16))
    return max(100, min(per * rounds, 100000))


def apply_side_exec_params(cfg: dict, direction: Optional[str]) -> Dict[str, Any]:
    """按正/反 T 覆盖执行参数：成交模式、回补、午后闸等（第二腿仅 τ 出场价闸）。"""
    out = dict(cfg or {})
    if direction not in {"sell_then_buy", "buy_then_sell"}:
        return out
    suf = "_buy_then_sell" if direction == "buy_then_sell" else "_sell_then_buy"
    fm = out.get(f"fill_mode{suf}")
    if fm is not None and str(fm).strip():
        fs = str(fm).strip().lower()
        if fs in {"trigger", "mid", "optimistic"}:
            out["fill_mode"] = fs
    mc = out.get(f"must_cover_same_day{suf}")
    if mc is not None:
        default_mc = True
        out["must_cover_same_day"] = coerce_cfg_bool(mc, default_mc)
    pm = out.get(f"t0_pm_degrade{suf}")
    if pm is not None:
        s = str(pm).strip()
        out["t0_pm_degrade"] = "" if s in {"", "0", "off", "none", "-"} else s
    elif "t0_pm_degrade" not in out or out.get("t0_pm_degrade") is None:
        # 缺侧向键时回落默认：正T/反T 均为 13:00
        out["t0_pm_degrade"] = "13:00"
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


# 盘中 Worker：与 5m K 线对齐的轮询与分钟缓存 TTL
T0_INTRADAY_TICK_SEC = 300.0
T0_INTRADAY_MINUTE_CACHE_HOURS = 5.0 / 60.0
T0_INTRADAY_MINUTE_LOOKBACK_DAYS = 5
