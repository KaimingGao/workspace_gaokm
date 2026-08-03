"""分组权 live 产物：晋升 / 回滚 / 健康检查（不写 signal_config.weights）。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence


SCHEMA_VERSION = 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dirs() -> None:
    from core.paths import CLUSTER_WEIGHTS_HISTORY_DIR, LIVE_DIR

    os.makedirs(LIVE_DIR, exist_ok=True)
    os.makedirs(CLUSTER_WEIGHTS_HISTORY_DIR, exist_ok=True)


def get_cluster_scoring_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    from core.signal.config import load_signal_config

    cfg = config or load_signal_config()
    raw = dict(cfg.get("cluster_scoring") or {})
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode not in ("off", "shadow", "active"):
        mode = "off"
    if not raw.get("enabled", False) and mode != "off":
        # enabled=false 强制 off（除非显式只读 shadow 调试——仍尊重 mode 若 enabled）
        mode = "off"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "top_n_per_group": max(1, min(int(raw.get("top_n_per_group") or 10), 10)),
        "max_names": max(1, min(int(raw.get("max_names") or 40), 80)),
        "min_coverage": float(raw.get("min_coverage") or 0.5),
        "max_age_days": max(1, min(int(raw.get("max_age_days") or 14), 90)),
        "auto_demote_on_stale": bool(raw.get("auto_demote_on_stale", True)),
    }


def load_active_cluster_weights(
    *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    from core.paths import CLUSTER_WEIGHTS_ACTIVE_PATH

    p = path or CLUSTER_WEIGHTS_ACTIVE_PATH
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("code_map"), dict):
            return data
    except Exception:
        return None
    return None


def lookup_code_weights(
    code: str,
    *,
    active: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """返回 {weights, cluster_label, cluster_id, version} 或 None。"""
    art = active if active is not None else load_active_cluster_weights()
    if not art:
        return None
    cmap = art.get("code_map") or {}
    code = str(code or "").strip()
    meta = cmap.get(code)
    if not isinstance(meta, dict):
        # 尝试去前缀匹配少见
        return None
    w = meta.get("weights")
    if not isinstance(w, dict) or not w:
        return None
    weights: Dict[str, float] = {}
    for k, v in w.items():
        try:
            weights[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    if not weights:
        return None
    return {
        "weights": weights,
        "cluster_label": meta.get("cluster_label") or meta.get("label"),
        "cluster_id": meta.get("cluster_id"),
        "version": art.get("version"),
        "weight_source": f"cluster:{meta.get('cluster_label') or meta.get('label') or '?'}",
    }


def _validate_artifact_for_promote(artifact: Dict[str, Any]) -> Optional[str]:
    cmap = artifact.get("code_map") if isinstance(artifact, dict) else None
    if not isinstance(cmap, dict) or not cmap:
        return "code_map 为空"
    ok = 0
    singletonish = 0
    by_label: Dict[str, int] = {}
    for code, meta in cmap.items():
        if not str(code).strip() or not isinstance(meta, dict):
            continue
        w = meta.get("weights")
        if not isinstance(w, dict) or not w:
            continue
        ok += 1
        lab = str(meta.get("cluster_label") or "?")
        by_label[lab] = by_label.get(lab, 0) + 1
    if ok < 2:
        return "至少需要 2 只带组权的映射"
    for lab, n in by_label.items():
        if n == 1:
            singletonish += 1
    if singletonish == len(by_label) and len(by_label) > 3:
        return "单票组过多，拒绝晋升（请重聚类或缩小 k）"
    return None


def promote_cluster_artifact(
    artifact: Dict[str, Any],
    *,
    note: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """研究产物 → active；旧 active 进 history。不写 signal_config.weights。"""
    from core.paths import (
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        CLUSTER_WEIGHTS_HISTORY_DIR,
    )

    err = _validate_artifact_for_promote(artifact)
    if err and not force:
        return {"success": False, "error": err, "task": "cluster_promote"}

    _ensure_dirs()
    prev = load_active_cluster_weights()
    version = 1
    if prev and prev.get("version") is not None:
        try:
            version = int(prev["version"]) + 1
        except (TypeError, ValueError):
            version = 1

    if prev and os.path.isfile(CLUSTER_WEIGHTS_ACTIVE_PATH):
        stamp = str(prev.get("promoted_at") or "prev").replace(":", "").replace("-", "")
        hist = os.path.join(
            CLUSTER_WEIGHTS_HISTORY_DIR,
            f"cluster_weights_v{prev.get('version', 0)}_{stamp}.json",
        )
        try:
            shutil.copy2(CLUSTER_WEIGHTS_ACTIVE_PATH, hist)
        except Exception:
            pass

    cmap = {}
    for code, meta in (artifact.get("code_map") or {}).items():
        c = str(code).strip()
        if not c or not isinstance(meta, dict):
            continue
        w = meta.get("weights")
        if not isinstance(w, dict) or not w:
            continue
        cmap[c] = {
            "cluster_id": meta.get("cluster_id"),
            "cluster_label": meta.get("cluster_label") or meta.get("label"),
            "weights": {str(k): float(v) for k, v in w.items() if _num(v)},
            "oos_passed": meta.get("oos_passed"),
        }

    active = {
        "success": True,
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "promoted_at": _iso_now(),
        "source_created_at": artifact.get("created_at"),
        "n_clusters": artifact.get("n_clusters") or len({
            m.get("cluster_label") for m in cmap.values()
        }),
        "n_mapped_codes": len(cmap),
        "code_map": cmap,
        "clusters": artifact.get("clusters"),
        "pool_book": artifact.get("pool_book"),
        "promote_note": str(note or "")[:500],
        "signal_config_touched": False,
        "note": "live 生效映射；权向量不在 signal_config.json",
    }
    with open(CLUSTER_WEIGHTS_ACTIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(active, f, ensure_ascii=False, indent=2)

    return {
        "success": True,
        "task": "cluster_promote",
        "version": version,
        "path": CLUSTER_WEIGHTS_ACTIVE_PATH,
        "n_mapped_codes": len(cmap),
        "promoted_at": active["promoted_at"],
        "previous_version": prev.get("version") if prev else None,
        "signal_config_touched": False,
        "note": "已晋升为 live active；请将 cluster_scoring.mode 设为 shadow/active",
    }


def rollback_cluster_weights(*, to_version: Optional[int] = None) -> Dict[str, Any]:
    """回滚到 history 中上一版或指定 version。"""
    from core.paths import (
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        CLUSTER_WEIGHTS_HISTORY_DIR,
    )

    _ensure_dirs()
    if not os.path.isdir(CLUSTER_WEIGHTS_HISTORY_DIR):
        return {"success": False, "error": "无历史版本可回滚"}

    files = sorted(
        [
            f
            for f in os.listdir(CLUSTER_WEIGHTS_HISTORY_DIR)
            if f.startswith("cluster_weights_") and f.endswith(".json")
        ],
        reverse=True,
    )
    if not files:
        return {"success": False, "error": "历史目录为空"}

    chosen = None
    if to_version is not None:
        for f in files:
            if f"v{int(to_version)}_" in f or f"_v{int(to_version)}_" in f:
                chosen = f
                break
            # cluster_weights_v3_...
            if f.startswith(f"cluster_weights_v{int(to_version)}_"):
                chosen = f
                break
        if not chosen:
            return {"success": False, "error": f"未找到 version={to_version}"}
    else:
        chosen = files[0]

    src = os.path.join(CLUSTER_WEIGHTS_HISTORY_DIR, chosen)
    # 当前 active 先备份
    cur = load_active_cluster_weights()
    if cur and os.path.isfile(CLUSTER_WEIGHTS_ACTIVE_PATH):
        stamp = _iso_now().replace(":", "").replace("-", "")
        bak = os.path.join(
            CLUSTER_WEIGHTS_HISTORY_DIR,
            f"cluster_weights_v{cur.get('version', 0)}_pre_rollback_{stamp}.json",
        )
        try:
            shutil.copy2(CLUSTER_WEIGHTS_ACTIVE_PATH, bak)
        except Exception:
            pass

    shutil.copy2(src, CLUSTER_WEIGHTS_ACTIVE_PATH)
    restored = load_active_cluster_weights()
    return {
        "success": True,
        "task": "cluster_rollback",
        "restored_from": chosen,
        "version": (restored or {}).get("version"),
        "n_mapped_codes": (restored or {}).get("n_mapped_codes"),
        "signal_config_touched": False,
    }


def save_cluster_draft(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """重聚类草稿（≠ active），待人审 promote。"""
    from core.paths import CLUSTER_WEIGHTS_DRAFT_PATH

    _ensure_dirs()
    draft = {
        "success": True,
        "is_draft": True,
        "schema_version": SCHEMA_VERSION,
        "created_at": artifact.get("created_at") or _iso_now(),
        "saved_at": _iso_now(),
        "code_map": artifact.get("code_map"),
        "clusters": artifact.get("clusters"),
        "pool_book": artifact.get("pool_book"),
        "n_clusters": artifact.get("n_clusters"),
        "n_mapped_codes": artifact.get("n_mapped_codes"),
        "note": "草稿；不自动生效，须 promote",
    }
    with open(CLUSTER_WEIGHTS_DRAFT_PATH, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)
    return {"success": True, "path": CLUSTER_WEIGHTS_DRAFT_PATH, "is_draft": True}


def load_cluster_draft() -> Optional[Dict[str, Any]]:
    from core.paths import CLUSTER_WEIGHTS_DRAFT_PATH

    if not os.path.isfile(CLUSTER_WEIGHTS_DRAFT_PATH):
        return None
    try:
        with open(CLUSTER_WEIGHTS_DRAFT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_active_cluster_book(book: Sequence[Dict[str, Any]], *, meta: Optional[dict] = None) -> str:
    """L4：落盘分池合并簿供 execution 只读。"""
    from core.paths import CLUSTER_BOOK_ACTIVE_PATH

    _ensure_dirs()
    payload = {
        "success": True,
        "updated_at": _iso_now(),
        "book": list(book or []),
        "meta": meta or {},
        "signal_config_touched": False,
        "note": "分池合并簿；execution 只读，不写全局 weights",
    }
    with open(CLUSTER_BOOK_ACTIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return CLUSTER_BOOK_ACTIVE_PATH


def load_active_cluster_book() -> Optional[Dict[str, Any]]:
    from core.paths import CLUSTER_BOOK_ACTIVE_PATH

    if not os.path.isfile(CLUSTER_BOOK_ACTIVE_PATH):
        return None
    try:
        with open(CLUSTER_BOOK_ACTIVE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _default_health_universe() -> List[str]:
    """优先纸面持仓（与分组宇宙一致），否则观察池。"""
    codes: List[str] = []
    try:
        from core.paper import load_paper
        from core.paths import PAPER_PATH

        paper = load_paper(PAPER_PATH)
        for h in paper.get("holdings") or []:
            if isinstance(h, dict) and h.get("stock_code"):
                codes.append(str(h["stock_code"]).strip())
    except Exception:
        pass
    if len(codes) >= 2:
        return codes[:40]
    try:
        from core.watching_store import read_watching

        return list((read_watching().get("watchlist") or [])[:40])
    except Exception:
        return codes[:40]


def assess_cluster_live_health(
    *,
    universe: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """覆盖率 / 陈旧 / 模式门禁（L3）。"""
    cs = get_cluster_scoring_cfg()
    active = load_active_cluster_weights()
    alerts: List[str] = []
    mapped = set((active or {}).get("code_map") or {})
    if universe is None:
        universe = _default_health_universe()
    uni = [str(c).strip() for c in (universe or []) if str(c).strip()]
    hit = sum(1 for c in uni if c in mapped)
    coverage = (hit / len(uni)) if uni else None

    age_days = None
    stale = False
    if active and active.get("promoted_at"):
        try:
            promoted = datetime.strptime(
                str(active["promoted_at"]).replace("Z", ""), "%Y-%m-%dT%H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - promoted).total_seconds() / 86400.0
            if age_days > float(cs["max_age_days"]):
                stale = True
                alerts.append(
                    f"映射陈旧 {age_days:.1f}d > {cs['max_age_days']}d"
                )
        except Exception:
            pass

    if coverage is not None and coverage < float(cs["min_coverage"]):
        alerts.append(
            f"覆盖率 {coverage:.0%} < 阈值 {float(cs['min_coverage']):.0%}"
        )

    mode = cs["mode"]
    demoted = False
    if mode == "active" and (stale or (coverage is not None and coverage < cs["min_coverage"])):
        alerts.append("建议降级：active 条件不满足（请改 shadow/off）")
        if cs.get("auto_demote_on_stale") and stale:
            demoted = True  # 调用方决定是否改 config；此处只报告

    allow_active = (
        bool(active)
        and not stale
        and (coverage is None or coverage >= float(cs["min_coverage"]))
    )

    return {
        "success": True,
        "task": "cluster_live_health",
        "mode": mode,
        "enabled": cs["enabled"],
        "has_active": bool(active),
        "version": (active or {}).get("version"),
        "promoted_at": (active or {}).get("promoted_at"),
        "n_mapped_codes": (active or {}).get("n_mapped_codes"),
        "universe_size": len(uni),
        "mapped_in_universe": hit,
        "coverage": round(coverage, 3) if coverage is not None else None,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "stale": stale,
        "allow_active": allow_active,
        "suggest_demote": demoted or (mode == "active" and not allow_active),
        "alerts": alerts,
        "signal_config_touched": False,
    }


def set_cluster_scoring_mode(
    mode: str,
    *,
    enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """仅改 signal_config.cluster_scoring 开关；不写 weights。"""
    from core.paths import SIGNAL_CONFIG_PATH
    from core.signal.config import load_signal_config

    mode = str(mode or "off").strip().lower()
    if mode not in ("off", "shadow", "active"):
        return {"success": False, "error": "mode 须为 off|shadow|active"}

    if mode == "active":
        health = assess_cluster_live_health()
        evidence = build_cluster_enable_evidence(health=health)
        if not health.get("allow_active") or not (evidence.get("gate") or {}).get(
            "ok"
        ):
            blockers = list((evidence.get("gate") or {}).get("blockers") or [])
            if not blockers:
                blockers = list(health.get("alerts") or []) or ["无 active 映射"]
            return {
                "success": False,
                "error": "启用证据包未通过，禁止 active："
                + ("；".join(str(b) for b in blockers[:6])),
                "health": health,
                "enable_evidence": evidence,
            }

    path = os.environ.get("INVESTMENT_SIGNAL_CONFIG", SIGNAL_CONFIG_PATH)
    raw: Dict[str, Any] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f) or {}
    cs = dict(raw.get("cluster_scoring") or {})
    cs["mode"] = mode
    if enabled is None:
        cs["enabled"] = mode != "off"
    else:
        cs["enabled"] = bool(enabled)
    raw["cluster_scoring"] = cs
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    # 清缓存
    try:
        from core.signal import config as cfg_mod

        cfg_mod._cached = None
    except Exception:
        pass
    load_signal_config(reload=True)
    return {
        "success": True,
        "task": "cluster_set_mode",
        "cluster_scoring": get_cluster_scoring_cfg(),
        "path": path,
        "signal_config_weights_touched": False,
        "note": "仅更新 cluster_scoring 开关；weights 未改",
    }


def refresh_cluster_book_daily() -> Dict[str, Any]:
    """L3：不重聚类，仅按 active map 重打分并刷新合并簿。"""
    from core.signal.cluster_rank import rank_cluster_pools

    health = assess_cluster_live_health()
    ranked = rank_cluster_pools(None, persist_book=True)
    return {
        "success": bool(ranked.get("success")),
        "task": "cluster_daily_refresh",
        "health": health,
        "rank": {
            "success": ranked.get("success"),
            "book": ranked.get("book"),
            "cluster_version": ranked.get("cluster_version"),
            "error": ranked.get("error"),
            "name_count": len(ranked.get("book") or []),
        },
        "signal_config_touched": False,
    }


def maybe_auto_demote_stale() -> Dict[str, Any]:
    """陈旧且 auto_demote_on_stale：active → shadow（只改 mode，不写 weights）。"""
    cs = get_cluster_scoring_cfg()
    health = assess_cluster_live_health()
    out: Dict[str, Any] = {
        "success": True,
        "task": "cluster_auto_demote",
        "demoted": False,
        "health": health,
        "cluster_scoring": cs,
        "signal_config_touched": False,
    }
    if cs.get("mode") != "active":
        return out
    if not health.get("suggest_demote") and not health.get("stale"):
        return out
    if not (cs.get("auto_demote_on_stale") and health.get("stale")):
        # 覆盖率不足：仅告警，不自动降（与陈旧区分）
        if health.get("suggest_demote") and not health.get("stale"):
            out["note"] = "建议降级但未自动执行（非陈旧）"
        return out
    mode_out = set_cluster_scoring_mode("shadow")
    out["demoted"] = bool(mode_out.get("success"))
    out["mode_result"] = mode_out
    out["cluster_scoring"] = get_cluster_scoring_cfg()
    out["signal_config_touched"] = False
    out["note"] = "映射陈旧，已自动降为 shadow"
    return out


def prepare_cluster_for_daily() -> Dict[str, Any]:
    """日更入口：陈旧降级 +（shadow|active 时）刷新分池簿。"""
    demote = maybe_auto_demote_stale()
    cs = get_cluster_scoring_cfg()
    mode = cs.get("mode") or "off"
    refresh = None
    if mode in ("shadow", "active") and load_active_cluster_weights():
        refresh = refresh_cluster_book_daily()
    return {
        "success": True,
        "task": "cluster_prepare_daily",
        "demote": demote,
        "refresh": refresh,
        "cluster_scoring": get_cluster_scoring_cfg(),
        "health": assess_cluster_live_health(),
        "signal_config_touched": False,
    }


def _pick_audit_codes(
    cmap: Dict[str, Any],
    book_rows: List[Any],
    n: int,
    *,
    offset: int = 0,
) -> List[str]:
    """
    选双分对照样本：优先合并簿，再按组轮询；``offset`` 旋转起点，避免永远同一批。
    """
    n = max(1, min(int(n), 12))
    off = abs(int(offset or 0))
    book_codes: List[str] = []
    for row in book_rows or []:
        code = str((row or {}).get("stock_code") or "").strip()
        if code and code in cmap and code not in book_codes:
            book_codes.append(code)

    by_label: Dict[str, List[str]] = {}
    # 簿内优先：按簿序入组，再补 code_map 其余
    seed_order = list(book_codes)
    for code in cmap.keys():
        c = str(code).strip()
        if c and c not in seed_order:
            seed_order.append(c)
    for c in seed_order:
        meta = cmap.get(c) or {}
        lab = str(meta.get("cluster_label") or "?")
        by_label.setdefault(lab, []).append(c)

    if not by_label:
        return []

    labels = sorted(by_label.keys())
    # 旋转：组起点 + 组内起点，刷新对照时换票
    label_start = off % len(labels)
    labels = labels[label_start:] + labels[:label_start]
    for lab in labels:
        members = by_label[lab]
        if not members:
            continue
        m0 = off % len(members)
        by_label[lab] = members[m0:] + members[:m0]

    codes: List[str] = []
    idx = 0
    while len(codes) < n:
        progressed = False
        for lab in labels:
            members = by_label.get(lab) or []
            if idx < len(members):
                c = members[idx]
                if c not in codes:
                    codes.append(c)
                    progressed = True
                if len(codes) >= n:
                    break
        if not progressed:
            break
        idx += 1
    return codes


def cluster_score_audit_sample(
    *,
    limit: int = 8,
    offset: Optional[int] = None,
    rotate: bool = False,
) -> Dict[str, Any]:
    """对照审计：优先分池簿样本，按组轮询取票，算 score_global vs score_cluster。

    ``rotate=True`` 或显式 ``offset``：旋转样本起点（刷新对照换一批票）。
    """
    active = load_active_cluster_weights()
    cs = get_cluster_scoring_cfg()
    mode = cs.get("mode") or "off"
    if not active or mode not in ("shadow", "active"):
        return {
            "success": True,
            "task": "cluster_score_audit",
            "rows": [],
            "mode": mode,
            "note": "非 shadow/active 或无 active 映射，跳过双分样本",
        }
    cmap = active.get("code_map") or {}
    n = max(1, min(int(limit), 12))
    if offset is None and rotate:
        offset = int(datetime.now(timezone.utc).timestamp() * 1000) % 10_000_000
    elif offset is None:
        offset = 0
    book = load_active_cluster_book() or {}
    codes = _pick_audit_codes(
        cmap,
        list(book.get("book") or []),
        n,
        offset=int(offset),
    )
    rows: List[Dict[str, Any]] = []
    fails: List[str] = []
    try:
        from core.signal.score_stock import score_stock
    except Exception as exc:
        return {
            "success": False,
            "task": "cluster_score_audit",
            "error": str(exc),
            "rows": [],
        }
    for code in codes:
        try:
            # 强制 shadow 语义：始终拉出全局/组权双分供对照（不影响交易 mode）
            result = score_stock(
                code,
                cluster_mode="shadow",
                skip_fundamentals=True,
            )
        except Exception as exc:
            fails.append(f"{code}:{exc}")
            continue
        if not isinstance(result, dict) or not result.get("success"):
            fails.append(f"{code}:{result.get('error') if isinstance(result, dict) else 'fail'}")
            continue
        item = result.get("signal_item") or {}
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "stock_code": code,
                "stock_name": item.get("stock_name") or result.get("stock_name") or code,
                "cluster_label": item.get("cluster_label")
                or (cmap.get(code) or {}).get("cluster_label"),
                "score_global": item.get("score_global"),
                "score_cluster": item.get("score_cluster"),
                "delta_vs_global": item.get("delta_vs_global"),
                "weight_source": item.get("weight_source"),
                "score": item.get("score"),
            }
        )
    out: Dict[str, Any] = {
        "success": True,
        "task": "cluster_score_audit",
        "mode": mode,
        "version": active.get("version"),
        "rows": rows,
        "sample_offset": int(offset),
        "sample_codes": list(codes),
        "sampled_at": _iso_now(),
        "signal_config_touched": False,
    }
    if not rows and fails:
        out["success"] = False
        out["error"] = f"打分失败 {len(fails)} 只：" + "；".join(fails[:3])
    elif fails:
        out["note"] = f"部分失败 {len(fails)}/{len(codes)}"
    elif rotate or int(offset) != 0:
        out["note"] = f"轮换样本 offset={int(offset)}"
    return out


def apply_cluster_live_shortcut(
    artifact: Optional[Dict[str, Any]] = None,
    *,
    from_draft: bool = True,
    note: str = "",
    mode: str = "shadow",
    refresh_book: bool = True,
) -> Dict[str, Any]:
    """一键：晋升 → 设 mode（默认 shadow）→ 刷新分池簿。

    省去「晋升 / 影子 / 分池排序」三连点；仍不写全局 weights。
    """
    art = artifact
    if from_draft or not art:
        art = load_cluster_draft() or art
    if not art or not art.get("code_map"):
        return {
            "success": False,
            "error": "无产物可应用（先跑 β 分组或传 artifact）",
            "task": "cluster_apply_shortcut",
        }

    promo = promote_cluster_artifact(art, note=note or "一键应用分组")
    if not promo.get("success"):
        return {**promo, "task": "cluster_apply_shortcut"}

    mode = str(mode or "shadow").strip().lower()
    if mode not in ("off", "shadow", "active"):
        mode = "shadow"
    # active 走健康门禁；失败则降级 shadow
    mode_out = set_cluster_scoring_mode(mode)
    if not mode_out.get("success") and mode == "active":
        mode_out = set_cluster_scoring_mode("shadow")
        mode_out["demoted_to_shadow"] = True
        mode_out["demote_reason"] = "active 未过健康检查，已用影子"

    rank_out = None
    if refresh_book and mode != "off":
        rank_out = refresh_cluster_book_daily()

    return {
        "success": True,
        "task": "cluster_apply_shortcut",
        "promote": promo,
        "mode": mode_out,
        "refresh": rank_out,
        "version": promo.get("version"),
        "cluster_scoring": get_cluster_scoring_cfg(),
        "signal_config_touched": False,
        "note": (
            f"已应用分组 v{promo.get('version')} · mode="
            f"{(mode_out.get('cluster_scoring') or {}).get('mode', mode)} · "
            "分池簿已刷新（若开启）"
        ),
    }


def build_cluster_enable_evidence(
    *,
    include_audit: bool = True,
    health: Optional[Dict[str, Any]] = None,
    audit: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    EP1：对照→启用证据包（JSON）。

    门禁：健康 allow_active；若有 OOS 记录且全部失败则禁止启用。
    """
    cs = get_cluster_scoring_cfg()
    active = load_active_cluster_weights()
    book_doc = load_active_cluster_book() or {}
    book_rows = list(book_doc.get("book") or [])
    meta = book_doc.get("meta") if isinstance(book_doc.get("meta"), dict) else {}
    h = health if isinstance(health, dict) else assess_cluster_live_health()

    top_n = meta.get("top_n_per_group") or cs.get("top_n_per_group")
    max_names = meta.get("max_names") or cs.get("max_names")
    name_count = len(book_rows)

    # OOS：来自 active 映射内 clusters[].oos_gate
    oos_pass = 0
    oos_fail = 0
    oos_unknown = 0
    for cl in (active or {}).get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        gate = cl.get("oos_gate") or {}
        if not isinstance(gate, dict) or not gate:
            oos_unknown += 1
            continue
        if gate.get("ok") and gate.get("passed"):
            oos_pass += 1
        elif gate.get("ok") is False or gate.get("passed") is False:
            oos_fail += 1
        else:
            oos_unknown += 1
    oos_summary = {
        "pass_count": oos_pass,
        "fail_count": oos_fail,
        "unknown_count": oos_unknown,
        "n_clusters": oos_pass + oos_fail + oos_unknown,
        "note": "来自 active 映射 clusters.oos_gate；无记录不拦启用",
    }

    # 换手估计：纸面持仓 vs 分池簿（只数，无报价）
    book_codes = {
        str(r.get("stock_code") or "").strip()
        for r in book_rows
        if r.get("stock_code")
    }
    held_codes: set = set()
    try:
        from core.paper import load_paper

        paper = load_paper()
        for hh in (paper or {}).get("holdings") or []:
            c = str(hh.get("stock_code") or "").strip()
            if c and float(hh.get("shares") or 0) > 0:
                held_codes.add(c)
    except Exception:
        held_codes = set()
    would_sell = sorted(held_codes - book_codes)
    would_buy = sorted(book_codes - held_codes)
    turnover_est = {
        "held_count": len(held_codes),
        "book_count": len(book_codes),
        "would_sell_count": len(would_sell),
        "would_buy_count": len(would_buy),
        "would_sell": would_sell[:12],
        "would_buy": would_buy[:12],
        "note": "相对当前纸面 vs 合并簿只数估计，非金额换手",
    }

    # 行业集中度简表（簿内）
    exposure_summary: Dict[str, Any] = {"sectors": [], "top_sector": None}
    try:
        from core.portfolio_optimize import _sector_for, load_sector_map

        smap = load_sector_map()
        sec_cnt: Dict[str, int] = {}
        for c in book_codes:
            sec = _sector_for(c, smap)
            sec_cnt[sec] = int(sec_cnt.get(sec) or 0) + 1
        total = sum(sec_cnt.values()) or 1
        sectors = [
            {
                "name": k,
                "count": v,
                "weight_pct": round(v / total * 100.0, 1),
            }
            for k, v in sorted(sec_cnt.items(), key=lambda x: -x[1])
        ]
        exposure_summary = {
            "sectors": sectors[:8],
            "top_sector": sectors[0] if sectors else None,
            "note": "按簿内只数占比（非市值）",
        }
    except Exception:
        pass

    if include_audit and audit is None and (cs.get("mode") in ("shadow", "active")):
        try:
            audit = cluster_score_audit_sample(limit=6)
        except Exception as exc:
            audit = {"success": False, "rows": [], "error": str(exc)}
    audit_rows = list((audit or {}).get("rows") or [])[:6]

    blockers: List[str] = []
    warnings: List[str] = []
    if not active:
        blockers.append("无 active 映射")
    if not h.get("allow_active"):
        blockers.extend(list(h.get("alerts") or []) or ["健康检查未通过"])
    if oos_fail > 0 and oos_pass == 0 and oos_unknown == 0:
        blockers.append(f"组 OOS 全部失败（{oos_fail}）")
    if name_count <= 0:
        warnings.append("分池簿为空 · 启用后请刷新簿再分池调仓")

    gate_ok = len(blockers) == 0
    return {
        "success": True,
        "task": "cluster_enable_evidence",
        "ok": gate_ok,
        "gate": {"ok": gate_ok, "blockers": blockers, "warnings": warnings},
        "top_n_per_group": top_n,
        "max_names": max_names,
        "name_count": name_count,
        "cluster_version": (active or {}).get("version"),
        "mode": cs.get("mode") or "off",
        "oos_summary": oos_summary,
        "turnover_est": turnover_est,
        "exposure_summary": exposure_summary,
        "score_audit_sample": {
            "rows": audit_rows,
            "count": len(audit_rows),
            "error": (audit or {}).get("error"),
        },
        "health": {
            "coverage": h.get("coverage"),
            "age_days": h.get("age_days"),
            "stale": h.get("stale"),
            "allow_active": h.get("allow_active"),
        },
        "note": "启用前证据包 · 健康门禁 + OOS/簿长/换手估计/行业简表/双分样本",
    }


