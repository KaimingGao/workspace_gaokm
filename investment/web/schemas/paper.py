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
        description="观察池开/加上限（rank_lots；省略则用观察池容量，不再用 max_positions）",
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
        description="调仓策略 ID（short_conservative）；省略则沿用 paper.strategy_id",
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
    lookback: int = Field(default=10, ge=10, le=500)
    initial_shares: float = Field(default=1000, ge=100, le=100000)
    initial_cash: float = Field(
        default=200_000,
        ge=10_000,
        le=1e8,
        description="做 T 回测本金（元）；累计收益比例分母",
    )
    t0_ratio: float = Field(default=1.0, ge=0.05, le=1.0)
    must_cover_same_day: bool = True
    must_cover_same_day_sell_then_buy: Optional[bool] = Field(default=None)
    must_cover_same_day_buy_then_sell: Optional[bool] = Field(default=None)
    fill_mode: Optional[str] = Field(default=None, max_length=16)
    fill_mode_sell_then_buy: Optional[str] = Field(default=None, max_length=16)
    fill_mode_buy_then_sell: Optional[str] = Field(default=None, max_length=16)
    direction: Optional[str] = Field(default=None, max_length=16)
    path_mode: Optional[str] = Field(default=None, max_length=16)
    use_minute: bool = True
    y_trade_enter: Optional[float] = Field(
        default=None, ge=0.0, le=5.0,         description="dual_y：|y_trade|入场下限（收益百分点）"
    )
    y_trade_floor: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="已弃用：别名 y_trade_enter"
    )
    y_tau_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="dual_y：τ 入场兜底（侧向未设时正/反共用）；范围 0–100%",
    )
    y_tau_enter_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="dual_y：反T（y_τ<0）入场 |y_τ| 门槛；范围 0–100%",
    )
    y_tau_enter_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="dual_y：正T（y_τ>0）入场 |y_τ| 门槛；范围 0–100%",
    )
    y_enter_enabled: Optional[bool] = Field(
        default=None,
        description="门槛1 启用；关则本档不参与入场 OR；默认开",
    )
    y_enter_alt_enabled: Optional[bool] = Field(
        default=None,
        description="门槛2 启用；关则本档不参与入场 OR；默认开",
    )
    fusion_w_τc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="做 T residual 融合：ŷ_τc 权重；与 residual_w_oc 归一化",
    )
    fusion_w_tc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="fusion_w_τc 的 ASCII 别名",
    )
    residual_w_oc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="做 T residual 融合：remaining(ŷ_oc) 权重",
    )
    residual_w_mode: Optional[str] = Field(
        default=None,
        max_length=24,
        description="residual 融合：fixed=固定权（默认）| inv_var=OOS 逆方差",
    )
    y_tau_enter_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="已弃用：并入 y_tau_enter（load 时取 max）",
    )
    y_on_allow: Optional[float] = Field(
        default=None, ge=0.01, le=10.0, description="dual_y：|y_on|隔夜放行门槛（收益百分点）"
    )
    y_on_risk: Optional[float] = Field(default=None, ge=0.01, le=10.0)
    t0_y_oc_target_scale: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="C_τ：y_oc 放大，clip(ŷ_oc×scale, y_oc_l, y_oc_u) 的倍数，默认 10",
    )
    t0_y_oc_l: Optional[float] = Field(
        default=None,
        ge=-20.0,
        le=20.0,
        description="C_τ clip 下界（百分点，默认 −3）",
    )
    t0_y_oc_u: Optional[float] = Field(
        default=None,
        ge=-20.0,
        le=20.0,
        description="C_τ clip 上界（百分点，默认 +3）",
    )
    y_hl_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="HL 入场%：|y_hl| 低于此值横盘跳过（默认 0；0=关幅度；范围 0–100%）",
    )
    y_path_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_hl_enter 旧键",
    )
    y_hl_enter_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="dual_y：反T HL 入场门槛；范围 0–100%",
    )
    y_path_enter_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_hl_enter_sell_then_buy 旧键",
    )
    y_hl_enter_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="dual_y：正T HL 入场门槛；范围 0–100%",
    )
    y_path_enter_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_hl_enter_buy_then_sell 旧键",
    )
    y_hl_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="HL强%：|y_hl| 超此值须与 y_τ 同号，异号跳过（默认 5）",
    )
    y_path_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=5.0,
        description="y_hl_strong 旧键",
    )
    y_t30_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ŷ_τ30 旁路强%：0=任意有符号须同号；1=关",
    )
    y_τ30_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_t30_strong 的 Unicode 别名",
    )
    y_tw_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=3.0,
        description="ŷ_τw 旁路：0=任意有符号须同号；3=关",
    )
    y_τw_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=3.0,
        description="y_tw_strong 的 Unicode 别名",
    )
    y_t30_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛1 |ŷ_τ30| 入场下限（百分点）；0=关；缺 ŷ_τ30 不拦",
    )
    y_τ30_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_t30_enter 的 Unicode 别名",
    )
    y_t30_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 |ŷ_τ30| 入场下限（百分点）；0=关；缺 ŷ_τ30 不拦",
    )
    y_τ30_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_t30_enter_alt 的 Unicode 别名",
    )
    y_t60_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ŷ_τ60 旁路强%：0=任意有符号须同号；1=关",
    )
    y_τ60_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_t60_strong 的 Unicode 别名",
    )
    y_t60_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛1 |ŷ_τ60| 入场下限（百分点）；0=关；缺 ŷ_τ60 不拦",
    )
    y_τ60_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_t60_enter 的 Unicode 别名",
    )
    y_t60_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 |ŷ_τ60| 入场下限（百分点）；0=关；缺 ŷ_τ60 不拦",
    )
    y_τ60_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_t60_enter_alt 的 Unicode 别名",
    )
    y_t90_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="ŷ_τ90 旁路强%：0=任意有符号须同号；1=关",
    )
    y_τ90_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_t90_strong 的 Unicode 别名",
    )
    y_t90_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛1 |ŷ_τ90| 入场下限（百分点）；0=关；缺 ŷ_τ90 不拦",
    )
    y_τ90_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_t90_enter 的 Unicode 别名",
    )
    y_t90_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 |ŷ_τ90| 入场下限（百分点）；0=关；缺 ŷ_τ90 不拦",
    )
    y_τ90_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_t90_enter_alt 的 Unicode 别名",
    )
    y_tau_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 |y_τ| 入场下限（百分点）；默认 0；范围 0–100%；0=关",
    )
    y_hl_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="门槛2 |y_hl| 入场下限（百分点）；默认 0；范围 0–100%；0=关幅度",
    )
    y_path_enter_alt: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="y_hl_enter_alt 旧键",
    )
    y_hl_required: Optional[bool] = None
    y_path_required: Optional[bool] = None
    t0_close_band_delta_pct: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    t0_price_space_gate: Optional[bool] = Field(
        default=None, description="日分价空间门禁：|O_d/O_m−1| 超阈跳过"
    )
    t0_price_space_max_dev_pct: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="开盘差%：|日开/分开−1|×100 上限（默认 5）"
    )
    t0_price_space_prev_dev_pct: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="昨收差%：|日昨/分昨−1|×100 上限（默认 5）"
    )
    t0_round_ratio: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    t0_max_position_pct: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    t0_slots_max_rounds: Optional[int] = Field(default=None, ge=0, le=16)
    t0_slots_enabled: Optional[bool] = Field(
        default=None, description="v6 恒为多轮收盘带宽壳；仅兼容旧补丁"
    )
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
        default=True,
        description="正/反T止损固定收盘破线确认；表单已去掉，入参忽略",
    )
    t0_giveback_pct_buy_then_sell: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=20.0,
        description="正T自高回吐%（相对入场后峰值）；0=关；须先涨过激活%",
    )
    t0_giveback_pct_sell_then_buy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=20.0,
        description="反T自低回吐%（相对入场后谷值）；0=关；须先跌过激活%",
    )
    t0_giveback_arm_pct: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=20.0,
        description="回吐激活%：正T自买价上涨 / 反T自卖价下跌达到此值后才启用回吐",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/t0-backtest",
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
        description="调仓时机；含 path_matrix.rank_lots（09:30 开盘 200/500 股）",
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
    must_cover_same_day: Optional[bool] = None
    must_cover_same_day_sell_then_buy: Optional[bool] = None
    must_cover_same_day_buy_then_sell: Optional[bool] = None
    y_trade_enter: Optional[float] = None
    y_trade_floor: Optional[float] = None
    y_tau_enter: Optional[float] = None
    y_tau_enter_strong: Optional[float] = None
    y_tau_enter_sell_then_buy: Optional[float] = None
    y_tau_enter_buy_then_sell: Optional[float] = None
    y_enter_enabled: Optional[bool] = None
    y_enter_alt_enabled: Optional[bool] = None
    fusion_w_τc: Optional[float] = None
    fusion_w_tc: Optional[float] = None
    residual_w_oc: Optional[float] = None
    residual_w_mode: Optional[str] = None
    y_on_allow: Optional[float] = None
    y_on_risk: Optional[float] = None
    t0_y_oc_target_scale: Optional[float] = None
    t0_y_oc_l: Optional[float] = None
    t0_y_oc_u: Optional[float] = None
    y_hl_enter: Optional[float] = None
    y_path_enter: Optional[float] = None
    y_hl_enter_sell_then_buy: Optional[float] = None
    y_path_enter_sell_then_buy: Optional[float] = None
    y_hl_enter_buy_then_sell: Optional[float] = None
    y_path_enter_buy_then_sell: Optional[float] = None
    y_hl_strong: Optional[float] = None
    y_path_strong: Optional[float] = None
    y_t30_strong: Optional[float] = None
    y_τ30_strong: Optional[float] = None
    y_tw_strong: Optional[float] = None
    y_τw_strong: Optional[float] = None
    y_t30_enter: Optional[float] = None
    y_τ30_enter: Optional[float] = None
    y_t30_enter_alt: Optional[float] = None
    y_τ30_enter_alt: Optional[float] = None
    y_t60_strong: Optional[float] = None
    y_τ60_strong: Optional[float] = None
    y_t60_enter: Optional[float] = None
    y_τ60_enter: Optional[float] = None
    y_t60_enter_alt: Optional[float] = None
    y_τ60_enter_alt: Optional[float] = None
    y_t90_strong: Optional[float] = None
    y_τ90_strong: Optional[float] = None
    y_t90_enter: Optional[float] = None
    y_τ90_enter: Optional[float] = None
    y_t90_enter_alt: Optional[float] = None
    y_τ90_enter_alt: Optional[float] = None
    y_tau_enter_alt: Optional[float] = None
    y_hl_enter_alt: Optional[float] = None
    y_path_enter_alt: Optional[float] = None
    y_hl_required: Optional[bool] = None
    y_path_required: Optional[bool] = None
    t0_close_band_delta_pct: Optional[float] = None
    t0_price_space_gate: Optional[bool] = None
    t0_price_space_max_dev_pct: Optional[float] = None
    t0_price_space_prev_dev_pct: Optional[float] = None
    t0_round_ratio: Optional[float] = None
    t0_max_position_pct: Optional[float] = None
    t0_slots_max_rounds: Optional[int] = None
    t0_slots_enabled: Optional[bool] = None
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
    t0_giveback_pct_buy_then_sell: Optional[float] = None
    t0_giveback_pct_sell_then_buy: Optional[float] = None
    t0_giveback_arm_pct: Optional[float] = None

