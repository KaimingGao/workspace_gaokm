"""因子经济族 + 数据源状态（研究枢纽分组表用）。

经济族以 ``signal_config.factor_groups`` 为准；未入五组的反转 / 舆情单独成族。
来源徽章：真源 / 代理 / 旁路。
"""


import logging

logger = logging.getLogger(__name__)
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.signal.factor_health import PROXY_OR_UNSOURCED

# id, 短标, 悬停说明
FAMILY_META: Tuple[Tuple[str, str, str], ...] = (
    ("trend", "趋势", "价格方向、均线、形态；OLS 趋势族共线只打这一组"),
    ("value_quality", "价值质量", "基本面：估值、盈利收益率、质量、成长、股息"),
    ("liquidity_flow", "流动性", "成交、冲击成本、资金流"),
    ("risk", "风险", "波动、跳空"),
    ("residual", "残差", "相对市场、特异动量、规模"),
    ("reversal", "反转", "短线反转，与动量拆开避免双计"),
    ("sentiment", "舆情", "研究旁路，默认不进 ŷ"),
    ("other", "其他", "未编入 factor_groups"),
)

FAMILY_LABELS: Dict[str, str] = {fid: lab for fid, lab, _ in FAMILY_META}
FAMILY_TIPS: Dict[str, str] = {fid: tip for fid, _, tip in FAMILY_META}
FAMILY_ORDER: Dict[str, int] = {fid: i for i, (fid, _, _) in enumerate(FAMILY_META)}

# 未进 factor_groups 的注册名
EXTRA_FAMILY: Dict[str, str] = {
    "reversal": "reversal",
    "alt_sentiment": "sentiment",
    "llm_sentiment": "sentiment",
}

SOURCE_LABELS: Dict[str, str] = {
    "sourced": "真源",
    "proxy": "代理",
    "prior_only": "旁路",
}

# 已退役 raw_basis 试点因子（旧分组报告可能仍带键；UI/读盘时剔除）
REMOVED_RAW_BASIS_NAMES: frozenset = frozenset(
    {
        "mom3_pct",
        "mom5_pct",
        "mom_overheat",
        "atr_pct_raw",
        "atr_pct_sq",
        "vol_elevated",
        "pe_raw",
        "value_fair",
        "value_expensive",
    }
)


def is_removed_factor(name: str) -> bool:
    return str(name or "").strip() in REMOVED_RAW_BASIS_NAMES


def _strip_factor_dict(d: Optional[dict]) -> None:
    if not isinstance(d, dict):
        return
    for k in list(d.keys()):
        if is_removed_factor(k):
            d.pop(k, None)


def _strip_factor_list(seq: Optional[Sequence[str]]) -> List[str]:
    if not seq:
        return []
    return [str(x) for x in seq if str(x).strip() and not is_removed_factor(x)]


def _strip_dropped_in_fingerprint(fp: Optional[dict]) -> None:
    if not isinstance(fp, dict):
        return
    dropped = fp.get("dropped")
    if isinstance(dropped, dict):
        for k, v in list(dropped.items()):
            if isinstance(v, list):
                dropped[k] = _strip_factor_list(v)


def _strip_return_model(rm: Optional[dict]) -> None:
    if not isinstance(rm, dict):
        return
    _strip_factor_dict(rm.get("coefficients"))
    _strip_factor_dict(rm.get("exclusion_reasons"))
    _strip_factor_dict(rm.get("z_means") or rm.get("zscore_means"))
    _strip_factor_dict(rm.get("z_stds") or rm.get("zscore_stds"))
    if isinstance(rm.get("excluded_features"), list):
        rm["excluded_features"] = _strip_factor_list(rm["excluded_features"])
    if isinstance(rm.get("active_features"), list):
        rm["active_features"] = _strip_factor_list(rm["active_features"])
    _strip_dropped_in_fingerprint(rm.get("sample_fingerprint"))


def _strip_code_map_entry(entry: Optional[dict]) -> None:
    if not isinstance(entry, dict):
        return
    _strip_return_model(entry.get("return_model"))
    _strip_factor_dict(entry.get("weights"))
    _strip_factor_dict(entry.get("coefficients"))


def _strip_one_cluster(cl: dict) -> None:
    if not isinstance(cl, dict):
        return
    ols = cl.get("ols")
    if isinstance(ols, dict):
        _strip_return_model(ols)
    ic = cl.get("factor_ic_panel")
    if isinstance(ic, dict):
        _strip_factor_dict(ic.get("exclusion_reasons"))
        src_key = (
            "rows"
            if isinstance(ic.get("rows"), list)
            else "factors"
            if isinstance(ic.get("factors"), list)
            else None
        )
        if src_key:
            ic[src_key] = [
                r
                for r in ic[src_key]
                if isinstance(r, dict)
                and not is_removed_factor(r.get("factor") or r.get("name"))
            ]
    _strip_return_model(cl.get("return_model"))
    ws = cl.get("weight_suggest")
    if isinstance(ws, dict):
        _strip_factor_dict(ws.get("coefficients"))
        _strip_factor_dict(ws.get("weights"))
    _strip_factor_dict(cl.get("weights"))


