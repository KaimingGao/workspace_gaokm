"""模型工厂跑次版本：Arm·L / Arm·T 各自归档，不接 suite ``model_versions``。

选中历史版只回看参数、指标和日志。删除只去掉该条归档和日志副本。不写 live，也不改当前拟合。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from core.io_atomic import atomic_write_json

logger = logging.getLogger(__name__)

KEEP = 20
LOG_MAX_BYTES = 262_144
_SAFE_ID = re.compile(r"[^0-9A-Za-z._+-]+")
_VID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,159}$")


def _desk():
    from core.research import yhat_tf_desk as desk

    return desk


def _factory() -> str:
    return _desk().normalize_factory()


def _versions_root(head: str) -> str:
    return os.path.join(_desk()._store_dir(), "versions", head)


def _index_path(head: str) -> str:
    return os.path.join(_versions_root(head), "index.json")


def _snap_path(head: str, version_id: str) -> str:
    return os.path.join(_versions_root(head), f"{version_id}.json")


def _log_copy_path(head: str, version_id: str) -> str:
    return os.path.join(_versions_root(head), f"{version_id}.log")


def _check_version_id(version_id: Any) -> Optional[str]:
    vid = str(version_id or "").strip()
    if not vid or not _VID.fullmatch(vid) or ".." in vid:
        return None
    return vid


def _safe_id(raw: Any) -> str:
    base = _SAFE_ID.sub("_", str(raw or "")).strip("._")
    if not base or ".." in base:
        return ""
    return base[:140]


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        import json

        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_index(head: str) -> List[Dict[str, Any]]:
    raw = _read_json(_index_path(head))
    items = (raw or {}).get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append(dict(it))
    return out


def _write_index(head: str, factory: str, items: List[Dict[str, Any]]) -> None:
    atomic_write_json(
        _index_path(head),
        {
            "version": 1,
            "factory": factory,
            "head": head,
            "items": items,
        },
    )


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None or value == "":
        return bool(default)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"1", "true", "yes", "on", "full"}:
            return True
        if s in {"0", "false", "no", "off", "core"}:
            return False
        return bool(default)
    return bool(value)


def _fit_params(
    job: Optional[Dict[str, Any]], last: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    if isinstance(job, dict) and isinstance(job.get("params"), dict):
        return dict(job["params"])
    if not isinstance(last, dict):
        return None
    raw = last.get("fit_params")
    if not isinstance(raw, dict):
        raw = last.get("params")
    return dict(raw) if isinstance(raw, dict) else None


def _metrics(last: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(last, dict):
        return None
    metrics = last.get("metrics")
    if isinstance(metrics, dict) and metrics:
        return dict(metrics)
    return None


def _factor_tag(head: str, params: Optional[Dict[str, Any]]) -> Optional[str]:
    if _factory() == "L" or not isinstance(params, dict):
        return None
    if head in ("tc", "tc_rank"):
        return "atc_full" if _as_bool(params.get("full_atc", True), default=True) else "atc_core"
    return "aoo_full" if _as_bool(params.get("full_aoo", False), default=False) else "aoo_core"


def _sample_pct(params: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(params, dict) or "t_sample_pct" not in params:
        return None
    try:
        pct = float(params.get("t_sample_pct"))
    except (TypeError, ValueError):
        return None
    if pct != pct:
        return None
    return pct


def _rank_ic(metrics: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(metrics, dict):
        return None
    try:
        ic = float(metrics.get("cs_rank_ic"))
    except (TypeError, ValueError):
        return None
    if ic != ic:
        return None
    return ic


def _loss_label(params: Optional[Dict[str, Any]]) -> str:
    loss = str((params or {}).get("loss") or "").strip().lower()
    if loss in ("lambdarank", "listwise"):
        return "lambdarank"
    if loss:
        return "l2"
    return ""


def _badges(
    head: str,
    params: Optional[Dict[str, Any]],
    last: Optional[Dict[str, Any]],
    metrics: Optional[Dict[str, Any]],
    status: str,
) -> Dict[str, Any]:
    if not metrics:
        return {
            "gate_pass": None,
            "formal": False,
            "sample_pct": _sample_pct(params),
            "factor": _factor_tag(head, params),
            "failed": status == "error",
            "cs_rank_ic": None,
        }
    parity = last.get("parity") if isinstance(last, dict) and isinstance(last.get("parity"), dict) else {}
    gate_pass = None
    if isinstance(metrics, dict) and metrics.get("gate_pass") is not None:
        gate_pass = bool(metrics.get("gate_pass"))
    elif isinstance(last, dict):
        hg = last.get("hard_gate_vs_arm_L")
        tg = hg.get("T") if isinstance(hg, dict) else None
        if isinstance(tg, dict) and tg.get("pass") is not None:
            gate_pass = bool(tg.get("pass"))
    formal = bool(parity.get("formal")) if parity else False
    return {
        "gate_pass": gate_pass,
        "formal": formal,
        "sample_pct": _sample_pct(params),
        "factor": _factor_tag(head, params),
        "failed": status == "error",
        "cs_rank_ic": _rank_ic(metrics),
    }


def _short_stamp(raw: Any) -> str:
    s = str(raw or "")
    if len(s) >= 16 and s[4] == "-" and s[7] == "-":
        return f"{s[5:10]} {s[11:16]}"
    return s[:16]


def _label(
    *,
    started_at: Any,
    status: str,
    badges: Dict[str, Any],
    params: Optional[Dict[str, Any]],
) -> str:
    bits = [_short_stamp(started_at) or "—"]
    if status == "error" or badges.get("failed"):
        bits.append("失败")
        return " · ".join(bits)
    if status == "partial":
        bits.append("部分完成")
    ic = badges.get("cs_rank_ic")
    if isinstance(ic, (int, float)):
        bits.append(f"RankIC {float(ic):.3f}")
    if _factory() == "L":
        loss = _loss_label(params)
        if loss:
            bits.append(loss)
    else:
        pct = badges.get("sample_pct")
        if isinstance(pct, (int, float)):
            shown = int(pct) if float(pct) == int(pct) else pct
            bits.append(f"{shown}%")
        factor = str(badges.get("factor") or "")
        if factor.endswith("_core"):
            bits.append("core")
        elif factor.endswith("_full"):
            bits.append("full")
    if badges.get("formal"):
        bits.append("正式")
    elif badges.get("gate_pass") is True:
        bits.append("过闸")
    elif badges.get("gate_pass") is False:
        bits.append("未过闸")
    return " · ".join(bit for bit in bits if bit)


def _archive_id(head: str, job: Optional[Dict[str, Any]], last: Optional[Dict[str, Any]]) -> str:
    run_id = ""
    pid = 0
    started = ""
    if isinstance(job, dict):
        run_id = str(job.get("run_id") or "").strip()
        try:
            pid = int(job.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        started = str(job.get("started_at") or "").strip()
    if not started and isinstance(last, dict):
        started = str(last.get("updated_at") or "").strip()
    if not run_id:
        stamp = started.replace(":", "").replace("-", "") or "unknown"
        run_id = f"{head}-{stamp}"
    return _safe_id(f"{run_id}-p{pid}")


def _copy_log_tail(src: str, dest: str) -> str:
    if not src or not os.path.isfile(src):
        return ""
    try:
        size = os.path.getsize(src)
        with open(src, "rb") as f:
            if size > int(LOG_MAX_BYTES):
                f.seek(max(0, size - int(LOG_MAX_BYTES)))
                f.readline()
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.exception("factory version log copy failed dest=%s", dest)
    return text


def _eval_fields(last: Optional[Dict[str, Any]], metrics: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """没有本跑指标时不继承上一份 last 的闸门和评测。"""
    if not metrics or not isinstance(last, dict):
        return {
            "hard_gate_vs_arm_L": None,
            "gate": None,
            "parity": None,
            "eval_universe": None,
        }
    parity = last.get("parity") if isinstance(last.get("parity"), dict) else None
    eu = last.get("eval_universe") if isinstance(last.get("eval_universe"), dict) else None
    return {
        "hard_gate_vs_arm_L": last.get("hard_gate_vs_arm_L"),
        "gate": last.get("gate"),
        "parity": parity,
        "eval_universe": eu,
    }


def archive_factory_run(
    head: str,
    *,
    job: Optional[Dict[str, Any]] = None,
    last: Optional[Dict[str, Any]] = None,
    status: str = "done",
    keep: int = KEEP,
) -> Optional[str]:
    """把一次终态跑次写入当前工厂目录。同一归档 id 覆盖。已在头锁与 factory_scope 内调用。"""
    desk = _desk()
    h = desk._validate_head(head)
    fac = _factory()
    if fac not in ("L", "T"):
        fac = "T"
    st = str(status or "").strip().lower()
    if st not in ("done", "partial", "error"):
        st = "error"
    vid = _archive_id(h, job, last)
    if not vid:
        return None
    params = _fit_params(job, last)
    metrics = _metrics(last)
    badges = _badges(h, params, last, metrics, st)
    eval_fields = _eval_fields(last, metrics)
    job_d = job if isinstance(job, dict) else {}
    last_d = last if isinstance(last, dict) else {}
    started_at = str(job_d.get("started_at") or "") or None
    finished_at = str(job_d.get("finished_at") or last_d.get("updated_at") or "") or None
    message = str(last_d.get("message") or job_d.get("error") or "").strip()
    action = str(last_d.get("action") or "")
    if not action:
        action = "holdout_result" if st == "done" else "holdout_failed"
    backend = "lgb" if fac == "L" else "transformer"
    try:
        pid = int(job_d.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    doc: Dict[str, Any] = {
        "role": "yhat_factory_run",
        "model_id": f"{backend}:{h}:{vid}",
        "factory": fac,
        "backend": backend,
        "head": h,
        "run_id": vid,
        "source_run_id": str(job_d.get("run_id") or "") or None,
        "pid": pid,
        "status": st,
        "action": action,
        "started_at": started_at,
        "finished_at": finished_at,
        "fit_params": params,
        "metrics": metrics,
        "message": message,
        "cli": str(job_d.get("cli") or last_d.get("cli") or "") or None,
        "param_warnings": list(job_d.get("param_warnings") or []),
        "exit_hint": str(job_d.get("exit_hint") or "") or None,
        "badges": badges,
        "live_allowed": False,
        **eval_fields,
    }
    atomic_write_json(_snap_path(h, vid), doc)
    log_src = desk._log_path(h)
    _copy_log_tail(log_src, _log_copy_path(h, vid))
    item = {
        "id": vid,
        "model_id": doc["model_id"],
        "started_at": started_at,
        "finished_at": finished_at,
        "status": st,
        "action": action,
        "label": _label(started_at=started_at or finished_at, status=st, badges=badges, params=params),
        "badges": badges,
    }
    items = [it for it in _read_index(h) if str(it.get("id") or "") != vid]
    items.insert(0, item)
    drop = items[int(keep) :]
    items = items[: int(keep)]
    for old in drop:
        oid = _check_version_id(old.get("id"))
        if not oid:
            continue
        for path in (_snap_path(h, oid), _log_copy_path(h, oid)):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                logger.exception("factory version prune failed path=%s", path)
    _write_index(h, fac, items)
    return vid


def list_factory_versions(head: str, *, factory: Optional[str] = None) -> Dict[str, Any]:
    desk = _desk()
    fac = desk.normalize_factory(factory)
    with desk.factory_scope(fac):
        try:
            h = desk._validate_head(head)
        except ValueError as exc:
            return {"success": False, "ok": False, "error": str(exc), "items": []}
        return {
            "success": True,
            "ok": True,
            "factory": _factory(),
            "head": h,
            "items": _read_index(h),
        }


def preview_factory_version(
    head: str,
    version_id: str,
    *,
    factory: Optional[str] = None,
) -> Dict[str, Any]:
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
        doc = _read_json(_snap_path(h, vid))
        if not isinstance(doc, dict):
            return {"success": False, "ok": False, "error": "版本不存在", "run_id": vid}
        log_tail = ""
        path = _log_copy_path(h, vid)
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    log_tail = f.read()
            except OSError:
                log_tail = ""
        out = dict(doc)
        out["log_tail"] = log_tail
        out["success"] = True
        out["ok"] = True
        return out


def delete_factory_version(
    head: str,
    version_id: str,
    *,
    factory: Optional[str] = None,
) -> Dict[str, Any]:
    """去掉一条跑次归档。不改 last / draft / 正在写的 fit.log。"""
    desk = _desk()
    vid = _check_version_id(version_id)
    if not vid:
        return {"success": False, "ok": False, "error": "invalid run_id"}
    try:
        desk._validate_head(head)
    except ValueError as exc:
        return {"success": False, "ok": False, "error": str(exc)}

    def _do(h: str, factory: Optional[str] = None) -> Dict[str, Any]:
        del factory  # 锁内工厂由 factory_scope 决定
        items = _read_index(h)
        snap = _snap_path(h, vid)
        log_path = _log_copy_path(h, vid)
        known = any(str(it.get("id") or "") == vid for it in items)
        if not known and not os.path.isfile(snap):
            return {"success": False, "ok": False, "error": "版本不存在", "run_id": vid}
        for path in (snap, log_path):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                logger.exception("factory version delete failed path=%s", path)
                return {"success": False, "ok": False, "error": "删除失败", "run_id": vid}
        kept = [it for it in items if str(it.get("id") or "") != vid]
        _write_index(h, _factory(), kept)
        return {
            "success": True,
            "ok": True,
            "factory": _factory(),
            "head": h,
            "run_id": vid,
            "items": kept,
        }

    return desk._with_head_lock(_do)(head, factory=factory)
