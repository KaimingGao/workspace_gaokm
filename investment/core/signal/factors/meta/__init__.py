"""因子平台层：注册表、面板、相关、健康、分类、系数、共线、风险归因。

单因子打分实现仍在 ``core.signal.factors`` 各模块；本包为编排与研究元数据。
对外稳定路径为 ``core.signal.factors.meta``（或各子模块 ``registry`` / ``panel`` 等）。
"""

from core.signal.factors.meta.coefs import (
    coefficients_from_return_model,
    display_weights_from_coefficients,
    display_weights_from_return_model,
    has_factor_coefficients,
)
from core.signal.factors.meta.collinearity import (
    TREND_FAMILY,
    collinearity_from_cluster_report,
    collinearity_from_panel_rows,
    trend_family_collinearity,
)
from core.signal.factors.meta.corr import (
    compute_factor_corr_matrix,
    pearson_with_reason,
    redundancy_warnings_from_corr,
)
from core.signal.factors.meta.health import (
    PROXY_OR_UNSOURCED,
    assess_factor_health,
    guard_weights_for_promote,
)
from core.signal.factors.meta.panel import build_factor_panel, build_factor_panel_rows
from core.signal.factors.meta.registry import (
    FactorFn,
    compute_configured_factors,
    compute_factor,
    factor_label,
    list_factors,
    registered_factor_names,
    run_factor_experiment,
)
from core.signal.factors.meta.risk_attribution import (
    brinson_attribution,
    condition_number,
    factor_correlation_matrix,
    factor_covariance_matrix,
    gram_schmidt_orthogonalize,
    risk_attribution,
    summarize_factor_collinearity,
    symmetric_orthogonalize,
)
from core.signal.factors.meta.taxonomy import (
    EXTRA_FAMILY,
    FAMILY_LABELS,
    FAMILY_META,
    FAMILY_ORDER,
    FAMILY_TIPS,
    REMOVED_RAW_BASIS_NAMES,
    SOURCE_LABELS,
    attach_taxonomy,
    classify_factor,
    classify_factors,
    is_removed_factor,
    strip_removed_factors_from_cluster_report,
    strip_removed_factors_from_pool_artifact,
)

__all__ = [
    "EXTRA_FAMILY",
    "FAMILY_LABELS",
    "FAMILY_META",
    "FAMILY_ORDER",
    "FAMILY_TIPS",
    "FactorFn",
    "PROXY_OR_UNSOURCED",
    "REMOVED_RAW_BASIS_NAMES",
    "SOURCE_LABELS",
    "TREND_FAMILY",
    "assess_factor_health",
    "attach_taxonomy",
    "brinson_attribution",
    "build_factor_panel",
    "build_factor_panel_rows",
    "classify_factor",
    "classify_factors",
    "coefficients_from_return_model",
    "collinearity_from_cluster_report",
    "collinearity_from_panel_rows",
    "compute_configured_factors",
    "compute_factor",
    "compute_factor_corr_matrix",
    "condition_number",
    "display_weights_from_coefficients",
    "display_weights_from_return_model",
    "factor_correlation_matrix",
    "factor_covariance_matrix",
    "factor_label",
    "gram_schmidt_orthogonalize",
    "guard_weights_for_promote",
    "has_factor_coefficients",
    "is_removed_factor",
    "list_factors",
    "pearson_with_reason",
    "registered_factor_names",
    "redundancy_warnings_from_corr",
    "risk_attribution",
    "run_factor_experiment",
    "strip_removed_factors_from_cluster_report",
    "strip_removed_factors_from_pool_artifact",
    "summarize_factor_collinearity",
    "symmetric_orthogonalize",
    "trend_family_collinearity",
]
