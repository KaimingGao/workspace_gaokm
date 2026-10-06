"""因子权重微调建议：截面 IC/ICIR 优先，OLS / 近零降权回退（研究只读）。"""


import logging

logger = logging.getLogger(__name__)
import math
from typing import Any, Dict, List, Optional, Tuple

from core.signal.config import load_signal_config
from core.signal.factors.meta.registry import list_factors

DEFAULT_FACTOR_NAMES = {f["name"] for f in list_factors()}


def _ols_coefficients(ols_report: Optional[Dict[str, Any]]) -> Dict[str, float]:
    if not ols_report or not ols_report.get("success"):
        return {}
    coefs = ols_report.get("coefficients") or {}
    out: Dict[str, float] = {}
    for k, v in coefs.items():
        if k in ("intercept", "_intercept", "const"):
            continue
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _ic_index(
    factor_experiment: Dict[str, Any],
) -> Dict[str, Tuple[Optional[float], int, Optional[float]]]:
    """factor → (ic|None, sample_count, icir|None)。"""
    idx: Dict[str, Tuple[Optional[float], int, Optional[float]]] = {}
    for row in factor_experiment.get("factors") or []:
        fac = row.get("factor")
        if not fac or fac == "score":
            continue
        n = int(row.get("sample_count") or 0)
        ic = row.get("ic")
        if ic is None and isinstance(row.get("pearson"), dict):
            ic = row["pearson"].get("ic_mean")
        icir = row.get("icir")
        if icir is None and isinstance(row.get("pearson"), dict):
            icir = row["pearson"].get("icir")
        try:
            ic_f = float(ic) if ic is not None else None
        except (TypeError, ValueError):
            ic_f = None
        try:
            icir_f = float(icir) if icir is not None else None
        except (TypeError, ValueError):
            icir_f = None
        if icir_f is not None and (math.isnan(icir_f) or math.isinf(icir_f)):
            icir_f = None
        idx[str(fac)] = (ic_f, n, icir_f)
    return idx


def _strong_ic_step(
    ic_val: float,
    icir: Optional[float],
    *,
    max_delta: float,
    min_ic: float,
    min_icir: float,
    require_icir: bool,
) -> Optional[float]:
    """强证据步长；不满足则返回 None。"""
    if abs(ic_val) < min_ic:
        return None
    if icir is not None:
        if abs(icir) < min_icir:
            return None
        scale = max(0.5, min(1.5, abs(icir) / 0.5))
    else:
        if require_icir:
            return None
        scale = 1.0
    mag = float(max_delta) * scale
    return mag if ic_val > 0 else -mag


def apply_group_caps(
    weights: Dict[str, float],
    factor_groups: Optional[Dict[str, List[str]]] = None,
    *,
    max_group_share: float = 0.45,
) -> Tuple[Dict[str, float], List[str]]:
    """组内权重上限：超限组压缩；剩余权重补给未满组；最后归一。"""
    w = {k: max(0.0, float(v)) for k, v in (weights or {}).items()}
    warnings: List[str] = []
    groups = {str(g): list(ms or []) for g, ms in (factor_groups or {}).items()}
    cap = max(0.15, min(0.8, float(max_group_share or 0.45)))

    def _group_sum(members: List[str]) -> float:
        return sum(w.get(m, 0.0) for m in members if m in w)

    for gname, members in groups.items():
        keys = [m for m in members if m in w]
        gsum = _group_sum(keys)
        if gsum > cap + 1e-9:
            scale = cap / gsum
            for k in keys:
                w[k] *= scale
            warnings.append(
                f"组 {gname} 权重和 {gsum:.3f} > {cap:.2f}，已压缩至上限"
            )

    total = sum(w.values())
    slack = 1.0 - total
    if slack > 1e-9:
        # 补给仍低于上限的组（按组内现有权重比例）
        receivers: List[str] = []
        headroom: Dict[str, float] = {}
        for members in groups.values():
            keys = [m for m in members if m in w]
            if not keys:
                continue
            gsum = _group_sum(keys)
            room = max(0.0, cap - gsum)
            if room <= 1e-12:
                continue
            for k in keys:
                receivers.append(k)
                headroom[k] = headroom.get(k, 0.0) + room * (
                    (w[k] / gsum) if gsum > 0 else 1.0 / len(keys)
                )
        if not receivers:
            receivers = list(w.keys())
            for k in receivers:
                headroom[k] = 1.0
        hsum = sum(headroom.get(k, 0.0) for k in receivers) or float(len(receivers))
        for k in receivers:
            w[k] = w.get(k, 0.0) + slack * (headroom.get(k, 0.0) / hsum)

    # 再硬卡一次组上限（补给后可能贴边）
    for gname, members in groups.items():
        keys = [m for m in members if m in w]
        gsum = _group_sum(keys)
        if gsum > cap + 1e-9:
            scale = cap / gsum
            for k in keys:
                w[k] *= scale

    total2 = sum(w.values()) or 1.0
    w = {k: round(v / total2, 3) for k, v in w.items()}
    # round 后再微压超限组
    for members in groups.values():
        keys = [m for m in members if m in w]
        gsum = sum(w[k] for k in keys)
        if gsum > cap + 1e-9 and gsum > 0:
            scale = cap / gsum
            for k in keys:
                w[k] = round(w[k] * scale, 3)
    total3 = sum(w.values()) or 1.0
    if abs(total3 - 1.0) > 1e-6:
        # 差额塞进任意未满组的最大头寸
        diff = round(1.0 - total3, 3)
        for members in groups.values():
            keys = [m for m in members if m in w]
            if keys and sum(w[k] for k in keys) + 1e-9 < cap:
                top = max(keys, key=lambda x: w[x])
                w[top] = round(w[top] + diff, 3)
                break
    return w, warnings