def strip_removed_factors_from_pool_artifact(art: dict) -> dict:
    """从组权/池产物剔除已退役 raw_basis 键（versioned weights / draft / history）。"""
    if not isinstance(art, dict):
        return art
    _strip_dropped_in_fingerprint(art.get("sample_fingerprint"))
    cm = art.get("code_map")
    if isinstance(cm, dict):
        for entry in cm.values():
            _strip_code_map_entry(entry)
    for cl in art.get("clusters") or []:
        _strip_one_cluster(cl)
    return art


def strip_removed_factors_from_cluster_report(report: dict) -> dict:
    """从分组报告剔除已退役 raw_basis 因子键（旧缓存/last_report 兼容）。"""
    if not isinstance(report, dict):
        return report
    for key in ("feature_names", "ic_feature_names"):
        if isinstance(report.get(key), list):
            report[key] = _strip_factor_list(report[key])
    for cl in report.get("clusters") or []:
        _strip_one_cluster(cl)
    pa = report.get("pool_artifact")
    if isinstance(pa, dict):
        strip_removed_factors_from_pool_artifact(pa)
    # 组权文档本身也可能被当成 report 形读入
    if isinstance(report.get("code_map"), dict):
        strip_removed_factors_from_pool_artifact(report)
    return report


def _default_factor_groups() -> Dict[str, List[str]]:
    try:
        from core.signal.config import load_signal_config

        cfg = load_signal_config() or {}
        groups = cfg.get("factor_groups") or {}
        if isinstance(groups, dict) and groups:
            return {str(k): [str(x) for x in (v or [])] for k, v in groups.items()}
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in factor_taxonomy.py", exc_info=True)
        pass
    from core.signal.config import DEFAULT_SIGNAL_CONFIG

    groups = DEFAULT_SIGNAL_CONFIG.get("factor_groups") or {}
    return {str(k): [str(x) for x in (v or [])] for k, v in groups.items()}


def _family_from_groups(
    name: str, factor_groups: Dict[str, Sequence[str]]
) -> Optional[str]:
    for fid, members in (factor_groups or {}).items():
        for m in members or []:
            if str(m) == name:
                return str(fid)
    return None


def classify_factor(
    name: str,
    *,
    factor_groups: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, Any]:
    """单个因子的族 / 来源。未知名仍返回 other + sourced。"""
    key = str(name or "").strip()
    groups = (
        {str(k): list(v or []) for k, v in factor_groups.items()}
        if factor_groups is not None
        else _default_factor_groups()
    )

    family = EXTRA_FAMILY.get(key) or _family_from_groups(key, groups) or "other"
    pm = PROXY_OR_UNSOURCED.get(key) or {}
    if pm:
        source = str(pm.get("status") or "proxy")
        source_note = str(pm.get("note") or "")
    else:
        source = "sourced"
        source_note = ""

    if family not in FAMILY_ORDER:
        family = "other"
    if source not in SOURCE_LABELS:
        source = "sourced"

    return {
        "name": key,
        "family": family,
        "family_label": FAMILY_LABELS[family],
        "family_tip": FAMILY_TIPS[family],
        "family_order": FAMILY_ORDER[family],
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "source_note": source_note,
        "sourced": source == "sourced",
    }


def classify_factors(
    names: Iterable[str],
    *,
    factor_groups: Optional[Dict[str, Sequence[str]]] = None,
) -> Dict[str, Dict[str, Any]]:
    groups = factor_groups
    if groups is None:
        groups = _default_factor_groups()
    out: Dict[str, Dict[str, Any]] = {}
    for n in names:
        key = str(n or "").strip()
        if not key:
            continue
        out[key] = classify_factor(key, factor_groups=groups)
    return out


def attach_taxonomy(row: dict, *, factor_groups: Optional[Dict[str, Sequence[str]]] = None) -> dict:
    """把分类字段并进面板行（不覆盖已有非空 family）。"""
    name = str((row or {}).get("factor") or (row or {}).get("name") or "").strip()
    tax = classify_factor(name, factor_groups=factor_groups)
    merged = dict(row or {})
    if not merged.get("family"):
        merged.update(
            {
                "family": tax["family"],
                "family_label": tax["family_label"],
                "family_tip": tax["family_tip"],
                "family_order": tax["family_order"],
                "source": tax["source"],
                "source_label": tax["source_label"],
                "source_note": tax["source_note"],
            }
        )
    return merged