def cluster_status_public(
    *,
    include_audit: bool = True,
    audit_rotate: bool = False,
    audit_offset: Optional[int] = None,
) -> Dict[str, Any]:
    """供 API/UI 的状态摘要（含落地下一步 + 可选双分样本）。"""
    from core.paths import (
        CLUSTER_BOOK_ACTIVE_PATH,
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        CLUSTER_WEIGHTS_DRAFT_PATH,
    )

    cs = get_cluster_scoring_cfg()
    active = load_active_cluster_weights()
    health = assess_cluster_live_health()
    draft = load_cluster_draft()
    book = load_active_cluster_book()
    paper_land = _paper_cluster_landed(active)
    mode = cs.get("mode") or "off"
    has_draft = bool(draft and draft.get("code_map"))
    has_active = bool(active)

    audit = None
    if include_audit and mode in ("shadow", "active") and has_active:
        try:
            audit = cluster_score_audit_sample(
                limit=8,
                offset=audit_offset,
                rotate=bool(audit_rotate),
            )
        except Exception as exc:
            audit = {"success": False, "rows": [], "error": str(exc)}

    evidence = build_cluster_enable_evidence(
        include_audit=False,
        health=health,
        audit=audit,
    )
    allow_active = bool(health.get("allow_active")) and bool(
        (evidence.get("gate") or {}).get("ok")
    )
    if not has_draft and not has_active:
        next_step = "run_cluster"
        next_label = "先跑分组"
    elif mode == "off" or not has_active:
        next_step = "apply_shadow"
        next_label = "① 对照"
    elif mode == "shadow":
        next_step = "enable_active" if allow_active else "fix_health"
        next_label = "② 启用" if allow_active else "修复健康/证据包后再启用"
    else:
        # mode=active：研究侧权责结束；调仓走侧栏交易执行页
        next_step = "go_follow"
        next_label = "已启用 · 侧栏进交易执行"

    return {
        "success": True,
        "cluster_scoring": cs,
        "active": {
            "exists": has_active,
            "path": CLUSTER_WEIGHTS_ACTIVE_PATH,
            "version": (active or {}).get("version"),
            "promoted_at": (active or {}).get("promoted_at"),
            "n_mapped_codes": (active or {}).get("n_mapped_codes"),
            "n_clusters": (active or {}).get("n_clusters"),
        },
        "draft": {
            "exists": has_draft,
            "path": CLUSTER_WEIGHTS_DRAFT_PATH,
            "saved_at": (draft or {}).get("saved_at"),
            "n_mapped_codes": (draft or {}).get("n_mapped_codes"),
            "n_clusters": (draft or {}).get("n_clusters"),
            "clusters": [
                {
                    "label": c.get("label"),
                    "member_count": c.get("member_count"),
                    "members": list(c.get("members") or [])[:12],
                }
                for c in ((draft or {}).get("clusters") or [])
                if isinstance(c, dict)
            ],
            "holdings_assignment": (draft or {}).get("holdings_assignment"),
        },
        "book": {
            "exists": bool(book),
            "path": CLUSTER_BOOK_ACTIVE_PATH,
            "updated_at": (book or {}).get("updated_at"),
            "name_count": len((book or {}).get("book") or []),
            "top_n_per_group": (
                ((book or {}).get("meta") or {}).get("top_n_per_group")
                if isinstance((book or {}).get("meta"), dict)
                else None
            )
            or cs.get("top_n_per_group"),
            "max_names": (
                ((book or {}).get("meta") or {}).get("max_names")
                if isinstance((book or {}).get("meta"), dict)
                else None
            )
            or cs.get("max_names"),
        },
        "health": health,
        "landing": {
            "next_step": next_step,
            "next_label": next_label,
            "can_apply": has_draft or has_active,
            "can_activate": allow_active and has_active,
            # 研究枢纽不调仓；就绪后跳转 /follow
            "ready_for_follow": mode == "active" and has_active,
            "can_paper": False,
            "paper_applied": bool(paper_land.get("applied")),
            "paper_applied_at": paper_land.get("applied_at"),
            "paper_cluster_version": paper_land.get("cluster_version"),
        },
        "enable_evidence": evidence,
        "audit_sample": audit,
        "note": "组权在 live 产物；调仓仅交易执行页；signal_config 仅开关 mode",
    }


def _paper_cluster_landed(active: Optional[dict]) -> Dict[str, Any]:
    """纸面是否已按当前 active 映射完成过分池调仓。"""
    if not active:
        return {"applied": False}
    try:
        from core.paper import load_paper

        paper = load_paper()
    except Exception:
        return {"applied": False}
    last = paper.get("last_cluster_pool") if isinstance(paper, dict) else None
    if not isinstance(last, dict):
        return {"applied": False}
    av = active.get("version")
    pv = last.get("cluster_version")
    if av is None or pv is None:
        return {
            "applied": False,
            "applied_at": last.get("applied_at"),
            "cluster_version": pv,
        }
    try:
        matched = int(av) == int(pv)
    except (TypeError, ValueError):
        matched = str(av) == str(pv)
    return {
        "applied": matched,
        "applied_at": last.get("applied_at"),
        "cluster_version": pv,
    }


def _num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
