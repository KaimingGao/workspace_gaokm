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
    dir_enter: Optional[float] = Field(
        default=None, ge=0.05, le=1.0, description="signal 入场门槛 |score|；默认 0.35"
    )
    min_range_pct: Optional[float] = Field(
        default=None, ge=0.2, le=30.0, description="振幅下限%；空=自动 max(卖+买)*0.6"
    )
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
    dir_enter: Optional[float] = None
    min_range_pct: Optional[float] = None
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
    paper_holding_cycle: Optional[bool] = None  # alias for paper_run
    paper_buy: Optional[bool] = None
    eval_mock: Optional[bool] = None
    eval_agent: Optional[bool] = None
    quant_report: Optional[bool] = None
    watching_refresh: Optional[bool] = None
    cross_section: Optional[bool] = None
    sync_paper_watchlist: Optional[bool] = None
    paper_rebalance: Optional[bool] = None
    paper_cross_section_rebalance: Optional[bool] = None  # alias for paper_rebalance
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


class FactorOlsClusterRequest(BaseModel):
    """研究池：单票 OLS β 聚类 → 组内共用权草案（不写 config）。"""

    lookback: int = Field(default=80, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(
        default=12,
        ge=3,
        le=20,
        description="兼容字段：watching 宇宙取全部观察池，本参数不截断；仅 union 等模式参考",
    )
    n_clusters: Optional[int] = Field(
        default=None,
        ge=2,
        le=100,
        description="目标组数；null=自动约 n/5（夹在 4～10）；显式 ≥2，实际上限=有效票数-1",
    )
    cluster_method: str = Field(
        default="hierarchical",
        description="hierarchical（默认，配合 cluster_linkage）| kmeans",
    )
    cluster_linkage: str = Field(
        default="complete",
        description="层次连接：complete（默认，控大团）| average",
    )
    within_dist_quantile: float = Field(
        default=0.75,
        ge=0.05,
        le=0.95,
        description="类内直径 τ = 两两距离分位数（默认 0.75）；越小越紧、单票组越多",
    )
    ridge_lambda: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Ridge λ 种子；select_ridge=true 时作网格起点/回退",
    )
    select_ridge: bool = Field(
        default=True,
        description="B3：时间切分网格选 Ridge λ（写入 ridge_lambda_selected）",
    )
    collinearity_policy: str = Field(
        default="drop_redundant",
        description="B3：趋势族共线进模 keep_all|drop_redundant|orthogonalize_lite",
    )
    respect_regime: bool = Field(
        default=True,
        description="B5：与 live regime 白名单对齐裁剪因子。枢纽 UI 默认不勾（发 false=全因子）；勾选后表内未进白名单的因子会显示未算",
    )
    pit_fundamentals: bool = Field(
        default=True,
        description="FH5：默认 PIT 基本面；false 时报告带非 PIT 红旗",
    )
    sync: bool = Field(
        default=False,
        description="FH2：true=同步跑（单测/兼容）；默认入队 Job 轮询",
    )
    beta_scale: str = Field(
        default="feature_zscore",
        description="β 尺度：feature_zscore|l2|none；默认因子维 z-score，避免极端票独占一组",
    )
    l2_normalize_betas: Optional[bool] = Field(
        default=None,
        description="兼容旧参：True 强制 l2；优先用 beta_scale",
    )
    run_oos_gate: bool = Field(
        default=True,
        description="为每组跑组内建议权 vs 当前权的 Top-K OOS 对照",
    )
    oos_tol_pp: float = Field(default=1.0, ge=0.0, le=10.0)
    run_group_score: bool = Field(
        default=True,
        description="每组用组权打分并组内排序（对照全局权统一排名）",
    )
    run_pool_merge: bool = Field(
        default=True,
        description="各组 Top-N 合成候选簿 + 分池对照回测（研究预览，不写纸面）",
    )
    top_n_per_group: int = Field(
        default=10,
        ge=1,
        le=10,
        description="每组取组内排名前 N 只合成候选（默认 10）",
    )
    refresh_bars: bool = Field(
        default=True,
        description="刷新过期日线（默认开）：约 36h 内缓存仍复用；过期/缺条限流拉网。关=纯缓存重算（改参快跑，行情未变则结果几乎不变）",
    )


