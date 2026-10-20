"""双层 predicted_score：ŷ_oo + ŷ_τc；调仓 y_oo_fuse 正交加权。

ŷ_oo       预估 open[T+1]/open[T]−1（分组 β；主字段 y_oo，别名 predicted_score）
ŷ_τc       预估 close[T]/price[τ]−1（主字段 y_τc，含时钟对齐）
           原始模型头在 y_tau_raw。τ=open 时等于 close/open−1。
ŷ_co       预估 open[T+1]/close[T]−1（主字段 y_co / predicted_score_co）
y_oo_fuse τc：w·((ŷ_oo+1)/(1+rot)−1) + w·((1+ŷ_τc)(1+w_co·ŷ_co)−1)
           旧 OC 标签的行也读 y_τc（写入前已对齐到 price(τ)→close）
residual   R̂_τ = remaining；表列 y_τc 保持 Ridge 原值

规范见 docs/quant.md · ŷ 全链路。

实现按用例拆到本包子模块（``fusion`` / ``resolve`` / ``tau`` / ``book`` 等）；本包再导出。
"""

from __future__ import annotations

from core.signal.dual_score.book import dual_score_book_fields
from core.signal.dual_score.fusion import (
    fuse_remaining_heads,
    fuse_remaining_heads_meta,
    merge_tau_features,
    normalize_fusion_mode,
    oo_remaining_at_tau,
    realized_t1_to_tau_pct,
    resolve_fusion_weights,
    trade_blend_vs_prev_close,
)
from core.signal.dual_score.resolve import (
    DEFAULT_DUAL_SCORE,
    align_trade_score_fields,
    buy_passes_tau_gate,
    compute_predicted_score_blend,
    decision_score_for_item,
    get_dual_score_cfg,
    is_heuristic_score_scale,
    oo_gate_score_for_item,
    rank_key_field,
    rank_key_for_item,
    resolve_predicted_score_oo,
    resolve_predicted_score_oo_rem,
    resolve_predicted_score_tau,
    resolve_tau_buy_floor_for_pool,
)
from core.signal.dual_score.tau import (
    apply_tau_score_fields,
    attach_dual_score_bulk,
    attach_dual_score_pit,
    ensure_formula_terms_tau,
    rem_factor_coefficients_public,
    tau_explain_feats_row,
)

__all__ = [
    "DEFAULT_DUAL_SCORE",
    "align_trade_score_fields",
    "apply_tau_score_fields",
    "attach_dual_score_bulk",
    "attach_dual_score_pit",
    "buy_passes_tau_gate",
    "compute_predicted_score_blend",
    "decision_score_for_item",
    "dual_score_book_fields",
    "ensure_formula_terms_tau",
    "fuse_remaining_heads",
    "fuse_remaining_heads_meta",
    "get_dual_score_cfg",
    "is_heuristic_score_scale",
    "merge_tau_features",
    "normalize_fusion_mode",
    "oo_gate_score_for_item",
    "oo_remaining_at_tau",
    "rank_key_field",
    "rank_key_for_item",
    "realized_t1_to_tau_pct",
    "rem_factor_coefficients_public",
    "resolve_fusion_weights",
    "resolve_predicted_score_oo",
    "resolve_predicted_score_oo_rem",
    "resolve_predicted_score_tau",
    "resolve_tau_buy_floor_for_pool",
    "tau_explain_feats_row",
    "trade_blend_vs_prev_close",
]
