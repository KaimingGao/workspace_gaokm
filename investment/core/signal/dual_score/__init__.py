"""双层 predicted_score：ŷ_EOD + ŷ_τ；昨收口径正交加权融合。

ŷ_EOD      预估 close[T]/close[T-1]−1（现价对昨收）
ŷ_τ        预估 close[T]/open[T]−1（独立 τ 头；买入闸仍用这一层）
ŷ_trade    = w·ŷ_EOD + w·(缺口∘ŷ_τ)  同为现价对昨收，不经过 ŷ_EOD_rem
ŷ_EOD_rem  仅派生对照（y_state / cascade / nowcast），不进 ŷ_trade
ŷ_nowcast  = 顺序 Kalman(EOD → open → 可选分钟 τ)；默认影子，不替换 predicted_score

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
    realized_t1_to_tau_pct,
    resolve_fusion_weights,
    stamp_trade_prev_close,
    trade_blend_vs_prev_close,
    unlifted_trade_blend_stale,
)
from core.signal.dual_score.resolve import (
    DEFAULT_DUAL_SCORE,
    align_nowcast_score_fields,
    align_trade_score_fields,
    buy_passes_tau_gate,
    compute_predicted_score_blend,
    decision_score_for_item,
    dual_track_score_fields,
    eod_gate_score_for_item,
    get_dual_score_cfg,
    is_heuristic_score_scale,
    rank_key_field,
    rank_key_for_item,
    resolve_predicted_score_eod,
    resolve_predicted_score_eod_rem,
    resolve_predicted_score_tau,
    resolve_tau_buy_floor_for_pool,
)
from core.signal.dual_score.shadow import (
    build_nowcast_shadow_book,
    build_tau_shadow_book,
    compare_book_overlap,
    nowcast_shadow_alerts,
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
    "align_nowcast_score_fields",
    "align_trade_score_fields",
    "apply_tau_score_fields",
    "attach_dual_score_bulk",
    "attach_dual_score_pit",
    "build_nowcast_shadow_book",
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
    "nowcast_shadow_alerts",
    "rank_key_field",
    "rank_key_for_item",
    "realized_t1_to_tau_pct",
    "recover_sub_scores_for_tau",
    "rem_factor_coefficients_public",
    "resolve_fusion_weights",
    "resolve_predicted_score_eod",
    "resolve_predicted_score_eod_rem",
    "resolve_predicted_score_tau",
    "resolve_tau_buy_floor_for_pool",
    "stamp_trade_prev_close",
    "trade_blend_vs_prev_close",
    "unlifted_trade_blend_stale",
]
