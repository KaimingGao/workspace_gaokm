"""独立 L1/L2 物化管道（不取代票级 Feature Lake）。"""

from core.research.materialize.catalog import catalog_payload, normalize_families
from core.research.materialize.cube_z import (
    apply_day_cube_mu_sigma_dicts,
    apply_day_cube_mu_sigma_matrix,
    cs_z_dicts_prefer_cube,
    cs_z_matrix_prefer_cube,
)
from core.research.materialize.flags import fit_use_day_cube, require_day_cube
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
]
