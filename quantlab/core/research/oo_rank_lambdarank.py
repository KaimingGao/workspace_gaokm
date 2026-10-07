"""ŷ_oo_rank：LightGBM LambdaRank 旁路对照。

训练：按日观察池 y_oo 排序，优化 NDCG。
推理：Booster 相对分 → 当日截面编成 1..n 名次（1=最高）写入成交明细。
旁路对照：不进 ranking / 买序。
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from core.io_atomic import atomic_write_json

Y_OO_RANK_HAT_KEYS = (
    "y_oo_rank",
    "y_oo_rank_hat",
    "predicted_score_oo_rank",
)
Y_OO_RANK_LABEL_KEYS = (
    "y_oo_rank_realized",
    "oo_rank_realized",
)
FORMULA_OO_RANK = "rank(open[T+1]/open[T]-1 | watching_day)"
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
    同分按 ``stock_code`` 稳定破平。不改 ranking / 买序。
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
    """加载影子模型。默认优先研究套 sidecar；缺则 live 文件。

    新路径优先，旧 ``oo_rank_pairwise_*.json`` 可读兼容。
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
    for key in ("return_model", "return_model_research"):
        nested = report.get(key)
        if isinstance(nested, dict) and not nested.get("fitted_at"):
            nested["fitted_at"] = ts
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
    """上次拟合草稿。落盘影子只写这份，不再重训。新路径优先，旧文件可读。"""
    doc = _first_existing_model(
        oo_rank_last_report_path(),
        oo_rank_last_report_path_legacy(),
    )
    if doc and doc.get("success"):
        return doc
    return None


def persist_oo_rank_model(report: Dict[str, Any], *, also_research: bool = True) -> Dict[str, str]:
    """落盘执行套；可选同时写研究套（Phase1 影子默认双写同一 β）。只写新路径。"""
    if not isinstance(report, dict) or not report.get("success"):
        return {}
    stamp_oo_rank_fitted_at(report)
    model = report.get("return_model")
    if not isinstance(model, dict):
        return {}
    live = oo_rank_model_path()
    os.makedirs(os.path.dirname(live), exist_ok=True)
    doc = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "return_model": model,
        "oos": report.get("oos"),
        "y_spec": report.get("y_spec") or model.get("y_spec"),
        "note": report.get("note"),
        "n_days": report.get("n_days"),
        "n_train_days": report.get("n_train_days"),
        "n_test_days": report.get("n_test_days"),
        "sample_count": report.get("sample_count"),
        "shadow_track": report.get("shadow_track"),
        "shadow_only": True,
        "fitted_at": report.get("fitted_at"),
    }
    atomic_write_json(live, doc)
    out = {"live": live}
    if also_research:
        try:
            from core.research.holdout import research_model_path

            research = dict(model)
            research["model_role"] = "research"
            rdoc = dict(doc)
            rdoc["return_model"] = research
            if isinstance(report.get("return_model_research"), dict):
                rdoc["return_model"] = report["return_model_research"]
            rpath = research_model_path(live)
            atomic_write_json(rpath, rdoc)
            out["research"] = rpath
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank research persist failed", exc_info=True)
    save_oo_rank_last_report(report)
    return out


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


