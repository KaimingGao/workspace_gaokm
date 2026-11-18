"""工厂跑次权重：成功拟合写入临时目录，归档时改成版本号。

不写研究套 / 执行套。失败跑次不留权重。加载要求版本 JSON 已经存在。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

FORMAT = "yhat_factory_weights_v1"
ENV_RUN_ID = "YHAT_FACTORY_RUN_ID"


def _desk():
    from core.research import yhat_tf_desk as desk

    return desk


def _factory() -> str:
    return _desk().normalize_factory()


def pending_weights_dir(head: str) -> str:
    return os.path.join(_desk()._store_dir(), f"{head}.weights.pending")


def version_weights_dir(head: str, version_id: str) -> str:
    from core.research.yhat_factory_versions import _versions_root

    return os.path.join(_versions_root(head), f"{version_id}.weights")


def clear_pending_weights(head: str) -> None:
    """开跑前清掉上一轮没归档的临时权重。"""
    for path in (
        pending_weights_dir(head),
        pending_weights_dir(head) + ".tmp",
    ):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def discard_pending_weights(head: str) -> None:
    clear_pending_weights(head)


def remove_version_weights(head: str, version_id: str) -> None:
    path = version_weights_dir(head, version_id)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _commit_pending(head: str, meta: Dict[str, Any], files: Dict[str, bytes]) -> bool:
    run_id = str(os.environ.get(ENV_RUN_ID) or "").strip()
    if not run_id or not str(head or "").strip():
        return False
    body = dict(meta)
    body["format"] = FORMAT
    body["run_id"] = run_id
    body["pid"] = int(os.getpid())
    final = pending_weights_dir(head)
    tmp = final + ".tmp"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        for name, payload in files.items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(payload)
        with open(os.path.join(tmp, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        if os.path.isdir(final):
            shutil.rmtree(final, ignore_errors=True)
        os.rename(tmp, final)
    except OSError:
        logger.exception("factory weights pending write failed head=%s", head)
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    return True


def save_transformer_pending(state_dict: Dict[str, Any], meta: Dict[str, Any]) -> bool:
    """把 CPU state_dict 写到当次 head 的临时目录。没有 YHAT_FACTORY_RUN_ID 则跳过。"""
    if not str(os.environ.get(ENV_RUN_ID) or "").strip():
        return False
    head = str((meta or {}).get("head") or "").strip()
    if not head:
        return False
    import torch

    buf = io.BytesIO()
    torch.save(state_dict, buf)
    body = dict(meta or {})
    body["backend"] = "transformer"
    body.pop("state_dict", None)
    return _commit_pending(head, body, {"state.pt": buf.getvalue()})


def lightgbm_text_from_blob(blob: Optional[Dict[str, Any]]) -> str:
    if not isinstance(blob, dict):
        return ""
    raw = ""
    booster = blob.get("booster")
    if isinstance(booster, dict):
        raw = str(booster.get("payload_b64") or "")
    if not raw:
        raw = str(blob.get("booster_b64") or "")
    if not raw:
        return ""
    try:
        return base64.b64decode(raw.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeError):
        return ""


def save_lightgbm_pending(model_text: str, meta: Dict[str, Any]) -> bool:
    """把 LightGBM save_model 文本写到当次 head 的临时目录。"""
    if not str(os.environ.get(ENV_RUN_ID) or "").strip():
        return False
    head = str((meta or {}).get("head") or "").strip()
    text = str(model_text or "")
    if not head or not text.strip():
        return False
    body = dict(meta or {})
    body["backend"] = "lightgbm"
    return _commit_pending(head, body, {"model.txt": text.encode("utf-8")})


def save_ridge_pending(blob: Dict[str, Any], meta: Dict[str, Any]) -> bool:
    """把截距和系数写到当次 head 的临时目录。"""
    if not str(os.environ.get(ENV_RUN_ID) or "").strip():
        return False
    head = str((meta or {}).get("head") or "").strip()
    coef = blob.get("coef") if isinstance(blob, dict) else None
    if not head or not isinstance(coef, list) or not coef:
        return False
    body = dict(meta or {})
    body["backend"] = "ridge"
    body["alpha"] = blob.get("alpha")
    body["intercept"] = blob.get("intercept")
    payload = json.dumps(
        {
            "alpha": blob.get("alpha"),
            "intercept": blob.get("intercept"),
            "coef": coef,
            "feature_names": list(blob.get("feature_names") or []),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return _commit_pending(head, body, {"ridge.json": payload})


def _read_meta(path: str) -> Optional[Dict[str, Any]]:
    meta_path = os.path.join(path, "meta.json")
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def publish_pending_weights(
    head: str,
    version_id: str,
    *,
    run_id: str,
    pid: int,
) -> Optional[str]:
    """run_id 与 pid 一致才把临时目录改名为 ``{version_id}.weights``。"""
    src = pending_weights_dir(head)
    meta = _read_meta(src)
    if not meta:
        return None
    try:
        meta_pid = int(meta.get("pid") or 0)
    except (TypeError, ValueError):
        meta_pid = 0
    if str(meta.get("run_id") or "") != str(run_id or "") or meta_pid != int(pid or 0):
        discard_pending_weights(head)
        return None
    if str(meta.get("format") or "") != FORMAT:
        discard_pending_weights(head)
        return None
    dest = version_weights_dir(head, version_id)
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.rename(src, dest)
    except OSError:
        logger.exception("factory weights publish failed head=%s vid=%s", head, version_id)
        return None
    return f"{version_id}.weights"


def load_factory_weights(
    head: str,
    version_id: str,
    *,
    factory: Optional[str] = None,
) -> Dict[str, Any]:
    """按版本号加载。版本 JSON 不存在则拒绝，即使权重目录还在。"""
    from core.research.yhat_factory_versions import _check_version_id, _snap_path

    desk = _desk()
    fac = desk.normalize_factory(factory)
    vid = _check_version_id(version_id)
    if not vid:
        return {"success": False, "ok": False, "error": "invalid run_id"}
    with desk.factory_scope(fac):
        try:
            h = desk._validate_head(head)
        except ValueError as exc:
            return {"success": False, "ok": False, "error": str(exc)}
        if not os.path.isfile(_snap_path(h, vid)):
            return {"success": False, "ok": False, "error": "版本不存在", "run_id": vid}
        path = version_weights_dir(h, vid)
        meta = _read_meta(path)
        if not meta:
            return {"success": False, "ok": False, "error": "权重不存在", "run_id": vid}
        backend = str(meta.get("backend") or "")
        out: Dict[str, Any] = {
            "success": True,
            "ok": True,
            "factory": _factory(),
            "head": h,
            "run_id": vid,
            "weights_ref": f"{vid}.weights",
            "meta": meta,
            "backend": backend,
        }
        if backend == "transformer":
            state_path = os.path.join(path, "state.pt")
            if not os.path.isfile(state_path):
                return {"success": False, "ok": False, "error": "权重不存在", "run_id": vid}
            import torch

            try:
                state = torch.load(state_path, map_location="cpu", weights_only=True)
            except TypeError:
                state = torch.load(state_path, map_location="cpu")
            out["state_dict"] = state
            return out
        if backend == "lightgbm":
            model_path = os.path.join(path, "model.txt")
            if not os.path.isfile(model_path):
                return {"success": False, "ok": False, "error": "权重不存在", "run_id": vid}
            with open(model_path, encoding="utf-8") as f:
                out["model_text"] = f.read()
            return out
        if backend == "ridge":
            ridge_path = os.path.join(path, "ridge.json")
            if not os.path.isfile(ridge_path):
                return {"success": False, "ok": False, "error": "权重不存在", "run_id": vid}
            try:
                with open(ridge_path, encoding="utf-8") as f:
                    blob = json.load(f)
            except (OSError, ValueError):
                return {"success": False, "ok": False, "error": "权重不存在", "run_id": vid}
            if not isinstance(blob, dict) or not isinstance(blob.get("coef"), list):
                return {"success": False, "ok": False, "error": "权重不存在", "run_id": vid}
            out["coef"] = blob.get("coef")
            out["intercept"] = blob.get("intercept")
            out["alpha"] = blob.get("alpha")
            return out
        return {"success": False, "ok": False, "error": "未知权重后端", "run_id": vid}
