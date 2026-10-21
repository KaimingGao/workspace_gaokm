"""物化结果的结构化 track：阶段 · 依赖 · 耗时 · 摘要。

Job / UI / 运维对照用；不替代嵌套的 ``l1`` / ``l2`` 明细报告。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _stage_status(*, planned: bool, report: Optional[Dict[str, Any]]) -> str:
    if not planned:
        return "skip"
    if not isinstance(report, dict):
        return "fail"
    if report.get("ok"):
        return "ok"
    return "fail"


def _kind_meta(do_l1: bool, do_tau: bool, do_l2: bool) -> Dict[str, str]:
    if do_l1 and not do_tau:
        return {"id": "materialize_open", "label": "日线物化"}
    if do_tau and not do_l1:
        return {"id": "materialize_tau", "label": "分钟 τ 物化"}
    return {"id": "materialize_l1l2", "label": "混合物化"}


def _l1_family_metrics(by_family: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(by_family, dict):
        return out
    for fam, row in by_family.items():
        if not isinstance(row, dict):
            continue
        compact: Dict[str, Any] = {"ok": bool(row.get("ok"))}
        elapsed = _as_float(row.get("elapsed_s"))
        if elapsed is not None:
            compact["elapsed_s"] = elapsed
        for key in ("n_stocks", "n_rows", "n_dates", "n_features", "n_panels"):
            n = _as_int(row.get(key))
            if n is not None:
                compact[key] = n
        err = str(row.get("error") or "").strip()
        if err:
            compact["error"] = err[:200]
        out[str(fam)] = compact
    return out


def _l1_stage(report: Optional[Dict[str, Any]], *, planned: bool) -> Dict[str, Any]:
    status = _stage_status(planned=planned, report=report)
    stage: Dict[str, Any] = {
        "id": "l1_open",
        "label": "L1 Feature Lake（open）",
        "status": status,
        "depends_on": ["l0_daily_bars"],
        "elapsed_s": None,
        "metrics": {},
    }
    if not planned:
        return stage
    doc = report if isinstance(report, dict) else {}
    stage["elapsed_s"] = _as_float(doc.get("elapsed_s"))
    by_fam = _l1_family_metrics(doc.get("by_family"))
    ok_fams = [k for k, v in by_fam.items() if v.get("ok")]
    fail_fams = [k for k, v in by_fam.items() if not v.get("ok")]
    stage["metrics"] = {
        "n_codes": _as_int(doc.get("n_codes")),
        "families": list(doc.get("families") or list(by_fam.keys())),
        "families_ok": ok_fams,
        "families_fail": fail_fams,
        "by_family": by_fam,
    }
    if doc.get("oo_rank_optional_failed"):
        stage["metrics"]["oo_rank_optional_failed"] = True
    note = str(doc.get("note") or "").strip()
    if note:
        stage["metrics"]["note"] = note[:200]
    err = str(doc.get("error") or "").strip()
    if err:
        stage["error"] = err[:240]
    return stage


def _tau_stage(report: Optional[Dict[str, Any]], *, planned: bool) -> Dict[str, Any]:
    status = _stage_status(planned=planned, report=report)
    stage: Dict[str, Any] = {
        "id": "l1_tau",
        "label": "L1 τ 网格（tc_HHMM）",
        "status": status,
        "depends_on": ["l0_daily_bars", "l0_minute_5m"],
        "elapsed_s": None,
        "metrics": {},
    }
    if not planned:
        return stage
    doc = report if isinstance(report, dict) else {}
    stage["elapsed_s"] = _as_float(doc.get("elapsed_s"))
    clocks = [str(c) for c in (doc.get("clocks") or [])]
    clocks_ok = _as_int(doc.get("clocks_ok"))
    if clocks_ok is None and isinstance(doc.get("by_clock"), dict):
        clocks_ok = sum(
            1 for row in doc["by_clock"].values() if isinstance(row, dict) and row.get("ok")
        )
    stage["metrics"] = {
        "n_codes": _as_int(doc.get("n_codes")),
        "clocks_n": len(clocks) or None,
        "clocks_ok": clocks_ok,
        "lake_eligible": _as_int(doc.get("lake_eligible")),
        "lake_span_days": _as_int(doc.get("lake_span_days")),
        "minute_attached": _as_int(doc.get("minute_attached")),
    }
    err = str(doc.get("error") or "").strip()
    if err:
        stage["error"] = err[:240]
    return stage


def _l2_clock_metrics(by_clock: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(by_clock, dict):
        return out
    for clock, row in by_clock.items():
        if not isinstance(row, dict):
            continue
        compact: Dict[str, Any] = {"ok": bool(row.get("ok"))}
        for key in (
            "cubes_hit",
            "cubes_built",
            "slim_days",
            "days_skipped",
            "slim_reject",
            "days_short_rebuild",
        ):
            n = _as_int(row.get(key))
            if n is not None:
                compact[key] = n
        err = str(row.get("error") or "").strip()
        if err:
            compact["error"] = err[:200]
        out[str(clock)] = compact
    return out


def _l2_stage(
    report: Optional[Dict[str, Any]],
    *,
    planned: bool,
    tau_path: bool,
) -> Dict[str, Any]:
    status = _stage_status(planned=planned, report=report)
    stage: Dict[str, Any] = {
        "id": "l2_tau" if tau_path else "l2_open",
        "label": "L2 DayCube（按钟）" if tau_path else "L2 DayCube（open）",
        "status": status,
        "depends_on": (
            ["l1_tau", "l0_minute_5m"] if tau_path else ["l1_open"]
        ),
        "elapsed_s": None,
        "metrics": {},
    }
    if not planned:
        return stage
    doc = report if isinstance(report, dict) else {}
    stage["elapsed_s"] = _as_float(doc.get("elapsed_s"))
    clocks = [str(c) for c in (doc.get("clocks") or [])]
    by_clock = _l2_clock_metrics(doc.get("by_clock"))
    clocks_ok = _as_int(doc.get("clocks_ok"))
    if clocks_ok is None:
        clocks_ok = sum(1 for row in by_clock.values() if row.get("ok"))
    metrics: Dict[str, Any] = {
        "n_codes": _as_int(doc.get("n_codes")),
        "dates_n": len(doc.get("dates") or []) or None,
        "clocks_n": len(clocks) or None,
        "clocks_ok": clocks_ok,
        "by_clock": by_clock,
    }
    for key in ("minute_days", "minute_span", "minute_skip"):
        if doc.get(key) is not None:
            metrics[key] = doc.get(key)
    incr = doc.get("l2_incremental")
    if isinstance(incr, dict) and incr:
        metrics["incremental"] = {
            k: incr.get(k)
            for k in ("pending_days", "cubes_reused", "note")
            if incr.get(k) is not None
        }
    if isinstance(doc.get("gc"), dict):
        metrics["gc"] = {
            k: doc["gc"].get(k)
            for k in ("removed_dirs", "kept_hashes", "ok")
            if k in doc["gc"]
        }
    stage["metrics"] = metrics
    err = str(doc.get("error") or "").strip()
    if err:
        stage["error"] = err[:240]
    return stage


def _headline(kind_label: str, stages: Sequence[Dict[str, Any]], elapsed_s: Optional[float]) -> str:
    bits: List[str] = [kind_label]
    for stage in stages:
        if stage.get("status") == "skip":
            continue
        label = str(stage.get("id") or stage.get("label") or "")
        short = {
            "l1_open": "L1",
            "l1_tau": "τL1",
            "l2_open": "L2",
            "l2_tau": "L2",
        }.get(label, label)
        status = str(stage.get("status") or "")
        elapsed = stage.get("elapsed_s")
        piece = short
        if status == "fail":
            piece = f"{short} FAIL"
        elif elapsed is not None:
            piece = f"{short} {elapsed}s"
        metrics = stage.get("metrics") if isinstance(stage.get("metrics"), dict) else {}
        if stage.get("id") == "l1_tau":
            cok = metrics.get("clocks_ok")
            cn = metrics.get("clocks_n")
            if cok is not None and cn is not None:
                piece = f"{piece} {cok}/{cn}"
        if stage.get("id") in ("l2_open", "l2_tau"):
            by_clock = metrics.get("by_clock") if isinstance(metrics.get("by_clock"), dict) else {}
            if len(by_clock) == 1 and "open" in by_clock:
                o = by_clock["open"] or {}
                if o.get("cubes_hit") is not None:
                    piece = (
                        f"{piece} hit {o.get('cubes_hit')}/built {o.get('cubes_built') or 0}"
                    )
            elif by_clock:
                hit = sum(int((r or {}).get("cubes_hit") or 0) for r in by_clock.values())
                built = sum(int((r or {}).get("cubes_built") or 0) for r in by_clock.values())
                piece = f"{piece} {len(by_clock)}钟 hit {hit}/built {built}"
            span = metrics.get("minute_span")
            if span:
                days = metrics.get("minute_days")
                piece = f"{piece} · 5m窗 {span}" + (f"·{days}日" if days is not None else "")
        bits.append(piece)
    if elapsed_s is not None:
        bits.append(f"总 {elapsed_s}s")
    return " · ".join(bits)


def build_materialize_track(out: Dict[str, Any] | None) -> Dict[str, Any]:
    """从 ``run_materialize`` 返回体合成可审计 track。"""
    doc = out if isinstance(out, dict) else {}
    do_l1 = bool(doc.get("do_l1"))
    do_tau = bool(doc.get("do_l1_tau_grid"))
    do_l2 = bool(doc.get("do_l2"))
    kind = _kind_meta(do_l1, do_tau, do_l2)
    stages: List[Dict[str, Any]] = []
    if do_l1:
        stages.append(_l1_stage(doc.get("l1"), planned=True))
    if do_tau:
        stages.append(_tau_stage(doc.get("l1_tau_grid"), planned=True))
    if do_l2:
        stages.append(
            _l2_stage(
                doc.get("l2"),
                planned=True,
                tau_path=bool(do_tau and not do_l1),
            )
        )
    # 混合物化：L2 可能含 open+分钟；依赖标成两者
    if do_l1 and do_tau and do_l2 and stages:
        last = stages[-1]
        if last.get("id") in ("l2_open", "l2_tau"):
            last["id"] = "l2"
            last["label"] = "L2 DayCube"
            last["depends_on"] = ["l1_open", "l1_tau"]

    elapsed = _as_float(doc.get("elapsed_s"))
    planned_n = sum(1 for s in stages if s.get("status") != "skip")
    ok_n = sum(1 for s in stages if s.get("status") == "ok")
    schema = None
    try:
        from core.research.feature_registry import FEATURE_SCHEMA

        schema = int(FEATURE_SCHEMA)
    except Exception:  # noqa: BLE001
        schema = None

    track: Dict[str, Any] = {
        "id": kind["id"],
        "label": kind["label"],
        "ok": bool(doc.get("ok")),
        "elapsed_s": elapsed,
        "codes_n": _as_int(doc.get("codes_n")),
        "schema": schema,
        "steps_ok": f"{ok_n}/{planned_n}" if planned_n else "0/0",
        "stages": stages,
        "headline": _headline(kind["label"], stages, elapsed),
    }
    err = str(doc.get("error") or "").strip()
    if err:
        track["error"] = err[:280]
    return track


__all__ = ["build_materialize_track"]
