"""分组因子系数 live 产物：晋升 / 回滚 / 健康检查（不写 signal_config.weights）。

真源为 code_map.return_model；weights 可由 |β| 派生供旧路径。

ŷ 门禁（FH0）：``mode=off`` 不算组 ŷ；``shadow`` 可算 ``score_cluster`` 对照；
仅 ``active`` 时组 β 写入 ``predicted_score`` / primary。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from core.io_atomic import atomic_write_json


SCHEMA_VERSION = 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dirs() -> None:
    from core.paths import CLUSTER_WEIGHTS_HISTORY_DIR, LIVE_DIR

    os.makedirs(LIVE_DIR, exist_ok=True)
    os.makedirs(CLUSTER_WEIGHTS_HISTORY_DIR, exist_ok=True)


def normalize_cluster_scoring_mode(
    mode: Optional[str],
    *,
    enabled: bool = True,
) -> str:
    """归一化 off|shadow|active；enabled=false 时强制 off。"""
    m = str(mode or "off").strip().lower()
    if m not in ("off", "shadow", "active"):
        m = "off"
    if not enabled:
        return "off"
    return m


def cluster_yhat_primary_allowed(mode: str) -> bool:
    """仅 active 时组 β 可写入 primary / predicted_score 主分。"""
    return normalize_cluster_scoring_mode(mode) == "active"


def cluster_yhat_shadow_compute_allowed(mode: str) -> bool:
    """shadow/active 可算 score_cluster 对照；off 不算组 ŷ。"""
    return normalize_cluster_scoring_mode(mode) in ("shadow", "active")


def get_cluster_scoring_cfg(config: Optional[dict] = None) -> Dict[str, Any]:
    from core.signal.config import load_signal_config

    cfg = config or load_signal_config()
    raw = dict(cfg.get("cluster_scoring") or {})
    mode = normalize_cluster_scoring_mode(
        raw.get("mode"), enabled=bool(raw.get("enabled", False))
    )
    return {
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "top_n_per_group": max(1, min(int(raw.get("top_n_per_group") or 10), 10)),
        "max_names": max(1, min(int(raw.get("max_names") or 40), 80)),
        "min_coverage": float(raw.get("min_coverage") or 0.5),
        # B4：refit_max_age_days 与 max_age_days 同义（配置任一侧即可）
        "max_age_days": max(
            1,
            min(
                int(
                    raw.get("refit_max_age_days")
                    if raw.get("refit_max_age_days") is not None
                    else (raw.get("max_age_days") or 14)
                ),
                90,
            ),
        ),
        "refit_max_age_days": max(
            1,
            min(
                int(
                    raw.get("refit_max_age_days")
                    if raw.get("refit_max_age_days") is not None
                    else (raw.get("max_age_days") or 14)
                ),
                90,
            ),
        ),
        "auto_demote_on_stale": bool(raw.get("auto_demote_on_stale", True)),
        # FH0：缺省从 1.0 收紧到 0.5（配置显式写出仍优先生效）
        "max_oos_fail_rate": max(
            0.0,
            min(
                float(
                    raw.get("max_oos_fail_rate")
                    if raw.get("max_oos_fail_rate") is not None
                    else 0.5
                ),
                1.0,
            ),
        ),
        "min_yhat_rolling_ic": float(
            raw.get("min_yhat_rolling_ic") if raw.get("min_yhat_rolling_ic") is not None else 0.0
        ),
        # FS1：默认硬拦（与 DEFAULT_SIGNAL_CONFIG 对齐）；配置显式 false 可关
        "block_active_on_yhat_ic": bool(
            raw.get("block_active_on_yhat_ic")
            if raw.get("block_active_on_yhat_ic") is not None
            else True
        ),
        "min_sector_map_coverage": max(
            0.0,
            min(
                float(
                    raw.get("min_sector_map_coverage")
                    if raw.get("min_sector_map_coverage") is not None
                    else 0.5
                ),
                1.0,
            ),
        ),
    }


def load_active_cluster_weights(
    *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """读 live 组权：优先指针指向的版本化 artifact，否则回退 active 镜像。"""
    from core.signal.cluster_pointer import resolve_cluster_weights_path

    p = path or resolve_cluster_weights_path()
    if not p or not os.path.isfile(p):
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
    """返回 {weights, cluster_label, …}；无存盘权时由 |β| 派生。

    派生权仅诊断/兼容；选股真源是 return_model。
    """
    from core.signal.factor_coefs import display_weights_from_return_model

    art = active if active is not None else load_active_cluster_weights()
    if not art:
        return None
    cmap = art.get("code_map") or {}
    code = str(code or "").strip()
    meta = cmap.get(code)
    if not isinstance(meta, dict):
        return None
    lab = meta.get("cluster_label") or meta.get("label")
    rm = meta.get("return_model")
    if not isinstance(rm, dict):
        for cl in art.get("clusters") or []:
            if not isinstance(cl, dict):
                continue
            if str(cl.get("label") or "") == str(lab or "") and isinstance(
                cl.get("return_model"), dict
            ):
                rm = cl.get("return_model")
                break

    weight_source = f"cluster:{lab or '?'}"
    weights: Dict[str, float] = {}
    w = meta.get("weights")
    if isinstance(w, dict) and w:
        for k, v in w.items():
            try:
                weights[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        if meta.get("weights_derived_from_beta"):
            weight_source = "derived_from_beta"
    if not weights:
        derived = display_weights_from_return_model(
            rm if isinstance(rm, dict) else None
        )
        if derived:
            weights = derived
            weight_source = "derived_from_beta"
    if not weights and not isinstance(rm, dict):
        return None
    if not weights and isinstance(rm, dict):
        # 有系数但无法派生（全零）时仍返回映射，供 return_model 路径
        return {
            "weights": {},
            "cluster_label": lab,
            "cluster_id": meta.get("cluster_id"),
            "version": art.get("version"),
            "weight_source": "derived_from_beta",
            "return_model": rm,
        }
    return {
        "weights": weights,
        "cluster_label": lab,
        "cluster_id": meta.get("cluster_id"),
        "version": art.get("version"),
        "weight_source": weight_source,
        "return_model": rm if isinstance(rm, dict) else meta.get("return_model"),
    }


def lookup_code_return_model(
    code: str,
    *,
    active: Optional[Dict[str, Any]] = None,
):
    """返回该票所属组的 ``ReturnScoreModel``，无则 None。"""
    from core.signal.return_score import ReturnScoreModel

    art = active if active is not None else load_active_cluster_weights()
    if not art:
        return None
    cmap = art.get("code_map") or {}
    code = str(code or "").strip()
    meta = cmap.get(code)
    if not isinstance(meta, dict):
        return None
    rm = meta.get("return_model")
    if not isinstance(rm, dict):
        # 回退：按组 label 从 clusters 取
        lab = str(meta.get("cluster_label") or meta.get("label") or "")
        for cl in art.get("clusters") or []:
            if not isinstance(cl, dict):
                continue
            if str(cl.get("label") or "") == lab and isinstance(cl.get("return_model"), dict):
                rm = cl.get("return_model")
                break
    return ReturnScoreModel.from_dict(rm) if isinstance(rm, dict) else None


def load_cluster_return_models_by_code(
    *,
    active: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """code → ReturnScoreModel（仅含有分组收益分模型的映射）。"""
    from core.signal.return_score import ReturnScoreModel

    art = active if active is not None else load_active_cluster_weights()
    out: Dict[str, Any] = {}
    if not art:
        return out
    by_label: Dict[str, Any] = {}
    for cl in art.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        lab = str(cl.get("label") or "")
        rm = cl.get("return_model")
        if lab and isinstance(rm, dict) and rm.get("coefficients"):
            model = ReturnScoreModel.from_dict(rm)
            if model is not None:
                by_label[lab] = model
    for code, meta in (art.get("code_map") or {}).items():
        c = str(code or "").strip()
        if not c or not isinstance(meta, dict):
            continue
        rm = meta.get("return_model")
        model = ReturnScoreModel.from_dict(rm) if isinstance(rm, dict) else None
        if model is None:
            lab = str(meta.get("cluster_label") or meta.get("label") or "")
            model = by_label.get(lab)
        if model is not None:
            out[c] = model
    return out


def _validate_artifact_for_promote(artifact: Dict[str, Any]) -> Optional[str]:
    from core.signal.factor_coefs import has_factor_coefficients

    cmap = artifact.get("code_map") if isinstance(artifact, dict) else None
    if not isinstance(cmap, dict) or not cmap:
        return "code_map 为空"
    # B1：样本指纹 / 验证宇宙不足则拒绝 promote（force 可豁免）
    fp = artifact.get("sample_fingerprint")
    if isinstance(fp, dict) and fp.get("promote_ok") is False:
        blockers = []
        for b in fp.get("blockers") or []:
            bs = str(b)
            # 历史产物里单票/双票组的 n_names<3 不再硬拦
            if ("n_names=" in bs and "min_names=" in bs) or bs.startswith("组内：n_names="):
                try:
                    part = bs.split("n_names=")[1].split("<")[0].strip()
                    if int(float(part)) < 3:
                        continue
                except Exception:
                    pass
            blockers.append(bs)
        if blockers:
            return "样本指纹未过：" + ("；".join(blockers[:4]) or "n 不足")
    try:
        from core.validation_universe import universe_sample_gate

        ug = universe_sample_gate()
        if not ug.get("ok"):
            return "验证宇宙不足：" + ("；".join(ug.get("blockers") or []) or "min_codes")
    except Exception:
        pass
    # 组表 return_model 回填（校验前）
    by_label_rm: Dict[str, Any] = {}
    for cl in (artifact.get("clusters") or []):
        if not isinstance(cl, dict):
            continue
        lab = str(cl.get("label") or cl.get("cluster_label") or "")
        rm = cl.get("return_model")
        if lab and isinstance(rm, dict) and has_factor_coefficients(rm):
            by_label_rm[lab] = rm
        # 组级指纹不足也拦
        gfp = None
        if isinstance(rm, dict):
            gfp = rm.get("sample_fingerprint")
        if not isinstance(gfp, dict):
            ols = cl.get("ols") if isinstance(cl.get("ols"), dict) else {}
            gfp = ols.get("sample_fingerprint")
        if isinstance(gfp, dict) and gfp.get("promote_ok") is False:
            # 小组员数不足 3：聚类允许单票/双票组，不因此拒晋升
            bad = []
            for b in gfp.get("blockers") or []:
                bs = str(b)
                if "n_names=" in bs and "min_names=" in bs:
                    try:
                        n_part = bs.split("n_names=")[1].split("<")[0].strip()
                        if int(float(n_part)) < 3:
                            continue
                    except Exception:
                        pass
                bad.append(bs)
            if not bad:
                continue
            return (
                f"组 {lab or '?'} 样本不足："
                + ("；".join(bad[:3]) or "n 不足")
            )
    ok = 0
    singletonish = 0
    by_label: Dict[str, int] = {}
    for code, meta in cmap.items():
        if not str(code).strip() or not isinstance(meta, dict):
            continue
        rm = meta.get("return_model")
        if not has_factor_coefficients(rm if isinstance(rm, dict) else None):
            lab = str(meta.get("cluster_label") or meta.get("label") or "")
            rm = by_label_rm.get(lab)
        if not has_factor_coefficients(rm if isinstance(rm, dict) else None):
            continue
        ok += 1
        lab = str(meta.get("cluster_label") or "?")
        by_label[lab] = by_label.get(lab, 0) + 1
    if ok < 2:
        return "至少需要 2 只带 return_model.coefficients 的映射"
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
    """研究产物 → 版本化 artifact + 原子指针切换；旧版进 history。不写 signal_config.weights。"""
    from core.paths import (
        CLUSTER_WEIGHTS_ACTIVE_PATH,
        CLUSTER_WEIGHTS_HISTORY_DIR,
    )
    from core.signal.cluster_pointer import (
        append_promote_audit,
        publish_cluster_weights_doc,
        resolve_cluster_weights_path,
    )

    err = _validate_artifact_for_promote(artifact)
    if err and not force:
        return {"success": False, "error": err, "task": "cluster_promote"}

    # FM0 · 伪/proxy 因子权重硬门（artifact 内遗留 weights）
    try:
        from core.signal.factor_health import guard_weights_for_promote

        merged_w: Dict[str, float] = {}
        for meta in (artifact.get("code_map") or {}).values():
            if not isinstance(meta, dict):
                continue
            w = meta.get("weights")
            if isinstance(w, dict):
                for k, v in w.items():
                    try:
                        merged_w[str(k)] = float(v)
                    except (TypeError, ValueError):
                        pass
        for cl in artifact.get("clusters") or []:
            if not isinstance(cl, dict):
                continue
            w = cl.get("weights")
            if isinstance(w, dict):
                for k, v in w.items():
                    try:
                        merged_w[str(k)] = float(v)
                    except (TypeError, ValueError):
                        pass
        if merged_w:
            guard = guard_weights_for_promote(merged_w, force=force)
            if guard.get("blocked"):
                return {
                    "success": False,
                    "error": guard.get("error") or "factor_health_blocked",
                    "task": "cluster_promote",
                    "factor_health": guard.get("factor_health"),
                }
            if guard.get("forced"):
                try:
                    from core.signal.cluster_pointer import append_promote_audit

                    append_promote_audit(
                        {
                            "event": "factor_health_force",
                            "note": note or "",
                            "blockers": (guard.get("factor_health") or {}).get("blockers"),
                        }
                    )
                except Exception:
                    pass
    except Exception:
        pass

    _ensure_dirs()
    prev = load_active_cluster_weights()
    version = 1
    if prev and prev.get("version") is not None:
        try:
            version = int(prev["version"]) + 1
        except (TypeError, ValueError):
            version = 1

    prev_path = resolve_cluster_weights_path()
    if prev and prev_path and os.path.isfile(prev_path):
        stamp = str(prev.get("promoted_at") or "prev").replace(":", "").replace("-", "")
        hist = os.path.join(
            CLUSTER_WEIGHTS_HISTORY_DIR,
            f"cluster_weights_v{prev.get('version', 0)}_{stamp}.json",
        )
        try:
            shutil.copy2(prev_path, hist)
        except Exception as e:
            # FH4：历史备份失败可见，不阻断晋升
            hist_warn = f"history_backup_failed:{e}"
        else:
            hist_warn = None
    else:
        hist_warn = None

    from core.signal.factor_coefs import (
        display_weights_from_return_model,
        has_factor_coefficients,
    )

    by_label_rm: Dict[str, Any] = {}
    for cl in artifact.get("clusters") or []:
        if not isinstance(cl, dict):
            continue
        lab = str(cl.get("label") or cl.get("cluster_label") or "")
        rm = cl.get("return_model")
        if lab and isinstance(rm, dict) and has_factor_coefficients(rm):
            by_label_rm[lab] = rm

    cmap = {}
    for code, meta in (artifact.get("code_map") or {}).items():
        c = str(code).strip()
        if not c or not isinstance(meta, dict):
            continue
        lab = meta.get("cluster_label") or meta.get("label")
        rm = meta.get("return_model")
        if not has_factor_coefficients(rm if isinstance(rm, dict) else None):
            rm = by_label_rm.get(str(lab or ""))
        if not has_factor_coefficients(rm if isinstance(rm, dict) else None):
            # 兼容旧产物：仅有 weights、无 return_model 时仍可晋升（force 或遗留）
            w_only = meta.get("weights")
            if not (isinstance(w_only, dict) and w_only):
                continue
            entry = {
                "cluster_id": meta.get("cluster_id"),
                "cluster_label": lab,
                "weights": {str(k): float(v) for k, v in w_only.items() if _num(v)},
                "oos_passed": meta.get("oos_passed"),
            }
            cmap[c] = entry
            continue
        derived = display_weights_from_return_model(rm)
        stored_w = meta.get("weights")
        used_derived = False
        if isinstance(stored_w, dict) and stored_w:
            w = stored_w
        elif derived:
            w = derived
            used_derived = True
        else:
            w = None
        entry = {
            "cluster_id": meta.get("cluster_id"),
            "cluster_label": lab,
            "return_model": rm,
            "oos_passed": meta.get("oos_passed"),
        }
        if isinstance(w, dict) and w:
            entry["weights"] = {str(k): float(v) for k, v in w.items() if _num(v)}
            if used_derived:
                entry["weights_derived_from_beta"] = True
        cmap[c] = entry

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
        "n_codes_with_coefs": sum(
            1
            for m in cmap.values()
            if has_factor_coefficients(m.get("return_model"))
        ),
        "code_map": cmap,
        "clusters": artifact.get("clusters"),
        "pool_book": artifact.get("pool_book"),
        "promote_note": str(note or "")[:500],
        "signal_config_touched": False,
        "note": "live 因子系数映射；真源=return_model；weights 可选派生",
    }
    published = publish_cluster_weights_doc(active, note=note or f"promote v{version}")
    if not published.get("success"):
        return {
            "success": False,
            "error": published.get("error") or "指针切换失败",
            "task": "cluster_promote",
            "previous_version": prev.get("version") if prev else None,
        }

    if force and err:
        append_promote_audit(
            {
                "action": "cluster_promote_force",
                "version": version,
                "validation_error": err,
                "note": str(note or "")[:200],
            }
        )

    warnings: List[str] = []
    if hist_warn:
        warnings.append(hist_warn)

    manifest = None
    try:
        from core.live_config_manifest import write_live_config_manifest

        manifest = write_live_config_manifest(note=f"after cluster_promote v{version}")
    except Exception as e:
        warnings.append(f"live_manifest_write_failed:{e}")

    return {
        "success": True,
        "task": "cluster_promote",
        "version": version,
        "path": published.get("artifact") or CLUSTER_WEIGHTS_ACTIVE_PATH,
        "pointer": (published.get("pointer") or {}).get("path"),
        "mirror": published.get("mirror"),
        "n_mapped_codes": len(cmap),
        "promoted_at": active["promoted_at"],
        "previous_version": prev.get("version") if prev else None,
        "signal_config_touched": False,
        "warnings": warnings,
        "live_manifest": {
            "consistent": (manifest or {}).get("consistent"),
            "alerts": (manifest or {}).get("alerts") or [],
            "path": (manifest or {}).get("path"),
            "error": None if manifest else (warnings[-1] if warnings else "manifest_missing"),
        }
        if manifest or warnings
        else None,
        "note": "已晋升为 live（指针已切换）；请将 cluster_scoring.mode 设为 shadow/active",
    }


def rollback_cluster_weights(*, to_version: Optional[int] = None) -> Dict[str, Any]:
    """回滚到 history 中上一版或指定 version（经指针原子切换）。"""
    from core.paths import CLUSTER_WEIGHTS_HISTORY_DIR
    from core.signal.cluster_pointer import (
        append_promote_audit,
        publish_cluster_weights_doc,
        resolve_cluster_weights_path,
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
            if f.startswith(f"cluster_weights_v{int(to_version)}_"):
                chosen = f
                break
        if not chosen:
            return {"success": False, "error": f"未找到 version={to_version}"}
    else:
        chosen = files[0]

    src = os.path.join(CLUSTER_WEIGHTS_HISTORY_DIR, chosen)
    cur = load_active_cluster_weights()
    cur_path = resolve_cluster_weights_path()
    warnings: List[str] = []
    if cur and cur_path and os.path.isfile(cur_path):
        stamp = _iso_now().replace(":", "").replace("-", "")
        bak = os.path.join(
            CLUSTER_WEIGHTS_HISTORY_DIR,
            f"cluster_weights_v{cur.get('version', 0)}_pre_rollback_{stamp}.json",
        )
        try:
            shutil.copy2(cur_path, bak)
        except Exception as e:
            warnings.append(f"pre_rollback_backup_failed:{e}")

    try:
        with open(src, encoding="utf-8") as f:
            restored_doc = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"读取历史失败: {e}"}
    if not isinstance(restored_doc, dict) or not isinstance(
        restored_doc.get("code_map"), dict
    ):
        return {"success": False, "error": "历史 artifact 无效"}

    # 回滚发布：保留历史 version 号，bump 为新指针版本以免覆盖
    try:
        new_ver = int((cur or {}).get("version") or 0) + 1
    except (TypeError, ValueError):
        new_ver = 1
    restored_doc = dict(restored_doc)
    restored_doc["version"] = new_ver
    restored_doc["rolled_back_from"] = chosen
    restored_doc["promoted_at"] = _iso_now()
    published = publish_cluster_weights_doc(
        restored_doc, note=f"rollback from {chosen}"
    )
    if not published.get("success"):
        return {
            "success": False,
            "error": published.get("error") or "回滚指针切换失败",
            "task": "cluster_rollback",
        }
    append_promote_audit(
        {
            "action": "cluster_rollback",
            "version": new_ver,
            "restored_from": chosen,
        }
    )
    try:
        from core.live_config_manifest import write_live_config_manifest

        write_live_config_manifest(note=f"after cluster_rollback v{new_ver}")
    except Exception as e:
        warnings.append(f"live_manifest_write_failed:{e}")

    restored = load_active_cluster_weights()
    return {
        "success": True,
        "task": "cluster_rollback",
        "restored_from": chosen,
        "version": (restored or {}).get("version"),
        "n_mapped_codes": (restored or {}).get("n_mapped_codes"),
        "pointer": (published.get("pointer") or {}).get("path"),
        "warnings": warnings,
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
    atomic_write_json(CLUSTER_WEIGHTS_DRAFT_PATH, draft)
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
    atomic_write_json(CLUSTER_BOOK_ACTIVE_PATH, payload)
    # 昨日复盘：按会话交易日冻结 ŷ 账本（失败不影响落书）
    try:
        from core.market_calendar import resolve_session_date
        from core.score_ledger import freeze_from_cluster_book

        freeze_from_cluster_book(as_of=resolve_session_date(), book_doc=payload)
    except Exception:
        pass
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


def _save_active_doc(active: Dict[str, Any]) -> str:
    """写回当前 live 组权（OOS 补丁等）：经指针发布，避免半文件。"""
    from core.signal.cluster_pointer import publish_cluster_weights_doc

    _ensure_dirs()
    published = publish_cluster_weights_doc(
        active, note=f"save_active_doc v{active.get('version')}"
    )
    if not published.get("success"):
        raise RuntimeError(published.get("error") or "save_active_doc failed")
    return str(published.get("artifact") or "")


def ensure_active_cluster_oos_gates(
    *,
    persist: bool = True,
    force: bool = False,
    lookback: int = 80,
    horizon_days: int = 3,
    oos_tol_pp: float = 1.0,
) -> Dict[str, Any]:
    """给 live active 各组补算 / 刷新 ``oos_gate``（heuristic 基线 vs 组 ŷ）。"""
    from core.signal.cluster_live_evidence import _summarize_cluster_oos
    from core.signal.config import load_signal_config
    from core.signal.weight_oos_gate import evaluate_research_oos
    from core.research.oos_slim import slim_oos_gate

    active = load_active_cluster_weights()
    if not active:
        return {"success": False, "error": "无 active 映射", "filled": 0}
    clusters = list(active.get("clusters") or [])
    if not clusters:
        return {"success": False, "error": "active 无 clusters", "filled": 0}

    need = force or any(
        not (isinstance(cl.get("oos_gate"), dict) and cl.get("oos_gate"))
        for cl in clusters
        if isinstance(cl, dict)
    )
    if not need:
        return {
            "success": True,
            "filled": 0,
            "skipped_existing": True,
            "oos_summary": _summarize_cluster_oos(clusters),
        }

    cfg = load_signal_config() or {}
    cur_w = dict(cfg.get("weights") or {})
    filled = 0
    lb = max(40, min(int(lookback or 80), 90))
    hz = max(1, min(int(horizon_days or 3), 10))

    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        if (
            not force
            and isinstance(cl.get("oos_gate"), dict)
            and cl.get("oos_gate")
        ):
            continue
        members = [str(c).strip() for c in (cl.get("members") or []) if str(c).strip()]
        rm = cl.get("return_model") if isinstance(cl.get("return_model"), dict) else {}
        if not rm.get("coefficients"):
            cmap0 = (active.get("code_map") or {}) if isinstance(active, dict) else {}
            for m in members:
                entry = cmap0.get(m) or {}
                cand = entry.get("return_model") if isinstance(entry, dict) else None
                if isinstance(cand, dict) and cand.get("coefficients"):
                    rm = cand
                    break
        label = cl.get("label")
        if cl.get("singleton") or len(members) < 2:
            gate = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "cluster_too_small",
                "note": "组成员不足 2 只，跳过组内 Top-K OOS。",
                "stock_count": len(members),
                "cluster_label": label,
                "scope": "cluster_members",
            }
        elif not rm.get("coefficients"):
            gate = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "no_return_model",
                "note": "组无 return_model（ŷ），跳过 OOS。",
                "cluster_label": label,
                "scope": "cluster_members",
            }
        elif not cur_w:
            gate = {
                "ok": False,
                "passed": False,
                "skipped": True,
                "reason": "no_baseline_weights",
                "note": "无全局人工权，无法跑 heuristic 基线。",
                "cluster_label": label,
                "scope": "cluster_members",
            }
        else:
            top_k = 1 if len(members) <= 2 else min(2, len(members))
            gate = evaluate_research_oos(
                codes=members,
                research_models_by_code={m: rm for m in members},
                baseline_weights=cur_w,
                watching_limit=min(40, len(members)),
                min_names=2,
                lookback=lb,
                top_k=top_k,
                horizon_days=hz,
                oos_tol_pp=float(oos_tol_pp),
                ridge_lambda=float(rm.get("ridge_lambda") or 0.0),
            )
            gate = dict(gate)
            gate["scope"] = "cluster_members"
            gate["cluster_label"] = label
            if gate.get("note"):
                gate["note"] = (
                    str(gate["note"])
                    + " 补算自 live active；基线=heuristic；研究臂=ŷ。"
                )
        slim = slim_oos_gate(gate) or gate
        cl["oos_gate"] = slim
        cl["oos_passed"] = bool(
            slim.get("ok") and slim.get("passed") and not slim.get("skipped")
        )
        filled += 1

    active["clusters"] = clusters
    cmap = active.get("code_map") if isinstance(active.get("code_map"), dict) else {}
    by_label = {str(c.get("label")): c for c in clusters if isinstance(c, dict)}
    for _code, meta in list(cmap.items()):
        if not isinstance(meta, dict):
            continue
        lab = str(meta.get("cluster_label") or "")
        src = by_label.get(lab)
        if src is not None:
            meta["oos_passed"] = src.get("oos_passed")
    active["code_map"] = cmap
    path = _save_active_doc(active) if persist else None

    return {
        "success": True,
        "filled": filled,
        "path": path,
        "oos_summary": _summarize_cluster_oos(clusters),
        "note": "已为 active 各组补 oos_gate" if filled else "无需补算",
    }


def _default_health_universe() -> List[str]:
    from core.signal.cluster_live_health import _default_health_universe as _impl

    return _impl()


def assess_cluster_live_health(
    *,
    universe: Optional[Sequence[str]] = None,
    compute_ic: Optional[bool] = None,
) -> Dict[str, Any]:
    """覆盖率 / 陈旧 / 模式门禁（L3）。实现见 ``cluster_live_health``。"""
    from core.signal.cluster_live_health import assess_cluster_live_health as _impl

    return _impl(universe=universe, compute_ic=compute_ic)


def set_cluster_scoring_mode(
    mode: str,
    *,
    enabled: Optional[bool] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """仅改 signal_config.cluster_scoring 开关；不写 weights。

    mode=active 时：健康/证据包 + live manifest 半晋升阻断；
    ``force=True`` 可豁免并写 ``promote_audit.jsonl``。
    """
    from core.io_atomic import atomic_write_json
    from core.paths import SIGNAL_CONFIG_PATH
    from core.signal.cluster_pointer import active_enable_blockers, append_promote_audit
    from core.signal.config import load_signal_config

    mode = str(mode or "off").strip().lower()
    if mode not in ("off", "shadow", "active"):
        return {"success": False, "error": "mode 须为 off|shadow|active"}

    force_audit = False
    health = None
    evidence = None
    if mode == "active":
        health = assess_cluster_live_health()
        evidence = build_cluster_enable_evidence(health=health)
        blockers = active_enable_blockers(health=health, evidence=evidence)
        if blockers and not force:
            return {
                "success": False,
                "error": "启用证据包未通过，禁止 active："
                + ("；".join(str(b) for b in blockers[:6])),
                "health": health,
                "enable_evidence": evidence,
                "blockers": blockers,
            }
        if blockers and force:
            force_audit = True
            append_promote_audit(
                {
                    "action": "cluster_set_mode_force_active",
                    "blockers": blockers[:20],
                    "note": "force=true 豁免 active 门禁",
                }
            )

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
    atomic_write_json(path, raw)
    # 清缓存
    try:
        from core.signal import config as cfg_mod

        cfg_mod._cached = None
    except Exception:
        pass
    load_signal_config(reload=True)
    warnings: List[str] = []
    manifest = None
    try:
        from core.live_config_manifest import write_live_config_manifest

        manifest = write_live_config_manifest(note=f"after cluster_set_mode={mode}")
    except Exception as e:
        warnings.append(f"live_manifest_write_failed:{e}")
    return {
        "success": True,
        "task": "cluster_set_mode",
        "cluster_scoring": get_cluster_scoring_cfg(),
        "path": path,
        "force": bool(force_audit),
        "signal_config_weights_touched": False,
        "warnings": warnings,
        "health": health,
        "enable_evidence": evidence,
        "live_manifest": {
            "consistent": (manifest or {}).get("consistent"),
            "alerts": (manifest or {}).get("alerts") or [],
            "error": None if manifest else (warnings[-1] if warnings else None),
        }
        if manifest or warnings
        else None,
        "note": "仅更新 cluster_scoring 开关；weights 未改",
    }


def refresh_cluster_book_daily(*, light: bool = False) -> Dict[str, Any]:
    """L3：不重聚类，仅按 active map 重打分并刷新合并簿。

    ``light=True``：跳过健康 IC（对照一键应用用，避免再等一轮）。
    """
    from core.signal.cluster_rank import rank_cluster_pools

    health = assess_cluster_live_health(compute_ic=False if light else None)
    ranked = rank_cluster_pools(None, persist_book=True)
    try:
        from core.live_config_manifest import write_live_config_manifest

        write_live_config_manifest(note="after cluster_daily_refresh")
    except Exception:
        pass
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
        "light": bool(light),
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
    if not (cs.get("auto_demote_on_stale") and (health.get("stale") or health.get("ic_demote"))):
        # 覆盖率不足：仅告警，不自动降（与陈旧/IC 区分）
        if health.get("suggest_demote") and not (
            health.get("stale") or health.get("ic_demote")
        ):
            out["note"] = "建议降级但未自动执行（非陈旧/IC）"
        return out
    mode_out = set_cluster_scoring_mode("shadow")
    out["demoted"] = bool(mode_out.get("success"))
    out["mode_result"] = mode_out
    out["cluster_scoring"] = get_cluster_scoring_cfg()
    out["signal_config_touched"] = False
    reason = "映射陈旧" if health.get("stale") else "滚动 ŷ IC 破线"
    out["note"] = f"{reason}，已自动降为 shadow"
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
    from core.signal.cluster_live_audit import pick_audit_codes

    return pick_audit_codes(cmap, book_rows, n, offset=offset)


def cluster_score_audit_sample(
    *,
    limit: int = 8,
    offset: Optional[int] = None,
    rotate: bool = False,
) -> Dict[str, Any]:
    """对照审计：优先分池簿样本（实现见 cluster_live_audit）。"""
    from core.signal.cluster_live_audit import (
        cluster_score_audit_sample as _impl,
    )

    return _impl(limit=limit, offset=offset, rotate=rotate)



def apply_cluster_live_shortcut(
    artifact: Optional[Dict[str, Any]] = None,
    *,
    from_draft: bool = True,
    note: str = "",
    mode: str = "shadow",
    refresh_book: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """一键：晋升 → 刷新分池簿 → 设 mode（默认 shadow）。

    FH1：先刷簿再 active，避免「active 无簿」硬拦；仍不写全局 weights。
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

    promo = promote_cluster_artifact(art, note=note or "一键应用分组", force=force)
    if not promo.get("success"):
        return {**promo, "task": "cluster_apply_shortcut"}

    mode = str(mode or "shadow").strip().lower()
    if mode not in ("off", "shadow", "active"):
        mode = "shadow"

    rank_out = None
    if refresh_book and mode != "off":
        # 对照/一键：轻量刷簿（跳过 IC；打分跳过舆情）
        rank_out = refresh_cluster_book_daily(light=True)

    # active 走健康门禁；失败则降级 shadow
    mode_out = set_cluster_scoring_mode(mode, force=force)
    if not mode_out.get("success") and mode == "active":
        mode_out = set_cluster_scoring_mode("shadow")
        mode_out["demoted_to_shadow"] = True
        mode_out["demote_reason"] = "active 未过健康检查，已用影子"

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


def _num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


from core.signal.cluster_live_evidence import (  # noqa: E402
    _paper_cluster_landed,
    _summarize_cluster_oos,
    build_cluster_enable_evidence,
    cluster_status_public,
)
