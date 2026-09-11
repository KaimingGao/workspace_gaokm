"""分组拟合三档：A 强 / B 中 / C 弱。

研究展示 + live/回测宇宙过滤。
A：OOS 过门且截面 IC、ICIR > 0 且 ŷOOS>0
B：OOS 过门但未达 A
C：未过 / 跳过 / 单票 / 离群 / 无模型
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

FIT_TIER_LABELS = {"A": "强", "B": "中", "C": "弱"}
ALL_FIT_TIERS = ("A", "B", "C")
DEFAULT_UNIVERSE_FIT_TIERS = ["A", "B", "C"]
FIT_TIER_NOTE = (
    "A 强=OOS过门且截面 IC/ICIR>0 且 ŷOOS>0；"
    "B 中=过门未达强；"
    "C 弱=未过/跳过/单票/无模型。"
)
UNIVERSE_FIT_TIER_NOTE = (
    "观察池按拟合档过滤宇宙：只对入选档开/加仓；已持仓仍可卖/持。"
    "未映射票在未选满 A+B+C 时不进新买。"
)


def _finite(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def _ic_from_block(block: Any) -> Tuple[Optional[float], Optional[float]]:
    if not isinstance(block, dict):
        return None, None
    ic = _finite(block.get("ic"))
    if ic is None:
        ic = _finite(block.get("ic_mean"))
    return ic, _finite(block.get("icir"))


def cluster_score_ic(cl: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """组截面 ŷ IC / ICIR：优先 score_ic Spearman，再 Pearson，再 rows 均值。"""
    panel = cl.get("factor_ic_panel") if isinstance(cl.get("factor_ic_panel"), dict) else {}
    score = panel.get("score_ic") if isinstance(panel.get("score_ic"), dict) else {}
    spear = score.get("spearman") if isinstance(score.get("spearman"), dict) else None
    pear = score.get("pearson") if isinstance(score.get("pearson"), dict) else None
    ic, icir = _ic_from_block(spear)
    if ic is None and icir is None:
        ic, icir = _ic_from_block(pear)
    if ic is not None and icir is not None:
        return ic, icir
    rows = panel.get("rows") if isinstance(panel.get("rows"), list) else []
    ics: List[float] = []
    icirs: List[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        r_ic, r_icir = _ic_from_block(row)
        if r_ic is not None:
            ics.append(r_ic)
        if r_icir is not None:
            icirs.append(r_icir)
    if ic is None and ics:
        ic = sum(ics) / len(ics)
    if icir is None and icirs:
        icir = sum(icirs) / len(icirs)
    return ic, icir


def cluster_yhat_oos(gate: Dict[str, Any]) -> Optional[float]:
    res = gate.get("research") if isinstance(gate.get("research"), dict) else None
    if not isinstance(res, dict):
        res = gate.get("suggested") if isinstance(gate.get("suggested"), dict) else {}
    oos = res.get("oos") if isinstance(res.get("oos"), dict) else {}
    return _finite(oos.get("oos_return_pct"))


def _pack(tier: str, reason: str) -> Dict[str, str]:
    t = tier if tier in FIT_TIER_LABELS else "C"
    return {
        "fit_tier": t,
        "fit_tier_label": FIT_TIER_LABELS[t],
        "fit_tier_reason": str(reason or ""),
    }


def classify_cluster_fit_tier(cl: Any) -> Dict[str, str]:
    """单组分档（绝对门槛，非组间三分位）。"""
    if not isinstance(cl, dict):
        return _pack("C", "invalid_cluster")

    members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
    n = cl.get("member_count")
    try:
        n_mem = int(n) if n is not None else len(members)
    except (TypeError, ValueError):
        n_mem = len(members)

    if cl.get("outlier_singleton"):
        return _pack("C", "outlier_singleton")
    if cl.get("singleton") or n_mem < 2:
        return _pack("C", "singleton")

    rm = cl.get("return_model")
    if not isinstance(rm, dict) or not rm.get("coefficients"):
        return _pack("C", "no_return_model")

    gate = cl.get("oos_gate") if isinstance(cl.get("oos_gate"), dict) else {}
    if not gate:
        return _pack("C", "oos_missing")
    if gate.get("skipped"):
        return _pack("C", str(gate.get("reason") or "oos_skipped"))
    passed = bool(gate.get("ok")) and bool(gate.get("passed"))
    if not passed:
        return _pack("C", str(gate.get("reason") or "oos_failed"))

    ic, icir = cluster_score_ic(cl)
    yhat = cluster_yhat_oos(gate)
    ic_ok = ic is not None and ic > 0
    icir_ok = icir is not None and icir > 0
    y_ok = yhat is not None and yhat > 0
    if ic_ok and icir_ok and y_ok:
        return _pack("A", "oos_passed_ic_icir_yhat_positive")

    bits: List[str] = []
    if not ic_ok:
        bits.append("ic_nonpositive" if ic is not None else "ic_missing")
    if not icir_ok:
        bits.append("icir_nonpositive" if icir is not None else "icir_missing")
    if not y_ok:
        bits.append("yhat_oos_nonpositive" if yhat is not None else "yhat_oos_missing")
    return _pack("B", "oos_passed;" + ",".join(bits))


def attach_cluster_fit_tiers(
    report: Dict[str, Any],
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """就地为 ``report["clusters"]`` 挂 ``fit_tier`` / label / reason，并写汇总。

    ``force=False``：已有 A/B/C 则保留（live 瘦产物缺 IC，避免把 A 算成 B）。
    """
    clusters = list(report.get("clusters") or [])
    counts = {"A": 0, "B": 0, "C": 0}
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        tagged = str(cl.get("fit_tier") or "").strip().upper()
        if not force and tagged in FIT_TIER_LABELS:
            cl["fit_tier"] = tagged
            cl["fit_tier_label"] = (
                str(cl.get("fit_tier_label") or "").strip() or FIT_TIER_LABELS[tagged]
            )
            cl["fit_tier_reason"] = str(cl.get("fit_tier_reason") or "")
            counts[tagged] += 1
            continue
        info = classify_cluster_fit_tier(cl)
        cl["fit_tier"] = info["fit_tier"]
        cl["fit_tier_label"] = info["fit_tier_label"]
        cl["fit_tier_reason"] = info["fit_tier_reason"]
        counts[info["fit_tier"]] += 1
    report["clusters"] = clusters
    report["fit_tier_summary"] = {
        "A": counts["A"],
        "B": counts["B"],
        "C": counts["C"],
        "n": len(clusters),
        "note": FIT_TIER_NOTE,
    }
    return report


def normalize_universe_fit_tiers(raw: Any) -> List[str]:
    """解析宇宙分档：空 / all / 满三档 = 不过滤。至少一档。"""
    if raw is None or raw is False:
        return list(DEFAULT_UNIVERSE_FIT_TIERS)
    if isinstance(raw, str):
        s = raw.strip().upper().replace(" ", "")
        if not s or s in ("ALL", "*", "ABC"):
            return list(DEFAULT_UNIVERSE_FIT_TIERS)
        parts = [p for p in s.replace(";", ",").split(",") if p]
        raw = parts
    if not isinstance(raw, (list, tuple, set)):
        return list(DEFAULT_UNIVERSE_FIT_TIERS)
    out: List[str] = []
    seen = set()
    for item in raw:
        t = str(item or "").strip().upper()
        if t in ("STRONG", "强"):
            t = "A"
        elif t in ("MID", "MIDD", "中"):
            t = "B"
        elif t in ("WEAK", "弱"):
            t = "C"
        if t not in FIT_TIER_LABELS or t in seen:
            continue
        seen.add(t)
        out.append(t)
    if not out:
        return list(DEFAULT_UNIVERSE_FIT_TIERS)
    return [t for t in ALL_FIT_TIERS if t in seen]


def universe_fit_tiers_unrestricted(tiers: Sequence[str]) -> bool:
    return set(str(t).upper() for t in (tiers or [])) >= set(ALL_FIT_TIERS)


def _tier_from_meta(meta: Any) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    t = str(meta.get("fit_tier") or "").strip().upper()
    return t if t in FIT_TIER_LABELS else None


def _stored_code_fit_tiers(art: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """只读已落盘 fit_tier，不按瘦 OOS 重算。"""
    out: Dict[str, str] = {}
    if not isinstance(art, dict):
        return out
    by_label: Dict[str, str] = {}
    for cl in art.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        t = str(cl.get("fit_tier") or "").strip().upper()
        if t not in FIT_TIER_LABELS:
            continue
        lab = str(cl.get("label") or cl.get("cluster_label") or "").strip()
        if lab:
            by_label[lab] = t
        for m in cl.get("members") or []:
            c = str(m).strip()
            if c:
                out[c] = t
    cmap = art.get("code_map") if isinstance(art.get("code_map"), dict) else {}
    for code, meta in cmap.items():
        c = str(code).strip()
        if not c:
            continue
        t = _tier_from_meta(meta)
        if t:
            out[c] = t
            continue
        if c in out:
            continue
        lab = ""
        if isinstance(meta, dict):
            lab = str(meta.get("cluster_label") or meta.get("label") or "").strip()
        if lab and lab in by_label:
            out[c] = by_label[lab]
    return out


def code_fit_tier_map_from_artifact(art: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """code → A/B/C。有存档则用存档，缺的再 classify。"""
    if not isinstance(art, dict):
        return {}
    stored = _stored_code_fit_tiers(art)
    attach_cluster_fit_tiers(art, force=False)
    out = {}
    for cl in art.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        t = str(cl.get("fit_tier") or "").strip().upper()
        if t not in FIT_TIER_LABELS:
            continue
        for m in cl.get("members") or []:
            c = str(m).strip()
            if c:
                out[c] = t
    cmap = art.get("code_map") if isinstance(art.get("code_map"), dict) else {}
    for code, meta in cmap.items():
        c = str(code).strip()
        if not c:
            continue
        t = _tier_from_meta(meta)
        if t:
            out[c] = t
    out.update(stored)
    return out


def _overlay_last_report_tiers(
    out: Dict[str, str], last_map: Dict[str, str]
) -> Dict[str, str]:
    """last_report 补缺；含 IC 的 A 不被瘦产物（缺 IC 常标成 B）覆盖。"""
    for code, t in (last_map or {}).items():
        prev = out.get(code)
        if prev is None or t == "A":
            out[code] = t
    return out


def load_code_fit_tier_map(*, prefer_research: bool = False) -> Dict[str, str]:
    """回测优先研究套；live 用执行套。last_report 补缺（含 IC 的 A 不被瘦产物盖成 B）。"""
    live = None
    research = None
    last = None
    try:
        from core.signal.cluster.live import (
            load_active_cluster_weights,
            load_research_cluster_weights,
        )

        live = load_active_cluster_weights()
        if prefer_research:
            research = load_research_cluster_weights()
    except Exception:
        pass
    try:
        from core.signal.cluster.job_hydrate import load_latest_cluster_report

        last = load_latest_cluster_report()
    except Exception:
        pass
    out: Dict[str, str] = {}
    if isinstance(live, dict):
        out.update(_stored_code_fit_tiers(live))
    if prefer_research and isinstance(research, dict):
        stored_r = _stored_code_fit_tiers(research)
        if stored_r:
            out.update(stored_r)
        else:
            out.update(code_fit_tier_map_from_artifact(research))
    if isinstance(last, dict):
        _overlay_last_report_tiers(out, code_fit_tier_map_from_artifact(last))
    return out


def filter_codes_by_fit_tiers(
    codes: Sequence[str],
    *,
    tiers: Any = None,
    keep: Iterable[str] = (),
    prefer_research: bool = False,
    code_tiers: Optional[Dict[str, str]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """宇宙过滤：入选档保留；``keep``（已持仓）始终留下。"""
    allowed = normalize_universe_fit_tiers(tiers)
    cleaned = [str(c).strip() for c in (codes or []) if str(c).strip()]
    keep_set = {str(c).strip() for c in (keep or []) if str(c).strip()}
    unrestricted = universe_fit_tiers_unrestricted(allowed)
    meta: Dict[str, Any] = {
        "universe_fit_tiers": allowed,
        "unrestricted": unrestricted,
        "n_in": len(cleaned),
        "n_kept": len(cleaned),
        "n_dropped": 0,
        "n_kept_held": 0,
        "n_unmapped": 0,
        "note": UNIVERSE_FIT_TIER_NOTE,
    }
    if unrestricted:
        return list(cleaned), meta
    mapping = code_tiers if isinstance(code_tiers, dict) else load_code_fit_tier_map(
        prefer_research=prefer_research
    )
    kept: List[str] = []
    dropped = 0
    held_extra = 0
    unmapped = 0
    n_by_tier = {k: 0 for k in ALL_FIT_TIERS}
    seen = set()
    for c in cleaned:
        if c in seen:
            continue
        seen.add(c)
        t = mapping.get(c)
        if t in n_by_tier:
            n_by_tier[t] += 1
        if t in allowed:
            kept.append(c)
            continue
        if c in keep_set:
            kept.append(c)
            held_extra += 1
            continue
        if t is None:
            unmapped += 1
        dropped += 1
    for c in keep_set:
        if c not in seen:
            kept.append(c)
            held_extra += 1
            seen.add(c)
    meta["n_kept"] = len(kept)
    meta["n_dropped"] = dropped
    meta["n_kept_held"] = held_extra
    meta["n_unmapped"] = unmapped
    meta["n_mapped"] = len(mapping)
    meta["n_by_tier"] = n_by_tier
    return kept, meta
