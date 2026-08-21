"""IC 契约标签（P1）：主 IC = 日频截面 Spearman；其它为辅并打 kind。

禁止把时序 Pearson、全样本回放 IC 与主 IC 混称为同一个「IC」。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Optional

# 主验收口径
PRIMARY_IC_KIND = "cs_spearman"
PRIMARY_IC_LABEL = "日频截面 Spearman"

IC_KIND_META: Dict[str, Dict[str, str]] = {
    "cs_spearman": {
        "label": "日频截面 Spearman",
        "role": "primary",
        "note": "每日横截面秩相关再对日平均；选股排序主验收。",
    },
    "cs_pearson": {
        "label": "日频截面 Pearson",
        "role": "aux",
        "note": "截面线性相关；辅看，易受极端值影响。",
    },
    "chrono_pearson": {
        "label": "时序拼样本 Pearson",
        "role": "aux",
        "note": "组 holdout ŷ vs y 拼时间序列；易被共同日冲击灌水，≠ 截面选股 IC。",
    },
    "chrono_spearman": {
        "label": "时序 Spearman",
        "role": "aux",
        "note": "单票/拼样本秩相关；辅看。",
    },
    "full_sample_replay": {
        "label": "全样本回放",
        "role": "display_only",
        "note": "概览展示用；不是 OOS，不可做人审 promote 唯一依据。",
    },
    "tau_spearman": {
        "label": "τ/rem Spearman",
        "role": "aux_tau",
        "note": "盘中剩余收益头；与 EOD 主 IC 分轨，勿并排成同一数字。",
    },
}


def annotate_ic_block(
    block: Optional[dict],
    *,
    kind: str,
    primary: bool = False,
) -> Dict[str, Any]:
    """给已有 IC 字典打上 kind / role / label。"""
    meta = IC_KIND_META.get(kind) or {
        "label": kind,
        "role": "unknown",
        "note": "",
    }
    out = dict(block or {})
    out["ic_kind"] = kind
    out["ic_label"] = meta["label"]
    out["ic_role"] = "primary" if primary else meta["role"]
    out["ic_note"] = meta.get("note") or ""
    out["is_primary_ic"] = bool(primary)
    return out


def primary_ic_from_cs_factor_row(row: dict) -> Dict[str, Any]:
    """从 factor_cs_ic 单行提取主 IC（优先 spearman）。"""
    spear = (row or {}).get("spearman") or {}
    pear = (row or {}).get("pearson") or {}
    ic = spear.get("ic_mean")
    icir = spear.get("icir")
    src = "spearman"
    if ic is None:
        ic = pear.get("ic_mean")
        icir = pear.get("icir")
        src = "pearson"
    kind = "cs_spearman" if src == "spearman" else "cs_pearson"
    return annotate_ic_block(
        {
            "ic": ic,
            "icir": icir,
            "day_count": spear.get("day_count") or pear.get("day_count"),
            "source_agg": src,
        },
        kind=kind,
        primary=(kind == PRIMARY_IC_KIND),
    )


def ic_contract_banner() -> Dict[str, Any]:
    return {
        "primary_ic_kind": PRIMARY_IC_KIND,
        "primary_ic_label": PRIMARY_IC_LABEL,
        "kinds": dict(IC_KIND_META),
        "note": "人审/选模看 primary；chrono_* 与 full_sample_replay 不得单独宣称模型有效。",
    }
