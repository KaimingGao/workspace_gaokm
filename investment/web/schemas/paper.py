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
        description="true=分池 live（组权打分→全局排序截断）；不写全局 weights",
    )
    dry_run: bool = Field(
        default=False,
        description="true=仅预演不写 paper.json（交易执行页）",
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
    lookback: int = Field(default=10, ge=10, le=500)
    initial_shares: float = Field(default=1000, ge=100, le=100000)
    t0_ratio: float = Field(default=1.0, ge=0.05, le=1.0)
    sell_trigger_pct: float = Field(default=1.0, ge=0.1, le=20)
    buy_trigger_pct: float = Field(default=1.0, ge=0.1, le=20)
    must_cover_same_day: bool = True
    fill_mode: Optional[str] = Field(default=None, max_length=16)
    direction: Optional[str] = Field(default=None, max_length=16)
    path_mode: Optional[str] = Field(default=None, max_length=16)
    dir_enter: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=1.0,
        description="已废弃于 dual_y；仅旧 signal 选向 |score| 门槛（约 ±1），默认 0.35",
    )
    min_range_pct: Optional[float] = Field(
        default=None, ge=0.2, le=30.0, description="振幅下限%；空=自动 max(卖+买)*0.6"
    )
    compare_optimistic: bool = True
    use_minute: bool = True
    compare_daily: bool = False  # 已废弃：日线模拟已删除
    use_atr: Optional[bool] = None
    y_trade_floor: Optional[float] = Field(
        default=None, ge=0.0, le=5.0, description="dual_y：|y_trade|下限（收益百分点，预期日波动幅度）"
    )
    y_tau_enter: Optional[float] = Field(
        default=None, ge=0.01, le=5.0, description="dual_y：|y_τ|入场门槛（收益百分点）"
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
    y_on_allow: Optional[float] = Field(
        default=None, ge=0.01, le=10.0, description="dual_y：|y_on|隔夜放行门槛（收益百分点）"
    )
    y_on_risk: Optional[float] = Field(default=None, ge=0.01, le=10.0)
    y_block_tau_nowcast_sign: Optional[bool] = None
    y_nowcast_enter: Optional[float] = Field(
        default=None,
        ge=0.05,
        le=10.0,
        description="dual_y：|nowcast|≥此值且与 y_τ 异号才拦（收益百分点）",
    )
    y_tau_map: Optional[str] = Field(
        default=None, max_length=24, description="scalp|trend|fixed_long|fixed_reverse"
    )
    y_use_path: Optional[bool] = None
    y_path_enter: Optional[float] = Field(
        default=None,
        ge=1.0,
        le=100.0,
        description="dual_y：|y_path|≥此值才入场（±100）；不足横盘跳过",
    )
    y_path_required: Optional[bool] = None
    y_gap_tier_mode: Optional[str] = Field(default=None, max_length=24)
    y_gap_tier_pct: Optional[float] = Field(default=None, ge=0.3, le=8.0)
    y_nowcast_oc_gate: Optional[bool] = None
    y_path_abandon_enabled: Optional[bool] = None
    y_path_abandon_bars: Optional[int] = Field(default=None, ge=2, le=48)
    t0_pm_degrade: Optional[str] = Field(
        default=None,
        max_length=8,
        description="中点追价起算 HH:MM：禁新开；已开未平则旧目标↔现价中点",
    )
    t0_pm_chase_interval_min: Optional[int] = Field(
        default=None,
        ge=1,
        le=60,
        description="中点追价间隔（分钟），默认 10",
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
    lock: bool = True
    note: str = ""
    # 扁平快捷字段（写入 t0）
    enabled: Optional[bool] = None
    t0_ratio: Optional[float] = None
    sell_trigger_pct: Optional[float] = None
    buy_trigger_pct: Optional[float] = None
    fill_mode: Optional[str] = None
    direction: Optional[str] = None
    path_mode: Optional[str] = None
    dir_enter: Optional[float] = None
    min_range_pct: Optional[float] = None
    use_atr: Optional[bool] = None
    must_cover_same_day: Optional[bool] = None
    y_trade_floor: Optional[float] = None
    y_tau_enter: Optional[float] = None
    y_tau_enter_strong: Optional[float] = None
    y_ratio_cut: Optional[float] = None
    y_ratio_boost_cap: Optional[float] = None
    y_eod_prior: Optional[float] = None
    y_on_allow: Optional[float] = None
    y_on_risk: Optional[float] = None
    y_block_tau_nowcast_sign: Optional[bool] = None
    y_nowcast_enter: Optional[float] = None
    y_tau_map: Optional[str] = Field(
        default=None, max_length=24, description="scalp|trend|fixed_long|fixed_reverse"
    )
    y_use_path: Optional[bool] = None
    y_path_enter: Optional[float] = None
    y_path_required: Optional[bool] = None
    y_gap_tier_mode: Optional[str] = None
    y_gap_tier_pct: Optional[float] = None
    y_nowcast_oc_gate: Optional[bool] = None
    y_path_abandon_enabled: Optional[bool] = None
    y_path_abandon_bars: Optional[int] = None
    y_score_source: Optional[str] = Field(
        default=None, max_length=24, description="compute|live_book|ledger"
    )
    t0_pm_degrade: Optional[str] = None
    t0_pm_chase_interval_min: Optional[int] = None

