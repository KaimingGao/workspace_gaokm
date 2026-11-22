"""模型工厂跑次版本：Arm·L / Arm·T 各自归档，不接 suite ``model_versions``。

选中历史版只回看参数、指标、曲线和日志。删除去掉该条归档、日志副本、曲线副本和权重目录。不写 live，也不改磁盘上的 last / job。已从列表删掉的终态跑次不再当作上次拟合，拟合日志改看列表里还在的最新一条。
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


def _curve_copy_path(head: str, version_id: str) -> str:
    return os.path.join(_versions_root(head), f"{version_id}.curve.jsonl")


def _weights_dir(head: str, version_id: str) -> str:
    return os.path.join(_versions_root(head), f"{version_id}.weights")


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


def _enabled_map(head: str) -> Dict[str, str]:
    raw = (_read_json(_index_path(head)) or {}).get("enabled")
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for role in ("research", "live"):
        vid = _check_version_id(raw.get(role))
        if vid:
            out[role] = vid
    return out


def _write_index(
    head: str,
    factory: str,
    items: List[Dict[str, Any]],
    enabled: Optional[Dict[str, str]] = None,
) -> None:
    current = _enabled_map(head) if enabled is None else dict(enabled)
    ids = {str(it.get("id") or "") for it in items}
    kept = {
        role: vid
        for role, vid in current.items()
        if role in ("research", "live") and vid in ids
    }
    payload: Dict[str, Any] = {
        "version": 1,
        "factory": factory,
        "head": head,
        "items": items,
    }
    if kept:
        payload["enabled"] = kept
    atomic_write_json(_index_path(head), payload)


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
    # L / R 固定全量，版本条不再标 core/full。
    if _factory() in ("L", "R") or not isinstance(params, dict):
        return None
    if head in ("tc", "tc_rank"):
        return "atc_full" if _as_bool(params.get("full_atc", True), default=True) else "atc_core"
    return "aoo_full" if _as_bool(params.get("full_aoo", True), default=True) else "aoo_core"


def _sample_pct(params: Optional[Dict[str, Any]]) -> Optional[float]:
    if _factory() == "R":
        return None
    if not isinstance(params, dict) or "t_sample_pct" not in params:
        return None
    try:
        pct = float(params.get("t_sample_pct"))
    except (TypeError, ValueError):
        return None
    if pct != pct:
        return None
    return pct


def _marked_suite(raw: Any) -> str:
    role = str(raw or "").strip().lower()
    return role if role in ("live", "research") else ""


def _suite_of_item(item: Dict[str, Any]) -> str:
    """执行套以拟合时的套别为准。没标套别时，Holdout 0 仍只算执行套。"""
    marked = _marked_suite(item.get("suite"))
    if marked:
        return marked
    return "live" if item.get("holdout_trading_days") == 0 else "research"


def _holdout_days(params: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(params, dict) or "holdout_trading_days" not in params:
        return None
    try:
        return int(params.get("holdout_trading_days"))
    except (TypeError, ValueError):
        return None


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


def _shown_label(label: str, factory: str = "") -> str:
    """版本名只留时间、RankIC、状态，末尾补 R/L/T。"""
    drop = {"core", "full", "l2", "lambdarank"}
    kept = []
    for bit in str(label or "").split(" · "):
        text = bit.strip()
        low = text.lower()
        if not text or text.startswith("λ") or text.endswith("%") or low in drop or low in {"r", "l", "t"}:
            continue
        kept.append(text)
    fac = str(factory or "").strip().upper()
    if fac in {"R", "L", "T"}:
        kept.append(fac)
    return " · ".join(kept)


def _label(
    *,
    started_at: Any,
    status: str,
    badges: Dict[str, Any],
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
    if badges.get("formal"):
        bits.append("正式")
    elif badges.get("gate_pass") is True:
        bits.append("过闸")
    elif badges.get("gate_pass") is False:
        bits.append("未过闸")
    return " · ".join(bit for bit in bits if bit)


def _pretrain_label(label: str, params: Optional[Dict[str, Any]]) -> str:
    """大池预训练没有微调结果，版本名单独标出来。"""
    stage = str((params or {}).get("train_stage") or "").strip().lower()
    if stage != "pretrain" or "预训练" in str(label or ""):
        return label
    return f"{label} · 预训练"


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


def _copy_curve(src: str, dest: str) -> None:
    """曲线文件很小，整份带走。缺文件或空文件不写副本。"""
    if not src or not os.path.isfile(src):
        return
    try:
        size = os.path.getsize(src)
    except OSError:
        return
    if size <= 0:
        return
    try:
        with open(src, "rb") as handle:
            if size > 2_000_000:
                handle.seek(size - 2_000_000)
                handle.readline()
            data = handle.read()
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(data)
    except OSError:
        logger.exception("factory version curve copy failed dest=%s", dest)


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
    if fac not in ("L", "T", "R"):
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
    backend = {"L": "lgb", "R": "ridge"}.get(fac, "transformer")
    try:
        pid = int(job_d.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    from core.research.yhat_factory_weights import (
        discard_pending_weights,
        publish_pending_weights,
        remove_version_weights,
    )

    weights_ref = None
    if st in ("done", "partial"):
        weights_ref = publish_pending_weights(
            h,
            vid,
            run_id=str(job_d.get("run_id") or ""),
            pid=pid,
        )
    else:
        discard_pending_weights(h)
    if not weights_ref:
        remove_version_weights(h, vid)
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
        "weights_ref": weights_ref,
        "live_allowed": False,
        **eval_fields,
    }
    holdout_days = _holdout_days(params)
    marked = _marked_suite(job_d.get("suite"))
    if not marked:
        marked = "live" if holdout_days == 0 else "research"
    if marked == "live":
        holdout_days = 0
        if isinstance(params, dict):
            params = dict(params)
            params["holdout_trading_days"] = 0
            doc["fit_params"] = params
    doc["suite"] = marked
    atomic_write_json(_snap_path(h, vid), doc)
    log_src = desk._log_path(h)
    _copy_log_tail(log_src, _log_copy_path(h, vid))
    if fac == "T":
        _copy_curve(desk._fit_curve_path(h), _curve_copy_path(h, vid))
    item = {
        "id": vid,
        "model_id": doc["model_id"],
        "started_at": started_at,
        "finished_at": finished_at,
        "status": st,
        "action": action,
        "label": _pretrain_label(
            _label(started_at=started_at or finished_at, status=st, badges=badges),
            params,
        ),
        "badges": badges,
        "weights_ref": weights_ref,
        "suite": marked,
    }
    if holdout_days is not None:
        item["holdout_trading_days"] = holdout_days
    items = [it for it in _read_index(h) if str(it.get("id") or "") != vid]
    items.insert(0, item)
    drop = items[int(keep) :]
    items = items[: int(keep)]
    for old in drop:
        oid = _check_version_id(old.get("id"))
        if not oid:
            continue
        from core.research.yhat_factory_weights import remove_version_weights

        remove_version_weights(h, oid)
        for path in (_snap_path(h, oid), _log_copy_path(h, oid), _curve_copy_path(h, oid)):
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                logger.exception("factory version prune failed path=%s", path)
    _write_index(h, fac, items)
    return vid


def _stamp_weights_ref(head: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """列表项带上权重目录是否还在。旧索引没有该字段时按磁盘补。"""
    from core.research.yhat_factory_weights import version_weights_dir

    out: List[Dict[str, Any]] = []
    for it in items:
        row = dict(it)
        vid = str(row.get("id") or "")
        path = version_weights_dir(head, vid) if vid else ""
        has = bool(path) and os.path.isfile(os.path.join(path, "meta.json"))
        row["weights_ref"] = f"{vid}.weights" if has else None
        out.append(row)
    return out


def _with_holdout(head: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """旧索引没有 Holdout 时从归档参数补上。0 只属于执行套。"""
    out: List[Dict[str, Any]] = []
    for it in items:
        row = dict(it)
        doc = None
        if "holdout_trading_days" not in row or not _marked_suite(row.get("suite")):
            doc = _read_json(_snap_path(head, str(row.get("id") or "")))
        if isinstance(doc, dict):
            if "holdout_trading_days" not in row:
                days = _holdout_days(doc.get("fit_params") if isinstance(doc.get("fit_params"), dict) else None)
                if days is not None:
                    row["holdout_trading_days"] = days
            if not _marked_suite(row.get("suite")):
                marked = _marked_suite(doc.get("suite"))
                if marked:
                    row["suite"] = marked
        out.append(row)
    return out


def _filter_suite(items: List[Dict[str, Any]], suite: Optional[str]) -> List[Dict[str, Any]]:
    role = str(suite or "").strip().lower()
    if role == "live":
        return [it for it in items if _suite_of_item(it) == "live"]
    if role == "research":
        return [it for it in items if _suite_of_item(it) != "live"]
    return items


def latest_transformer_pretrain(head: str) -> Dict[str, Any]:
    """最近一次大池预训练的 Transformer 权重。微调从这里接着训。"""
    desk = _desk()
    with desk.factory_scope("T"):
        try:
            h = desk._validate_head(head)
        except ValueError as exc:
            return {"success": False, "ok": False, "error": str(exc)}
        from core.research.yhat_factory_weights import load_factory_weights

        for it in _read_index(h):
            vid = _check_version_id(it.get("id"))
            if not vid or str(it.get("status") or "") not in ("done", "partial"):
                continue
            snap = _read_json(_snap_path(h, vid)) or {}
            params = snap.get("fit_params") if isinstance(snap.get("fit_params"), dict) else {}
            stage = str(params.get("train_stage") or "")
            fs = str(params.get("feature_set") or "")
            if stage in ("finetune", "chain") or fs == "hs300":
                continue
            if stage != "pretrain" and fs not in ("pool", "pool1600"):
                continue
            loaded = load_factory_weights(h, vid, factory="T")
            if loaded.get("success") and isinstance(loaded.get("state_dict"), dict):
                loaded["pretrain_run_id"] = vid
                return loaded
    return {"success": False, "ok": False, "error": "还没有大池预训练权重"}


def list_factory_versions(
    head: str,
    *,
    factory: Optional[str] = None,
    suite: Optional[str] = None,
) -> Dict[str, Any]:
    desk = _desk()
    fac = desk.normalize_factory(factory)
    with desk.factory_scope(fac):
        try:
            h = desk._validate_head(head)
        except ValueError as exc:
            return {"success": False, "ok": False, "error": str(exc), "items": []}
        items = _filter_suite(_with_holdout(h, _stamp_weights_ref(h, _read_index(h))), suite)
        for it in items:
            it["label"] = _shown_label(str(it.get("label") or ""), _factory())
        role = str(suite or "").strip().lower()
        enabled_id = _enabled_map(h).get(role, "")
        if enabled_id and not any(str(it.get("id") or "") == enabled_id for it in items):
            enabled_id = ""
        return {
            "success": True,
            "ok": True,
            "factory": _factory(),
            "head": h,
            "suite": str(suite or ""),
            "enabled_id": enabled_id,
            "items": items,
        }


def enable_factory_version(
    head: str,
    version_id: str,
    *,
    suite: str,
    factory: Optional[str] = None,
) -> Dict[str, Any]:
    """把一条跑次定为该套的当前版本。研究套给回测，执行套只收 Holdout = 0、给 live。"""
    desk = _desk()
    role = str(suite or "").strip().lower()
    if role not in ("research", "live"):
        return {"success": False, "ok": False, "error": "suite 须为 research 或 live"}
    vid = _check_version_id(version_id)
    if not vid:
        return {"success": False, "ok": False, "error": "invalid run_id"}
    fac = desk.normalize_factory(factory)
    with desk.factory_scope(fac):
        try:
            h = desk._validate_head(head)
        except ValueError as exc:
            return {"success": False, "ok": False, "error": str(exc)}
        items = _read_index(h)
        rows = _with_holdout(h, items)
        hit = next((it for it in rows if str(it.get("id") or "") == vid), None)
        if hit is None:
            return {"success": False, "ok": False, "error": "版本不存在", "run_id": vid}
        marked = _suite_of_item(hit)
        if role == "live" and marked != "live":
            return {
                "success": False,
                "ok": False,
                "error": "执行套只启用拟合执行产出的版本",
                "run_id": vid,
            }
        if role == "research" and marked == "live":
            return {
                "success": False,
                "ok": False,
                "error": "拟合执行的版本只属于执行套",
                "run_id": vid,
            }
        enabled = _enabled_map(h)
        enabled[role] = vid
        _write_index(h, _factory(), items, enabled=enabled)
        return {
            "success": True,
            "ok": True,
            "factory": _factory(),
            "head": h,
            "suite": role,
            "run_id": vid,
            "enabled_id": vid,
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
        out = _desk().without_retired_param_warnings(dict(doc)) or dict(doc)
        out["log_tail"] = log_tail
        if fac == "T":
            from core.research.yhat_transformer.fit_curve import load_fit_curve

            out["fit_curve"] = load_fit_curve(_curve_copy_path(h, vid))
        else:
            out["fit_curve"] = None
        out["success"] = True
        out["ok"] = True
        return out


def delete_factory_version(
    head: str,
    version_id: str,
    *,
    factory: Optional[str] = None,
) -> Dict[str, Any]:
    """去掉一条跑次归档。不改 last / draft / 正在写的 fit.log。

    概览和拟合日志不把已删掉的终态跑次再当成上次拟合；那一步在读状态时做，不写回这里。
    """
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
        curve_path = _curve_copy_path(h, vid)
        known = any(str(it.get("id") or "") == vid for it in items)
        if not known and not os.path.isfile(snap):
            return {"success": False, "ok": False, "error": "版本不存在", "run_id": vid}
        from core.research.yhat_factory_weights import remove_version_weights

        remove_version_weights(h, vid)
        for path in (snap, log_path, curve_path):
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
