"""ŷ_oo_rank：LambdaRank / Ridge 双 backend，启用时按 Holdout NDCG@K 自适应。

训练：同窗训 LambdaRank + Ridge ŷ_oo；推理：相对分 → 当日截面 1..n 名次（1=最高）。
启用研究/执行：按 NDCG@K 自动落盘更强一侧（打平或缺指标 → lambdarank）。
回测 / live 只消费已启用模型，无 backend 开关。
不进 y_oo_fuse；过入场后作买序；可选 ``oo_rank_max`` 入场闸（UI「rank 入场」）。
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json
from core.research.oos_finite import as_float, finite_pairs
from core.research.oo_rank_panel import label_floats

Y_OO_RANK_HAT_KEYS = (
    "y_oo_rank",
    "y_oo_rank_hat",
    "predicted_score_oo_rank",
)
Y_OO_RANK_LABEL_KEYS = (
    "y_oo_rank_realized",
    "oo_rank_realized",
)
FORMULA_OO_RANK = "rank(open[T]->open[T+1])"
SCHEMA = "oo_rank_v1"
SCHEMA_LEGACY = "oo_rank_pairwise_v1"
TASK = "oo_rank"
TASK_LEGACY = "oo_rank_pairwise"
DEFAULT_MIN_NAMES = 8
DEFAULT_L2 = 1.0
DEFAULT_TOPK_TRACK = 10
DEFAULT_NDCG_K = 10
DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK = 20

_MODEL_BASENAME = "oo_rank_model.json"
_LAST_REPORT_BASENAME = "oo_rank_last_report.json"
_MODEL_BASENAME_LEGACY = "oo_rank_pairwise_model.json"
_LAST_REPORT_BASENAME_LEGACY = "oo_rank_pairwise_last_report.json"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x


def pick_y_oo_rank_hat(*objs: Any) -> Optional[float]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_OO_RANK_HAT_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def pick_y_oo_rank_label(*objs: Any) -> Optional[float]:
    for obj in objs:
        if not isinstance(obj, dict):
            continue
        for k in Y_OO_RANK_LABEL_KEYS:
            v = _f(obj.get(k))
            if v is not None:
                return v
    return None


def write_y_oo_rank_hat(item: dict, value: Optional[float]) -> None:
    if not isinstance(item, dict):
        return
    if value is None:
        return
    v = round(float(value), 6)
    item["y_oo_rank"] = v
    item["y_oo_rank_hat"] = v
    item["predicted_score_oo_rank"] = v


def assign_oo_rank_day_ranks(items: Sequence[dict]) -> List[dict]:
    """把当日截面相对分改成 1..n 名次（1=相对分最高）。

    原始分写入 ``y_oo_rank_score``；``y_oo_rank`` / hat / predicted_score_oo_rank 改为整数名次。
    同分按 ``stock_code`` 稳定破平。不改 y_oo_fuse；过入场后作买序。
    """

    def _sort_key(it: dict) -> Tuple[float, str]:
        raw = it.get("y_oo_rank_score")
        if raw is None:
            raw = pick_y_oo_rank_hat(it)
        return (-float(raw), str(it.get("stock_code") or ""))

    rows: List[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if pick_y_oo_rank_hat(it) is None and it.get("y_oo_rank_score") is None:
            continue
        rows.append(it)
    rows.sort(key=_sort_key)
    n = len(rows)
    for i, it in enumerate(rows):
        if it.get("y_oo_rank_score") is None:
            raw = pick_y_oo_rank_hat(it)
            if raw is not None:
                it["y_oo_rank_score"] = round(float(raw), 6)
        rank = i + 1
        it["y_oo_rank"] = rank
        it["y_oo_rank_hat"] = rank
        it["predicted_score_oo_rank"] = rank
        it["y_oo_rank_n"] = n
    return list(items or [])


def write_y_oo_rank_realized(item: dict, value: Optional[float]) -> None:
    if not isinstance(item, dict) or value is None:
        return
    v = round(float(value), 6)
    item["y_oo_rank_realized"] = v
    item["oo_rank_realized"] = v


def oo_rank_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _MODEL_BASENAME)


def oo_rank_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _LAST_REPORT_BASENAME)


def oo_rank_model_path_legacy() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _MODEL_BASENAME_LEGACY)


def oo_rank_last_report_path_legacy() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, _LAST_REPORT_BASENAME_LEGACY)


def _load_model_file(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        logger.debug("load oo_rank json failed: %s", path, exc_info=True)
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("return_model"), dict):
        return None
    if doc.get("success") is False:
        return None
    return doc


def _first_existing_model(*paths: str) -> Optional[Dict[str, Any]]:
    for path in paths:
        doc = _load_model_file(path)
        if doc:
            return doc
    return None


def load_oo_rank_model(*, prefer_research: bool = True) -> Optional[Dict[str, Any]]:
    """加载已启用模型。按槽严格取，缺文件返回 None（不跨槽回落）。

    - ``prefer_research=True``：只读研究套 sidecar
    - ``prefer_research=False``：只读执行套（新路径优先，旧 ``oo_rank_pairwise_*.json`` 兼容）
    """
    live = oo_rank_model_path()
    legacy = oo_rank_model_path_legacy()
    if prefer_research:
        try:
            from core.research.holdout import load_research_promoted_json

            for path in (live, legacy):
                doc = load_research_promoted_json(path)
                if doc:
                    return doc
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank research load failed", exc_info=True)
        return None
    return _first_existing_model(live, legacy)


def stamp_oo_rank_fitted_at(report: Dict[str, Any]) -> Dict[str, Any]:
    """成功拟合打 ``fitted_at``，并写入 ``return_model`` / 研究套。"""
    from core.research.holdout import stamp_fitted_at

    if not isinstance(report, dict) or not report.get("success"):
        return report
    stamp_fitted_at(report)
    ts = report.get("fitted_at")
    if not ts:
        return report

    def _stamp_nested(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for key in ("return_model", "return_model_research"):
            nested = container.get(key)
            if isinstance(nested, dict) and not nested.get("fitted_at"):
                nested["fitted_at"] = ts

    _stamp_nested(report)
    backends = report.get("backends")
    if isinstance(backends, dict):
        for pack in backends.values():
            _stamp_nested(pack)
    return report


def save_oo_rank_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    stamp_oo_rank_fitted_at(report)
    path = oo_rank_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def load_oo_rank_last_report() -> Optional[Dict[str, Any]]:
    """上次拟合草稿。启用研究/执行只写这份，不再重训。新路径优先，旧文件可读。"""
    doc = _first_existing_model(
        oo_rank_last_report_path(),
        oo_rank_last_report_path_legacy(),
    )
    if doc and doc.get("success"):
        return doc
    return None


def select_oo_rank_backend(
    oos_rank: Optional[Dict[str, Any]] = None,
    oos_ridge: Optional[Dict[str, Any]] = None,
) -> str:
    """按 Holdout NDCG@K 选更好的 backend；打平或缺指标 → ``lambdarank``。"""

    def _ndcg(m: Any) -> Optional[float]:
        if not isinstance(m, dict):
            return None
        if m.get("success") is False:
            return None
        v = m.get("ndcg_at_k")
        try:
            if v is None or v == "":
                return None
            x = float(v)
        except (TypeError, ValueError):
            return None
        if x != x:  # NaN
            return None
        return x

    lr = _ndcg(oos_rank)
    rg = _ndcg(oos_ridge)
    if rg is not None and lr is not None and rg > lr + 1e-12:
        return "ridge"
    if rg is not None and lr is None:
        return "ridge"
    return "lambdarank"


def _backend_pack_from_report(
    report: Dict[str, Any], backend: str
) -> Optional[Dict[str, Any]]:
    backends = report.get("backends") if isinstance(report.get("backends"), dict) else {}
    pack = backends.get(backend) if isinstance(backends, dict) else None
    if isinstance(pack, dict) and isinstance(pack.get("return_model"), dict):
        return pack
    if backend == "lambdarank" and isinstance(report.get("return_model"), dict):
        return {
            "return_model": report.get("return_model"),
            "return_model_research": report.get("return_model_research"),
        }
    return None


def persist_oo_rank_model(
    report: Dict[str, Any], *, note: str = "", role: str = "live"
) -> Dict[str, Any]:
    """启用研究=报告→研究槽；启用执行=研究槽→执行槽（晋级）。按 NDCG@K 选 backend。"""
    from core.numbers import now_iso_utc
    from core.research.holdout import (
        MODEL_ROLE_LIVE,
        MODEL_ROLE_RESEARCH,
        normalize_model_role,
        require_research_suite,
        research_model_path,
        select_fit_return_model,
        stamp_live_suite_doc,
    )

    live = oo_rank_model_path()
    role_n = normalize_model_role(role)

    if role_n == MODEL_ROLE_LIVE:
        research_doc, err = require_research_suite(live)
        if err:
            return err
        doc = stamp_live_suite_doc(
            research_doc, note=note or "oo_rank research → live promote"
        )
        os.makedirs(os.path.dirname(live), exist_ok=True)
        atomic_write_json(live, doc)
        out = {
            "success": True,
            "path": live,
            "promoted_at": doc.get("promoted_at"),
            "model_role": MODEL_ROLE_LIVE,
            "backend": doc.get("backend"),
            "recommended_backend": doc.get("recommended_backend") or doc.get("backend"),
            "promoted_from": "research",
            "live": live,
        }
        return out

    if not isinstance(report, dict) or not report.get("success"):
        return {"success": False, "error": report.get("error") if isinstance(report, dict) else "no report"}
    stamp_oo_rank_fitted_at(report)
    oos = report.get("oos") if isinstance(report.get("oos"), dict) else {}
    backend = select_oo_rank_backend(
        oos.get("oo_rank") if isinstance(oos, dict) else None,
        oos.get("ridge_oo_baseline") if isinstance(oos, dict) else None,
    )
    pack = _backend_pack_from_report(report, backend)
    view = dict(report)
    if isinstance(pack, dict):
        view["return_model"] = pack.get("return_model")
        if isinstance(pack.get("return_model_research"), dict):
            view["return_model_research"] = pack.get("return_model_research")
    model = select_fit_return_model(view)
    if not isinstance(model, dict):
        return {"success": False, "error": "return_model missing"}
    if not _oo_rank_model_valid(model):
        return {"success": False, "error": "return_model missing usable oo_rank pack"}
    model = dict(model)
    model["model_role"] = MODEL_ROLE_RESEARCH
    model["backend"] = backend
    model.pop("shadow_only", None)
    fitted_at = report.get("fitted_at") or model.get("fitted_at")
    promoted_at = now_iso_utc()
    y_spec = report.get("y_spec") or model.get("y_spec")
    if isinstance(y_spec, dict):
        y_spec = dict(y_spec)
        y_spec["backend"] = backend
    doc = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": backend,
        "recommended_backend": backend,
        "return_model": model,
        "return_model_research": dict(model),
        "oos": report.get("oos"),
        "y_spec": y_spec,
        "note": note
        or report.get("note")
        or f"oo_rank promote research backend={backend}",
        "n_days": report.get("n_days"),
        "n_train_days": report.get("n_train_days"),
        "n_test_days": report.get("n_test_days"),
        "sample_count": report.get("sample_count") or model.get("sample_count"),
        "shadow_track": report.get("shadow_track"),
        "fitted_at": fitted_at,
        "promoted_at": promoted_at,
        "model_role": MODEL_ROLE_RESEARCH,
    }
    for k in (
        "zscore_universe",
        "zscore_universe_tiers",
        "tier_codes",
        "n_tier_codes",
        "tier_fingerprint",
        "tier_as_of_last",
        "holdout_n",
        "tier_n",
        "cross_section_zscore",
    ):
        if k in report and report.get(k) is not None:
            doc[k] = report.get(k)
            model[k] = report.get(k)
            if isinstance(doc.get("return_model_research"), dict):
                doc["return_model_research"][k] = report.get(k)
    backends = report.get("backends")
    if isinstance(backends, dict) and backends:
        doc["backends"] = backends
    ridge_pack = backends.get("ridge") if isinstance(backends, dict) else None
    if (
        backend != "ridge"
        and isinstance(ridge_pack, dict)
        and isinstance(ridge_pack.get("return_model_research"), dict)
    ):
        doc["ridge_return_model_research"] = ridge_pack.get("return_model_research")
    path = research_model_path(live)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, doc)
    save_oo_rank_last_report(report)
    return {
        "success": True,
        "path": path,
        "promoted_at": promoted_at,
        "model_role": MODEL_ROLE_RESEARCH,
        "backend": backend,
        "recommended_backend": backend,
        "research": path,
    }


def _ndcg_at_k(
    pred: Sequence[float],
    ys: Sequence[float],
    k: int = DEFAULT_NDCG_K,
) -> Optional[float]:
    """NDCG@K：gain = 真实收益的截面升序 rank（0=最差，n-1=最好）。

    按模型预测分降序取前 K 计算 DCG，除以按真实收益降序的理想 DCG。
    与 LambdaRank 训练目标（NDCG）对齐，聚焦头部排位质量。
    """
    n = len(pred)
    if n < 2 or k < 1:
        return None
    kk = min(int(k), n)
    order_by_ys = sorted(range(n), key=lambda i: float(ys[i]))
    gain = [0.0] * n
    for rank, idx in enumerate(order_by_ys):
        gain[idx] = float(rank)

    pred_order = sorted(range(n), key=lambda i: float(pred[i]), reverse=True)
    dcg = 0.0
    for i, idx in enumerate(pred_order[:kk]):
        dcg += gain[idx] / math.log2(i + 2)

    ideal_order = sorted(range(n), key=lambda i: float(ys[i]), reverse=True)
    idcg = 0.0
    for i, idx in enumerate(ideal_order[:kk]):
        idcg += gain[idx] / math.log2(i + 2)

    if idcg < 1e-12:
        return None
    return round(dcg / idcg, 4)


def _day_X(day: Dict[str, Any]) -> Optional[Tuple[np.ndarray, List[str]]]:
    from core.research.oo_rank_panel import _day_matrix

    return _day_matrix(day)


def _collect_feature_names(days: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for day in days or []:
        packed = _day_X(day)
        if packed is None:
            continue
        _X, col_names = packed
        for k in col_names:
            if k in seen:
                continue
            seen.add(k)
            names.append(k)
    return names


def _raw_day_aligned(day: Dict[str, Any], feature_names: Sequence[str]) -> Optional[np.ndarray]:
    packed = _day_X(day)
    names = [str(n) for n in feature_names]
    p = len(names)
    if packed is not None:
        X, col_names = packed
        if list(col_names) == names:
            return np.asarray(X, dtype=np.float64)
        idx = {n: j for j, n in enumerate(col_names)}
        out = np.full((X.shape[0], p), np.nan, dtype=np.float64)
        for j, name in enumerate(names):
            src = idx.get(name)
            if src is not None:
                out[:, j] = X[:, src]
        return out
    return None


def _filter_days_with_l2(
    days: Sequence[Dict[str, Any]],
    *,
    clock: str = "open",
) -> List[Dict[str, Any]]:
    """与 ŷ_oo ``align_fit_rows_to_l2`` 同：丢掉无 Cube 日；全日无则报物化。"""
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.materialize.cube_z import (
        _format_l2_miss_error,
        _l2_materialize_hint,
        groups_with_day_cube,
    )

    day_list = [d for d in (days or []) if isinstance(d, dict)]
    if not day_list:
        return []
    dates = [str(d.get("date") or "")[:10] for d in day_list]
    mask, miss = groups_with_day_cube(dates, clock=str(clock or "open"))
    n_keep = int(mask.sum())
    n_drop = len(day_list) - n_keep
    if n_keep == 0:
        hint, clks = _l2_materialize_hint(dates, default_clock=clock)
        raise MaterializeRequired(
            {
                "error_code": "materialize_required",
                "error": _format_l2_miss_error(
                    {
                        "error": f"cube miss {miss[0]}" if miss else "截面立方缺失",
                        "clock": clks[0] if clks else clock,
                    },
                    default_clock=str(clock or "open"),
                ),
                "layer": "l2",
                "hint": hint,
                "clocks": clks,
            }
        )
    if n_drop > 0:
        logger.warning(
            "oo_rank fit drop %s/%s days without L2 (per-stock L1 earlier than pool cubes); miss e.g. %s",
            n_drop,
            len(day_list),
            miss[:5],
        )
    return [d for d, ok in zip(day_list, mask) if ok]


def _z_day_cross_section(
    day: Dict[str, Any],
    feature_names: Sequence[str],
    *,
    clock: str = "open",
) -> Optional[np.ndarray]:
    """当天截面 z：只用全池 L2 DayCube μ/σ，**不**进批现算。

    缺 Cube 返回 ``None``（该日跳过），与 ŷ_oo ``align_fit_rows_to_l2`` 同口径：
    单票 L1 可早于池立方，拟合丢掉无 Cube 日，不因一日 miss 整次失败。
    打分写盘仍走 ``rank_cross_section_z_dicts``（缺 L2 才 ``MaterializeRequired``）。
    """
    raw = _raw_day_aligned(day, feature_names)
    if raw is None or int(raw.shape[0]) == 0:
        return None
    n = int(raw.shape[0])
    names = [str(n) for n in feature_names]
    day_s = str((day or {}).get("date") or "")[:10]
    if len(day_s) < 10:
        return None
    groups = [day_s] * n
    from core.research.materialize.cube_z import apply_day_cube_mu_sigma_matrix
    from core.research.materialize.consumer import MaterializeRequired

    try:
        # 与 ŷ_oo ``cs_z_matrix_prefer_cube`` 同：用全池 universe 读 Cube。
        # 传入当日面板 codes 会换 hash，整池已物化的 open Cube 反而 miss。
        z, meta = apply_day_cube_mu_sigma_matrix(
            raw,
            names,
            groups,
            clock=str(clock or "open"),
            codes=None,
        )
    except MaterializeRequired:
        # 拟合按日跳过；勿把单日 miss 抬成整次物化闸。
        logger.debug("rank _z_day skip MaterializeRequired date=%s", day_s)
        return None
    except Exception:  # noqa: BLE001
        logger.debug("rank _z_day L2 failed date=%s", day_s, exc_info=True)
        return None
    if z is not None and isinstance(meta, dict) and meta.get("ok"):
        return np.where(np.isfinite(z), z, 0.0)
    err = str((meta or {}).get("error") or "截面立方缺失")
    logger.debug("rank _z_day skip no cube date=%s clock=%s err=%s", day_s, clock, err)
    return None


def _item_cs_group(it: Mapping[str, Any], *, fallback: str = "d") -> str:
    """打分行上的截面分组日：lake_score_day / as_of / date。"""
    for key in ("lake_score_day", "as_of", "date"):
        day = str((it or {}).get(key) or "")[:10]
        if len(day) >= 10:
            return day
    return str(fallback or "d")


def rank_cross_section_z_dicts(
    raw_rows: Sequence[Mapping[str, Any]],
    names: Sequence[str],
    groups: Sequence[Any],
    *,
    day_cube: Any = None,
    codes: Optional[Sequence[str]] = None,
    clock: str = "open",
) -> Optional[List[dict]]:
    """排序头截面 z：只用 ``day_cube`` / 全池 L2 μ/σ，**不**进批现算。

    缺 L2 时：消费只读 → ``MaterializeRequired``；否则返回 ``None``（调用方跳过写分）。
    """
    name_list = [str(n) for n in names]
    rows = [dict(r) for r in (raw_rows or [])]
    if not rows:
        return []
    grp_list = list(groups) if groups is not None else ["d"] * len(rows)
    if len(grp_list) != len(rows):
        fill = str(grp_list[0] if grp_list else "d")
        grp_list = [fill] * len(rows)
    code_list = [str(c or "").strip() for c in (codes or [])]
    if len(code_list) != len(rows):
        code_list = [""] * len(rows)

    if day_cube is not None:
        try:
            from core.research.day_cube import apply_cube_z_to_dicts

            z_all = apply_cube_z_to_dicts(rows, code_list, day_cube)
            out: List[dict] = []
            for i in range(len(rows)):
                src = z_all[i] if i < len(z_all) and isinstance(z_all[i], dict) else {}
                out.append({k: src.get(k) for k in name_list})
            return out
        except Exception:  # noqa: BLE001
            logger.debug("rank day_cube apply failed", exc_info=True)

    from core.research.materialize.consumer import MaterializeRequired, consumer_read_only
    from core.research.materialize.cube_z import apply_day_cube_mu_sigma_dicts

    try:
        # 读 Cube 用全池 universe（同 ŷ_oo）；勿用当日行 codes 换 hash。
        z_rows, meta = apply_day_cube_mu_sigma_dicts(
            rows,
            name_list,
            grp_list,
            clock=str(clock or "open"),
            codes=None,
        )
    except MaterializeRequired:
        raise
    except Exception:  # noqa: BLE001
        logger.debug("rank L2 dict z failed", exc_info=True)
        meta = {"ok": False, "error": "cube read failed"}
        z_rows = None
    if z_rows is not None and isinstance(meta, dict) and meta.get("ok"):
        return z_rows
    if consumer_read_only():
        err = str((meta or {}).get("error") or "截面立方缺失")
        raise MaterializeRequired(
            {
                "error_code": "materialize_required",
                "error": f"请先物化 L2：排序头 {err}",
                "layer": "l2",
                "hint": "日线物化",
                "clocks": [str(clock or "open")],
            }
        )
    return None


def _normalize_code_allow(
    train_codes: Optional[Collection[str]],
) -> Optional[set]:
    if train_codes is None:
        return None
    out = {str(c).strip() for c in train_codes if str(c or "").strip()}
    return out or None


def _keep_idx_for_codes(
    day: Dict[str, Any],
    n_rows: int,
    allow: Optional[set],
) -> Optional[List[int]]:
    """``None`` = 保留全行；否则返回落在 ``allow`` 内的行下标。"""
    if allow is None:
        return None
    codes = day.get("codes") if isinstance(day, dict) else None
    if not isinstance(codes, (list, tuple)) or len(codes) != int(n_rows):
        return []
    return [
        i
        for i, c in enumerate(codes)
        if str(c or "").strip() in allow
    ]


def _take_rows(
    ys: Sequence[float],
    Z: np.ndarray,
    keep: Optional[List[int]],
) -> Tuple[List[float], np.ndarray]:
    if keep is None:
        return list(ys), Z
    if not keep:
        empty = Z[:0] if isinstance(Z, np.ndarray) else np.zeros((0, 0))
        return [], empty
    return [ys[i] for i in keep], Z[np.asarray(keep, dtype=np.int64)]


def _z_day(
    day: Dict[str, Any],
    feature_names: Sequence[str],
    means: Dict[str, float],
    stds: Dict[str, float],
) -> Optional[np.ndarray]:
    raw = _raw_day_aligned(day, feature_names)
    if raw is None:
        return None
    mu = np.asarray([float(means.get(n) or 0.0) for n in feature_names], dtype=np.float64)
    sd = np.asarray([float(stds.get(n) or 1.0) for n in feature_names], dtype=np.float64)
    sd = np.where(sd < 1e-9, 1.0, sd)
    z = (raw - mu) / sd
    return np.where(np.isfinite(raw), z, 0.0).astype(np.float64, copy=False)


def _slice_finite_label_rows(
    ys: Sequence[float], Z: np.ndarray
) -> Tuple[List[float], np.ndarray, int]:
    """丢掉非有限 y，并切掉 Z 的对应行。NaN 不编成名次。"""
    n = len(ys)
    if Z is None or int(getattr(Z, "ndim", 0)) != 2 or int(Z.shape[0]) != n:
        empty = Z[:0] if isinstance(Z, np.ndarray) else np.zeros((0, 0))
        return [], empty, n
    parsed = [as_float(y) for y in ys]
    keep = [i for i, y in enumerate(parsed) if math.isfinite(y)]
    dropped = n - len(keep)
    if not keep:
        return [], Z[:0], dropped
    if dropped == 0:
        return parsed, Z, 0
    return [parsed[i] for i in keep], Z[keep], dropped


def _rank_labels(ys: Sequence[float]) -> List[int]:
    """日截面 ys → LambdaRank 整数标签 0..n-1（高 ys = 高 label）。"""
    n = len(ys)
    if n == 0:
        return []
    order = np.argsort(list(ys))
    labels = [0] * n
    for rank, idx in enumerate(order):
        labels[int(idx)] = int(rank)
    return labels


def _global_z_stats(
    days: Sequence[Dict[str, Any]], feature_names: Sequence[str]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """训练窗全局列向 μ/σ（与日截面互斥）。"""
    names = [str(n) for n in feature_names]
    blocks: List[np.ndarray] = []
    for day in days or []:
        raw = _raw_day_aligned(day, names)
        if raw is not None and int(raw.shape[0]) > 0:
            blocks.append(raw)
    if not blocks:
        return {}, {}
    x = np.vstack(blocks)
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    for j, n in enumerate(names):
        col = x[:, j]
        finite = col[np.isfinite(col)]
        if int(finite.size) < 2:
            means[n] = 0.0
            stds[n] = 1.0
            continue
        means[n] = float(np.mean(finite))
        sd = float(np.std(finite))
        stds[n] = sd if sd >= 1e-9 else 1.0
    return means, stds


def fit_lambdarank(
    days: Sequence[Dict[str, Any]],
    *,
    feature_names: Optional[Sequence[str]] = None,
    n_estimators: int = 120,
    max_depth: int = 5,
    learning_rate: float = 0.05,
    subsample: float = 0.85,
    colsample_bytree: float = 0.9,
    num_leaves: int = 31,
    min_child_samples: int = 20,
    l2: float = 1.0,
    cross_section_zscore: bool = True,
    train_codes: Optional[Collection[str]] = None,
) -> Dict[str, Any]:
    """LightGBM LambdaRank：按日截面 group，优化 NDCG。

    日截面 z **先按当天全行**计算，再按 ``train_codes``（可选）缩损失行，
    再丢掉非有限 y 及对应 Z 行后才打标签。
    剩下不足 4 行，或标签只剩一种，跳过该日。NaN 不编成最高名次。

    输出与推理共用：
    - ``coefficients``：feature importance (gain)，仅诊断
    - ``zscore_scope=cross_section``：按日截面标准化（不再落盘训练窗 μ/σ）
    - ``cross_section_zscore=False``：训练窗全局 μ/σ
    - ``solver`` = ``lambdarank``（旧包 ``lightgbm_lambda`` 仍可读）
    - ``booster_b64``：LightGBM 模型
    """
    import lightgbm as lgb

    names = list(feature_names) if feature_names else _collect_feature_names(days)
    if not names:
        return {"success": False, "error": "no_features"}
    p = len(names)
    use_cs = bool(cross_section_zscore)
    allow = _normalize_code_allow(train_codes)
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    if use_cs:
        days = _filter_days_with_l2(days, clock="open")
    if not use_cs:
        means, stds = _global_z_stats(days, names)
    X_blocks: List[np.ndarray] = []
    y_blocks: List[List[int]] = []
    group_sizes: List[int] = []
    n_rank_rows_dropped = 0
    for day in days:
        ys = label_floats(day.get("ys"))
        Z = (
            _z_day_cross_section(day, names)
            if use_cs
            else _z_day(day, names, means, stds)
        )
        if Z is not None:
            Z = np.where(np.isfinite(Z), Z, 0.0)
        if Z is None or int(Z.shape[0]) != len(ys):
            continue
        n_full = len(ys)
        keep = _keep_idx_for_codes(day, n_full, allow)
        if keep is not None:
            n_rank_rows_dropped += n_full - len(keep)
        ys, Z = _take_rows(ys, Z, keep)
        ys, Z, n_drop = _slice_finite_label_rows(ys, Z)
        n_rank_rows_dropped += int(n_drop)
        if len(ys) < 4:
            continue
        labels = _rank_labels(ys)
        if len(set(labels)) < 2:
            continue  # 单 group 全同 label 学不出排序
        X_blocks.append(Z)
        y_blocks.append(labels)
        group_sizes.append(len(ys))
    n_groups = len(group_sizes)
    if n_groups < 5:
        return {
            "success": False,
            "error": f"groups_insufficient n_days={n_groups}（需≥5）",
            "n_days": n_groups,
            "n_groups": n_groups,
            "n_rank_rows_dropped": n_rank_rows_dropped,
        }
    X = np.vstack(X_blocks)
    del X_blocks
    y = np.asarray([lab for block in y_blocks for lab in block], dtype=np.int32)
    del y_blocks
    group = np.asarray(group_sizes, dtype=np.int32)
    if X.shape[0] != y.shape[0] or X.shape[0] != int(group.sum()):
        return {
            "success": False,
            "error": "shape_mismatch",
            "n_rank_rows_dropped": n_rank_rows_dropped,
        }
    lam = max(0.0, float(l2))
    # label_gain 须覆盖全部 label；默认指数表只有 31 项，日池>31 会崩。
    # 用线性 [0,1,2,…] 与 OOS NDCG（gain=rank）对齐。
    n_class = int(y.max()) + 1
    label_gain = [float(i) for i in range(n_class)]
    try:
        # 原生 lgb.train（不依赖 sklearn），objective=lambdarank
        dtrain = lgb.Dataset(X, label=y, group=group)
        del X
        booster = lgb.train(
            {
                "objective": "lambdarank",
                "metric": ["ndcg"],
                "label_gain": label_gain,
                "max_depth": max(1, int(max_depth)),
                "num_leaves": int(num_leaves),
                "learning_rate": float(learning_rate),
                "subsample": min(1.0, max(0.4, float(subsample))),
                "colsample_bytree": min(1.0, max(0.4, float(colsample_bytree))),
                "min_data_in_leaf": int(min_child_samples),
                "lambda_l2": lam,
                "seed": 42,
                "num_threads": 1,
                "verbose": -1,
            },
            dtrain,
            num_boost_round=max(1, int(n_estimators)),
        )
        del dtrain
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"lightgbm_fit_failed: {exc}",
            "n_rank_rows_dropped": n_rank_rows_dropped,
        }
    # 序列化：lgb.Booster.save_model 只接受文件路径，用临时文件中转
    import tempfile

    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=True, encoding="utf-8") as tmp:
        tmp_path = tmp.name
    booster.save_model(tmp_path)
    try:
        with open(tmp_path, encoding="utf-8") as f:
            text = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    booster_b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    # feature importance → coefficients（诊断用）
    try:
        imp = booster.feature_importance(importance_type="gain")
        total = float(np.sum(np.clip(imp, 0.0, None)))
        coefs = {
            names[i]: round(float(imp[i]) / total, 8) if total > 1e-12 else 0.0
            for i in range(min(p, int(imp.size)))
        }
    except Exception:  # noqa: BLE001
        coefs = {}
    active = [n for n, w in coefs.items() if w > 0.0]
    out = {
        "success": True,
        "intercept": 0.0,
        "coefficients": coefs,
        "active_features": active,
        "zscore_means": {},
        "zscore_stds": {},
        "z_means": {},
        "z_stds": {},
        "feature_zscore": True,
        "solver": "lambdarank",
        "backend": "lambdarank",
        "booster_b64": booster_b64,
        "ridge_lambda": lam,
        "sample_count": int(y.shape[0]),
        "n_groups": n_groups,
        "n_rank_rows_dropped": n_rank_rows_dropped,
    }
    if use_cs:
        from core.research.feature_standardize import stamp_cross_section_zscore

        stamp_cross_section_zscore(out)
    else:
        out["zscore_means"] = {k: round(float(v), 6) for k, v in means.items()}
        out["zscore_stds"] = {k: round(float(v), 6) for k, v in stds.items()}
        out["z_means"] = dict(out["zscore_means"])
        out["z_stds"] = dict(out["zscore_stds"])
        out["zscore_scope"] = ""
    return out


def _predict_rows_lightgbm(
    fit: Dict[str, Any],
    xs: List[dict],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    """LightGBM LambdaRank 推理：z-score → booster.predict → 浮点分数（相对分）。"""
    import base64 as _b64

    import lightgbm as lgb

    # 必须用训练时的完整特征列表（顺序一致），不能用 active_features（子集），
    # 否则 LightGBM predict 形状检查失败
    zm = fit.get("zscore_means")
    if isinstance(zm, dict) and zm:
        names = list(zm.keys())
    elif isinstance(fit.get("z_means"), dict) and fit.get("z_means"):
        names = list(fit.get("z_means").keys())
    else:
        names = list((fit.get("coefficients") or {}).keys())
    if not names:
        return [None] * len(xs)
    means = zm if isinstance(zm, dict) else (fit.get("z_means") if isinstance(fit.get("z_means"), dict) else {})
    stds_raw = fit.get("zscore_stds")
    stds = stds_raw if isinstance(stds_raw, dict) else (fit.get("z_stds") if isinstance(fit.get("z_stds"), dict) else {})
    b64 = str(fit.get("booster_b64") or "")
    if not b64:
        return [None] * len(xs)
    try:
        text = _b64.b64decode(b64.encode("ascii")).decode("utf-8")
        booster = lgb.Booster(model_str=text)
    except Exception:  # noqa: BLE001
        return [None] * len(xs)
    from core.signal.factors.z_oo import feature_get

    out: List[Optional[float]] = []
    for row in xs:
        vec: List[float] = []
        ok = True
        src = row if isinstance(row, dict) else {}
        cs = str(fit.get("zscore_scope") or "") == "cross_section"
        for name in names:
            v = feature_get(src, name)
            if v is None:
                if not impute_missing or not (means or cs):
                    ok = False
                    break
                vec.append(0.0)
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                if not impute_missing or not (means or cs):
                    ok = False
                    break
                vec.append(0.0)
                continue
            if cs or not means:
                vec.append(fv)
                continue
            mu = float(means.get(name, 0.0))
            sd = float(stds.get(name, 1.0))
            if sd < 1e-9:
                sd = 1.0
            vec.append((fv - mu) / sd)
        if not ok:
            out.append(None)
            continue
        try:
            pred = booster.predict(np.asarray([vec], dtype=np.float64))
            out.append(round(float(pred[0]), 6) if pred.size else None)
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


def _oo_rank_use_booster(model: Dict[str, Any]) -> bool:
    solver = str(model.get("solver") or "").strip().lower()
    return solver in {"lambdarank", "lightgbm_lambda"} and bool(model.get("booster_b64"))


def _oo_rank_use_ridge(model: Dict[str, Any]) -> bool:
    """已标注的 oo_rank Ridge 包（非旧 LambdaRank gain→β 误用包）。"""
    if not isinstance(model, dict) or not _is_ridge_linear(model):
        return False
    backend = str(model.get("backend") or "").strip().lower()
    if backend == "ridge":
        return True
    target = str(model.get("target") or "").strip().lower()
    mode = str(model.get("horizon_mode") or "").strip().lower()
    return target == "oo_rank" or mode == "oo_rank"


def _oo_rank_model_valid(model: Any) -> bool:
    if not isinstance(model, dict):
        return False
    if _oo_rank_use_booster(model):
        return True
    return _oo_rank_use_ridge(model)


def _predict_ridge_rows(
    model: Dict[str, Any],
    rows: Sequence[dict],
) -> List[Optional[float]]:
    """Ridge 线性打分：与 ŷ_oo ``ReturnScoreModel.predict`` 同口径。"""
    from core.signal.return_score import ReturnScoreModel

    xs = list(rows or [])
    try:
        report = dict(model)
        report.setdefault("success", True)
        rm = ReturnScoreModel.from_ols_report(report)
    except Exception:  # noqa: BLE001
        return [None] * len(xs)
    if rm is None:
        return [None] * len(xs)
    out: List[Optional[float]] = []
    for row in xs:
        try:
            out.append(rm.predict(row if isinstance(row, dict) else {}))
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


def _predict_oo_rank_rows(
    model: Dict[str, Any],
    rows: Sequence[dict],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    """LambdaRank booster 或已标注 Ridge 包；旧 lambdarank 无 booster 线性包不打分。"""
    xs = list(rows or [])
    if _oo_rank_use_booster(model):
        return _predict_rows_lightgbm(model, xs, impute_missing=impute_missing)
    if _oo_rank_use_ridge(model):
        return _predict_ridge_rows(model, xs)
    return [None] * len(xs)


def _predict_oo_rank_matrix(
    model: Dict[str, Any],
    day: Dict[str, Any],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    n = len(day.get("ys") or [])
    if _oo_rank_use_ridge(model):
        return _predict_ridge_matrix(model, day)
    if not _oo_rank_use_booster(model):
        return [None] * n
    zm = model.get("zscore_means")
    if isinstance(zm, dict) and zm:
        names = list(zm.keys())
    elif isinstance(model.get("z_means"), dict) and model.get("z_means"):
        names = list(model.get("z_means").keys())
    else:
        names = list((model.get("coefficients") or {}).keys())
    if not names:
        return [None] * n
    import base64 as _b64

    import lightgbm as lgb

    from core.research.feature_standardize import is_cross_section_zscore

    if is_cross_section_zscore(model):
        z = _z_day_cross_section(day, names)
        if z is not None:
            z = np.where(np.isfinite(z), z, 0.0)
    else:
        means = zm if isinstance(zm, dict) else (model.get("z_means") or {})
        stds_raw = model.get("zscore_stds")
        stds = stds_raw if isinstance(stds_raw, dict) else (model.get("z_stds") or {})
        z = _z_day(day, names, means, stds)
    if z is None:
        return [None] * n
    b64 = str(model.get("booster_b64") or "")
    if not b64:
        return [None] * n
    try:
        text = _b64.b64decode(b64.encode("ascii")).decode("utf-8")
        booster = lgb.Booster(model_str=text)
        pred = booster.predict(z)
    except Exception:  # noqa: BLE001
        return [None] * n
    return [round(float(v), 6) for v in pred]


def _resolve_oo_rank_fit(
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """接受 LambdaRank booster 或已标注 Ridge 包；旧 lambdarank 无 booster 线性包拒绝。

    显式传入的 ``fit`` / ``model_doc`` 无效时不回落磁盘，避免误用执行套。
    """

    if fit is not None:
        return fit if _oo_rank_model_valid(fit) else None
    if model_doc is not None:
        model = model_doc.get("return_model") if isinstance(model_doc, dict) else None
        return model if _oo_rank_model_valid(model) else None
    doc = load_oo_rank_model(prefer_research=True)
    if isinstance(doc, dict):
        model = doc.get("return_model")
        if _oo_rank_model_valid(model):
            return model
    return None


def predict_oo_rank_from_features(
    sub_scores: Optional[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """对单行 sub_scores 打 ŷ_oo_rank（与 y_oo 同口径，特征为原始 sub_score）。"""
    from core.signal.factors.z_oo import stamp_z_oo_aliases

    model = _resolve_oo_rank_fit(model_doc=model_doc, fit=fit)
    if not isinstance(model, dict):
        return None
    row = dict(sub_scores or {})
    stamp_z_oo_aliases(row)
    preds = _predict_oo_rank_rows(model, [row], impute_missing=True)
    if not preds:
        return None
    return preds[0]


def _oo_rank_feature_names(model: Dict[str, Any]) -> List[str]:
    zm = model.get("zscore_means")
    if isinstance(zm, dict) and zm:
        return [str(n) for n in zm.keys()]
    if isinstance(model.get("z_means"), dict) and model.get("z_means"):
        return [str(n) for n in model.get("z_means").keys()]
    coefs = model.get("coefficients") or {}
    if isinstance(coefs, dict) and coefs:
        return [str(n) for n in coefs.keys()]
    active = model.get("active_features") or []
    return [str(n) for n in active if n]


def _explain_oo_rank_ridge(
    features: Optional[dict],
    model: Dict[str, Any],
    *,
    y_hat: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    from core.signal.return_score import ReturnScoreModel

    try:
        report = dict(model)
        report.setdefault("success", True)
        rm = ReturnScoreModel.from_ols_report(report)
    except Exception:  # noqa: BLE001
        return None
    if rm is None:
        return None
    try:
        expl = rm.explain_prediction(features if isinstance(features, dict) else {})
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank ridge explain failed", exc_info=True)
        return None
    if not isinstance(expl, dict):
        return None
    out = dict(expl)
    out["head"] = "oo_rank"
    out["backend"] = "ridge"
    if y_hat is not None and out.get("total") is not None:
        try:
            if abs(float(out["total"]) - float(y_hat)) < 1e-3:
                out["total"] = round(float(y_hat), 6)
        except (TypeError, ValueError):
            pass
    terms = list(out.get("terms") or [])
    if len(terms) > 24:
        out["terms"] = terms[:24]
    return out


def _explain_oo_rank_lambdarank_rows(
    rows: Sequence[dict],
    model: Dict[str, Any],
    *,
    hats: Optional[Sequence[Optional[float]]] = None,
) -> List[Optional[Dict[str, Any]]]:
    """LambdaRank：LightGBM pred_contrib → tip 因子表（无 β 列，model_role=tree）。"""
    import base64 as _b64

    import lightgbm as lgb

    from core.signal.factors.z_oo import feature_get

    xs = list(rows or [])
    n = len(xs)
    if n == 0:
        return []
    names = _oo_rank_feature_names(model)
    if not names:
        return [None] * n
    b64 = str(model.get("booster_b64") or "")
    if not b64:
        return [None] * n
    try:
        text = _b64.b64decode(b64.encode("ascii")).decode("utf-8")
        booster = lgb.Booster(model_str=text)
    except Exception:  # noqa: BLE001
        return [None] * n

    means_raw = model.get("zscore_means")
    means = (
        means_raw
        if isinstance(means_raw, dict)
        else (model.get("z_means") if isinstance(model.get("z_means"), dict) else {})
    )
    stds_raw = model.get("zscore_stds")
    stds = (
        stds_raw
        if isinstance(stds_raw, dict)
        else (model.get("z_stds") if isinstance(model.get("z_stds"), dict) else {})
    )
    cs = str(model.get("zscore_scope") or "") == "cross_section"

    mat = np.zeros((n, len(names)), dtype=np.float64)
    imputed: List[List[bool]] = []
    for i, row in enumerate(xs):
        src = row if isinstance(row, dict) else {}
        flags: List[bool] = []
        for j, name in enumerate(names):
            v = feature_get(src, name)
            if v is None:
                mat[i, j] = 0.0
                flags.append(True)
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                mat[i, j] = 0.0
                flags.append(True)
                continue
            if cs or not means:
                mat[i, j] = fv
            else:
                mu = float(means.get(name, 0.0))
                sd = float(stds.get(name, 1.0))
                if sd < 1e-9:
                    sd = 1.0
                mat[i, j] = (fv - mu) / sd
            flags.append(False)
        imputed.append(flags)

    try:
        contrib = np.asarray(booster.predict(mat, pred_contrib=True), dtype=np.float64)
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank lambdarank pred_contrib failed", exc_info=True)
        return [None] * n
    if contrib.ndim != 2 or int(contrib.shape[0]) != n or int(contrib.shape[1]) < len(names) + 1:
        return [None] * n

    try:
        from core.signal.factors.meta.registry import factor_label
    except Exception:  # noqa: BLE001
        factor_label = lambda k: str(k)  # noqa: E731

    # 模型 gain 份额（与研究枢纽因子表同口径；落盘在 coefficients）
    gain_by_key: Dict[str, float] = {}
    coefs = model.get("coefficients")
    if isinstance(coefs, dict):
        for k, v in coefs.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv) or fv <= 0:
                continue
            # 份额≤1.5 视为 0–1；否则已是百分数
            gain_by_key[str(k)] = fv * 100.0 if fv <= 1.5 else fv
    fi = model.get("feature_importance")
    if isinstance(fi, list):
        for g in fi:
            if not isinstance(g, dict):
                continue
            k = str(g.get("key") or g.get("name") or "").strip()
            if not k or k in gain_by_key:
                continue
            share = g.get("share")
            if share is None:
                share = g.get("gain")
            try:
                fv = float(share)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fv) or fv <= 0:
                continue
            gain_by_key[k] = fv * 100.0 if fv <= 1.5 else fv

    out: List[Optional[Dict[str, Any]]] = []
    for i in range(n):
        bias = float(contrib[i, len(names)])
        terms: List[Dict[str, Any]] = []
        total = bias
        for j, name in enumerate(names):
            c = float(contrib[i, j])
            total += c
            if abs(c) < 1e-8:
                continue
            term: Dict[str, Any] = {
                "key": name,
                "label": str(factor_label(name) or name),
                "z": round(float(mat[i, j]), 4),
                "contrib": round(c, 6),
            }
            gshare = gain_by_key.get(name)
            if gshare is not None:
                term["gain"] = round(float(gshare), 2)
            if imputed[i][j]:
                term["note"] = "缺特征·z≈0"
            terms.append(term)
        terms.sort(key=lambda t: -abs(float(t.get("contrib") or 0.0)))
        y = None
        if hats is not None and i < len(hats) and hats[i] is not None:
            try:
                y = round(float(hats[i]), 6)
                if abs(total - y) < 1e-3:
                    total = y
            except (TypeError, ValueError):
                y = None
        out.append(
            {
                "intercept": round(bias, 6),
                "terms": terms[:24],
                "total": round(float(total if y is None else total), 6),
                "head": "oo_rank",
                "backend": "lambdarank",
                "model_role": "tree",
            }
        )
    return out


def explain_oo_rank_prediction(
    features: Optional[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
    y_hat: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """单行 tip 组成：Ridge=α+Σβ·z；LambdaRank=pred_contrib（树表）。"""
    model = _resolve_oo_rank_fit(model_doc=model_doc, fit=fit)
    if not isinstance(model, dict):
        return None
    row = features if isinstance(features, dict) else {}
    if _oo_rank_use_ridge(model):
        return _explain_oo_rank_ridge(row, model, y_hat=y_hat)
    if _oo_rank_use_booster(model):
        expls = _explain_oo_rank_lambdarank_rows(
            [row], model, hats=[y_hat] if y_hat is not None else None
        )
        return expls[0] if expls else None
    return None


def _stamp_formula_terms_oo_rank(
    item: dict,
    expl: Optional[Dict[str, Any]],
) -> None:
    if not isinstance(item, dict) or not isinstance(expl, dict):
        return
    item["formula_terms_oo_rank"] = expl
    item["score_formula_terms_oo_rank"] = expl


_OO_RANK_DROP = (
    "y_oo_rank",
    "y_oo_rank_hat",
    "y_oo_rank_score",
    "y_oo_rank_n",
    "predicted_score_oo_rank",
    "formula_terms_oo_rank",
    "score_formula_terms_oo_rank",
)


def apply_oo_rank_scores(
    items: Sequence[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
    assign_day_ranks: bool = True,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
) -> List[dict]:
    """就地写入 y_oo_rank；缺 sub_scores 则跳过。不改 y_oo_fuse；过入场后作买序。

    截面 z 只用全池 L2（``day_cube`` / ``cs_z_*_prefer_cube``）；缺 Cube 不写分。
    ``score_codes`` 只限制写分/名次；训练宇宙（A）不影响打分 z。
    默认 ``assign_day_ranks=True``：对有相对分的票编 1..n 名次（1=最高分）。
    同时写入 ``formula_terms_oo_rank`` 供成交明细 oo_rank tip。
    """
    from core.research.feature_standardize import (
        is_cross_section_zscore,
        normalize_score_codes,
    )

    doc = model_doc
    if doc is None and fit is None:
        doc = load_oo_rank_model(prefer_research=True)
    model = _resolve_oo_rank_fit(model_doc=doc, fit=fit)

    from core.signal.factors.z_oo import stamp_z_oo_aliases

    allow = normalize_score_codes(score_codes)

    out = list(items)
    raw_rows: List[dict] = []
    slots: List[int] = []
    for i, it in enumerate(out):
        if not isinstance(it, dict):
            continue
        subs = it.get("sub_scores") or it.get("sub_scores_raw")
        if not isinstance(subs, dict):
            continue
        row = dict(subs)
        stamp_z_oo_aliases(row)
        raw_rows.append(row)
        slots.append(i)
    keep: List[int] = []
    for j, slot in enumerate(slots):
        code = str(out[slot].get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            for key in _OO_RANK_DROP:
                out[slot].pop(key, None)
            continue
        keep.append(j)
    feat_rows: List[dict] = raw_rows
    hats: List[Optional[float]] = [None] * len(raw_rows)
    if is_cross_section_zscore(model) and raw_rows:
        names = list((model or {}).get("active_features") or (model or {}).get("coefficients") or [])
        if not names:
            names = list(raw_rows[0].keys())
        codes = [str(out[s].get("stock_code") or "").strip() for s in slots]
        groups = [_item_cs_group(out[s]) for s in slots]
        feat_rows = rank_cross_section_z_dicts(
            raw_rows,
            [str(n) for n in names],
            groups,
            day_cube=day_cube,
            codes=codes,
            clock=str(clock or "open"),
        )
        if feat_rows is None:
            # 无 L2：不写分（禁止进批现算 / raw×β）
            return out
        pred_rows = [feat_rows[j] for j in keep]
        pred_hats = _predict_oo_rank_rows(model, pred_rows, impute_missing=True) if pred_rows else []
        for j, hat in zip(keep, pred_hats):
            hats[j] = hat
            write_y_oo_rank_hat(out[slots[j]], hat)
    else:
        for j in keep:
            hat = predict_oo_rank_from_features(
                raw_rows[j], model_doc=doc, fit=fit or model
            )
            hats[j] = hat
            write_y_oo_rank_hat(out[slots[j]], hat)

    # tip 因子表：相对分阶段写入（编名次前）；只写有分的票
    if isinstance(model, dict) and keep:
        try:
            expl_rows = [feat_rows[j] for j in keep]
            expl_hats = [hats[j] for j in keep]
            expl_slots = [slots[j] for j in keep]
            if _oo_rank_use_booster(model):
                expls = _explain_oo_rank_lambdarank_rows(expl_rows, model, hats=expl_hats)
                for slot, expl in zip(expl_slots, expls):
                    _stamp_formula_terms_oo_rank(out[slot], expl)
            elif _oo_rank_use_ridge(model):
                for slot, feats, hat in zip(expl_slots, expl_rows, expl_hats):
                    expl = _explain_oo_rank_ridge(feats, model, y_hat=hat)
                    _stamp_formula_terms_oo_rank(out[slot], expl)
        except Exception:  # noqa: BLE001
            logger.debug("stamp formula_terms_oo_rank failed", exc_info=True)

    if assign_day_ranks:
        assign_oo_rank_day_ranks(out)
    return out


def _spearman(preds: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(preds)
    if n < 5:
        return None

    def ranks(vals: Sequence[float]) -> List[float]:
        order = sorted(range(n), key=lambda i: float(vals[i]))
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and float(vals[order[j + 1]]) == float(vals[order[i]]):
                j += 1
            avg = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(preds), ranks(ys)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx < 1e-12 or dy < 1e-12:
        return None
    return round(num / (dx * dy), 4)


def _topk_overlap(pred: Sequence[float], ys: Sequence[float], k: int) -> Optional[float]:
    n = len(pred)
    if n < max(4, k):
        return None
    kk = max(1, min(int(k), n))
    pred_top = set(sorted(range(n), key=lambda i: float(pred[i]), reverse=True)[:kk])
    true_top = set(sorted(range(n), key=lambda i: float(ys[i]), reverse=True)[:kk])
    return round(len(pred_top & true_top) / float(kk), 4)


def _is_ridge_linear(model: Dict[str, Any]) -> bool:
    """同窗 Ridge ŷ_oo 基线：系数包，不是 LambdaRank booster。"""
    if not isinstance(model, dict) or _oo_rank_use_booster(model):
        return False
    solver = str(model.get("solver") or "").strip().lower()
    if solver in {"lambdarank", "lightgbm_lambda"}:
        return False
    coefs = model.get("coefficients")
    return isinstance(coefs, dict) and any(v is not None for v in coefs.values())


def _predict_ridge_matrix(
    model: Dict[str, Any], day: Dict[str, Any]
) -> List[Optional[float]]:
    """与 ``predict_oo_matrix`` 同口径：z-score 后线性打分；缺测跳过，不当成 0。"""
    n = len(day.get("ys") or [])
    coefs = model.get("coefficients") if isinstance(model.get("coefficients"), dict) else {}
    names = [str(k) for k, v in coefs.items() if v is not None]
    if n <= 0 or not names:
        return [None] * n
    raw = _raw_day_aligned(day, names)
    if raw is None or int(raw.shape[0]) != n:
        return [None] * n
    means = model.get("zscore_means")
    if not isinstance(means, dict):
        means = model.get("z_means") if isinstance(model.get("z_means"), dict) else {}
    stds = model.get("zscore_stds")
    if not isinstance(stds, dict):
        stds = model.get("z_stds") if isinstance(model.get("z_stds"), dict) else {}
    try:
        intercept = float(model.get("intercept") or 0.0)
    except (TypeError, ValueError):
        intercept = 0.0
    from core.research.feature_standardize import is_cross_section_zscore

    feature_zscore = bool(model.get("feature_zscore", True))
    if is_cross_section_zscore(model):
        z = _z_day_cross_section(day, names)
        if z is None or int(z.shape[0]) != n:
            return [None] * n
        raw = np.where(np.isfinite(z), z, 0.0)
        feature_zscore = False
    total = np.full(n, intercept, dtype=np.float64)
    used = np.zeros(n, dtype=bool)
    for j, name in enumerate(names):
        beta = coefs.get(name)
        if beta is None:
            continue
        col = raw[:, j]
        ok = np.isfinite(col)
        if not bool(ok.any()):
            continue
        vals = col
        if feature_zscore:
            try:
                mu = float(means.get(name) or 0.0)
            except (TypeError, ValueError):
                mu = 0.0
            try:
                sd = float(stds.get(name) or 1.0)
            except (TypeError, ValueError):
                sd = 1.0
            if sd < 1e-12:
                sd = 1.0
            vals = (col - mu) / sd
        total[ok] += float(beta) * vals[ok]
        used |= ok
    return [round(float(total[i]), 6) if used[i] else None for i in range(n)]


def _day_scores(
    day: Dict[str, Any], fit: Dict[str, Any]
) -> Tuple[List[Optional[float]], List[float]]:
    ys = label_floats(day.get("ys"))
    if _is_ridge_linear(fit):
        return _predict_ridge_matrix(fit, day), ys
    packed = _day_X(day)
    if packed is None:
        return [], ys
    preds = _predict_oo_rank_matrix(fit, day, impute_missing=True)
    return preds, ys


def _oos_day_metrics(
    days: Sequence[Dict[str, Any]],
    fit: Dict[str, Any],
    *,
    topk_track: int,
    ndcg_k: int = DEFAULT_NDCG_K,
    train_codes: Optional[Collection[str]] = None,
) -> Dict[str, Any]:
    from core.backtest.factor_cs_ic import _agg_ic_series
    from core.backtest.pool_ic import _pearson

    allow = _normalize_code_allow(train_codes)
    spearman_days: List[float] = []
    pearson_days: List[float] = []
    overlap_days: List[float] = []
    ndcg_days: List[float] = []
    topk_rets: List[float] = []
    n_scored = 0
    for day in days:
        preds, ys = _day_scores(day, fit)
        if allow is not None:
            codes = day.get("codes") if isinstance(day, dict) else None
            if isinstance(codes, (list, tuple)) and len(codes) == len(preds):
                preds = [
                    p
                    for p, c in zip(preds, codes)
                    if str(c or "").strip() in allow
                ]
                ys = [
                    y
                    for y, c in zip(ys, codes)
                    if str(c or "").strip() in allow
                ]
            else:
                preds, ys = [], []
        pairs_py = finite_pairs(preds, ys)
        if len(pairs_py) < 5:
            continue
        n_scored += len(pairs_py)
        pp = [a for a, _ in pairs_py]
        yy = [b for _, b in pairs_py]
        sp = _spearman(pp, yy)
        if sp is not None:
            spearman_days.append(sp)
        pe = _pearson(pp, yy)
        if pe is not None:
            pearson_days.append(float(pe))
        ov = _topk_overlap(pp, yy, topk_track)
        if ov is not None:
            overlap_days.append(ov)
        nd = _ndcg_at_k(pp, yy, k=ndcg_k)
        if nd is not None:
            ndcg_days.append(nd)
        kk = max(1, min(int(topk_track), len(pp)))
        idx = sorted(range(len(pp)), key=lambda i: pp[i], reverse=True)[:kk]
        topk_rets.append(sum(yy[i] for i in idx) / float(kk))

    def _mean(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        return round(sum(xs) / len(xs), 4)

    pear = _agg_ic_series(pearson_days)
    return {
        "n_days": len(spearman_days),
        "n_rows": n_scored,
        "spearman": _mean(spearman_days),
        "cs_ic": pear.get("ic_mean"),
        "cs_ic_std": pear.get("ic_std"),
        "cs_icir": pear.get("icir"),
        "cs_ic_day_count": pear.get("day_count"),
        "topk_overlap": _mean(overlap_days),
        "ndcg_at_k": _mean(ndcg_days),
        "ndcg_k": int(ndcg_k),
        "topk_mean_y_oo": _mean(topk_rets),
        "topk": int(topk_track),
    }


def _fit_ridge_baseline(
    days: Sequence[Dict[str, Any]],
    *,
    feature_names: Sequence[str],
    ridge_lambda: float = 1.0,
    cross_section_zscore: bool = True,
    train_codes: Optional[Collection[str]] = None,
) -> Dict[str, Any]:
    """Ridge 基线。日截面 z 用当天全行，损失可再缩到 ``train_codes``。"""
    from core.research.feature_standardize import stamp_cross_section_zscore

    names = [str(n) for n in feature_names]
    allow = _normalize_code_allow(train_codes)
    use_cs = bool(cross_section_zscore)
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    if use_cs:
        days = _filter_days_with_l2(days, clock="open")
    if not use_cs:
        means, stds = _global_z_stats(days, names)
    blocks: List[np.ndarray] = []
    y_parts: List[np.ndarray] = []
    date_parts: List[str] = []
    for day in days or []:
        ys = label_floats(day.get("ys"))
        if use_cs:
            Z = _z_day_cross_section(day, names)
        else:
            raw = _raw_day_aligned(day, names)
            if raw is None or len(ys) != int(raw.shape[0]):
                continue
            Z = _z_day(day, names, means, stds)
        if Z is None or len(ys) != int(Z.shape[0]):
            continue
        Z = np.where(np.isfinite(Z), Z, 0.0)
        keep = _keep_idx_for_codes(day, len(ys), allow)
        ys, Z = _take_rows(ys, Z, keep)
        ys, Z, _n_drop = _slice_finite_label_rows(ys, Z)
        if len(ys) < 1:
            continue
        blocks.append(Z)
        y_parts.append(np.asarray(ys, dtype=np.float64))
        day_s = str(day.get("date") or "")[:10]
        date_parts.extend([day_s] * len(ys))
    if blocks:
        from core.research.oo_ridge_compact import fit_oo_ridge_matrix

        x = np.vstack(blocks)
        del blocks
        y = np.concatenate(y_parts)
        del y_parts
        n_rows = int(y.shape[0])
        if n_rows < 20:
            return {"success": False, "error": f"ridge_n={n_rows}"}
        # 已按日截面 z（或全局 z）；不再二次 CS
        _model, report = fit_oo_ridge_matrix(
            x,
            y,
            names,
            ridge_lambda=float(ridge_lambda),
            horizon_days=1,
            row_dates=date_parts,
            cross_section_zscore=False,
        )
        if use_cs and isinstance(report, dict) and report.get("success"):
            stamp_cross_section_zscore(report)
            model = report.get("return_model") or report.get("model")
            if isinstance(model, dict):
                stamp_cross_section_zscore(model)
                model["zscore_means"] = {}
                model["zscore_stds"] = {}
                model["z_means"] = {}
                model["z_stds"] = {}
        return report
    return {"success": False, "error": "ridge_n=0"}


def _stamp_oo_rank_model_meta(
    model: Dict[str, Any],
    *,
    backend: str,
    role: str,
    y_spec: Dict[str, Any],
    horizon_days: int,
    ndcg_k: int,
) -> Dict[str, Any]:
    out = dict(model)
    out["backend"] = backend
    out["y_spec"] = y_spec
    out["horizon_days"] = int(horizon_days)
    out["horizon_mode"] = "oo_rank"
    out["target"] = "oo_rank"
    out["model_role"] = role
    out["ndcg_k"] = int(ndcg_k)
    out.pop("shadow_only", None)
    if backend == "ridge":
        solver = str(out.get("solver") or "").strip().lower()
        if solver in {"", "lambdarank", "lightgbm_lambda"}:
            out["solver"] = "ridge"
    return out


def fit_oo_rank_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    min_history: int = 12,
    min_names: int = DEFAULT_MIN_NAMES,
    holdout_trading_days: int = DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK,
    l2: float = DEFAULT_L2,
    topk_track: int = DEFAULT_TOPK_TRACK,
    ndcg_k: int = DEFAULT_NDCG_K,
    ridge_lambda: float = 1.0,
    index_bars: Optional[List[dict]] = None,
    excess_mode: str = "none",
    persist: bool = False,
    day_panels: Optional[Sequence[Dict[str, Any]]] = None,
    backend: str = "auto",
    lightgbm_params: Optional[Dict[str, Any]] = None,
    cross_section_zscore: bool = True,
    train_codes: Optional[Collection[str]] = None,
) -> Dict[str, Any]:
    """拟合 ŷ_oo_rank：同窗 LambdaRank + Ridge，按 Holdout NDCG@K 推荐 backend。

    ``day_panels``：若已预计算日截面（矩阵 ``X`` 或 raw sub_score dict），可跳过建面板。
    特征与 ŷ_oo 同口径：原始 sub_score；``cross_section_zscore`` 控制日截面 / 全局 μ/σ。
    日截面 z 用当天全池行；``train_codes`` 给定时损失与 Holdout 指标缩到该子集。
    默认建面板走 compact 矩阵（与 ŷ_oo 相同，逐只丢掉行 dict）。

    ``backend``：``"auto"``（默认，按 NDCG@K 选）/ ``"lambdarank"`` / ``"ridge"``。
    拟合始终双训；``backend`` 只影响报告里 ``return_model`` 默认指向哪一侧。
    ``lightgbm_params`` 可覆盖超参。
    OOS 指标：spearman / cs_ic / cs_icir / topk_overlap / ndcg_at_k / topk_mean_y_oo。
    ``cs_ic`` / ``cs_icir`` 与 ŷ_oo 同口径：Holdout 日截面 Pearson(排序分, y_oo) 的均值，
    ICIR = 该日序列 mean/std（不足 3 日不出）。用原始排序分，不用 1..n 名次。
    """
    from core.research.holdout import (
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        resolve_ridge_split,
    )
    from core.research.oo_rank_panel import build_oo_rank_day_panels

    # 与 ŷ_τc_rank 同：物化留下的整窗立方先卸掉，再进面板。
    try:
        from core.research.horizon_l1 import release_allocator_cache

        release_allocator_cache()
    except Exception:  # noqa: BLE001
        logger.debug("oo_rank release before panel failed", exc_info=True)

    if day_panels is not None:
        days = list(day_panels)
    else:
        days = build_oo_rank_day_panels(
            stock_bars,
            horizon_days=horizon_days,
            min_history=min_history,
            min_names=min_names,
            index_bars=index_bars,
            excess_mode=excess_mode,
        )
    if len(days) < 8:
        return {
            "success": False,
            "error": f"day_panels_insufficient n_days={len(days)}（需≥8）",
            "task": TASK,
            "n_days": len(days),
        }

    date_rows = [str(d["date"]) for d in days]
    hold_n = int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK)
    train_idx, test_idx, split_meta = resolve_ridge_split(
        date_rows,
        holdout_trading_days=hold_n,
        label_horizon_days=max(1, int(horizon_days)),
        calendar_dates=calendar_dates_from_stock_bars(stock_bars),
    )
    days_tr = [days[i] for i in train_idx]
    days_te = [days[i] for i in test_idx]
    if len(days_tr) < 5:
        return {
            "success": False,
            "error": f"train_days_insufficient n={len(days_tr)}",
            "task": TASK,
            "n_days": len(days),
        }

    feat_names = _collect_feature_names(days_tr)
    force_backend = str(backend or "auto").strip().lower()
    if force_backend in {"lightgbm", "lgb", "lambdarank"}:
        force_backend = "lambdarank"
    if force_backend not in {"auto", "lambdarank", "ridge"}:
        return {
            "success": False,
            "error": f"unsupported backend={force_backend}；支持 auto / lambdarank / ridge",
            "task": TASK,
            "n_days": len(days),
            "backend": force_backend,
        }
    use_cs = bool(cross_section_zscore)
    train_allow = _normalize_code_allow(train_codes)
    lgb_kw: Dict[str, Any] = dict(
        l2=l2, cross_section_zscore=use_cs, train_codes=train_allow
    )
    if isinstance(lightgbm_params, dict) and lightgbm_params:
        lgb_kw.update(lightgbm_params)
        lgb_kw["cross_section_zscore"] = use_cs
        lgb_kw["train_codes"] = train_allow
    fit_tr = fit_lambdarank(
        days_tr,
        feature_names=feat_names,
        **lgb_kw,
    )
    if not fit_tr.get("success"):
        return {
            "success": False,
            "error": fit_tr.get("error") or "lambdarank_fit_failed",
            "task": TASK,
            "n_days": len(days),
            "backend": "lambdarank",
            "detail": fit_tr,
        }

    oos_rank = _oos_day_metrics(
        days_te,
        fit_tr,
        topk_track=topk_track,
        ndcg_k=ndcg_k,
        train_codes=train_allow,
    )
    ridge_tr = _fit_ridge_baseline(
        days_tr,
        feature_names=feat_names,
        ridge_lambda=ridge_lambda,
        train_codes=train_allow,
        cross_section_zscore=use_cs,
    )
    oos_ridge: Dict[str, Any] = {"success": bool(ridge_tr.get("success"))}
    if ridge_tr.get("success"):
        oos_ridge.update(
            _oos_day_metrics(
                days_te,
                ridge_tr,
                topk_track=topk_track,
                ndcg_k=ndcg_k,
                train_codes=train_allow,
            )
        )

    # 只训 Holdout 前一套 β（LambdaRank / Ridge 两 backend 仍同窗双训）
    lambda_holdout = dict(fit_tr)
    ridge_holdout = dict(ridge_tr) if ridge_tr.get("success") else None

    recommended = select_oo_rank_backend(oos_rank, oos_ridge)
    chosen = recommended if force_backend == "auto" else force_backend
    if chosen == "ridge" and not (
        isinstance(ridge_holdout, dict) and ridge_holdout.get("success")
    ):
        chosen = "lambdarank"

    y_spec_base = {
        "formula": FORMULA_OO_RANK,
        "unit": "score",
        "anchor": "open[T]",
        "label": "rank(open[T]->open[T+1]) cross-section",
    }

    def _pack(backend_key: str, holdout_src: Dict[str, Any]) -> Dict[str, Any]:
        note = (
            "Ridge ŷ_oo 相对分；live/回测编当日截面 1..n 名次（1=最高）；"
            "不进 y_oo_fuse；过入场后作买序；可选 oo_rank_max 入场闸"
            if backend_key == "ridge"
            else "LambdaRank；模型输出相对分；live/回测成交明细编成当日截面 1..n 名次（1=最高）；"
            "不进 y_oo_fuse；过入场后作买序；可选 oo_rank_max 入场闸"
        )
        y_spec = dict(y_spec_base, backend=backend_key, note=note)
        research = _stamp_oo_rank_model_meta(
            make_research_model(holdout_src),
            backend=backend_key,
            role="research",
            y_spec=y_spec,
            horizon_days=horizon_days,
            ndcg_k=ndcg_k,
        )
        if backend_key == "lambdarank":
            research["intercept"] = 0.0
        return {
            "return_model": research,
            "return_model_research": research,
            "y_spec": y_spec,
        }

    lambda_pack = _pack("lambdarank", lambda_holdout)
    backends: Dict[str, Any] = {"lambdarank": lambda_pack}
    if isinstance(ridge_holdout, dict) and ridge_holdout.get("success"):
        backends["ridge"] = _pack("ridge", ridge_holdout)

    if chosen not in backends:
        chosen = "lambdarank"
    chosen_pack = backends[chosen]
    model = chosen_pack["return_model"]
    research_model = chosen_pack["return_model_research"]
    y_spec = chosen_pack["y_spec"]

    report: Dict[str, Any] = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": chosen,
        "recommended_backend": recommended,
        "solver": str(model.get("solver") or ""),
        "n_days": len(days),
        "n_train_days": len(days_tr),
        "n_test_days": len(days_te),
        "sample_count": int(model.get("sample_count") or 0),
        "n_features": len(feat_names),
        "ndcg_k": int(ndcg_k),
        "oos": {
            "oo_rank": oos_rank,
            "ridge_oo_baseline": oos_ridge,
            "holdout_trading_days": hold_n,
            "topk_track": int(topk_track),
            "ndcg_k": int(ndcg_k),
            "recommended_backend": recommended,
        },
        "backends": backends,
        "return_model": model,
        "return_model_research": research_model,
        "y_spec": y_spec,
        "cross_section_zscore": use_cs,
        "zscore_universe": "pool",
        "note": (
            f"ŷ_oo_rank backend={chosen}（NDCG@K 推荐 {recommended}）；"
            "只训 Holdout 前一套；启用研究→研究槽；启用执行=研究槽晋级；"
            "不进 y_oo_fuse；过入场后作买序；成交明细编当日截面 1..n 名次；"
            "可选 oo_rank_max 入场闸"
        ),
    }
    attach_holdout_meta(report, split_meta)
    stamp_oo_rank_fitted_at(report)
    if persist:
        # 默认只落研究槽；执行须显式晋级
        paths = persist_oo_rank_model(report, role="research")
        report["persisted"] = paths
    return report


def compare_oo_rank_shadow_track(
    stock_bars: Sequence[Dict[str, Any]],
    **kwargs: Any,
) -> Dict[str, Any]:
    """历史跑路对照入口：拟合报告 + OOS TopK 均值收益（rank vs ridge）。"""
    report = fit_oo_rank_report(stock_bars, persist=False, **kwargs)
    if not report.get("success"):
        return report
    oos = report.get("oos") or {}
    rank_m = oos.get("oo_rank") or {}
    ridge_m = oos.get("ridge_oo_baseline") or {}
    return {
        "success": True,
        "task": "oo_rank_shadow_track",
        "schema": SCHEMA,
        "backend": report.get("backend"),
        "recommended_backend": report.get("recommended_backend"),
        "n_days": report.get("n_days"),
        "n_train_days": report.get("n_train_days"),
        "n_test_days": report.get("n_test_days"),
        "ndcg_k": report.get("ndcg_k"),
        "n_features": report.get("n_features"),
        "oos": oos,
        "delta_topk_mean_y_oo": (
            None
            if rank_m.get("topk_mean_y_oo") is None or ridge_m.get("topk_mean_y_oo") is None
            else round(
                float(rank_m["topk_mean_y_oo"]) - float(ridge_m["topk_mean_y_oo"]),
                4,
            )
        ),
        "delta_spearman": (
            None
            if rank_m.get("spearman") is None or ridge_m.get("spearman") is None
            else round(float(rank_m["spearman"]) - float(ridge_m["spearman"]), 4)
        ),
        "delta_cs_ic": (
            None
            if rank_m.get("cs_ic") is None or ridge_m.get("cs_ic") is None
            else round(float(rank_m["cs_ic"]) - float(ridge_m["cs_ic"]), 4)
        ),
        "delta_cs_icir": (
            None
            if rank_m.get("cs_icir") is None or ridge_m.get("cs_icir") is None
            else round(float(rank_m["cs_icir"]) - float(ridge_m["cs_icir"]), 4)
        ),
        "delta_topk_overlap": (
            None
            if rank_m.get("topk_overlap") is None or ridge_m.get("topk_overlap") is None
            else round(
                float(rank_m["topk_overlap"]) - float(ridge_m["topk_overlap"]),
                4,
            )
        ),
        "delta_ndcg_at_k": (
            None
            if rank_m.get("ndcg_at_k") is None or ridge_m.get("ndcg_at_k") is None
            else round(float(rank_m["ndcg_at_k"]) - float(ridge_m["ndcg_at_k"]), 4)
        ),
        "note": report.get("note"),
        "return_model": report.get("return_model"),
        "return_model_research": report.get("return_model_research"),
    }


def stamp_rank_zscore_universe_meta(
    report: Dict[str, Any],
    *,
    zscore_universe: str = "pool",
    train_universe: Optional[str] = None,
    tier_codes: Optional[Sequence[str]] = None,
    tier_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把宇宙写进报告与各 backend 的 return_model。

    截面 z 恒 ``pool``；``train_universe`` 为损失/Holdout 子集（默认跟旧
    ``zscore_universe`` 参数，兼容「勾选 A」只训 A）。
    """
    from core.research.feature_standardize import (
        ZSCORE_UNIVERSE_POOL,
        normalize_train_universe,
        stamp_train_universe,
        stamp_zscore_universe,
    )

    if not isinstance(report, dict):
        return report
    train_u = normalize_train_universe(
        train_universe if train_universe is not None else zscore_universe
    )
    stamp_zscore_universe(report, ZSCORE_UNIVERSE_POOL)
    stamp_train_universe(
        report,
        train_u,
        tier_codes=tier_codes,
        tier_meta=tier_meta,
    )
    targets: List[dict] = []
    for key in ("return_model", "return_model_research"):
        m = report.get(key)
        if isinstance(m, dict):
            targets.append(m)
    backends = report.get("backends")
    if isinstance(backends, dict):
        for pack in backends.values():
            if not isinstance(pack, dict):
                continue
            for key in ("return_model", "return_model_research"):
                m = pack.get(key)
                if isinstance(m, dict):
                    targets.append(m)
    for m in targets:
        stamp_zscore_universe(m, ZSCORE_UNIVERSE_POOL)
        stamp_train_universe(
            m,
            train_u,
            tier_codes=tier_codes,
            tier_meta=tier_meta,
        )
    return report


