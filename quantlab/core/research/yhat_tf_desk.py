"""L4 四头 ŷ Transformer 研究台：槽位 IO（不写生产 score）。

真源 HeadSpec 在 ``yhat_transformer.heads``；本模块只负责 draft/research/last。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

from core.paths import YHAT_TF_DIR, YHAT_TF_LAST_PATH
from core.research.yhat_transformer.heads import get_head, head_ids, iter_heads


def _ensure_dir() -> None:
    os.makedirs(YHAT_TF_DIR, exist_ok=True)


def _path(head: str, slot: str) -> str:
    return os.path.join(YHAT_TF_DIR, f"{head}_{slot}.json")


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    _ensure_dir()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _validate_head(head: str) -> str:
    return get_head(head).id


def _scheme_blob() -> Dict[str, Any]:
    per_head: Dict[str, Any] = {}
    for spec in iter_heads():
        per_head[spec.id] = {
            "status": spec.status,
            "loss": spec.loss,
            "script": spec.default_cli,
            "task_type": spec.task_type,
            "gate": spec.gate,
            "factor_pack": spec.factor_pack,
            "target": spec.target_desc,
            "note": spec.role,
        }
    return {
        "name": "Scheme C · seq encoder ∥ factor MLP",
        "layer": "L4 research-only",
        "package": "yhat_transformer",
        "arms": [
            {"id": "O", "title": "Arm O", "desc": "现网 LGB 因子基线（硬闸对照）"},
            {"id": "A", "title": "Arm A", "desc": "因子 MLP（无序列）"},
            {"id": "B", "title": "Arm B", "desc": "日线序列 Transformer"},
            {
                "id": "C",
                "title": "Arm C",
                "desc": "序列 encoder ∥ 因子 MLP 融合（候选；须过 Holdout 硬闸）",
            },
        ],
        "gate": "Holdout CS rank IC / ICIR vs Arm O；不直连生产 score",
        "promote": "draft → research 仅研究槽；禁止 live / active",
        "per_head": per_head,
    }


def heads_meta() -> List[Dict[str, str]]:
    return [
        {
            "id": s.id,
            "label": s.label,
            "target": s.target_desc,
            "role": s.role,
        }
        for s in iter_heads()
    ]


def default_fit_params(head: str) -> Dict[str, Any]:
    spec = get_head(head)
    base: Dict[str, Any] = {
        "head": spec.id,
        "pool_n": 800,
        "lookback": 900,
        "holdout_trading_days": 60,
        "d_model": 64,
        "nlayers": 3,
        "nhead": 4,
        "batch_size": 32,
        "epochs": 12,
        "lr": 1e-3,
        "arms": "0,A,C",
        "seeds": "42,43",
        "device": "cpu",
        "factor_pack": spec.factor_pack,
        "loss": spec.loss,
    }
    if spec.id in ("tc", "tc_rank"):
        base["tau_end"] = "09:30"
        base["lookback"] = 120
    if spec.task_type == "rank":
        base["arms"] = "0,C"
    return base


def head_status(head: str) -> Dict[str, Any]:
    spec = get_head(head)
    draft = _read_json(_path(spec.id, "draft"))
    research = _read_json(_path(spec.id, "research"))
    last = _read_json(_path(spec.id, "last"))
    scheme = _scheme_blob()["per_head"][spec.id]
    return {
        "head": spec.id,
        "label": spec.label,
        "scheme": scheme,
        "draft": draft,
        "research": research,
        "last": last,
        "has_draft": draft is not None,
        "has_research": research is not None,
        "live_allowed": False,
    }


def desk_status() -> Dict[str, Any]:
    _ensure_dir()
    scheme = _scheme_blob()
    heads = [head_status(h) for h in head_ids()]
    last_global = _read_json(YHAT_TF_LAST_PATH)
    return {
        "ok": True,
        "success": True,
        "layer": scheme["layer"],
        "package": "yhat_transformer",
        "scheme": scheme,
        "heads_meta": heads_meta(),
        "heads": heads,
        "last_global": last_global,
        "live_allowed": False,
        "store_dir": YHAT_TF_DIR,
    }


def save_fit_draft(head: str, params: Dict[str, Any], *, note: str = "") -> Dict[str, Any]:
    h = _validate_head(head)
    merged = default_fit_params(h)
    if isinstance(params, dict):
        for k, v in params.items():
            if v is None:
                continue
            merged[k] = v
    merged["head"] = h
    payload = {
        "role": "yhat_tf_draft",
        "head": h,
        "params": merged,
        "note": str(note or ""),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live_allowed": False,
    }
    _write_json(_path(h, "draft"), payload)
    _write_json(
        YHAT_TF_LAST_PATH,
        {"action": "save_draft", "head": h, "updated_at": payload["updated_at"]},
    )
    return {"ok": True, "success": True, "draft": payload}


def record_holdout_intent(head: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """记录 Holdout 意图；不在 Web 内跑重训练。"""
    spec = get_head(head)
    draft = _read_json(_path(spec.id, "draft")) or {}
    p = dict(
        (draft.get("params") if isinstance(draft, dict) else None)
        or default_fit_params(spec.id)
    )
    if isinstance(params, dict):
        p.update({k: v for k, v in params.items() if v is not None})
    cli = spec.default_cli
    if cli:
        msg = f"已记录拟合意图。CLI：{cli}（不写生产 score；live_allowed=False）。"
    else:
        msg = f"已记录参数。head={spec.id} status={spec.status}；无默认 CLI。"
    last = {
        "role": "yhat_tf_last",
        "head": spec.id,
        "action": "holdout_intent",
        "params": p,
        "scheme_status": spec.status,
        "cli": cli,
        "message": msg,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live_allowed": False,
    }
    _write_json(_path(spec.id, "last"), last)
    _write_json(
        YHAT_TF_LAST_PATH,
        {
            "action": "holdout_intent",
            "head": spec.id,
            "updated_at": last["updated_at"],
        },
    )
    if not draft:
        save_fit_draft(spec.id, p, note="auto from holdout intent")
    return {"ok": True, "success": True, "last": last, "cli": cli}


def promote_draft_to_research(head: str) -> Dict[str, Any]:
    h = _validate_head(head)
    draft = _read_json(_path(h, "draft"))
    if not draft:
        return {"ok": False, "success": False, "error": "无草稿可晋级研究槽"}
    payload = {
        **draft,
        "role": "yhat_tf_research",
        "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live_allowed": False,
    }
    _write_json(_path(h, "research"), payload)
    _write_json(
        YHAT_TF_LAST_PATH,
        {
            "action": "promote_research",
            "head": h,
            "updated_at": payload["promoted_at"],
        },
    )
    return {"ok": True, "success": True, "research": payload}


def clear_slot(head: str, slot: str) -> Dict[str, Any]:
    h = _validate_head(head)
    s = str(slot or "").strip()
    if s not in ("draft", "research", "last"):
        raise ValueError("slot must be draft|research|last")
    path = _path(h, s)
    if os.path.isfile(path):
        os.remove(path)
    return {"ok": True, "success": True, "cleared": s, "head": h}
