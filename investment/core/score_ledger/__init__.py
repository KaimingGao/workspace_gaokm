"""打分账本（产品面已下线）：按决策日 as_of 冻结 ŷ。

写入：日报 ``freeze_from_daily_report``、日更 ``run_score_ledger_daily`` **已停写**
（观察池分档改吃 ŷ_oo Holdout OOS）。HTTP 全 stub；库函数 / 旧文件仅供脚本与单测。

实现按用例拆到子模块；本包再导出，保持 ``from core.score_ledger import …``。
"""

from __future__ import annotations

from core.score_ledger.asof import (
    default_as_of,
    infer_feature_as_of,
    resolve_freeze_as_of,
    session_allows_ledger_freeze,
)
from core.score_ledger.freeze import (
    freeze_from_cluster_book,
    freeze_from_daily_report,
    freeze_from_nowcast_shadow_book,
    freeze_from_tau_shadow_book,
    load_nowcast_shadow_membership,
    load_tau_shadow_membership,
)
from core.score_ledger.io import (
    _YHAT_EPS,
    delete_ledger,
    delete_ledgers,
    ledger_dir,
    ledger_path,
    list_ledger_dates,
    list_ledger_entries,
    load_ledger,
    load_outcomes,
    nowcast_shadow_membership_path,
    outcomes_path,
    row_from_scored_item,
    rows_for_book_review,
    tau_shadow_membership_path,
    upsert_ledger_rows,
)
from core.score_ledger.outcomes import (
    fill_outcomes,
    hydrate_ledger_yhat_tau,
)
from core.score_ledger.review import (
    build_nowcast_shadow_review,
    build_score_review,
    build_tau_shadow_review,
)
from core.score_ledger.series import (
    code_yhat_series,
    hit_rate_series,
    run_score_ledger_daily,
    stock_panel_series,
)

__all__ = [
    "_YHAT_EPS",
    "build_nowcast_shadow_review",
    "build_score_review",
    "build_tau_shadow_review",
    "code_yhat_series",
    "default_as_of",
    "delete_ledger",
    "delete_ledgers",
    "fill_outcomes",
    "freeze_from_cluster_book",
    "freeze_from_daily_report",
    "freeze_from_nowcast_shadow_book",
    "freeze_from_tau_shadow_book",
    "hit_rate_series",
    "hydrate_ledger_yhat_tau",
    "infer_feature_as_of",
    "ledger_dir",
    "ledger_path",
    "list_ledger_dates",
    "list_ledger_entries",
    "load_ledger",
    "load_nowcast_shadow_membership",
    "load_outcomes",
    "load_tau_shadow_membership",
    "nowcast_shadow_membership_path",
    "outcomes_path",
    "resolve_freeze_as_of",
    "row_from_scored_item",
    "rows_for_book_review",
    "run_score_ledger_daily",
    "session_allows_ledger_freeze",
    "stock_panel_series",
    "tau_shadow_membership_path",
    "upsert_ledger_rows",
]
