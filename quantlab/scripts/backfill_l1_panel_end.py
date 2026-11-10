#!/usr/bin/env python3
"""只读 npz 日期，回填旧 L1 meta 缺失的 ``panel_end`` / ``scored_through``。

升级后 ``lake_row_covers_bar_end`` 要求 meta 含 ``panel_end``；旧分片只有
``end_date`` / ``scored_through`` 时会全池 miss → 首轮全量重算。本脚本从不
重算特征，只把盘上 npz 的最大决策日写进 meta，避免白跑一遍 collect。

用法::

    cd quantlab
    python3 scripts/backfill_l1_panel_end.py            # 默认 dry-run
    python3 scripts/backfill_l1_panel_end.py --apply
    python3 scripts/backfill_l1_panel_end.py --family oo --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".l1_bf_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _panel_end_from_npz(npz_path: str) -> str:
    with np.load(npz_path, allow_pickle=False) as z:
        if "dates" not in z.files:
            return ""
        panel_end = ""
        for raw in z["dates"].tolist():
            day = str(raw or "")[:10]
            if len(day) >= 10 and day >= panel_end:
                panel_end = day
        return panel_end


def _iter_meta_paths(root: str, family: Optional[str]) -> Iterable[Tuple[str, str, str]]:
    """yield (family, code_stem, meta_path)."""
    if not os.path.isdir(root):
        return
    fam_filter = str(family or "").strip()
    for fam_name in sorted(os.listdir(root)):
        fam_dir = os.path.join(root, fam_name)
        if not os.path.isdir(fam_dir):
            continue
        if fam_filter and fam_name != fam_filter:
            continue
        for name in sorted(os.listdir(fam_dir)):
            if not name.endswith(".meta.json"):
                continue
            stem = name[: -len(".meta.json")]
            yield fam_name, stem, os.path.join(fam_dir, name)


def _backfill_one(
    meta_path: str,
    *,
    apply: bool,
) -> str:
    """返回 skip / would_write / wrote / error…"""
    npz_path = meta_path[: -len(".meta.json")] + ".npz"
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return f"skip_bad_meta:{exc}"
    if not isinstance(meta, dict):
        return "skip_bad_meta:not_dict"
    if "panel_end" in meta:
        pe = str(meta.get("panel_end") or "")[:10]
        if len(pe) >= 10:
            return "skip_has_panel_end"
        # panel_end="" 的空面板：仍可从 npz 回填（若 npz 非空）
    if not os.path.isfile(npz_path):
        return "skip_no_npz"
    try:
        panel_end = _panel_end_from_npz(npz_path)
    except (OSError, ValueError, KeyError, EOFError) as exc:
        return f"skip_npz:{exc}"
    if len(panel_end) < 10:
        return "skip_empty_dates"
    end_date = str(meta.get("end_date") or "")[:10]
    scored = str(meta.get("scored_through") or "")[:10]
    # 与 stem_extra_meta 对齐：scored_through=bar 末日；无 end_date 则用面板末日
    want_scored = end_date if len(end_date) >= 10 else panel_end
    if (
        str(meta.get("panel_end") or "")[:10] == panel_end
        and scored == want_scored
    ):
        return "skip_already"
    meta["panel_end"] = panel_end
    meta["scored_through"] = want_scored
    if not apply:
        return f"would_write panel_end={panel_end} scored_through={want_scored}"
    try:
        _atomic_write_json(meta_path, meta)
    except OSError as exc:
        return f"error_write:{exc}"
    return f"wrote panel_end={panel_end} scored_through={want_scored}"


def main(argv: Optional[List[str]] = None) -> int:
    from core.research.feature_lake import _root
    from core.research.feature_registry import FEATURE_SCHEMA

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="真正写 meta；默认只 dry-run",
    )
    ap.add_argument(
        "--family",
        default="",
        help="只处理该 family 目录名（如 oo / tc / tc_1030）；默认全部",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多处理 N 个 meta（0=不限）",
    )
    args = ap.parse_args(argv)

    schema_root = os.path.join(_root(), f"v{FEATURE_SCHEMA}")
    print(f"lake_root={schema_root} apply={bool(args.apply)}")
    counts: Dict[str, int] = {}
    n = 0
    for fam, stem, meta_path in _iter_meta_paths(schema_root, args.family or None):
        if args.limit and n >= int(args.limit):
            break
        n += 1
        status = _backfill_one(meta_path, apply=bool(args.apply))
        key = status.split(":", 1)[0].split(" ", 1)[0]
        counts[key] = counts.get(key, 0) + 1
        if not status.startswith("skip_"):
            print(f"{fam}/{stem}: {status}")
    print(f"scanned={n} counts={json.dumps(counts, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
