"""研究枢纽四头各自选定的模型臂。缺省 Arm·T。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping

from core.paths import LIVE_DIR

HEADS = ("oo", "tc", "oo_rank", "tc_rank")


def arms_path() -> str:
    return os.path.join(LIVE_DIR, "yhat_hub", "arms.json")


def normalize_arm(raw: Any) -> str:
    from core.research.yhat_tf_desk import normalize_factory

    fac = normalize_factory(raw)
    return fac if fac in ("R", "L", "T") else "T"


def read_hub_arms() -> Dict[str, str]:
    raw: Any = {}
    path = arms_path()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            doc = {}
        if isinstance(doc, dict):
            nested = doc.get("arms")
            raw = nested if isinstance(nested, dict) else doc
    if not isinstance(raw, dict):
        raw = {}
    return {head: normalize_arm(raw.get(head)) for head in HEADS}


def write_hub_arms(incoming: Mapping[str, Any]) -> Dict[str, str]:
    cur = read_hub_arms()
    src: Any = incoming
    if isinstance(incoming, Mapping) and isinstance(incoming.get("arms"), Mapping):
        src = incoming.get("arms")
    if isinstance(src, Mapping):
        for head in HEADS:
            if head in src:
                cur[head] = normalize_arm(src.get(head))
    path = arms_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"arms": cur}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return cur
