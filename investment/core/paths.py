"""项目路径常量（单一事实源）。"""

from __future__ import annotations

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
SCORE_LEDGER_DIR = os.path.join(QUANT_REPORTS_DIR, "score_ledger")
LIVE_DIR = os.path.join(DATA_DIR, "live")
CLUSTER_WEIGHTS_ACTIVE_PATH = os.path.join(LIVE_DIR, "cluster_weights_active.json")
CLUSTER_WEIGHTS_HISTORY_DIR = os.path.join(LIVE_DIR, "cluster_weights_history")
CLUSTER_WEIGHTS_DRAFT_PATH = os.path.join(LIVE_DIR, "cluster_weights_draft.json")
CLUSTER_BOOK_ACTIVE_PATH = os.path.join(LIVE_DIR, "cluster_book_active.json")
# P1：分组全量报告缓存（24h 内 + watchlist 指纹未变直接复用，避免重算 OLS/OOS/分池）
CLUSTER_REPORT_CACHE_PATH = os.path.join(LIVE_DIR, "cluster_report_cache.json")
# 最近一次成功分组（不论指纹；刷新进页优先恢复，避免重算导致组变）
CLUSTER_LAST_REPORT_PATH = os.path.join(LIVE_DIR, "cluster_last_report.json")
# FH1：指针指向版本化 artifact；active 文件为镜像兼容层
CLUSTER_POINTER_PATH = os.path.join(LIVE_DIR, "cluster_pointer.json")
PROMOTE_AUDIT_PATH = os.path.join(LIVE_DIR, "promote_audit.jsonl")
LIVE_CONFIG_MANIFEST_PATH = os.path.join(LIVE_DIR, "live_config_manifest.json")
RETURN_SCORE_MODEL_DRAFT_PATH = os.path.join(QUANT_REPORTS_DIR, "last_return_score_model.json")
RETURN_SCORE_MODEL_ACTIVE_PATH = os.path.join(LIVE_DIR, "return_score_model_active.json")
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
NORTH_STAR_LAST_BACKTEST_PATH = os.path.join(DATA_DIR, "north_star_last_backtest.json")
TTM_EVENTS_PATH = os.path.join(DATA_DIR, "ttm_events.jsonl")
NEWS_STORE_DIR = os.path.join(STORE_DIR, "news")
NEWS_HISTORY_DIR = os.path.join(NEWS_STORE_DIR, "history")
LLM_SENTIMENT_DIR = os.path.join(NEWS_STORE_DIR, "llm")
SENTIMENT_LEXICON_PATH = os.path.join(DATA_DIR, "sentiment_lexicon.json")
JOBS_DIR = os.path.join(DATA_DIR, "jobs")
PAPER_JOB_PATH = os.path.join(JOBS_DIR, "paper.json")
QUANT_OLS_CLUSTERS_JOB_PATH = os.path.join(JOBS_DIR, "quant_ols_clusters.json")


def cluster_weights_versioned_path(version: int) -> str:
    """版本化组权产物路径（``cluster_weights_v{n}.json``）。"""
    return os.path.join(LIVE_DIR, f"cluster_weights_v{int(version)}.json")