def suggest_weights_from_ic(
    factor_experiment: Dict[str, Any],
    *,
    current_weights: Optional[Dict[str, float]] = None,
    max_delta: float = 0.03,
    min_ic: float = 0.03,
    min_samples: int = 8,
    min_icir: float = 0.25,
    require_icir: bool = False,
    ols_report: Optional[Dict[str, Any]] = None,
    min_ols_beta: float = 0.05,
    ols_delta: float = 0.02,
    ols_scale_by_beta: bool = False,
    ols_scale_cap: float = 2.0,
    prefer_ols: bool = False,
    weak_ic_decay: float = 0.015,
    corr_report: Optional[Dict[str, Any]] = None,
    corr_threshold: float = 0.7,
    ic_mode: str = "single",
    max_group_share: float = 0.45,
    freeze_zero_weights: bool = True,
) -> Dict[str, Any]:
    """权重微调建议（不自动写配置）。

    默认优先级（对每个在 config 中的因子）：
    1. 强 IC（及可选 ICIR 门槛）→ ±max_delta × ICIR 缩放
    2. 否则 OLS β → ±ols_delta（可选按 |β| 放大）
    3. 否则近零 / 未过门槛的 IC → −weak_ic_decay

    ``prefer_ols=True`` 时改为 β 优先：先 OLS，无可用 β 再走 IC / 弱 IC 降权。
    随后：冻结原权重为 0 的因子、组内上限、归一化。
    """
    cfg = load_signal_config()
    base = dict(current_weights or cfg.get("weights") or {})
    if not base:
        return {"success": False, "error": "无当前权重配置"}

    mode = str(ic_mode or "single").strip().lower()
    if mode in ("cs_ic", "cross_section", "cs"):
        require_icir = True
    scale_cap = max(1.0, min(float(ols_scale_cap or 2.0), 5.0))

    ic_map = _ic_index(factor_experiment)
    ols_coefs = _ols_coefficients(ols_report)
    deltas = dict.fromkeys(base, 0.0)
    rationale: List[str] = []
    sources: Dict[str, str] = {}

    def _apply_ols_step(key: str, beta: float, ic_val: Optional[float], icir: Optional[float]) -> None:
        mag = float(ols_delta)
        if ols_scale_by_beta and min_ols_beta > 0:
            mag = mag * min(
                scale_cap,
                max(0.75, abs(float(beta)) / float(min_ols_beta)),
            )
        # prefer_ols：同向强 IC 可再加一点确认步长（不超过 max_delta 的 40%）
        if prefer_ols and ic_val is not None and abs(float(ic_val)) >= float(min_ic):
            if (float(ic_val) > 0) == (float(beta) > 0):
                mag = min(float(max_delta) * 1.2, mag + 0.4 * float(max_delta))
        step = mag if beta > 0 else -mag
        deltas[key] += step
        sources[key] = "ols"
        ic_note = (
            f"IC={ic_val:+.4f}" if ic_val is not None else "IC 缺测/常数"
        )
        if icir is not None:
            ic_note += f" ICIR={icir:+.3f}"
        rationale.append(
            f"{key} {ic_note} · OLS β={beta:+.4f} → 建议{'提高' if step > 0 else '降低'} "
            f"权重 {step:+.3f}（{'OLS 优先' if prefer_ols else 'OLS 回退'}"
            f"{'·按β放大' if ols_scale_by_beta else ''}）"
        )

    for key in base:
        if freeze_zero_weights and abs(float(base.get(key) or 0.0)) < 1e-12:
            continue
        ic_val, n, icir = ic_map.get(key, (None, 0, None))
        beta = ols_coefs.get(key)
        has_ols = beta is not None and abs(float(beta)) >= float(min_ols_beta)

        if prefer_ols and has_ols:
            _apply_ols_step(key, float(beta), ic_val, icir)
            continue

        if (not prefer_ols) and ic_val is not None and n >= min_samples:
            step = _strong_ic_step(
                ic_val,
                icir,
                max_delta=max_delta,
                min_ic=min_ic,
                min_icir=min_icir,
                require_icir=require_icir,
            )
            if step is not None:
                deltas[key] += step
                sources[key] = "cs_ic" if mode in ("cs_ic", "cross_section", "cs") else "ic"
                icir_note = f" ICIR={icir:+.3f}" if icir is not None else ""
                rationale.append(
                    f"{key} IC={ic_val:+.4f}{icir_note}（n={n}）→ 建议"
                    f"{'提高' if step > 0 else '降低'} 权重 {step:+.3f}"
                    f"（{'截面 IC/ICIR' if sources[key] == 'cs_ic' else 'IC'}）"
                )
                continue

        if (not prefer_ols) and has_ols:
            _apply_ols_step(key, float(beta), ic_val, icir)
            continue

        if ic_val is not None and n >= min_samples:
            # prefer_ols 且无 β 时：强 IC 仍可用；弱 IC 才降权
            if prefer_ols:
                step = _strong_ic_step(
                    ic_val,
                    icir,
                    max_delta=max_delta,
                    min_ic=min_ic,
                    min_icir=min_icir,
                    require_icir=require_icir,
                )
                if step is not None:
                    deltas[key] += step
                    sources[key] = "ic"
                    rationale.append(
                        f"{key} IC={ic_val:+.4f}（n={n}）→ 建议"
                        f"{'提高' if step > 0 else '降低'} 权重 {step:+.3f}"
                        f"（无可用 β · IC 补位）"
                    )
                    continue
            step = -abs(weak_ic_decay)
            deltas[key] += step
            sources[key] = "weak_ic_decay"
            why = "接近 0"
            if abs(ic_val) >= min_ic and icir is not None and abs(icir) < min_icir:
                why = f"ICIR 弱({icir:+.3f})"
            rationale.append(
                f"{key} IC={ic_val:+.4f}（n={n}，{why}）→ 建议降低权重 {step:+.3f}（证据不足）"
            )

    suggested: Dict[str, float] = {}
    frozen: List[str] = []
    for k, w in base.items():
        if freeze_zero_weights and abs(float(w)) < 1e-12:
            suggested[k] = 0.0
            frozen.append(k)
            continue
        floor = 0.0 if float(w) < 0.05 else 0.02
        suggested[k] = max(floor, min(0.60, float(w) + deltas.get(k, 0.0)))

    if frozen:
        rationale.append(
            f"冻结生产权重为 0 的因子（不复活）：{', '.join(frozen)}"
        )

    total = sum(suggested.values()) or 1.0
    suggested = {k: round(v / total, 3) for k, v in suggested.items()}
    # 再冻一次，避免归一化把 0 抬起来
    for k in frozen:
        suggested[k] = 0.0
    total = sum(suggested.values()) or 1.0
    suggested = {k: round(v / total, 3) for k, v in suggested.items()}
    for k in frozen:
        suggested[k] = 0.0

    group_warnings: List[str] = []
    suggested, group_warnings = apply_group_caps(
        suggested,
        cfg.get("factor_groups") or {},
        max_group_share=max_group_share,
    )
    for k in frozen:
        suggested[k] = 0.0
    total = sum(suggested.values()) or 1.0
    if frozen:
        suggested = {
            k: (0.0 if k in frozen else round(v / total, 3)) for k, v in suggested.items()
        }
        total = sum(suggested.values()) or 1.0
        suggested = {
            k: (0.0 if k in frozen else round(v / total, 3)) for k, v in suggested.items()
        }

    from core.signal.factors.meta.corr import redundancy_warnings_from_corr

    redundancy_warnings = redundancy_warnings_from_corr(
        corr_report or {},
        threshold=corr_threshold,
        factor_groups=cfg.get("factor_groups") or {},
        weights=suggested,
    )
    constraint_warnings = list(group_warnings)
    # 与 live regime 白名单对照（软提示）
    live_core = {
        "momentum",
        "volume_price",
        "relative_strength",
        "volatility",
        "reversal",
        "liquidity",
        "value",
        "quality",
    }
    boosted_research = [
        k
        for k, src in sources.items()
        if src in ("cs_ic", "ic", "ols")
        and k not in live_core
        and float(suggested.get(k) or 0) > float(base.get(k) or 0) + 1e-6
    ]
    if boosted_research:
        constraint_warnings.append(
            "上调了 regime 核心白名单外的因子："
            + ", ".join(boosted_research)
            + "（live 择时收紧时可能不算分）"
        )

    return {
        "success": True,
        "ic_mode": mode,
        "current_weights": {k: round(float(v), 3) for k, v in base.items()},
        "suggested_weights": suggested,
        "deltas": {k: round(deltas.get(k, 0.0), 3) for k in base},
        "delta_sources": sources,
        "rationale": rationale,
        "redundancy_warnings": redundancy_warnings,
        "constraint_warnings": constraint_warnings,
        "frozen_zero_factors": frozen,
        "factor_groups": cfg.get("factor_groups") or {},
        "ols_used": bool(ols_coefs),
        "params": {
            "max_delta": max_delta,
            "min_ic": min_ic,
            "min_samples": min_samples,
            "min_icir": min_icir,
            "require_icir": require_icir,
            "min_ols_beta": min_ols_beta,
            "ols_delta": ols_delta,
            "ols_scale_by_beta": bool(ols_scale_by_beta),
            "ols_scale_cap": scale_cap,
            "prefer_ols": bool(prefer_ols),
            "weak_ic_decay": weak_ic_decay,
            "corr_threshold": corr_threshold,
            "max_group_share": max_group_share,
            "freeze_zero_weights": freeze_zero_weights,
        },
        "note": (
            (
                "建议仅供研究：分组路径 OLS β 优先（可按 |β| 放大）；无 β 时 IC 补位；"
                if prefer_ols
                else "建议仅供研究：截面 IC/ICIR 优先；弱证据回退 OLS；"
            )
            + "组内上限与零权冻结已施加。须 OOS 门禁 / 人审后再改配置。"
        ),
    }


