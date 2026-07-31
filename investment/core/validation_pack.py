"""策略验证包导出（V4.1）：配置快照 + 回测摘要 + 源审计 + 成本 + 暴露 + 指纹。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Optional


def _fp(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_validation_pack(
    *,
    backtest_result: Optional[dict] = None,
    signal_config: Optional[dict] = None,
    north_star: Optional[dict] = None,
    sample_status: Optional[dict] = None,
    ops_report: Optional[dict] = None,
    exposure: Optional[dict] = None,
    risk_blocks: Optional[dict] = None,
    ab_compare: Optional[dict] = None,
    note: str = "",
) -> Dict[str, Any]:
    bt = dict(backtest_result or {})
    cfg = dict(signal_config or {})
    # 瘦身：去掉超大 trades 全量
    slim_bt = {
        k: bt.get(k)
        for k in (
            "success",
            "metrics",
            "params",
            "cost_compare",
            "source_audit",
            "pit_report",
            "oos_summary",
            "regime_summary",
            "wf_slices",
            "attribution",
            "signal_fill_sample",
            "data_quality",
            "skipped_limit",
            "skipped_limit_exit",
            "score_ic",
        )
        if k in bt
    }
    if bt.get("equity_curve"):
        slim_bt["equity_curve_tail"] = (bt.get("equity_curve") or [])[-30:]
        slim_bt["equity_curve_len"] = len(bt.get("equity_curve") or [])

    # S2.1：暴露 / 风控摘要
    exp = exposure
    if exp is None and isinstance(ops_report, dict):
        exp = ops_report.get("exposure") or ops_report.get("exposure_matrix")
    rb = risk_blocks
    if rb is None and isinstance(sample_status, dict):
        rb = sample_status.get("risk_blocks")
    if rb is None and isinstance(ops_report, dict):
        rb = {
            "block_count": ops_report.get("risk_block_count"),
            "labeled_count": (ops_report.get("risk_blocks") or {}).get("labeled_count")
            if isinstance(ops_report.get("risk_blocks"), dict)
            else None,
        }

    pack = {
        "version": 2,
        "kind": "strategy_validation_pack",
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "note": note
        or "策略验证包：供第三人复跑关键结论；demo/seeded 样本见 sample_status.discipline",
        "signal_config": cfg,
        "backtest": slim_bt,
        "north_star": north_star,
        "sample_status": sample_status,
        "ops_report": ops_report,
        "exposure": exp,
        "risk_blocks": rb,
        "ab_compare": ab_compare,
        "fit_gap": None,
    }
    try:
        from core.fit_gap import fit_gap_hints

        pack["fit_gap"] = fit_gap_hints(
            realization=(north_star or {}).get("realization"),
            cost_compare=slim_bt.get("cost_compare"),
            source_audit=slim_bt.get("source_audit"),
            paper_ops=ops_report,
            backtest_params=slim_bt.get("params") or {},
        )
    except Exception:
        pass

    pack["fingerprint"] = _fp(
        {
            "config": cfg.get("weights"),
            "metrics": (slim_bt.get("metrics") or {}),
            "exported_at": pack["exported_at"],
        }
    )
    return {"ok": True, "pack": pack}


def render_validation_pack_markdown(pack: dict) -> str:
    p = pack.get("pack") if "pack" in pack else pack
    lines = [
        f"# 策略验证包",
        f"",
        f"- 导出时间：`{p.get('exported_at')}`",
        f"- 指纹：`{p.get('fingerprint')}`",
        f"- 说明：{p.get('note')}",
        f"",
        f"## 成本对照",
    ]
    cc = (p.get("backtest") or {}).get("cost_compare") or {}
    if cc:
        lines.append(
            f"- return_gap_pp：`{cc.get('return_gap_pp')}` · avg_impact_bps：`{cc.get('avg_impact_bps')}`"
        )
    else:
        lines.append("- （无）")
    lines.append("")
    lines.append("## 源审计")
    sa = (p.get("backtest") or {}).get("source_audit") or {}
    lines.append(
        f"- status：`{sa.get('status')}` · fallback：`{sa.get('fallback_count')}`"
    )
    lines.append("")
    lines.append("## 拟合落差提示")
    for h in ((p.get("fit_gap") or {}).get("hints") or []):
        lines.append(f"- [{h.get('level')}] {h.get('message')}")
    lines.append("")
    lines.append("## 样本纪律")
    disc = (p.get("sample_status") or {}).get("discipline") or {}
    for w in disc.get("warnings") or []:
        lines.append(f"- {w}")
    lines.append("")
    lines.append("## Metrics")
    metrics = (p.get("backtest") or {}).get("metrics") or {}
    for k in sorted(metrics.keys()):
        lines.append(f"- `{k}`: {metrics[k]}")
    lines.append("")
    return "\n".join(lines)
