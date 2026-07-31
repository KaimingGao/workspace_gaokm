"""QuantService · 配置 / 策略列表（P94 拆分）。"""

from __future__ import annotations

import os
from typing import Any, Dict

from core.paths import SIGNAL_CONFIG_PATH
from core.signal.config import load_signal_config


class QuantConfigMixin:
    def config_summary(self) -> Dict[str, Any]:
        cfg = load_signal_config()
        return {
            "success": True,
            "config_path": os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH),
            "weights": cfg.get("weights"),
            "stance_thresholds": cfg.get("stance_thresholds"),
            "regime": cfg.get("regime"),
            "hard_reject": cfg.get("hard_reject"),
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
        from core.strategy import list_strategy_specs

        return {"success": True, "strategies": list_strategy_specs()}

    def validate_signal_config_draft(self, config: dict) -> Dict[str, Any]:
        from core.signal_config_draft import diff_against_production, validate_config_payload

        ok, normalized, errors = validate_config_payload(config)
        out: Dict[str, Any] = {
            "ok": ok,
            "success": ok,
            "errors": errors,
            "config": normalized if ok else None,
        }
        if ok:
            out["diff"] = diff_against_production(normalized)
        return out

    def save_signal_config_draft(self, config: dict, *, note: str = "") -> Dict[str, Any]:
        from core.signal_config_draft import save_draft

        return save_draft(config, note=note)

    def load_signal_config_draft(self) -> Dict[str, Any]:
        from core.signal_config_draft import load_draft

        return load_draft()

    def diff_signal_config_draft(self, config: dict = None) -> Dict[str, Any]:
        from core.signal_config_draft import diff_against_production

        return diff_against_production(config)

    def promote_signal_config_draft(
        self,
        config: dict = None,
        *,
        note: str = "",
    ) -> Dict[str, Any]:
        from core.signal_config_draft import promote_draft

        return promote_draft(draft=config, note=note)
