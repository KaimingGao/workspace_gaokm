"""Deprecated alias：canonical 在 ``core.research.tau_tree``（ŷ_τ_tree）。"""
from core.research.tau_tree import *  # noqa: F401,F403
from core.research.tau_tree import (  # noqa: F401
    TREE_SCHEMA as BOOST_SCHEMA,
    fit_tau_tree_report as fit_tau_boost_report,
    load_tau_tree_last_report as load_tau_boost_last_report,
    resolve_tree_backend as resolve_boost_backend,
    save_tau_tree_last_report as save_tau_boost_last_report,
    tau_boost_last_report_path,
    tau_tree_last_report_path,
)