def _fit_zscore(
    days: Sequence[Dict[str, Any]], feature_names: Sequence[str]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    names = [str(n) for n in feature_names]
    if not names:
        return {}, {}
    blocks: List[np.ndarray] = []
    for day in days or []:
        Z = _raw_day_aligned(day, names)
        if Z is not None and Z.shape[0] > 0:
            blocks.append(Z)
    if blocks:
        X = np.vstack(blocks)
        means: Dict[str, float] = {}
        stds: Dict[str, float] = {}
        for j, name in enumerate(names):
            col = X[:, j]
            finite = col[np.isfinite(col)]
            if finite.size <= 0:
                means[name] = 0.0
                stds[name] = 1.0
                continue
            mu = float(finite.mean())
            var = float(((finite - mu) ** 2).mean())
            sd = math.sqrt(var) if var > 0 else 1.0
            if sd < 1e-9:
                sd = 1.0
            means[name] = mu
            stds[name] = sd
        return means, stds
    return {n: 0.0 for n in names}, {n: 1.0 for n in names}


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
) -> Dict[str, Any]:
    """LightGBM LambdaRank：按日截面 group，优化 NDCG。

    输出与推理共用：
    - ``coefficients``：feature importance (gain)，仅诊断
    - ``zscore_means`` / ``zscore_stds``：推理时 z-score
    - ``solver`` = ``lambdarank``（旧包 ``lightgbm_lambda`` 仍可读）
    - ``booster_b64``：LightGBM 模型
    """
    import lightgbm as lgb

    names = list(feature_names) if feature_names else _collect_feature_names(days)
    if not names:
        return {"success": False, "error": "no_features"}
    means, stds = _fit_zscore(days, names)
    p = len(names)
    X_blocks: List[np.ndarray] = []
    y_blocks: List[List[int]] = []
    group_sizes: List[int] = []
    for day in days:
        ys = [float(v) for v in (day.get("ys") or [])]
        Z = _z_day(day, names, means, stds)
        if Z is None or len(ys) < 4 or int(Z.shape[0]) != len(ys):
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
        }
    X = np.vstack(X_blocks)
    y = np.asarray([lab for block in y_blocks for lab in block], dtype=np.int32)
    group = np.asarray(group_sizes, dtype=np.int32)
    if X.shape[0] != y.shape[0] or X.shape[0] != int(group.sum()):
        return {"success": False, "error": "shape_mismatch"}
    lam = max(0.0, float(l2))
    # label_gain 须覆盖全部 label；默认指数表只有 31 项，日池>31 会崩。
    # 用线性 [0,1,2,…] 与 OOS NDCG（gain=rank）对齐。
    n_class = int(y.max()) + 1
    label_gain = [float(i) for i in range(n_class)]
    try:
        # 原生 lgb.train（不依赖 sklearn），objective=lambdarank
        dtrain = lgb.Dataset(X, label=y, group=group)
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
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"lightgbm_fit_failed: {exc}"}
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
    return {
        "success": True,
        "intercept": 0.0,
        "coefficients": coefs,
        "active_features": active,
        "zscore_means": {k: round(float(means[k]), 6) for k in names},
        "zscore_stds": {k: round(float(stds[k]), 6) for k in names},
        "z_means": {k: round(float(means[k]), 6) for k in names},
        "z_stds": {k: round(float(stds[k]), 6) for k in names},
        "feature_zscore": True,
        "solver": "lambdarank",
        "backend": "lambdarank",
        "booster_b64": booster_b64,
        "ridge_lambda": lam,
        "sample_count": int(y.shape[0]),
        "n_groups": n_groups,
    }


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
    out: List[Optional[float]] = []
    for row in xs:
        vec: List[float] = []
        ok = True
        src = row if isinstance(row, dict) else {}
        for name in names:
            v = src.get(name)
            if v is None:
                if not impute_missing or not means:
                    ok = False
                    break
                vec.append(0.0)
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                if not impute_missing or not means:
                    ok = False
                    break
                vec.append(0.0)
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


