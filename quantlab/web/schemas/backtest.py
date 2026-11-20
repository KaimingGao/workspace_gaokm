"""组合回测请求模型。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class PaperReplayBacktestRequest(BaseModel):
    """产品历史回测（``/api/quant/portfolio-backtest``）：只跑 paper_replay / rank_lots。

    旧客户端若仍传 ``engine`` / ``top_k`` / ``horizon_days`` 等字段，Pydantic 默认忽略，
    服务端不切换引擎。
    """

    codes: Optional[list] = None
    lookback: int = Field(default=10, ge=10, le=500)
    score_model_role: str = Field(
        default="research",
        description="回测模型：research=研究槽（Holdout 前训，默认）；live=执行槽（由研究槽晋级的同一份模型）。",
        max_length=16,
    )
    score_backend: str = Field(
        default="ridge",
        description="调仓打分头：仅 ridge（影子树头已下线；传入 tree 会被忽略）。",
        max_length=16,
    )
    apply_costs: bool = True
    fetch_fundamentals: Optional[bool] = False
    exclude_st: bool = True
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_benchmark: bool = True
    benchmark_code: str = Field(
        default="pool",
        description="超额基准：pool/tier_a=观察池等权买持；或指数代码如 000300。",
    )
    fusion_w_oo: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_oo_fuse 中 ŷ_oo 权重",
    )
    fusion_w_oc: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="y_oo_fuse 中 ŷ_τc 权重",
    )
    fusion_w_oo_rank: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="已下线。读入忽略；买序 rank_score 固定 0.6/0.4。",
    )
    fusion_w_tc_rank: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="已下线。读入忽略；买序 rank_score 固定 0.6/0.4。",
    )
    fuse_enter: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="y_oo_fuse 入场下限（净收益，0.001=0.1%；UI 用百分数）",
    )
    fuse_strong: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description="y_oo_fuse 强手门槛（净收益；回测单一手数时与入场同）",
    )
    y_oo_gt0: bool = Field(
        default=False,
        description=(
            "开=入场须 y_*>0（四头 ŷ_oo / ŷ_τc / y_oo_rank_score / y_tc_rank_score 均 >0）；"
            "关=不看。缺分不拦。未过则已持仓清仓。"
        ),
    )
    y_τc_gt0: Optional[bool] = Field(
        default=None,
        description="已退役：旧单头 ŷ_τc>0 闸；若为 true 并入 y_oo_gt0（y_*>0）。",
    )
    sentiment_gate_enabled: bool = Field(
        default=False,
        description="遗留键：规则栏已下掉舆情软先验；服务端恒关，忽略入参。",
    )
    morph_gate_enabled: bool = Field(
        default=False,
        description=(
            "开=形态闸总开关；门槛写死：高波弱开(vol_p≥0.9∧a1<0) OR 异常低开(a1≤−8%) "
            "OR 涨停开盘（主板≥9.5% / 创业科创≥19.5% / ST≥4.5%）。"
            "缺数据不拦；已持仓不清仓。默认关。"
        ),
    )
    oo_rank_max: Optional[int] = Field(
        default=6,
        ge=2,
        le=5000,
        description="入场名次：max(oo_rank, τc_rank) 须 < N。默认 6；None=关。两头都缺不拦。过强档改看 oo_rank_strong_max。",
    )
    oo_rank_strong_max: Optional[int] = Field(
        default=12,
        ge=2,
        le=5000,
        description="强档名次：仍看 max(oo_rank, τc_rank) < N。默认 12；None=关。两头都缺不拦。",
    )
    initial_cash: float = Field(
        default=200_000.0,
        ge=10_000.0,
        le=100_000_000.0,
        description="历史回测本金（元）。默认 20 万；不套持仓市值帽",
    )
    fill_clock: str = Field(
        default="09:30",
        description="调仓成交钟 09:30–10:00 每 5 分钟；09:30 用日 K 开盘（不读整池 5m），其后用该档 5 分钟 K 收盘",
    )
    lot_base_amount: float = Field(
        default=10_000.0,
        ge=1_000.0,
        le=1_000_000.0,
        description="调仓入场金额（元）。默认 1 万；按成交价换算整手，不够一手则跳过。保存规则同步到交易执行自动调仓",
    )
    lot_strong_amount: float = Field(
        default=20_000.0,
        ge=1_000.0,
        le=1_000_000.0,
        description="调仓强档金额（元）。默认 2 万；不少于入场金额；单票持仓市值上限=此值×120%。保存规则同步到交易执行",
    )
    price_space_gate: bool = Field(
        default=True,
        description="调仓回测日分价闸：有分钟时 |日开/分开−1| 或 |日昨/分昨−1| 超阈则跳过。阈与做 T 共用。不改 live",
    )
    sync: bool = Field(
        default=False,
        description="true=同步跑（单测）；默认入队 Job，轮询 GET /api/jobs/portfolio-backtest",
    )

    @model_validator(mode="after")
    def _sync_fuse_thresholds(self) -> "PaperReplayBacktestRequest":
        if self.fuse_enter is None:
            self.fuse_enter = 0.001
        if self.fuse_strong is None:
            self.fuse_strong = 0.001
        self.fuse_enter = float(self.fuse_enter)
        self.fuse_strong = float(self.fuse_strong)
        return self

