"""L1/L2 物化目录：family / clock 清单与默认策略。"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from core.research.lake_bridge import (
    FAMILY_CO,
    FAMILY_OO,
    FAMILY_OO_RANK,
    FAMILY_TC,
)

# 对外可选 family（一键默认勾选 P0）
P0_FAMILIES: Tuple[str, ...] = (FAMILY_OO, FAMILY_TC, FAMILY_CO)
P1_FAMILIES: Tuple[str, ...] = (FAMILY_OO_RANK,)

ALL_FAMILIES: Tuple[str, ...] = P0_FAMILIES + P1_FAMILIES

DEFAULT_CLOCKS: Tuple[str, ...] = ("open",)

FAMILY_LABELS: Dict[str, str] = {
    FAMILY_OO: "ŷ_oo",
    FAMILY_TC: "ŷ_τc(open)",
    FAMILY_CO: "ŷ_co",
    FAMILY_OO_RANK: "ŷ_oo_rank",
}


def suggested_tau_clocks() -> List[str]:
    """做 T / ŷ_τ* 训练网格钟（不含 open）；供分钟线「物化 L1/L2」。"""
    try:
        from core.signal.minute_tau_grid import DEFAULT_MINUTE_TAU_GRID

        out: List[str] = []
        seen = set()
        for hm in DEFAULT_MINUTE_TAU_GRID or ():
            s = str(hm or "").strip()
            if not s or s.lower() in ("open", "09:30", "0930"):
                continue
            key = s[:5] if ":" in s else s
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out
    except Exception:  # noqa: BLE001
        return []


def normalize_families(raw: Sequence[str] | None) -> List[str]:
    allowed = set(ALL_FAMILIES)
    out: List[str] = []
    seen = set()
    for item in raw or list(P0_FAMILIES):
        key = str(item or "").strip().lower()
        if key == "tc_open":
            key = FAMILY_TC
        if key not in allowed or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out or list(P0_FAMILIES)


def normalize_clocks(raw: Sequence[str] | None) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in raw or list(DEFAULT_CLOCKS):
        key = str(item or "").strip().lower() or "open"
        if key in ("09:30", "0930"):
            key = "open"
        # tau_HHMM → 安全 clock 名（与 day_cube._safe_clock 对齐由调用方再 scrub）
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out or list(DEFAULT_CLOCKS)


def catalog_payload() -> Dict[str, object]:
    from core.research.materialize.flags import fit_use_day_cube, require_day_cube
    from core.research.materialize.horizon_names import (
        horizon_cube_required_names,
        horizon_l1_feature_names,
    )

    tau_clocks = suggested_tau_clocks()
    l1_names = horizon_l1_feature_names()
    return {
        "families": [
            {
                "id": fid,
                "label": FAMILY_LABELS.get(fid, fid),
                "tier": "P0" if fid in P0_FAMILIES else "P1",
                "default": fid in P0_FAMILIES,
            }
            for fid in ALL_FAMILIES
        ],
        "clocks": list(DEFAULT_CLOCKS),
        "suggested_tau_clocks": tau_clocks,
        "tau_grid": {
            "option": "do_l1_tau_grid",
            "clocks": tau_clocks,
            "n_feature_names": len(l1_names),
            "n_cube_required": len(horizon_cube_required_names()),
            "requires_minute": True,
            "note": "须本地 5m Ready；写 tc_HHMM（τc∪窗头列）后再物化按钟 L2",
        },
        "flags": {
            "fit_use_day_cube": bool(fit_use_day_cube()),
            "require_day_cube": bool(require_day_cube()),
        },
        "note": (
            "L1=票级 raw；L2=日截面 μ/σ（不取代 L1）。"
            "默认 L2 clock=open；τ 网格 L1 传 do_l1_tau_grid；"
            "按钟 L2 传 clocks=suggested_tau_clocks（须先有 tc_HHMM）。"
        ),
    }
