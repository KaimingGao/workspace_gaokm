"""signal_config 权重/阈值 diff 预览（P20.1，只读、不写盘）。"""


import logging

logger = logging.getLogger(__name__)
from datetime import datetime
from typing import Any, Dict, Optional


def _preview_from_suggestion(kind: str, suggestion: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not suggestion or not suggestion.get("success"):
        return {
            "success": False,
            "kind": kind,
            "error": (suggestion or {}).get("error") or "无建议数据",
        }
    diff = suggestion.get("config_diff") or {}
    return {
        "success": bool(diff.get("success")),
        "kind": kind,
        "changes": diff.get("changes") or {},
        "patch": diff.get("patch") or {},
        "rationale": (diff.get("rationale") or suggestion.get("rationale") or [])[:5],
        "apply_note": diff.get("apply_note"),
        "target_file": diff.get("target_file") or "data/signal_config.json",
    }


def build_config_diff_preview(
    report: Optional[Dict[str, Any]] = None,
    *,
    weight_suggest: Optional[Dict[str, Any]] = None,
    threshold_suggest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """合并权重与 stance 阈值 diff 预览。"""
    ws = weight_suggest
    ts = threshold_suggest
    if report and not report.get("empty"):
        ws = ws or report.get("weight_suggest")
        ts = ts or report.get("threshold_suggest")

    weights = _preview_from_suggestion("weights", ws)
    thresholds = _preview_from_suggestion("stance_thresholds", ts)
    has_any = weights.get("success") or thresholds.get("success")

    return {
        "success": True,
        "ok": has_any,
        "readonly": True,
        "weights": weights,
        "thresholds": thresholds,
        "note": "预览仅供手动合并 signal_config.json；不自动写盘。",
    }


def export_config_diff_bundle(preview: Dict[str, Any]) -> Dict[str, Any]:
    """导出可下载的 diff 合并包（仍不自动写 signal_config.json）。"""
    if not preview or not preview.get("success"):
        return {"success": False, "error": "无 diff 预览数据"}

    weights = preview.get("weights") or {}
    thresholds = preview.get("thresholds") or {}
    merged_patch: Dict[str, Any] = {}
    if weights.get("success") and weights.get("patch"):
        merged_patch.update(weights["patch"])
    if thresholds.get("success") and thresholds.get("patch"):
        merged_patch.update(thresholds["patch"])

    if not merged_patch:
        return {
            "success": False,
            "error": "无有效 patch，可先跑 quant 日报或 weight/threshold 建议",
        }

    return {
        "success": True,
        "bundle_version": 1,
        "readonly": True,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "target_file": "data/signal_config.json",
        "filename": "signal_config_diff_bundle.json",
        "merged_patch": merged_patch,
        "changes": {
            "weights": weights.get("changes") or {},
            "stance_thresholds": thresholds.get("changes") or {},
        },
        "weights": weights,
        "thresholds": thresholds,
        "apply_note": "请手动合并 merged_patch 到 signal_config.json；须先做样本外验证。",
    }
