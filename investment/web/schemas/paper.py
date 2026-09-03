"""纸面交易 / 调仓 / 做T / 执行参数模型。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

class PaperRunRequest(BaseModel):
    simulate_buy: bool = False
    background: bool = False
    strategy: str = "short_conservative"
    dry_run: bool = False


class PaperRebalanceRequest(BaseModel):
    top_k: Optional[int] = Field(
        default=None,
        ge=1,
        le=80,
        description="目标持仓只数；分池模式省略则按合并簿长度（上限 80）",
    )
    limit: int = Field(
        default=40,
        ge=1,
        le=80,
        description="横截面候选上限；分池模式对齐 max_names（≤80）",
    )
    cluster_mode: bool = Field(
        default=False,
        description="已停用：分池调仓不再支持；请用 matrix_mode",
    )
    matrix_mode: bool = Field(
        default=True,
        description="观察池实时算分 + path_matrix 预演/落账（默认且唯一调仓路径）",
    )
    dry_run: bool = Field(
        default=False,
        description="true=仅预演不写 paper.json（交易执行页）",
    )
    offline_only: bool = Field(
        default=True,
        description="true=日线/分钟/指数只用本地仓；UI 恒传 true（可拉远端先走增量补齐写仓）",
    )
    strategy: Optional[str] = Field(
        default=None,
        description="调仓策略 ID（short / short_conservative）；省略则沿用 paper.strategy_id",
    )


class PaperBuyRequest(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=16)
    amount: Optional[float] = Field(default=None, gt=0)
    shares: Optional[float] = Field(default=None, gt=0)


class PaperDepositRequest(BaseModel):
    amount: float = Field(default=1_000_000, gt=0, le=100_000_000)


class PaperCostModelRequest(BaseModel):
    """成交成本：simple_cn=佣金万2.5+卖出印花税万5（默认）；zero=教学/调试零成本。"""

    cost_model: str = Field(default="simple_cn", pattern="^(zero|simple_cn)$")


class PaperSellRequest(BaseModel):
    codes: Optional[list] = None
    stock_code: Optional[str] = Field(default=None, max_length=16)
    shares: Optional[float] = Field(default=None, gt=0)


class T0BacktestRequest(BaseModel):
    code: Optional[str] = None
    codes: Optional[list] = None
    from_paper: bool = True
    lookback: int = Field(default=20, ge=10, le=500)
    initial_shares: float = Field(default=1000, ge=100, le=100000)
    t0_ratio: float = Field(default=1.0, ge=0.05, le=1.0)
    must_cover_same_day: bool = True
    must_cover_same_day_sell_then_buy: Optional[bool] = Field(default=None)
    must_cover_same_day_buy_then_sell: Optional[bool] = Field(default=None)
    fill_mode: Optional[str] = Field(default=None, max_length=16)
    fill_mode_sell_then_buy: Optional[str] = Field(default=None, max_length=16)
    fill_mode_buy_then_sell: Optional[str] = Field(default=None, max_length=16)
    direction: Optional[str] = Field(default=None, max_length=16)
    path_mode: Optional[str] = Field(default=None, max_length=16)
    dir_enter: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=1.0,
        description="已废弃于 dual_y；仅旧 signal 选向 |score| 门槛（约 ±1），默认 0.35",
    )
    min_range_pct: Optional[float] = Field(
        default=None, ge=0.0, le=30.0, description="已下线：振幅下限%（0=关）"
    )
    min_range_pct_sell_then_buy: Optional[float] = Field(default=None, ge=0.0, le=30.0)
    min_range_pct_buy_then_sell: Optional[float] = Field(default=None, ge=0.0, le=30.0)
    use_minute: bool = True
    compare_daily: bool = False  # 已废弃：日线模拟已删除
    use_atr: Optional[bool] = None
    y_trade_enter: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="dual_y：|y_trade|入场下限（收益百分点）"
    )
    y_trade_strong: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=5.0,
        description="dual_y：|y_trade|>此值须与 y_τ 同号（默认 0.2%）",
    )
    y_trade_floor: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="已弃用：别名 y_trade_enter"
    )
    y_tau_enter: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="dual_y：τ 入场兜底（侧向未设时正/反共用）",
    )
    y_tau_enter_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="dual_y：反T（y_τ<0）入场 |y_τ| 门槛",
    )
    y_tau_enter_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="dual_y：正T（y_τ>0）入场 |y_τ| 门槛",
    )
    y_tau_enter_strong: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="已弃用：并入 y_tau_enter（load 时取 max）",
    )
    y_ratio_cut: Optional[float] = Field(
        default=None,
        ge=0.2,
        le=1.0,
        description="目标价弱信号下限倍数（相对基准触发，默认 0.6）",
    )
    y_ratio_boost_cap: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=2.0,
        description="目标价强信号上限倍数（相对基准触发，默认 2.0）",
    )
    y_eod_prior: Optional[float] = Field(
        default=None, ge=0.01, le=5.0, description="dual_y：|y_eod|同向略抬目标价信心门槛（收益百分点）"
    )
    y_eod_enter: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="dual_y：可得 ŷ_eod 时 |y_eod| 准入下限（收益百分点，默认 0.01）",
    )
    y_eod_strong: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=5.0,
        description="dual_y：|y_eod|>此值须与 y_τ 同号（默认 0.2%）",
    )
    y_eod_tau_sign_gate: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=5.0,
        description="已弃用：别名 y_eod_strong",
    )
    y_trade_tau_sign_gate: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=5.0,
        description="已弃用：别名 y_trade_strong",
    )
    y_on_allow: Optional[float] = Field(
        default=None, ge=0.01, le=10.0, description="dual_y：|y_on|隔夜放行门槛（收益百分点）"
    )
    y_on_risk: Optional[float] = Field(default=None, ge=0.01, le=10.0)
    y_block_tau_nowcast_sign: Optional[bool] = None
    y_tau_leg1_prior: Optional[bool] = Field(
        default=None,
        description="已弃用镜像：mode≠off；请用 y_tau_leg1_prior_mode",
    )
    y_tau_leg1_prior_mode: Optional[str] = Field(
        default=None,
        max_length=16,
        description="v6 日线先验：score=破带超额−k×逆势|y_τ|；skip=硬跳过；off=关",
    )
    y_tau_leg1_prior_risk: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="score 模式风险系数 k（默认 1）",
    )
    y_tau_leg1_prior_shift_scale: Optional[float] = Field(
        default=None,
        ge=0.1,
        le=0.9,
        description="score：|s|≤α·δ，α∈[0.1, 0.9]（默认 0.9；保证 upper=δ+s>0>lower）",
    )
    y_nc_enter: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=10.0,
        description="dual_y：|nc| 入场下限（收益百分点，默认 0.01）",
    )
    y_nc_strong: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=10.0,
        description="dual_y：|nc|>此值须与 y_τ 同号（默认 0.2%）",
    )
    y_nowcast_enter: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=10.0,
        description="已弃用：别名 y_nc_strong",
    )
    y_tau_map: Optional[str] = Field(
        default=None, max_length=24, description="scalp|trend|fixed_sell_then_buy|fixed_buy_then_sell"
    )
    y_use_path: Optional[bool] = None
    y_path_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="path 入场%：|y_path| 低于此值横盘跳过（默认 0.01；0=关幅度）",
    )
    y_path_enter_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="dual_y：反T path 入场门槛",
    )
    y_path_enter_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.01,
        le=5.0,
        description="dual_y：正T path 入场门槛",
    )
    y_path_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="path强%：|y_path| 超此值须与 y_τ 同号，异号跳过（默认 0.2）",
    )
    y_path_required: Optional[bool] = None
    y_gap_tier_mode: Optional[str] = Field(default=None, max_length=24)
    y_gap_tier_pct: Optional[float] = Field(default=None, ge=0.3, le=8.0)
    y_nowcast_oc_gate: Optional[bool] = None
    t0_close_band_delta_pct: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    t0_price_space_gate: Optional[bool] = Field(
        default=None, description="日分价空间门禁：|O_d/O_m−1| 超阈跳过"
    )
    t0_price_space_max_dev_pct: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="开盘差%：|日开/分开−1|×100 上限（默认 0.25）"
    )
    t0_price_space_prev_dev_pct: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="昨收差%：|日昨/分昨−1|×100 上限（默认 0.25）"
    )
    t0_round_ratio: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    t0_max_position_pct: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    t0_slots_max_rounds: Optional[int] = Field(default=None, ge=0, le=16)
    t0_slots_enabled: Optional[bool] = Field(
        default=None, description="v6 恒为多轮收盘带宽壳；仅兼容旧补丁"
    )
    y_tau_require_for_leg1: Optional[bool] = Field(
        default=None, description="缺 ŷ_τ 时禁止开第一腿"
    )
    y_tau_entry_price_skip: Optional[bool] = Field(
        default=None,
        description="legacy：等同 y_tau_entry_price_skip_buy_then_sell",
    )
    y_tau_entry_price_mult: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=5.0,
        description="legacy：等同 y_tau_entry_price_mult_buy_then_sell",
    )
    y_tau_entry_price_skip_buy_then_sell: Optional[bool] = Field(
        default=None,
        description="正T确认根：买价<open×(1+ŷ_τ×裕度)",
    )
    y_tau_entry_price_mult_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=5.0,
        description="正T 买价裕度：bound=open×(1+ŷ_τ%×本值/100)",
    )
    y_tau_entry_price_skip_sell_then_buy: Optional[bool] = Field(
        default=None,
        description="反T确认根：卖价>open×(1+ŷ_τ×裕度)",
    )
    y_tau_entry_price_mult_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=5.0,
        description="反T 卖价裕度",
    )
    y_tau_entry_price_bias: Optional[float] = Field(
        default=None,
        ge=-50.0,
        le=50.0,
        description="legacy：等同 y_tau_entry_price_bias_buy_then_sell（价偏，百分点）",
    )
    y_tau_entry_price_move_min: Optional[float] = Field(
        default=None,
        ge=-100.0,
        le=100.0,
        description="legacy：入场 clamp 动幅下限（百分点）",
    )
    y_tau_entry_price_move_max: Optional[float] = Field(
        default=None,
        ge=-100.0,
        le=100.0,
        description="legacy：入场 clamp 动幅上限（百分点）",
    )
    y_tau_entry_price_bias_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=-50.0,
        le=50.0,
        description="正T入场价偏：bound=open×(1+(clamp(ŷ_τ×裕度,min,max)+价偏)/100)",
    )
    y_tau_entry_price_move_min_buy_then_sell: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_entry_price_move_max_buy_then_sell: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_entry_price_bias_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=-50.0,
        le=50.0,
        description="反T入场价偏（百分点，代数可正可负）",
    )
    y_tau_entry_price_move_min_sell_then_buy: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_entry_price_move_max_sell_then_buy: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_exit_price_skip: Optional[bool] = Field(
        default=None,
        description="legacy：等同 y_tau_exit_price_skip_buy_then_sell",
    )
    y_tau_exit_price_mult: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=5.0,
        description="legacy：等同 y_tau_exit_price_mult_buy_then_sell",
    )
    y_tau_exit_price_skip_buy_then_sell: Optional[bool] = Field(
        default=None,
        description="正T第二腿：卖价>open×(1+ŷ_τ×裕度)",
    )
    y_tau_exit_price_mult_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=5.0,
        description="正T第二腿卖价裕度",
    )
    y_tau_exit_price_skip_sell_then_buy: Optional[bool] = Field(
        default=None,
        description="反T第二腿：买价<open×(1+ŷ_τ×裕度)",
    )
    y_tau_exit_price_mult_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=5.0,
        description="反T第二腿买价裕度",
    )
    y_tau_exit_price_bias: Optional[float] = Field(
        default=None,
        ge=-50.0,
        le=50.0,
        description="legacy：等同 y_tau_exit_price_bias_buy_then_sell（价偏，百分点）",
    )
    y_tau_exit_price_move_min: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_exit_price_move_max: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_exit_price_bias_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=-50.0,
        le=50.0,
        description="正T出场价偏（百分点）",
    )
    y_tau_exit_price_move_min_buy_then_sell: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_exit_price_move_max_buy_then_sell: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_exit_price_bias_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=-50.0,
        le=50.0,
        description="反T出场价偏（百分点，代数可正可负）",
    )
    y_tau_exit_price_move_min_sell_then_buy: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    y_tau_exit_price_move_max_sell_then_buy: Optional[float] = Field(default=None, ge=-100.0, le=100.0)
    t0_pm_degrade: Optional[str] = Field(
        default=None,
        max_length=8,
        description="legacy：等同 t0_pm_degrade_buy_then_sell",
    )
    t0_pm_degrade_sell_then_buy: Optional[str] = Field(
        default=None,
        max_length=8,
        description="反T午后闸/中点追价起算 HH:MM；默认 14:00；空=关",
    )
    t0_pm_degrade_buy_then_sell: Optional[str] = Field(
        default=None,
        max_length=8,
        description="正T午后闸/中点追价起算 HH:MM；默认 14:00；空=关",
    )
    t0_pm_chase_interval_min: Optional[int] = Field(
        default=None,
        ge=1,
        le=60,
        description="legacy：等同 t0_pm_chase_interval_min_buy_then_sell",
    )
    t0_pm_chase_interval_min_sell_then_buy: Optional[int] = Field(
        default=None,
        ge=1,
        le=60,
        description="反T中点追价间隔（分钟），默认 5（买回上移）",
    )
    t0_pm_chase_interval_min_buy_then_sell: Optional[int] = Field(
        default=None,
        ge=1,
        le=60,
        description="正T中点追价间隔（分钟），默认 5（卖旧目标下移）",
    )
    t0_stop_pct_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=20.0,
        description="正T跌破止损%（相对第一腿买价）；0=关",
    )
    t0_stop_pct_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=20.0,
        description="反T涨破止损%（相对第一腿卖价）；0=关",
    )
    t0_stop_arm_bars: Optional[int] = Field(
        default=None,
        ge=0,
        le=48,
        description="正/反T止损：入场后跳过 N 根 5m 再启用",
    )
    t0_stop_on_close: Optional[bool] = Field(
        default=None,
        description="正/反T止损：true=收盘破线才触发",
    )


class PaperT0Request(BaseModel):
    dry_run: bool = True
    confirm: bool = False


class PaperT0AutoRequest(BaseModel):
    enabled: Optional[bool] = None
    schedule: Optional[str] = Field(
        default=None,
        description="cron 链式触发：after_close | with_paper_daily（Follow UI 已移除，默认 after_close）",
    )
    run_now: bool = Field(default=False, description="立即执行（落账）")
    dry_run: bool = Field(default=False, description="run_now 时仅预演")
    force: bool = Field(default=False, description="忽略 enabled 开关")


class PaperT0WorkerRequest(BaseModel):
    enabled: bool = Field(description="启动/停止 Web 内后台 worker")


class PaperT0DeleteRequest(BaseModel):
    """删除落账明细；默认冲正账本。"""

    stock_codes: List[str] = Field(
        ...,
        min_length=1,
        description="要删除的股票代码列表",
    )
    reverse_ledger: bool = Field(
        default=True,
        description="是否冲正对应 t0_* 成交腿（现金/批次）",
    )


class PaperT0IntradayClearRequest(BaseModel):
    """清理今日盯盘状态（不冲正账本）。"""

    stock_codes: Optional[List[str]] = Field(
        default=None,
        description="要清理的股票代码；与 clear_all 二选一",
    )
    clear_all: bool = Field(default=False, description="清空全部盯盘状态")
    force: bool = Field(
        default=False,
        description="连已落账腿（一腿/完成）一并清；默认跳过以防重复落账",
    )


class PaperExecutionPatchRequest(BaseModel):
    """账户级 Execution / 做T 覆盖。t0 与扁平字段二选一。"""

    t0: Optional[dict] = None
    coupling: Optional[dict] = None
    rebalance_timing: Optional[dict] = Field(
        default=None,
        description="调仓时机；含 path_matrix（早盘路径择时）",
    )
    lock: bool = True
    note: str = ""
    # 扁平快捷字段（写入 t0）
    enabled: Optional[bool] = None
    t0_ratio: Optional[float] = None
    fill_mode: Optional[str] = None
    fill_mode_sell_then_buy: Optional[str] = None
    fill_mode_buy_then_sell: Optional[str] = None
    direction: Optional[str] = None
    path_mode: Optional[str] = None
    dir_enter: Optional[float] = None
    min_range_pct: Optional[float] = None
    min_range_pct_sell_then_buy: Optional[float] = None
    min_range_pct_buy_then_sell: Optional[float] = None
    use_atr: Optional[bool] = None
    must_cover_same_day: Optional[bool] = None
    must_cover_same_day_sell_then_buy: Optional[bool] = None
    must_cover_same_day_buy_then_sell: Optional[bool] = None
    y_trade_enter: Optional[float] = None
    y_trade_strong: Optional[float] = None
    y_trade_floor: Optional[float] = None
    y_tau_enter: Optional[float] = None
    y_tau_enter_strong: Optional[float] = None
    y_tau_enter_sell_then_buy: Optional[float] = None
    y_tau_enter_buy_then_sell: Optional[float] = None
    y_ratio_cut: Optional[float] = None
    y_ratio_boost_cap: Optional[float] = None
    y_eod_prior: Optional[float] = None
    y_eod_enter: Optional[float] = None
    y_eod_strong: Optional[float] = None
    y_eod_tau_sign_gate: Optional[float] = None
    y_trade_tau_sign_gate: Optional[float] = None
    y_on_allow: Optional[float] = None
    y_on_risk: Optional[float] = None
    y_block_tau_nowcast_sign: Optional[bool] = None
    y_tau_leg1_prior: Optional[bool] = None
    y_tau_leg1_prior_mode: Optional[str] = None
    y_tau_leg1_prior_risk: Optional[float] = None
    y_tau_leg1_prior_shift_scale: Optional[float] = None
    y_nc_enter: Optional[float] = None
    y_nc_strong: Optional[float] = None
    y_nowcast_enter: Optional[float] = None
    y_tau_map: Optional[str] = Field(
        default=None, max_length=24, description="scalp|trend|fixed_sell_then_buy|fixed_buy_then_sell"
    )
    y_use_path: Optional[bool] = None
    y_path_enter: Optional[float] = None
    y_path_enter_sell_then_buy: Optional[float] = None
    y_path_enter_buy_then_sell: Optional[float] = None
    y_path_strong: Optional[float] = None
    y_path_required: Optional[bool] = None
    y_gap_tier_mode: Optional[str] = None
    y_gap_tier_pct: Optional[float] = None
    y_nowcast_oc_gate: Optional[bool] = None
    t0_close_band_delta_pct: Optional[float] = None
    t0_price_space_gate: Optional[bool] = None
    t0_price_space_max_dev_pct: Optional[float] = None
    t0_price_space_prev_dev_pct: Optional[float] = None
    t0_round_ratio: Optional[float] = None
    t0_max_position_pct: Optional[float] = None
    t0_slots_max_rounds: Optional[int] = None
    t0_slots_enabled: Optional[bool] = None
    y_tau_require_for_leg1: Optional[bool] = None
    y_tau_entry_price_skip: Optional[bool] = None
    y_tau_entry_price_mult: Optional[float] = None
    y_tau_entry_price_skip_buy_then_sell: Optional[bool] = None
    y_tau_entry_price_mult_buy_then_sell: Optional[float] = None
    y_tau_entry_price_skip_sell_then_buy: Optional[bool] = None
    y_tau_entry_price_mult_sell_then_buy: Optional[float] = None
    y_tau_entry_price_bias: Optional[float] = None
    y_tau_entry_price_move_min: Optional[float] = None
    y_tau_entry_price_move_max: Optional[float] = None
    y_tau_entry_price_bias_buy_then_sell: Optional[float] = None
    y_tau_entry_price_move_min_buy_then_sell: Optional[float] = None
    y_tau_entry_price_move_max_buy_then_sell: Optional[float] = None
    y_tau_entry_price_bias_sell_then_buy: Optional[float] = None
    y_tau_entry_price_move_min_sell_then_buy: Optional[float] = None
    y_tau_entry_price_move_max_sell_then_buy: Optional[float] = None
    y_tau_exit_price_skip: Optional[bool] = None
    y_tau_exit_price_mult: Optional[float] = None
    y_tau_exit_price_skip_buy_then_sell: Optional[bool] = None
    y_tau_exit_price_mult_buy_then_sell: Optional[float] = None
    y_tau_exit_price_skip_sell_then_buy: Optional[bool] = None
    y_tau_exit_price_mult_sell_then_buy: Optional[float] = None
    y_tau_exit_price_bias: Optional[float] = None
    y_tau_exit_price_move_min: Optional[float] = None
    y_tau_exit_price_move_max: Optional[float] = None
    y_tau_exit_price_bias_buy_then_sell: Optional[float] = None
    y_tau_exit_price_move_min_buy_then_sell: Optional[float] = None
    y_tau_exit_price_move_max_buy_then_sell: Optional[float] = None
    y_tau_exit_price_bias_sell_then_buy: Optional[float] = None
    y_tau_exit_price_move_min_sell_then_buy: Optional[float] = None
    y_tau_exit_price_move_max_sell_then_buy: Optional[float] = None
    y_score_source: Optional[str] = Field(
        default=None, max_length=24, description="compute|live_book|ledger"
    )
    t0_pm_degrade: Optional[str] = None
    t0_pm_degrade_sell_then_buy: Optional[str] = None
    t0_pm_degrade_buy_then_sell: Optional[str] = None
    t0_pm_chase_interval_min: Optional[int] = None
    t0_pm_chase_interval_min_sell_then_buy: Optional[int] = None
    t0_pm_chase_interval_min_buy_then_sell: Optional[int] = None
    t0_stop_pct_buy_then_sell: Optional[float] = None
    t0_stop_pct_sell_then_buy: Optional[float] = None
    t0_stop_arm_bars: Optional[int] = None
    t0_stop_on_close: Optional[bool] = None