def _predict_oo_rank_rows(
    model: Dict[str, Any],
    rows: Sequence[dict],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    """仅 LambdaRank booster；无 booster 的旧线性包不再打分。"""
    if not _oo_rank_use_booster(model):
        return [None] * len(list(rows or []))
    return _predict_rows_lightgbm(model, rows, impute_missing=impute_missing)


def _predict_oo_rank_matrix(
    model: Dict[str, Any],
    day: Dict[str, Any],
    *,
    impute_missing: bool = True,
) -> List[Optional[float]]:
    n = len(day.get("ys") or [])
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
    """只接受带 booster 的 LambdaRank 包（旧线性 coefficients 包不再用）。

    显式传入的 ``fit`` / ``model_doc`` 无效时不回落磁盘，避免误用 live 影子。
    """

    def _is_valid(m: Any) -> bool:
        return isinstance(m, dict) and _oo_rank_use_booster(m)

    if fit is not None:
        return fit if _is_valid(fit) else None
    if model_doc is not None:
        model = model_doc.get("return_model") if isinstance(model_doc, dict) else None
        return model if _is_valid(model) else None
    doc = load_oo_rank_model(prefer_research=True)
    if isinstance(doc, dict):
        model = doc.get("return_model")
        if _is_valid(model):
            return model
    return None


def predict_oo_rank_from_features(
    sub_scores: Optional[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """对单行 sub_scores 打 ŷ_oo_rank（与 y_oo 同口径，特征为原始 sub_score）。"""
    model = _resolve_oo_rank_fit(model_doc=model_doc, fit=fit)
    if not isinstance(model, dict):
        return None
    row = dict(sub_scores or {})
    preds = _predict_oo_rank_rows(model, [row], impute_missing=True)
    if not preds:
        return None
    return preds[0]


def apply_oo_rank_scores(
    items: Sequence[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
    assign_day_ranks: bool = True,
) -> List[dict]:
    """就地写入 y_oo_rank；缺 sub_scores 则跳过。不改 ranking / 买序。

    特征与 ŷ_oo 同口径：原始 sub_score（fit_lambdarank 内部做样本内全局 z-score）。
    默认 ``assign_day_ranks=True``：对本次 items 截面编 1..n 名次（1=最高分）。
    """
    doc = model_doc
    if doc is None and fit is None:
        doc = load_oo_rank_model(prefer_research=True)
    model = _resolve_oo_rank_fit(model_doc=doc, fit=fit)

    out = list(items)
    for it in out:
        if not isinstance(it, dict):
            continue
        subs = it.get("sub_scores") or it.get("sub_scores_raw")
        if not isinstance(subs, dict):
            continue
        hat = predict_oo_rank_from_features(subs, model_doc=doc, fit=fit or model)
        write_y_oo_rank_hat(it, hat)
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
    feature_zscore = bool(model.get("feature_zscore", True))
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
    ys = [float(v) for v in (day.get("ys") or [])]
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
) -> Dict[str, Any]:
    spearman_days: List[float] = []
    overlap_days: List[float] = []
    ndcg_days: List[float] = []
    topk_rets: List[float] = []
    n_scored = 0
    for day in days:
        preds, ys = _day_scores(day, fit)
        pairs_py = [
            (float(p), float(y))
            for p, y in zip(preds, ys)
            if p is not None
        ]
        if len(pairs_py) < 5:
            continue
        n_scored += len(pairs_py)
        pp = [a for a, _ in pairs_py]
        yy = [b for _, b in pairs_py]
        sp = _spearman(pp, yy)
        if sp is not None:
            spearman_days.append(sp)
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

    return {
        "n_days": len(spearman_days),
        "n_rows": n_scored,
        "spearman": _mean(spearman_days),
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
) -> Dict[str, Any]:
    names = [str(n) for n in feature_names]
    blocks: List[np.ndarray] = []
    y_parts: List[np.ndarray] = []
    for day in days or []:
        raw = _raw_day_aligned(day, names)
        ys = [float(v) for v in (day.get("ys") or [])]
        if raw is None or len(ys) != int(raw.shape[0]):
            continue
        blocks.append(raw)
        y_parts.append(np.asarray(ys, dtype=np.float64))
    if blocks:
        from core.research.oo_ridge_compact import fit_oo_ridge_matrix

        x = np.vstack(blocks)
        y = np.concatenate(y_parts)
        if int(y.shape[0]) < 20:
            return {"success": False, "error": f"ridge_n={int(y.shape[0])}"}
        _model, report = fit_oo_ridge_matrix(
            x,
            y,
            names,
            ridge_lambda=float(ridge_lambda),
            horizon_days=1,
        )
        return report
    return {"success": False, "error": "ridge_n=0"}


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
    backend: str = "lambdarank",
    lightgbm_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """拟合 ŷ_oo_rank + Holdout OOS，并与同窗 Ridge ŷ_oo 对照。

    ``day_panels``：若已预计算日截面（矩阵 ``X`` 或 raw sub_score dict），可跳过建面板。
    特征与 ŷ_oo 同口径：原始 sub_score；fit_lambdarank 内部做样本内全局 z-score。
    默认建面板走 compact 矩阵（与 ŷ_oo 相同，逐只丢掉行 dict）。

    ``backend``：仅 ``"lambdarank"``（LightGBM LambdaRank，按日 group，优化 NDCG）。
    ``lightgbm_params`` 可覆盖超参。
    OOS 指标：spearman / topk_overlap / ndcg_at_k / topk_mean_y_oo（与 Ridge 同窗对照）。
    """
    from core.research.holdout import (
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        resolve_ridge_split,
    )
    from core.research.oo_rank_panel import build_oo_rank_day_panels

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
    backend_key = str(backend or "lambdarank").strip().lower()
    if backend_key in {"lightgbm", "lgb", "lambdarank"}:
        backend_key = "lambdarank"
    if backend_key != "lambdarank":
        return {
            "success": False,
            "error": f"unsupported backend={backend_key}；仅支持 lambdarank",
            "task": TASK,
            "n_days": len(days),
            "backend": backend_key,
        }
    lgb_kw: Dict[str, Any] = dict(l2=l2)
    if isinstance(lightgbm_params, dict) and lightgbm_params:
        lgb_kw.update(lightgbm_params)
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
            "backend": backend_key,
            "detail": fit_tr,
        }

    oos_rank = _oos_day_metrics(
        days_te,
        fit_tr,
        topk_track=topk_track,
        ndcg_k=ndcg_k,
    )
    ridge = _fit_ridge_baseline(days_tr, feature_names=feat_names, ridge_lambda=ridge_lambda)
    oos_ridge: Dict[str, Any] = {"success": bool(ridge.get("success"))}
    if ridge.get("success"):
        oos_ridge.update(
            _oos_day_metrics(
                days_te,
                ridge,
                topk_track=topk_track,
                ndcg_k=ndcg_k,
            )
        )

    # 全样本重估（影子执行套）
    fit_full = fit_lambdarank(
        days,
        feature_names=feat_names,
        **lgb_kw,
    )
    model = fit_full if fit_full.get("success") else dict(fit_tr)
    model = dict(model)
    y_spec = {
        "formula": FORMULA_OO_RANK,
        "unit": "score",
        "anchor": "open[T]",
        "label": "open[T+1]/open[T]-1 cross-section rank",
        "note": "LambdaRank shadow；模型输出相对分；live/回测成交明细编成当日截面 1..n 名次（1=最高）；不进 ranking / 买序",
    }
    model["y_spec"] = y_spec
    model["horizon_days"] = int(horizon_days)
    model["horizon_mode"] = "oo_rank"
    model["target"] = "oo_rank"
    model["model_role"] = "live"
    model["shadow_only"] = True
    model["ndcg_k"] = int(ndcg_k)

    research_model = make_research_model(fit_tr)
    for k in (
        "y_spec",
        "horizon_days",
        "horizon_mode",
        "target",
        "shadow_only",
        "ndcg_k",
    ):
        research_model[k] = model.get(k)
    research_model["model_role"] = "research"
    research_model["intercept"] = 0.0

    report: Dict[str, Any] = {
        "success": True,
        "task": TASK,
        "schema": SCHEMA,
        "backend": backend_key,
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
        },
        "return_model": model,
        "return_model_research": research_model,
        "y_spec": y_spec,
        "note": (
            f"ŷ_oo_rank = {backend_key}；"
            "旁路对照，不进 ranking / 买序；成交明细编当日截面 1..n 名次；"
            "OOS 含与同窗 Ridge ŷ_oo 对照"
        ),
    }
    attach_holdout_meta(report, split_meta)
    stamp_oo_rank_fitted_at(report)
    if persist:
        paths = persist_oo_rank_model(report, also_research=True)
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
    "assign_oo_rank_day_ranks",
    "compare_oo_rank_shadow_track",
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
    "stamp_oo_rank_fitted_at",
    "write_y_oo_rank_hat",
    "write_y_oo_rank_realized",
]
