"""QuantService · 配置 / 策略列表（P94 拆分）。"""

from __future__ import annotations

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
        from core.signal.score_display import resolve_buy_floor, resolve_hold_floor
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
