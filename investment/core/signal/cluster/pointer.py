"""FH1：组权指针 + 晋升审计。

``cluster_pointer.json`` 指向 ``cluster_weights_v{n}.json``；切换用原子 replace。
``cluster_weights_active.json`` 为镜像，兼容旧读者；真源以指针为准。
"""

import glob
import json
import logging
import os
from typing import Any, Dict, Optional

from core.numbers import now_iso_utc

logger = logging.getLogger(__name__)

CLUSTER_WEIGHTS_PRUNE_KEEP = 8


def load_cluster_pointer() -> Optional[Dict[str, Any]]:
    from core.paths import CLUSTER_POINTER_PATH

    if not os.path.isfile(CLUSTER_POINTER_PATH):
        return None
    try:
        with open(CLUSTER_POINTER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def resolve_cluster_weights_path() -> Optional[str]:
    """解析当前 live 组权文件路径；无指针时回退 active 镜像。"""
    from core.paths import CLUSTER_WEIGHTS_ACTIVE_PATH, LIVE_DIR

    ptr = load_cluster_pointer()
    if ptr:
        art = str(ptr.get("artifact") or "").strip()
        if art:
            # 仅允许 live 目录下 basename，防路径穿越
            base = os.path.basename(art)
            candidate = os.path.join(LIVE_DIR, base)
            if os.path.isfile(candidate):
                return candidate
            if os.path.isabs(art) and os.path.isfile(art):
                # 测试环境可能把 LIVE_DIR 指到临时目录且 artifact 为绝对路径
                return art
    if os.path.isfile(CLUSTER_WEIGHTS_ACTIVE_PATH):
        return CLUSTER_WEIGHTS_ACTIVE_PATH
    return None


def write_cluster_pointer(
    *,
    version: int,
    artifact_path: str,
    note: str = "",
) -> Dict[str, Any]:
    from core.io_atomic import atomic_write_json
    from core.paths import CLUSTER_POINTER_PATH, LIVE_DIR

    base = os.path.basename(artifact_path)
    # 若产物在 LIVE_DIR 内，指针只存 basename
    try:
        if os.path.dirname(os.path.abspath(artifact_path)) == os.path.abspath(LIVE_DIR):
            stored = base
        else:
            stored = artifact_path
    except OSError:
        stored = base
    doc = {
        "schema_version": 1,
        "version": int(version),
        "artifact": stored,
        "updated_at": now_iso_utc(),
        "note": str(note or "")[:300],
    }
    atomic_write_json(CLUSTER_POINTER_PATH, doc)
    doc["path"] = CLUSTER_POINTER_PATH
    return doc


def append_promote_audit(entry: Dict[str, Any]) -> str:
    """追加一行晋升/豁免审计（jsonl）。"""
    from core.paths import LIVE_DIR, PROMOTE_AUDIT_PATH

    os.makedirs(LIVE_DIR, exist_ok=True)
    row = dict(entry or {})
    row.setdefault("at", now_iso_utc())
    with open(PROMOTE_AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return PROMOTE_AUDIT_PATH


def publish_cluster_weights_doc(
    active: Dict[str, Any],
    *,
    note: str = "",
) -> Dict[str, Any]:
    """写入版本化 artifact → 切换指针 → 镜像 active（全原子）。"""
    from core.io_atomic import atomic_write_json
    from core.paths import (
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        LIVE_DIR,
    )

    try:
        from core.signal.factors.meta.taxonomy import strip_removed_factors_from_pool_artifact

        strip_removed_factors_from_pool_artifact(active)
    except Exception:  # noqa: BLE001 — best-effort 降级分支；不阻塞主流程
        logger.debug("catch except Exception: in cluster_pointer.py", exc_info=True)
        pass

    version = int(active.get("version") or 1)
    # 版本化路径跟随 LIVE_DIR（测试可 patch LIVE_DIR）
    artifact = os.path.join(LIVE_DIR, f"cluster_weights_v{version}.json")

    atomic_write_json(artifact, active)
    # 落盘后再读一遍，拒绝残缺
    try:
        with open(artifact, encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict) or not isinstance(loaded.get("code_map"), dict):
            raise ValueError("artifact code_map 无效")
    except Exception as e:
        logger.exception('unexpected error in publish_cluster_weights_doc')
        try:
            os.remove(artifact)
        except OSError:
            pass
        return {"success": False, "error": f"晋升校验失败（指针未切换）：{e}"}

    ptr = write_cluster_pointer(version=version, artifact_path=artifact, note=note)
    # 兼容镜像
    atomic_write_json(CLUSTER_WEIGHTS_ACTIVE_PATH, active)
    # 清理旧版本文件，保留最近 N 个
    pruned = prune_old_cluster_weight_artifacts(keep=CLUSTER_WEIGHTS_PRUNE_KEEP)
    return {
        "success": True,
        "version": version,
        "artifact": artifact,
        "pointer": ptr,
        "mirror": CLUSTER_WEIGHTS_ACTIVE_PATH,
        "pruned": pruned,
    }


def prune_old_cluster_weight_artifacts(keep: int = CLUSTER_WEIGHTS_PRUNE_KEEP) -> list:
    """删除旧版本 cluster_weights_v*.json，保留最近 ``keep`` 个。

    同时清理 history 目录中超出 ``keep`` 的旧备份。
    当前指针指向的版本永远不会被删除。
    """
    from core.paths import CLUSTER_WEIGHTS_HISTORY_DIR, LIVE_DIR

    removed = []

    # 1) LIVE_DIR 下的版本化 artifact
    pattern = os.path.join(LIVE_DIR, "cluster_weights_v*.json")
    files = []
    for fp in glob.glob(pattern):
        if os.path.basename(fp) == "cluster_weights_active.json":
            continue
        # 提取版本号
        name = os.path.basename(fp)
        try:
            ver_str = name.replace("cluster_weights_v", "").replace(".json", "")
            ver = int(ver_str)
        except (ValueError, TypeError):
            continue
        files.append((ver, fp))
    files.sort(key=lambda x: x[0], reverse=True)

    # 当前指针版本
    ptr = load_cluster_pointer()
    cur_ver = int(ptr.get("version", 0)) if ptr else -1

    for ver, fp in files[keep:]:
        if ver == cur_ver:
            continue
        try:
            os.remove(fp)
            removed.append(fp)
        except OSError as e:
            logger.warning("清理旧版本文件失败 %s: %s", fp, e)

    # 2) history 目录下的备份
    hist_pattern = os.path.join(CLUSTER_WEIGHTS_HISTORY_DIR, "cluster_weights_v*.json")
    hist_files = []
    for fp in glob.glob(hist_pattern):
        mtime = os.path.getmtime(fp) if os.path.isfile(fp) else 0
        hist_files.append((mtime, fp))
    hist_files.sort(key=lambda x: x[0], reverse=True)

    for _, fp in hist_files[keep:]:
        try:
            os.remove(fp)
            removed.append(fp)
        except OSError as e:
            logger.warning("清理历史备份失败 %s: %s", fp, e)

    if removed:
        logger.info("cluster_weights 清理: 删除 %d 个旧版本文件", len(removed))
    return removed


def active_enable_blockers(
    *,
    health: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> list:
    """切换 mode=active 前的硬阻断（不含「mode=off 半晋升」提示）。"""
    blockers: list = []
    if health is not None:
        if not health.get("allow_active"):
            blockers.extend(list(health.get("alerts") or []) or ["health.allow_active=false"])
    if evidence is not None:
        gate = evidence.get("gate") or {}
        if not gate.get("ok"):
            blockers.extend(list(gate.get("blockers") or []))
    # 指针残缺：有 pointer 文件但解析不到 artifact
    ptr = load_cluster_pointer()
    resolved = resolve_cluster_weights_path()
    from core.paths import CLUSTER_POINTER_PATH

    if os.path.isfile(CLUSTER_POINTER_PATH) and not resolved:
        blockers.append("cluster_pointer 存在但 artifact 不可读（拒绝 active）")
    if not resolved:
        blockers.append("无 cluster_weights（请先 promote）")
    else:
        # 缺簿：active 前须有 book（可 force 豁免）
        from core.paths import CLUSTER_BOOK_ACTIVE_PATH

        if not os.path.isfile(CLUSTER_BOOK_ACTIVE_PATH):
            blockers.append("无 cluster_book_active（请刷新簿）")
    # 去重
    seen = set()
    out = []
    for b in blockers:
        s = str(b)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out
