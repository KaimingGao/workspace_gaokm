"""QuantService · 配置 / 策略列表（P94 拆分）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
import os
from typing import Any, Dict, Optional

from core.paths import SIGNAL_CONFIG_PATH
from core.signal.config import load_signal_config


class QuantConfigMixin:
    def config_summary(self) -> Dict[str, Any]:
        cfg = load_signal_config()
        scoring = cfg.get("scoring") if isinstance(cfg.get("scoring"), dict) else {}
        rank_mode = scoring.get("rank_mode") or "predicted_score"
        return {
            "success": True,
            "config_path": os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH),
            "weights": cfg.get("weights"),
            "stance_thresholds": cfg.get("stance_thresholds"),
            "regime": cfg.get("regime"),
            "hard_reject": cfg.get("hard_reject"),
            "scoring": {
                "rank_mode": rank_mode,
                "min_predicted_score": scoring.get("min_predicted_score"),
            },
            "product_note": (
                "选股真源=predicted_score（ŷ）；"
                "heuristic 仅作研究 OOS 基线；过门≠自动 promote"
            ),
        }

    def read_signal_config_file(self) -> Dict[str, Any]:
        from core.signal.config import read_signal_config_file

        return read_signal_config_file(reload=True)

    def build_config_diff_preview(
        self,
        code: str = "茅台",
        *,
        use_saved: bool = True,
    ) -> Dict[str, Any]:
        from quant.services.signal_config_preview import build_config_diff_preview

        report = None
        if use_saved:
            saved = self.load_last_daily()
            if not saved.get("empty"):
                report = saved
        if report is None:
            ws = self.suggest_weights(code)
            ts = self.suggest_thresholds(code)
            return build_config_diff_preview(weight_suggest=ws, threshold_suggest=ts)
        return build_config_diff_preview(report)

    def export_config_diff_bundle(
        self,
        code: str = "茅台",
        *,
        use_saved: bool = True,
    ) -> Dict[str, Any]:
        from quant.services.signal_config_preview import export_config_diff_bundle

        preview = self.build_config_diff_preview(code=code, use_saved=use_saved)
        return export_config_diff_bundle(preview)

    def list_strategies(self) -> Dict[str, Any]:
        from core.signal.dual_score_config import read_dual_score_public
        from core.signal.score_display import resolve_buy_floor, resolve_hold_floor
        from core.signal.sentiment_prior_config import read_sentiment_prior_public
        from core.strategy import list_strategy_specs

        return {
            "success": True,
            "strategies": list_strategy_specs(),
            # live 选股门槛（ŷ%）；策略卡 params.min_score 为遗留 0–100 回测默认
            "scoring_floors": {
                "min_predicted_score": resolve_buy_floor(),
                "min_hold_predicted_score": resolve_hold_floor(),
                "unit": "predicted_score_pct",
            },
            "sentiment_prior": read_sentiment_prior_public(),
            "dual_score": read_dual_score_public(),
        }

    def save_scoring_floors(
        self,
        *,
        min_predicted_score: Optional[float] = None,
        min_hold_predicted_score: Optional[float] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.signal.scoring_floors import save_scoring_floors as _save

        return _save(
            min_predicted_score=min_predicted_score,
            min_hold_predicted_score=min_hold_predicted_score,
            note=note,
        )

    def save_stance_thresholds(
        self,
        *,
        avoid: Optional[float] = None,
        wait: Optional[float] = None,
        probe: Optional[float] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.signal.stance_save import save_stance_thresholds as _save

        return _save(avoid=avoid, wait=wait, probe=probe, note=note)

    def save_sentiment_prior(
        self,
        *,
        mode: Optional[str] = None,
        block_new_buys: Optional[bool] = None,
        scale_buy_pct: Optional[float] = None,
        scale_holds: Optional[bool] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.signal.sentiment_prior_config import save_sentiment_prior as _save

        return _save(
            mode=mode,
            block_new_buys=block_new_buys,
            scale_buy_pct=scale_buy_pct,
            scale_holds=scale_holds,
            note=note,
        )

    def read_sentiment_prior(self) -> Dict[str, Any]:
        from core.signal.sentiment_prior_config import read_sentiment_prior_public

        return {"success": True, "sentiment_prior": read_sentiment_prior_public()}

    def save_market_prior(
        self,
        *,
        cross_market_mode: Optional[str] = None,
        tech_drag_trigger_pct: Optional[float] = None,
        scale_buy_pct: Optional[float] = None,
        scale_holds: Optional[bool] = None,
        market_sentiment_mode: Optional[str] = None,
        market_sentiment_scale_buy_pct: Optional[float] = None,
        market_sentiment_scale_holds: Optional[bool] = None,
        regulatory_mode: Optional[str] = None,
        regulatory_scale_buy_pct: Optional[float] = None,
        ipo_drain_mode: Optional[str] = None,
        ipo_drain_scale_buy_pct: Optional[float] = None,
        ipo_drain_ratio_high: Optional[float] = None,
        merge_mode: Optional[str] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.signal.market_prior_config import save_market_prior as _save

        return _save(
            cross_market_mode=cross_market_mode,
            tech_drag_trigger_pct=tech_drag_trigger_pct,
            scale_buy_pct=scale_buy_pct,
            scale_holds=scale_holds,
            market_sentiment_mode=market_sentiment_mode,
            market_sentiment_scale_buy_pct=market_sentiment_scale_buy_pct,
            market_sentiment_scale_holds=market_sentiment_scale_holds,
            regulatory_mode=regulatory_mode,
            regulatory_scale_buy_pct=regulatory_scale_buy_pct,
            ipo_drain_mode=ipo_drain_mode,
            ipo_drain_scale_buy_pct=ipo_drain_scale_buy_pct,
            ipo_drain_ratio_high=ipo_drain_ratio_high,
            merge_mode=merge_mode,
            note=note,
        )

    def read_market_prior(self) -> Dict[str, Any]:
        from core.signal.market_prior_config import read_market_prior_public

        return {"success": True, "market_prior": read_market_prior_public()}

    def read_dual_score(self) -> Dict[str, Any]:
        from core.signal.dual_score_config import read_dual_score_public

        return {"success": True, "dual_score": read_dual_score_public()}

    def save_dual_score(
        self,
        *,
        fusion_mode: Optional[str] = None,
        min_predicted_score_tau: Optional[float] = None,
        w_eod: Optional[float] = None,
        w_tau: Optional[float] = None,
        w_mode: Optional[str] = None,
        block_buy_if_tau_missing: Optional[bool] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.signal.dual_score_config import save_dual_score as _save

        return _save(
            fusion_mode=fusion_mode,
            min_predicted_score_tau=min_predicted_score_tau,
            w_eod=w_eod,
            w_tau=w_tau,
            w_mode=w_mode,
            block_buy_if_tau_missing=block_buy_if_tau_missing,
            note=note,
        )