__all__ = [
    "DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK",
    "DEFAULT_NDCG_K",
    "DEFAULT_TOPK_TRACK",
    "FORMULA_OO_RANK",
    "SCHEMA",
    "SCHEMA_LEGACY",
    "TASK",
    "TASK_LEGACY",
    "Y_OO_RANK_HAT_KEYS",
    "Y_OO_RANK_LABEL_KEYS",
    "apply_oo_rank_scores",
    "rank_cross_section_z_dicts",
    "stamp_rank_zscore_universe_meta",
    "assign_oo_rank_day_ranks",
    "compare_oo_rank_shadow_track",
    "explain_oo_rank_prediction",
    "fit_oo_rank_report",
    "load_oo_rank_last_report",
    "load_oo_rank_model",
    "oo_rank_last_report_path",
    "oo_rank_last_report_path_legacy",
    "oo_rank_model_path",
    "oo_rank_model_path_legacy",
    "persist_oo_rank_model",
    "pick_y_oo_rank_hat",
    "pick_y_oo_rank_label",
    "predict_oo_rank_from_features",
    "save_oo_rank_last_report",
    "select_oo_rank_backend",
    "stamp_oo_rank_fitted_at",
    "write_y_oo_rank_hat",
    "write_y_oo_rank_realized",
]
