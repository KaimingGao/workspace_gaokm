"""独立 L1/L2 物化管道（不取代票级 Feature Lake）。"""

from core.research.materialize.catalog import catalog_payload, normalize_families
from core.research.materialize.cube_z import (
    apply_day_cube_mu_sigma_dicts,
    apply_day_cube_mu_sigma_matrix,
    cs_z_dicts_prefer_cube,
    cs_z_matrix_prefer_cube,
)
from core.research.materialize.flags import fit_use_day_cube, require_day_cube
from core.research.materialize.cover_policy import (
    HORIZON_COVER_FRAC,
    LAKE_COVER_FRAC,
    clamp_span_days,
    cover_need,
    cover_reaches,
    finalize_l2_report,
    first_nested_error,
    format_cube_cover_error,
    format_l1_clock_cover_error,
    resolve_tau_l2_clocks,
    scale_eligible_stocks,
)
from core.research.materialize.horizon_names import (
    horizon_cube_required_names,
    horizon_l1_feature_names,
    names_cover_horizon,
)
from core.research.materialize.orchestrate import (
    resolve_materialize_codes,
    run_materialize,
)

__all__ = [
    "catalog_payload",
    "normalize_families",
    "require_day_cube",
    "fit_use_day_cube",
    "resolve_materialize_codes",
    "run_materialize",
    "apply_day_cube_mu_sigma_matrix",
    "apply_day_cube_mu_sigma_dicts",
    "cs_z_matrix_prefer_cube",
    "cs_z_dicts_prefer_cube",
    "horizon_l1_feature_names",
    "horizon_cube_required_names",
    "names_cover_horizon",
    "LAKE_COVER_FRAC",
    "HORIZON_COVER_FRAC",
    "cover_need",
    "cover_reaches",
    "scale_eligible_stocks",
    "clamp_span_days",
    "format_cube_cover_error",
    "format_l1_clock_cover_error",
    "finalize_l2_report",
    "first_nested_error",
    "resolve_tau_l2_clocks",
]
