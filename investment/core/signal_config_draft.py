"""signal_config 草稿编辑 / diff / 人审晋升（R2 · 不静默写生产）。"""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.paths import DATA_DIR, SIGNAL_CONFIG_PATH
from core.signal.config import DEFAULT_SIGNAL_CONFIG, load_signal_config

DRAFT_PATH = os.path.join(DATA_DIR, "signal_config_draft.json")
BACKUP_DIR = os.path.join(DATA_DIR, "config_backups")

# 允许草稿改写的顶层键（防误写无关字段）
ALLOWED_TOP_KEYS = frozenset(
    {
        "version",
        "weights",
        "hard_reject",
        "rank",
        "stance_thresholds",
        "invalidation",
        "relative_strength",
        "regime",
        "fundamentals",
        "cross_section",
    }
)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _deep_diff(a: Any, b: Any, path: str = "") -> List[Dict[str, Any]]:
    """列出 b 相对 a 的变更（简易 JSON diff）。"""
    changes: List[Dict[str, Any]] = []
    if type(a) != type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        changes.append({"path": path or "$", "from": a, "to": b})
        return changes
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a.keys()) | set(b.keys())
        for k in sorted(keys):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                changes.append({"path": p, "from": None, "to": b[k]})
            elif k not in b:
                changes.append({"path": p, "from": a[k], "to": None})
            else:
                changes.extend(_deep_diff(a[k], b[k], p))
        return changes
    if isinstance(a, list) and isinstance(b, list):
        if a != b:
            changes.append({"path": path or "$", "from": a, "to": b})
        return changes
    if a != b:
        changes.append({"path": path or "$", "from": a, "to": b})
    return changes


def validate_config_payload(raw: Any) -> Tuple[bool, Dict[str, Any], List[str]]:
    """校验草稿 JSON；返回 (ok, normalized, errors)。"""
    errors: List[str] = []
    if not isinstance(raw, dict):
        return False, {}, ["根节点须为 JSON object"]

    unknown = [k for k in raw.keys() if k not in ALLOWED_TOP_KEYS]
    if unknown:
        errors.append(f"不允许的顶层键: {', '.join(unknown[:8])}")

    cfg = {k: deepcopy(raw[k]) for k in raw if k in ALLOWED_TOP_KEYS}

    weights = cfg.get("weights")
    if weights is not None:
        if not isinstance(weights, dict):
            errors.append("weights 须为 object")
        else:
            total = 0.0
            for k, v in weights.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    errors.append(f"weights.{k} 非数字")
                    continue
                if fv < 0 or fv > 1:
                    errors.append(f"weights.{k} 须在 [0,1]")
                total += fv
            if abs(total - 1.0) > 0.08:
                errors.append(f"weights 之和≈{total:.3f}，建议接近 1.0（容差 0.08）")

    rank = cfg.get("rank")
    if isinstance(rank, dict) and rank.get("min_score") is not None:
        try:
            ms = float(rank["min_score"])
            if ms < 0 or ms > 100:
                errors.append("rank.min_score 须在 0～100")
        except (TypeError, ValueError):
            errors.append("rank.min_score 非数字")

    ok = not errors
    return ok, cfg, errors


def read_production_config() -> Dict[str, Any]:
    return load_signal_config(reload=True)


def load_draft(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or DRAFT_PATH
    if not os.path.isfile(p):
        return {"ok": True, "empty": True, "path": p, "draft": None}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "empty": True, "error": str(e), "path": p, "draft": None}
    draft = data.get("config") if isinstance(data, dict) and "config" in data else data
    return {
        "ok": True,
        "empty": False,
        "path": p,
        "saved_at": data.get("saved_at") if isinstance(data, dict) else None,
        "note": data.get("note") if isinstance(data, dict) else None,
        "cycle_id": data.get("cycle_id") if isinstance(data, dict) else None,
        "draft": draft,
    }