def format_weight_config_diff(suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """生成可下载的 signal_config 权重 diff（不自动写盘）。"""
    from datetime import datetime

    if not suggestion.get("success"):
        return {"success": False, "error": suggestion.get("error") or "无效权重建议"}

    current = suggestion.get("current_weights") or {}
    suggested = suggestion.get("suggested_weights") or {}
    changes: Dict[str, dict] = {}
    for key, cur in current.items():
        c = float(cur)
        s = float(suggested.get(key, c))
        delta = round(s - c, 3)
        if abs(delta) >= 0.001:
            changes[key] = {"from": round(c, 3), "to": round(s, 3), "delta": delta}

    oos_gate = suggestion.get("oos_gate") or {}
    promote_ready = bool(suggestion.get("promote_ready"))
    factor_health = None
    try:
        from core.signal.factors.meta.health import assess_factor_health

        factor_health = assess_factor_health(
            config={"weights": suggestion.get("suggested_weights") or suggested}
        )
        if factor_health.get("promote_blocked"):
            promote_ready = False
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in weight_suggest.py", exc_info=True)
        factor_health = None
    apply_note = "请手动合并 patch.weights 到 signal_config.json；须先做样本外验证。"
    if factor_health and factor_health.get("blockers"):
        apply_note = (
            "因子健康拦截："
            + "; ".join(factor_health.get("blockers") or [])
            + "。归零 proxy 权重后再 promote。"
        )
    elif oos_gate.get("skipped"):
        apply_note = "OOS 门禁已跳过；导出仅供对照，不建议直接 promote。"
    elif oos_gate and not oos_gate.get("passed"):
        apply_note = (
            f"OOS 门禁未过（{oos_gate.get('reason')}）；"
            "不建议 promote，可继续研究或放宽后再试。"
        )
    elif promote_ready:
        apply_note = "OOS 门禁已通过：仍须人审后手动合并；系统不会自动写盘。"

    return {
        "success": True,
        "target_file": "data/signal_config.json",
        "patch": {"weights": suggested},
        "changes": changes,
        "deltas": suggestion.get("deltas") or {},
        "delta_sources": suggestion.get("delta_sources") or {},
        "ic_mode": suggestion.get("ic_mode"),
        "oos_gate": oos_gate,
        "promote_ready": promote_ready,
        "factor_health": factor_health,
        "constraint_warnings": suggestion.get("constraint_warnings") or [],
        "rationale": suggestion.get("rationale") or [],
        "redundancy_warnings": suggestion.get("redundancy_warnings") or [],
        "params": suggestion.get("params") or {},
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "apply_note": apply_note,
    }