class ClusterPaperPreviewRequest(BaseModel):
    """分池候选簿 → 纸面调仓预演；confirm=true 才写 paper.json（不写 signal_config）。"""

    book: List[dict] = Field(default_factory=list, description="pool_merge.book.book")
    top_k: Optional[int] = Field(default=None, ge=1, le=30)
    confirm: bool = Field(
        default=False,
        description="true=写入纸面账户；默认仅预演",
    )
    artifact: Optional[dict] = Field(
        default=None,
        description="可选 pool_artifact 摘要，落账时记入 last_cluster_pool",
    )


class ClusterMultiScoreRequest(BaseModel):
    """用 code_map 多权复打分（仅组内序；不写 config）。"""

    artifact: Optional[dict] = Field(
        default=None,
        description="含 code_map 的产物；空则读 last_cluster_pool_artifact.json",
    )
    lookback: int = Field(default=80, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=20, ge=1, le=40)


class ClusterPromoteRequest(BaseModel):
    """晋升研究产物为 live active 映射。"""

    artifact: Optional[dict] = None
    from_draft: bool = True
    note: str = Field(default="", max_length=500)
    force: bool = False


class ClusterRollbackRequest(BaseModel):
    to_version: Optional[int] = Field(default=None, ge=1, le=10000)


class ClusterModeRequest(BaseModel):
    mode: str = Field(default="shadow", pattern="^(off|shadow|active)$")
    enabled: Optional[bool] = None
    force: bool = False


class ScoringFloorsRequest(BaseModel):
    """Y0 · 人审写入 ŷ 滞回门槛（不改 weights）。"""

    min_predicted_score: Optional[float] = Field(
        default=None, description="买入/入簿 ŷ% 下限；省略则不改"
    )
    min_hold_predicted_score: Optional[float] = Field(
        default=None, description="卖出 ŷ% 上限（低于则卖）；省略则不改"
    )
    note: str = Field(default="", max_length=500)


class SentimentPriorRequest(BaseModel):
    """舆情先验旁路（不进 ŷ）；人审写 sentiment.prior。"""

    mode: Optional[str] = Field(default=None, description="off | risk | gate")
    bearish_score_min: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    block_new_buys: Optional[bool] = None
    scale_buy_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scale_holds: Optional[bool] = None
    note: str = Field(default="", max_length=500)


class ClusterApplyShortcutRequest(BaseModel):
    """一键应用分组：晋升 + mode + 刷新分池簿。"""

    artifact: Optional[dict] = None
    from_draft: bool = True
    mode: str = Field(default="shadow", pattern="^(off|shadow|active)$")
    note: str = Field(default="", max_length=500)
    force: bool = False


class FactorCsIcRequest(BaseModel):
    """S1 · 研究池逐因子日频截面 IC。"""

    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=12, ge=3, le=30)
    min_names: int = Field(default=5, ge=3, le=20)
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
    # 仅 predicted_score；规则分已退役
    rank_mode: str = Field(
        default="predicted_score",
        description="排序键：仅 predicted_score（收益分）；其它值亦按收益分",
    )
    min_predicted_score: Optional[float] = Field(
        default=None,
        description="收益分下限（百分点）；默认不截断",
    )
    return_model_min_samples: int = Field(default=24, ge=8, le=500)
    return_model_ridge_lambda: float = Field(default=0.0, ge=0.0, le=100.0)


class ReturnModelFitRequest(BaseModel):
    """拟合收益排序模型并可选落研究草稿。"""

    codes: Optional[list] = None
    lookback: int = Field(default=120, ge=40, le=500)
    horizon_days: int = Field(default=3, ge=1, le=10)
    watching_limit: int = Field(default=12, ge=3, le=40)
    ridge_lambda: float = Field(default=0.0, ge=0.0, le=100.0)
    min_samples: int = Field(default=24, ge=8, le=500)
    save_draft: bool = True


class ReturnModelPromoteRequest(BaseModel):
    note: str = ""


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


class ScoreReviewRequest(BaseModel):
    """昨日复盘：as_of=决策日；horizon=前瞻收益天数。"""

    as_of: Optional[str] = Field(
        default=None, description="决策日 YYYY-MM-DD；默认上一交易日"
    )
    horizon_days: int = Field(default=3, ge=1, le=10)
    autofill: bool = Field(default=True, description="缺 outcomes 时自动用日线回填")


class ScoreLedgerFreezeRequest(BaseModel):
    as_of: Optional[str] = Field(
        default=None, description="冻结日；默认当前会话交易日"
    )


class ScoreOutcomesFillRequest(BaseModel):
    as_of: Optional[str] = None
    horizon_days: int = Field(default=3, ge=1, le=10)