def save_draft(
    config: dict,
    *,
    note: str = "",
    path: Optional[str] = None,
) -> Dict[str, Any]:
    ok, normalized, errors = validate_config_payload(config)
    if not ok:
        return {"ok": False, "success": False, "errors": errors}
    p = path or DRAFT_PATH
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    try:
        import uuid

        cycle_id = f"draft-{uuid.uuid4().hex[:10]}"
    except Exception:
        cycle_id = f"draft-{_now_iso()}"
    payload = {
        "saved_at": _now_iso(),
        "note": note or "R2 策略规格草稿；未写生产 signal_config",
        "cycle_id": cycle_id,
        "config": normalized,
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    try:
        from core.north_star import TTM_EVENT_IDEA, append_ttm_event

        append_ttm_event(
            TTM_EVENT_IDEA,
            ref="signal_config_draft",
            meta={"note": note, "cycle_id": cycle_id},
        )
    except Exception:
        pass
    return {
        "ok": True,
        "success": True,
        "path": p,
        "saved_at": payload["saved_at"],
        "cycle_id": cycle_id,
        "config": normalized,
        "note": payload["note"],
    }


def diff_against_production(
    draft: Optional[dict] = None,
    *,
    production: Optional[dict] = None,
) -> Dict[str, Any]:
    prod = production if production is not None else read_production_config()
    if draft is None:
        pack = load_draft()
        draft = pack.get("draft")
        if not draft:
            return {
                "ok": False,
                "success": False,
                "error": "无草稿",
                "changes": [],
            }
    ok, normalized, errors = validate_config_payload(draft)
    if not ok:
        return {"ok": False, "success": False, "errors": errors, "changes": []}
    changes: List[Dict[str, Any]] = []
    for k in sorted(ALLOWED_TOP_KEYS):
        if k not in normalized:
            continue
        changes.extend(_deep_diff(prod.get(k), normalized.get(k), k))
    return {
        "ok": True,
        "success": True,
        "change_count": len(changes),
        "changes": changes[:80],
        "draft_keys": list(normalized.keys()),
        "note": "相对生产 signal_config 的 diff；晋升前请人工确认。",
    }


def promote_draft(
    *,
    draft: Optional[dict] = None,
    note: str = "",
    config_path: Optional[str] = None,
    draft_path: Optional[str] = None,
) -> Dict[str, Any]:
    """人审晋升：备份生产配置后写入 signal_config.json，并 reload。"""
    cycle_id = ""
    if draft is None:
        pack = load_draft(draft_path)
        draft = pack.get("draft")
        cycle_id = str(pack.get("cycle_id") or "")
        if not draft:
            return {"ok": False, "success": False, "error": "无草稿可晋升"}
    ok, normalized, errors = validate_config_payload(draft)
    if not ok:
        return {"ok": False, "success": False, "errors": errors}

    # V2.4：权重变更须填写 note（人审理由）
    try:
        prod = load_signal_config()
        prod_w = (prod or {}).get("weights") or {}
        draft_w = (normalized or {}).get("weights")
        if draft_w is not None and dict(draft_w) != dict(prod_w):
            if not str(note or "").strip():
                return {
                    "ok": False,
                    "success": False,
                    "error": "权重变更须填写 note（人审理由）后再晋升",
                }
    except Exception:
        pass

    # S0.3：synthetic 主导时拒绝权重晋升（避免把 demo 样本当已验证）
    allow_demo = bool((draft or {}).get("allow_demo")) if isinstance(draft, dict) else False
    # allow_demo 也可经 note 标记（显式豁免）
    if "allow_demo=true" in str(note or "").lower().replace(" ", ""):
        allow_demo = True
    try:
        draft_w = (normalized or {}).get("weights")
        prod = load_signal_config()
        prod_w = (prod or {}).get("weights") or {}
        weights_changing = draft_w is not None and dict(draft_w) != dict(prod_w)
        if weights_changing and not allow_demo:
            from core.sample_ops import fundamentals_history_coverage

            cov = fundamentals_history_coverage()
            syn = int(cov.get("synthetic_multi_point") or 0)
            real_m = int(cov.get("real_multi_point") or 0)
            real_cov = cov.get("real_multi_coverage")
            demo_dominated = syn > 0 and real_m < syn
            thin_real = real_cov is not None and float(real_cov) < 0.3 and syn > 0
            if demo_dominated or thin_real:
                return {
                    "ok": False,
                    "success": False,
                    "error": (
                        "财务样本仍偏 synthetic_demo / 真实多点覆盖过低，"
                        "拒绝权重晋升。请先 ingest-history，或 note 含 allow_demo=true 显式豁免。"
                    ),
                    "sample": {
                        "real_multi_point": real_m,
                        "synthetic_multi_point": syn,
                        "real_multi_coverage": real_cov,
                    },
                }
    except Exception:
        pass

    target = config_path or SIGNAL_CONFIG_PATH
    # 与默认合并，避免缺键
    merged = deepcopy(DEFAULT_SIGNAL_CONFIG)
    merged.update(normalized)
    # weights 若提供则整表替换该段
    if "weights" in normalized:
        merged["weights"] = normalized["weights"]

    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = None
    if os.path.isfile(target):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"signal_config_{stamp}.json")
        shutil.copy2(target, backup_path)

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    # 清缓存
    load_signal_config(reload=True)

    try:
        from core.north_star import TTM_EVENT_PAPER, append_ttm_event

        append_ttm_event(
            TTM_EVENT_PAPER,
            ref="signal_config_promote",
            meta={
                "note": note or "draft promote",
                "backup": backup_path,
                **({"cycle_id": cycle_id} if cycle_id else {}),
            },
        )
    except Exception:
        pass

    return {
        "ok": True,
        "success": True,
        "path": target,
        "backup": backup_path,
        "promoted_at": _now_iso(),
        "note": note or "草稿已人审写入 signal_config.json",
        "change_preview": diff_against_production(normalized, production=merged).get(
            "change_count"
        ),
    }
