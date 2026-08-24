"""FastAPI 请求体模型（按域拆分；``from web.schemas import X`` 仍可用）。"""

from __future__ import annotations

from web.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatAsyncResponse,
)

from web.schemas.paper import (
    PaperRunRequest,
    PaperRebalanceRequest,
    PaperBuyRequest,
    PaperDepositRequest,
    PaperCostModelRequest,
    PaperSellRequest,
    T0BacktestRequest,
    PaperT0Request,
    PaperT0AutoRequest,
    PaperT0WorkerRequest,
    PaperExecutionPatchRequest,
)

from web.schemas.strategy_ops import (
    StrategyPromoteRequest,
    EvalRunRequest,
    DailyRunRequest,
)

from web.schemas.watching import (
    WatchingWatchAdd,
    WatchingWatchRemove,
    WatchingSyncPaper,
    WatchingFile,
)

from web.schemas.research import (
    CrossSectionRequest,
    FactorExperimentRequest,
    FactorOlsPoolRequest,
    OnRidgeRequest,
    RemRidgeRequest,
    FactorOlsClusterRequest,
    FactorCsIcRequest,
    AbCompareRequest,
    ReturnModelFitRequest,
    ReturnModelPromoteRequest,
    ParamGridRequest,
    WeightSuggestRequest,
    ThresholdSuggestRequest,
    YhatResidualShadowRequest,
    ExcessModeShadowRequest,
)

from web.schemas.cluster import (
    ClusterPaperPreviewRequest,
    ClusterMultiScoreRequest,
    ClusterPromoteRequest,
    ClusterRollbackRequest,
    ClusterModeRequest,
    ClusterApplyShortcutRequest,
)

from web.schemas.config import (
    ScoringFloorsRequest,
    StanceThresholdsRequest,
    SentimentPriorRequest,
    MarketPriorRequest,
    DualScoreRequest,
)

from web.schemas.backtest import (
    PortfolioBacktestRequest,
)

from web.schemas.score import (
    ScoreReviewRequest,
    ScoreLedgerFreezeRequest,
    ScoreOutcomesFillRequest,
    ScoreLedgerDeleteRequest,
    ScoreCalibrationFitRequest,
    ScoreCalibrationPersistRequest,
)

from web.schemas.quant_misc import (
    QuantReportRequest,
    QuantInterpretRequest,
    QuantReportDeleteRequest,
)

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ChatAsyncResponse",
    "PaperRunRequest",
    "PaperRebalanceRequest",
    "PaperBuyRequest",
    "PaperDepositRequest",
    "PaperCostModelRequest",
    "PaperSellRequest",
    "T0BacktestRequest",
    "PaperT0Request",
    "PaperT0AutoRequest",
    "PaperT0WorkerRequest",
    "PaperExecutionPatchRequest",
    "StrategyPromoteRequest",
    "EvalRunRequest",
    "DailyRunRequest",
    "WatchingWatchAdd",
    "WatchingWatchRemove",
    "WatchingSyncPaper",
    "WatchingFile",
    "CrossSectionRequest",
    "FactorExperimentRequest",
    "FactorOlsPoolRequest",
    "OnRidgeRequest",
    "RemRidgeRequest",
    "FactorOlsClusterRequest",
    "FactorCsIcRequest",
    "AbCompareRequest",
    "ReturnModelFitRequest",
    "ReturnModelPromoteRequest",
    "ParamGridRequest",
    "WeightSuggestRequest",
    "ThresholdSuggestRequest",
    "YhatResidualShadowRequest",
    "ExcessModeShadowRequest",
    "ClusterPaperPreviewRequest",
    "ClusterMultiScoreRequest",
    "ClusterPromoteRequest",
    "ClusterRollbackRequest",
    "ClusterModeRequest",
    "ClusterApplyShortcutRequest",
    "ScoringFloorsRequest",
    "StanceThresholdsRequest",
    "SentimentPriorRequest",
    "MarketPriorRequest",
    "DualScoreRequest",
    "PortfolioBacktestRequest",
    "ScoreReviewRequest",
    "ScoreLedgerFreezeRequest",
    "ScoreOutcomesFillRequest",
    "ScoreLedgerDeleteRequest",
    "ScoreCalibrationFitRequest",
    "ScoreCalibrationPersistRequest",
    "QuantReportRequest",
    "QuantInterpretRequest",
    "QuantReportDeleteRequest",
]
