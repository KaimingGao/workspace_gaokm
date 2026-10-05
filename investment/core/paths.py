"""项目路径常量（单一事实源）。"""


import logging

logger = logging.getLogger(__name__)
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")
STORE_DIR = os.environ.get(
    "INVESTMENT_STORE_DIR",
    os.path.join(DATA_DIR, "store"),
)
PAPER_PATH = os.environ.get(
    "INVESTMENT_PAPER_PATH",
    os.path.join(DATA_DIR, "paper.json"),
)
PAPER_EXAMPLE_PATH = os.path.join(DATA_DIR, "paper.example.json")
POSITION_RULES_PATH = os.path.join(DATA_DIR, "position_rules.json")
EVALS_LAST_RUN_PATH = os.path.join(DATA_DIR, "evals_last_run.json")
EVALS_JOB_PATH = os.path.join(DATA_DIR, "evals_job.json")
QUANT_DAILY_PATH = os.path.join(DATA_DIR, "quant_daily.json")
QUANT_REPORTS_DIR = os.path.join(DATA_DIR, "reports")
LIVE_DIR = os.path.join(DATA_DIR, "live")
# cluster_* 路径常量：分组已退役（cluster_retired）；保留供历史落盘/迁移只读，勿再写入新指针。
CLUSTER_WEIGHTS_ACTIVE_PATH = os.path.join(LIVE_DIR, "cluster_weights_active.json")
CLUSTER_WEIGHTS_HISTORY_DIR = os.path.join(LIVE_DIR, "cluster_weights_history")
CLUSTER_WEIGHTS_DRAFT_PATH = os.path.join(LIVE_DIR, "cluster_weights_draft.json")
CLUSTER_BOOK_ACTIVE_PATH = os.path.join(LIVE_DIR, "cluster_book_active.json")
# A2：同池按 ŷ_τ 重排的影子簿（不驱动 execution）
CLUSTER_BOOK_TAU_SHADOW_PATH = os.path.join(LIVE_DIR, "cluster_book_tau_shadow.json")
# N3：同池按 Kalman nowcast 重排的影子簿（不驱动 execution）
CLUSTER_BOOK_NOWCAST_SHADOW_PATH = os.path.join(LIVE_DIR, "cluster_book_nowcast_shadow.json")
# P1：分组全量报告缓存（历史；分组 OLS 已退役）
CLUSTER_REPORT_CACHE_PATH = os.path.join(LIVE_DIR, "cluster_report_cache.json")
# 最近一次成功分组（历史；分组 OLS 已退役）
CLUSTER_LAST_REPORT_PATH = os.path.join(LIVE_DIR, "cluster_last_report.json")
# 当日已强制刷新观察池日线到最新（按会话日标记，避免同日重复打网）
BARS_FORCED_SESSION_PATH = os.path.join(
    LIVE_DIR, "bars_forced_session.json"
)
# FH1：指针指向版本化 artifact；active 文件为镜像兼容层（历史；勿再 promote）
CLUSTER_POINTER_PATH = os.path.join(LIVE_DIR, "cluster_pointer.json")
PROMOTE_AUDIT_PATH = os.path.join(LIVE_DIR, "promote_audit.jsonl")
# H3：promote / 改 mode 后的 live 配置指纹；读写须按当时的 LIVE_DIR 拼接
LIVE_CONFIG_MANIFEST_PATH = os.path.join(LIVE_DIR, "live_config_manifest.json")
# 调仓 10:00 板块截面：T 开→T+1 开周期内只写一次，下午补仓不得改 10:00 中位
REBALANCE_CS_10_PATH = os.path.join(LIVE_DIR, "rebalance_cs_10.json")
RETURN_SCORE_MODEL_DRAFT_PATH = os.path.join(QUANT_REPORTS_DIR, "last_return_score_model.json")
RETURN_SCORE_MODEL_ACTIVE_PATH = os.path.join(LIVE_DIR, "return_score_model_active.json")
# 可预测性分档：影子 last vs 调仓闸 active（档位跟随历史回测勾选）
PREDICTABILITY_TIERS_LAST_PATH = os.path.join(LIVE_DIR, "predictability_tiers_last.json")
PREDICTABILITY_TIERS_ACTIVE_PATH = os.path.join(LIVE_DIR, "predictability_tiers_active.json")
# 研究套：供历史回测；与 active（执行套）分离，对齐 τ / co 的 *_research.json
RETURN_SCORE_MODEL_RESEARCH_PATH = os.path.join(LIVE_DIR, "return_score_model_research.json")
DAILY_LAST_RUN_PATH = os.path.join(DATA_DIR, "daily_last_run.json")
SIGNAL_CONFIG_PATH = os.path.join(DATA_DIR, "signal_config.json")
WATCHING_PATH = os.environ.get(
    "INVESTMENT_WATCHING_PATH",
    os.path.join(DATA_DIR, "watching.json"),
)
WATCHING_EXAMPLE_PATH = os.path.join(DATA_DIR, "watching.example.json")
MEMORY_PATH = os.environ.get(
    "INVESTMENT_MEMORY_PATH",
    os.path.join(DATA_DIR, "memory.json"),
)
DECISIONS_PATH = os.environ.get(
    "INVESTMENT_DECISIONS_PATH",
    os.path.join(DATA_DIR, "decisions.jsonl"),
)
SCHEDULE_LAST_RUN_PATH = os.path.join(DATA_DIR, "schedule_last_run.json")
T0_AUTO_WORKER_PATH = os.path.join(DATA_DIR, "t0_auto_worker.json")
REBALANCE_AUTO_WORKER_PATH = os.path.join(DATA_DIR, "rebalance_auto_worker.json")
T0_INTRADAY_STATE_PATH = os.path.join(DATA_DIR, "t0_intraday_state.json")
NORTH_STAR_LAST_BACKTEST_PATH = os.path.join(DATA_DIR, "north_star_last_backtest.json")
# 最近一次产品回测（/replay 刷新恢复 KPI / 净值 / 成交账；不重跑）
LAST_PORTFOLIO_BACKTEST_PATH = os.path.join(DATA_DIR, "last_portfolio_backtest.json")
# 最近一次做 T 研究回测（/follow 刷新恢复指标 / 图 / 成交明细；不重跑）
LAST_T0_BACKTEST_PATH = os.path.join(DATA_DIR, "last_t0_backtest.json")
TTM_EVENTS_PATH = os.path.join(DATA_DIR, "ttm_events.jsonl")
NEWS_STORE_DIR = os.path.join(STORE_DIR, "news")
NEWS_HISTORY_DIR = os.path.join(NEWS_STORE_DIR, "history")
LLM_SENTIMENT_DIR = os.path.join(NEWS_STORE_DIR, "llm")
SENTIMENT_LEXICON_PATH = os.path.join(DATA_DIR, "sentiment_lexicon.json")
JOBS_DIR = os.path.join(DATA_DIR, "jobs")
PAPER_JOB_PATH = os.path.join(JOBS_DIR, "paper.json")
T0_BACKTEST_JOB_PATH = os.path.join(JOBS_DIR, "t0_backtest.json")
PORTFOLIO_BACKTEST_JOB_PATH = os.path.join(JOBS_DIR, "portfolio_backtest.json")
QUANT_OLS_CLUSTERS_JOB_PATH = os.path.join(JOBS_DIR, "quant_ols_clusters.json")
BARS_REFRESH_JOB_PATH = os.path.join(JOBS_DIR, "bars_refresh.json")
MINUTE_REFRESH_JOB_PATH = os.path.join(JOBS_DIR, "minute_refresh.json")
CHAT_JOB_PATH = os.path.join(JOBS_DIR, "chat.json")
T30_RIDGE_JOB_PATH = os.path.join(JOBS_DIR, "t30_ridge.json")
T45_RIDGE_JOB_PATH = os.path.join(JOBS_DIR, "t45_ridge.json")
T60_RIDGE_JOB_PATH = os.path.join(JOBS_DIR, "t60_ridge.json")
T75_RIDGE_JOB_PATH = os.path.join(JOBS_DIR, "t75_ridge.json")
T90_RIDGE_JOB_PATH = os.path.join(JOBS_DIR, "t90_ridge.json")
CO_RIDGE_JOB_PATH = os.path.join(JOBS_DIR, "co_ridge.json")


def cluster_weights_versioned_path(version: int) -> str:
    """版本化组权产物路径（``cluster_weights_v{n}.json``）。"""
    return os.path.join(LIVE_DIR, f"cluster_weights_v{int(version)}.json")
