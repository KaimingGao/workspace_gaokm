"""FastAPI 请求体模型（P94 从 app.py 抽出）。"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    turn_usage: dict
    session_usage: dict
    artifacts: list = Field(default_factory=list)
    primary_tab: str = "reply"

class PaperRunRequest(BaseModel):
    simulate_buy: bool = False
    background: bool = False
    strategy: str = "short_conservative"
    dry_run: bool = False


class PaperRebalanceRequest(BaseModel):
    top_k: int = Field(default=3, ge=1, le=10)
    limit: int = Field(default=10, ge=1, le=30)


class PaperBuyRequest(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=16)
    amount: Optional[float] = Field(default=None, gt=0)
    shares: Optional[float] = Field(default=None, gt=0)


class PaperDepositRequest(BaseModel):
    amount: float = Field(default=1_000_000, gt=0, le=100_000_000)


class PaperCostModelRequest(BaseModel):
    """成交成本：simple_cn=佣金万2.5+卖出印花税万5（默认）；zero=教学/调试零成本。"""

    cost_model: str = Field(default="simple_cn", pattern="^(zero|simple_cn)$")


class StrategyPromoteRequest(BaseModel):
    strategy: str = Field(default="short", min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)
    apply_to_paper: bool = False
    overrides: Optional[Dict] = None


class PaperSellRequest(BaseModel):
    codes: Optional[list] = None
    stock_code: Optional[str] = Field(default=None, max_length=16)
    shares: Optional[float] = Field(default=None, gt=0)


class T0BacktestRequest(BaseModel):
    code: Optional[str] = None
    codes: Optional[list] = None
    from_paper: bool = True
    lookback: int = Field(default=30, ge=20, le=500)
    initial_shares: float = Field(default=1000, ge=100, le=100000)
    t0_ratio: float = Field(default=0.4, ge=0.05, le=1.0)
    sell_trigger_pct: float = Field(default=2.0, ge=0.1, le=20)
    buy_trigger_pct: float = Field(default=1.5, ge=0.1, le=20)
    must_cover_same_day: bool = False
    fill_mode: Optional[str] = Field(default=None, max_length=16)
    direction: Optional[str] = Field(default=None, max_length=16)
    path_mode: Optional[str] = Field(default=None, max_length=16)
    compare_optimistic: bool = True
    use_minute: bool = True
    compare_daily: bool = True


class PaperT0Request(BaseModel):
    dry_run: bool = True
    confirm: bool = False


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
    use_atr: Optional[bool] = None


class EvalRunRequest(BaseModel):
    case_id: Optional[str] = None
    use_mock: bool = True
    with_agent: bool = False
    with_presets: bool = False
    quant_only: bool = False
    background: bool = False


class DailyRunRequest(BaseModel):
    preset: Optional[str] = None
    paper_run: Optional[bool] = None
    paper_buy: Optional[bool] = None
    eval_mock: Optional[bool] = None
    eval_agent: Optional[bool] = None
    quant_report: Optional[bool] = None
    watching_refresh: Optional[bool] = None
    cross_section: Optional[bool] = None
    sync_paper_watchlist: Optional[bool] = None
    paper_rebalance: Optional[bool] = None
    export_quant_report: Optional[bool] = None
    portfolio_neutral_compare: Optional[bool] = None


class QuantReportRequest(BaseModel):
    code: str = "茅台"
    save: bool = True
    include_cross_section: bool = False
    include_portfolio_backtest: bool = True


class WatchingWatchAdd(BaseModel):
    query: str = Field(..., min_length=1, max_length=64)
    sync_paper: bool = False


class WatchingWatchRemove(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    sync_paper: bool = False


class WatchingSyncPaper(BaseModel):
    """加入模拟账户（现价假买进持仓）；codes 为空或不传则处理全部观察，传则只处理勾选。

    定量优先：shares_by_code / amount_by_code > amount_per_code > position_pct > shares。
    都不传时默认按金额（每只 2 万）。
    """

    codes: Optional[list] = None
    shares: Optional[int] = Field(default=None, ge=100, le=1_000_000)
    shares_by_code: Optional[Dict[str, int]] = None
    amount_per_code: Optional[float] = Field(default=None, gt=0, le=100_000_000)
    amount_by_code: Optional[Dict[str, float]] = None
    position_pct: Optional[float] = Field(default=None, gt=0, le=1)


class WatchingFile(BaseModel):
    version: int = 1
    name: str = "default"
    max_size: int = Field(default=30, ge=5, le=100)
    sources: list = Field(default_factory=list)
    watchlist: list = Field(default_factory=list)
    watchlist_origins: Optional[list] = None
    watchlist_names: Optional[list] = None


class CrossSectionRequest(BaseModel):
    codes: Optional[list] = None
    limit: int = Field(default=10, ge=1, le=30)
    min_score: Optional[float] = None
    horizon_days: int = Field(default=3, ge=1, le=3)


class FactorExperimentRequest(BaseModel):
    code: str = "茅台"
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ；0=普通 OLS（QR），>0 收缩斜率系数",
    )


class FactorOlsPoolRequest(BaseModel):
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=8, ge=2, le=20)
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ；0=普通 OLS（QR），>0 收缩斜率系数",
    )


class FactorCsIcRequest(BaseModel):
    """S1 · 研究池逐因子日频截面 IC。"""

    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=12, ge=3, le=30)
    min_names: int = Field(default=5, ge=3, le=20)
    pit_fundamentals: bool = True


class NextDayTrendRequest(BaseModel):
    """观察池日频+1 趋势预判（收盘→次日方向）。"""

    lookback: int = Field(default=120, ge=40, le=500)
    lookback_eval_days: int = Field(default=60, ge=10, le=250)
    flat_band_pct: float = Field(default=0.5, ge=0.0, le=5.0)
    watching_limit: int = Field(default=20, ge=1, le=40)
    codes: Optional[List[str]] = None
    pit_fundamentals: bool = True


class AbCompareRequest(BaseModel):
    """S2 · A/B 对照指纹。"""

    label_a: str = "A"
    label_b: str = "B"
    result_a: Optional[dict] = None
    result_b: Optional[dict] = None
    config_a: Optional[dict] = None
    config_b: Optional[dict] = None
    note: str = ""


class PortfolioBacktestRequest(BaseModel):
    codes: Optional[list] = None
    lookback: int = Field(default=120, ge=40, le=500)
    top_k: int = Field(default=3, ge=1, le=10)
    horizon_days: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=55.0, ge=0, le=100)
    apply_costs: bool = True
    include_wf_slices: bool = True
    wf_n_splits: int = Field(default=3, ge=1, le=6)
    # 交互回测默认跳过慢速基本面批量，避免「回测中」卡住感
    fetch_fundamentals: Optional[bool] = False
    # equal | score_budget | risk_parity_lite（与纸面 optimize 同源）
    weight_mode: str = "equal"
    max_position_pct: float = Field(default=40.0, ge=5.0, le=100.0)
    max_sector_pct: float = Field(default=60.0, ge=10.0, le=100.0)
    # T7 TopK-Dropout 缓冲；0=硬截断
    dropout_n: int = Field(default=0, ge=0, le=10)
    exclude_st: bool = False
    # 池内成交额分位下限（0–100）；None=不过滤
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_score_ic: bool = True
    include_quantile: bool = True
    include_benchmark: bool = True
    # T10：000300 / 000905 / 399006 / pool（强制池等权）
    benchmark_code: str = "000300"


class ParamGridRequest(BaseModel):
    """W3.3 · Top-K × lookback 网格（限格，跳过 WF/成本对照以控时）。"""

    codes: Optional[list] = None
    top_k_values: Optional[list] = None
    lookback_values: Optional[list] = None
    horizon_days: int = Field(default=3, ge=1, le=10)
    min_score: float = Field(default=55.0, ge=0, le=100)
    apply_costs: bool = True
    max_cells: int = Field(default=12, ge=1, le=20)


class WeightSuggestRequest(BaseModel):
    code: str = "茅台"
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    use_cs_ic: bool = Field(
        default=True,
        description="优先用研究池截面 IC/ICIR 驱动建议；池不足回退单票",
    )
    watching_limit: int = Field(default=12, ge=3, le=30)
    run_oos_gate: bool = Field(
        default=True,
        description="建议权 vs 当前权的研究池 Top-K OOS 门禁",
    )
    oos_tol_pp: float = Field(default=1.0, ge=0.0, le=10.0)
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="嵌入 OLS 回退时的 Ridge λ；0=普通 OLS",
    )


class ThresholdSuggestRequest(BaseModel):
    code: str = "茅台"
    lookback: int = Field(default=120, ge=40, le=500)
    use_watching: bool = False
    watching_limit: int = Field(default=5, ge=2, le=10)


class QuantInterpretRequest(BaseModel):
    use_saved: bool = True
    code: str = "茅台"
    save_before_interpret: bool = False
    offline: bool = False
