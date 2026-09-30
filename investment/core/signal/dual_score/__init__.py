"""双层 predicted_score：ŷ_oo + ŷ_τc；调仓 ranking 正交加权。

ŷ_oo       预估 open[T+1]/open[T]−1（分组 β；主字段 y_oo，别名 predicted_score）
ŷ_τc       预估 close[T]/price[τ]−1（主字段 y_τc）
           拟合原值写入 y_τc，时钟对齐后写入 y_tau，并留别名 y_oc。τ=open 时等于 close/open−1。
           不再另跑一套 ŷ_τ 去覆盖 y_τc。
ŷ_co       预估 open[T+1]/close[T]−1（主字段 y_co；旧键 y_on 可读）
ranking    τc：w·((ŷ_oo+1)/(1+rot)−1) + w·((1+ŷ_τc)(1+w_co·ŷ_co)−1)
           旧 OC 模型仍用 y_oc 那条几何式
residual   R̂_τ = remaining；表列 y_τc 保持 Ridge 原值

规范见 docs/quant.md · ŷ 全链路。

实现按用例拆到本包子模块（``fusion`` / ``resolve`` / ``tau`` / ``book`` 等）；本包再导出。
"""

from __future__ import annotations

from core.signal.dual_score.book import dual_score_book_fields
from core.signal.dual_score.fusion import (
    cascade_tau_shadow,
    eod_remaining_at_tau,
    fuse_remaining_heads,
    fuse_remaining_heads_meta,
    item_gap_pct,
    lift_tau_vs_prev_close,
    merge_tau_features,
    normalize_fusion_mode,
    oo_remaining_at_tau,
    realized_t1_to_tau_pct,
    resolve_fusion_weights,
    stamp_trade_prev_close,
    trade_blend_vs_prev_close,
    unlifted_trade_blend_stale,
)
from core.signal.dual_score.resolve import (
    DEFAULT_DUAL_SCORE,
    align_trade_score_fields,
    buy_passes_tau_gate,
    compute_predicted_score_blend,
    decision_score_for_item,
    dual_track_score_fields,
    eod_gate_score_for_item,
    get_dual_score_cfg,
    is_heuristic_score_scale,
    oo_gate_score_for_item,
    rank_key_field,
    rank_key_for_item,
    resolve_predicted_score_eod,
    resolve_predicted_score_eod_rem,
    resolve_predicted_score_oo,
    resolve_predicted_score_oo_rem,
    resolve_predicted_score_tau,
    resolve_tau_buy_floor_for_pool,
)
from core.signal.dual_score.shadow import (
    build_tau_shadow_book,
    compare_book_overlap,
)

# 单测 patch 路径：core.signal.dual_score._eod_return_model_for_item
from core.signal.dual_score.tau import (
    _TAU_CORE_Z_KEYS,
    _TAU_FEATURE_KEYS,
    _eod_return_model_for_item,
    apply_tau_score_fields,
    attach_dual_score_bulk,
    attach_dual_score_pit,
    ensure_formula_terms_tau,
    features_tau_fill_diag,
    features_tau_snapshot,
    format_tau_formula_string,
    recover_sub_scores_for_tau,
    rem_factor_coefficients_public,
)

__all__ = [
    "DEFAULT_DUAL_SCORE",
    "_TAU_CORE_Z_KEYS",
    "_TAU_FEATURE_KEYS",
    "_eod_return_model_for_item",
    "align_trade_score_fields",
    "apply_tau_score_fields",
    "attach_dual_score_bulk",
    "attach_dual_score_pit",
    "build_tau_shadow_book",
    "buy_passes_tau_gate",
    "cascade_tau_shadow",
    "compare_book_overlap",
    "compute_predicted_score_blend",
    "decision_score_for_item",
    "dual_score_book_fields",
    "dual_track_score_fields",
    "ensure_formula_terms_tau",
    "eod_gate_score_for_item",
    "eod_remaining_at_tau",
    "features_tau_fill_diag",
    "features_tau_snapshot",
    "format_tau_formula_string",
    "fuse_remaining_heads",
    "fuse_remaining_heads_meta",
    "get_dual_score_cfg",
    "is_heuristic_score_scale",
    "item_gap_pct",
    "lift_tau_vs_prev_close",
    "merge_tau_features",
    "normalize_fusion_mode",
    "oo_gate_score_for_item",
    "oo_remaining_at_tau",
    "rank_key_field",
    "rank_key_for_item",
    "realized_t1_to_tau_pct",
    "recover_sub_scores_for_tau",
    "rem_factor_coefficients_public",
    "resolve_fusion_weights",
    "resolve_predicted_score_eod",
    "resolve_predicted_score_eod_rem",
    "resolve_predicted_score_oo",
    "resolve_predicted_score_oo_rem",
    "resolve_predicted_score_tau",
    "resolve_tau_buy_floor_for_pool",
    "stamp_trade_prev_close",
    "trade_blend_vs_prev_close",
    "unlifted_trade_blend_stale",
]
