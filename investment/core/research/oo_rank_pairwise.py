"""ŷ_oo_rank：线性 RankNet（pairwise LTR）影子头。

训练：按日观察池 y_oo 序采 Top–Bottom pair，优化 softplus 排序损失。
推理：s = β·z（无截距；相对分，非收益百分点）。
不进 live ranking / rank_lots 入场；仅研究 OOS + 历史跑路对照。
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
SCHEMA = "oo_rank_pairwise_v1"
# 放宽：按池比例取头/尾（约 35%），绝对下限 48；不再死守 Top10×Bottom10
DEFAULT_TOP_K = 48
DEFAULT_BOTTOM_K = 48
DEFAULT_TOP_FRAC = 0.35
DEFAULT_BOTTOM_FRAC = 0.35
DEFAULT_MAX_PAIRS_PER_DAY = 5000
DEFAULT_EXTRA_RANDOM_PAIRS = 400
DEFAULT_MIN_NAMES = 8
DEFAULT_L2 = 1.0
DEFAULT_LR = 0.05
DEFAULT_EPOCHS = 80
DEFAULT_TOPK_TRACK = 10
DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK = 20
DEFAULT_PAIR_PRESET = "wide"
PAIR_PRESETS: Dict[str, Dict[str, float]] = {
    "wide": {
        "top_k": 48,
        "bottom_k": 48,
        "top_frac": 0.35,
        "bottom_frac": 0.35,
        "min_abs_gap": 0.0,
    },
    "topk_focus": {
        "top_k": 20,
        "bottom_k": 20,
        "top_frac": 0.15,
        "bottom_frac": 0.15,
        "min_abs_gap": 0.5,
    },
}


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


def write_y_oo_rank_realized(item: dict, value: Optional[float]) -> None:
    if not isinstance(item, dict) or value is None:
        return
    v = round(float(value), 6)
    item["y_oo_rank_realized"] = v
    item["oo_rank_realized"] = v


def oo_rank_model_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "oo_rank_pairwise_model.json")


def oo_rank_last_report_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "oo_rank_pairwise_last_report.json")


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


def load_oo_rank_model(*, prefer_research: bool = True) -> Optional[Dict[str, Any]]:
    """加载影子模型。默认优先研究套 sidecar；缺则 live 文件。"""
    live = oo_rank_model_path()
    if prefer_research:
        try:
            from core.research.holdout import load_research_promoted_json

            doc = load_research_promoted_json(live)
            if doc:
                return doc
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank research load failed", exc_info=True)
    return _load_model_file(live)


def save_oo_rank_last_report(report: Dict[str, Any]) -> None:
    if not isinstance(report, dict) or not report.get("success"):
        return
    if not isinstance(report.get("return_model"), dict):
        return
    path = oo_rank_last_report_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, report)


def persist_oo_rank_model(report: Dict[str, Any], *, also_research: bool = True) -> Dict[str, str]:
    """落盘执行套；可选同时写研究套（Phase1 影子默认双写同一 β）。"""
    if not isinstance(report, dict) or not report.get("success"):
        return {}
    model = report.get("return_model")
    if not isinstance(model, dict):
        return {}
    live = oo_rank_model_path()
    os.makedirs(os.path.dirname(live), exist_ok=True)
    doc = {
        "success": True,
        "task": "oo_rank_pairwise",
        "schema": SCHEMA,
        "return_model": model,
        "oos": report.get("oos"),
        "y_spec": report.get("y_spec") or model.get("y_spec"),
        "pair_sampling": report.get("pair_sampling"),
        "note": report.get("note"),
        "n_days": report.get("n_days"),
        "n_train_days": report.get("n_train_days"),
        "n_test_days": report.get("n_test_days"),
        "sample_count": report.get("sample_count"),
        "shadow_track": report.get("shadow_track"),
        "shadow_only": True,
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


def _resolve_band_k(n: int, *, absolute: int, frac: float) -> int:
    """头/尾带宽：max(绝对下限, 池比例)，且不超过 n//2。"""
    half = max(1, int(n) // 2)
    abs_k = max(1, int(absolute or 0))
    try:
        f = float(frac)
    except (TypeError, ValueError):
        f = 0.0
    from_frac = int(math.ceil(n * f)) if f > 0 else 0
    return max(1, min(half, max(abs_k, from_frac)))


def normalize_pair_preset(preset: Optional[str]) -> str:
    key = str(preset or DEFAULT_PAIR_PRESET).strip().lower()
    if key in ("top_k", "topk", "focus", "narrow"):
        key = "topk_focus"
    if key not in PAIR_PRESETS:
        return DEFAULT_PAIR_PRESET
    return key


def resolve_pair_preset(
    pair_preset: Optional[str] = None,
    *,
    top_k: Optional[int] = None,
    bottom_k: Optional[int] = None,
    top_frac: Optional[float] = None,
    bottom_frac: Optional[float] = None,
    min_abs_gap: Optional[float] = None,
) -> Dict[str, Any]:
    """合并 pair_preset 与显式覆盖（显式非 None 优先）。"""
    key = normalize_pair_preset(pair_preset)
    base = dict(PAIR_PRESETS[key])
    if top_k is not None:
        base["top_k"] = int(top_k)
    if bottom_k is not None:
        base["bottom_k"] = int(bottom_k)
    if top_frac is not None:
        base["top_frac"] = float(top_frac)
    if bottom_frac is not None:
        base["bottom_frac"] = float(bottom_frac)
    if min_abs_gap is not None:
        base["min_abs_gap"] = float(min_abs_gap)
    base["pair_preset"] = key
    return base


def sample_top_bottom_pairs(
    ys: Sequence[float],
    *,
    top_k: int = DEFAULT_TOP_K,
    bottom_k: int = DEFAULT_BOTTOM_K,
    top_frac: float = DEFAULT_TOP_FRAC,
    bottom_frac: float = DEFAULT_BOTTOM_FRAC,
    min_abs_gap: float = 0.0,
    max_pairs: int = DEFAULT_MAX_PAIRS_PER_DAY,
    extra_random: int = DEFAULT_EXTRA_RANDOM_PAIRS,
    seed: int = 0,
) -> List[Tuple[int, int, float]]:
    """返回 (winner_idx, loser_idx, |Δy|)；winner 真实收益更高。

    默认：头/尾各约 35% 截面（绝对下限 48）做笛卡尔积；
    另抽若干全序随机对，避免只盯极端。超 ``max_pairs`` 时确定性子采样。
    """
    n = len(ys)
    if n < 4:
        return []
    order = sorted(range(n), key=lambda i: float(ys[i]), reverse=True)
    tk = _resolve_band_k(n, absolute=int(top_k or DEFAULT_TOP_K), frac=top_frac)
    bk = _resolve_band_k(
        n, absolute=int(bottom_k or DEFAULT_BOTTOM_K), frac=bottom_frac
    )
    tops = order[:tk]
    bots = order[-bk:]
    gap_floor = max(0.0, float(min_abs_gap or 0.0))
    pairs: List[Tuple[int, int, float]] = []
    seen = set()
    for i in tops:
        for j in bots:
            if i == j:
                continue
            gap = float(ys[i]) - float(ys[j])
            if gap <= gap_floor:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((i, j, gap))

    # 全序随机补充：任意收益更高者对更低者
    n_extra = max(0, int(extra_random or 0))
    if n_extra > 0 and n >= 6:
        rank_of = {idx: r for r, idx in enumerate(order)}
        state = (int(seed) ^ (n * 10007) ^ (tk * 17) ^ (bk * 31)) & 0x7FFFFFFF
        if state == 0:
            state = 1
        tries = 0
        added = 0
        limit_tries = n_extra * 12 + 20
        while added < n_extra and tries < limit_tries:
            tries += 1
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            a = state % n
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            b = state % n
            if a == b:
                continue
            if rank_of[a] > rank_of[b]:
                a, b = b, a
            gap = float(ys[a]) - float(ys[b])
            if gap <= gap_floor:
                continue
            key = (a, b)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((a, b, gap))
            added += 1

    cap = max(50, int(max_pairs or DEFAULT_MAX_PAIRS_PER_DAY))
    if len(pairs) <= cap:
        return pairs
    pairs.sort(key=lambda t: (-float(t[2]), int(t[0]), int(t[1])))
    step = len(pairs) / float(cap)
    uniq: List[Tuple[int, int, float]] = []
    seen2 = set()
    for m in range(cap):
        p = pairs[min(len(pairs) - 1, int(m * step))]
        key = (p[0], p[1])
        if key in seen2:
            continue
        seen2.add(key)
        uniq.append(p)
    return uniq


def _collect_feature_names(days: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    seen = set()
    for day in days or []:
        for row in day.get("xs") or []:
            if not isinstance(row, dict):
                continue
            for k, v in row.items():
                if k in seen or v is None:
                    continue
                seen.add(k)
                names.append(str(k))
    return names


def _fit_zscore(
    days: Sequence[Dict[str, Any]], feature_names: Sequence[str]
) -> Tuple[Dict[str, float], Dict[str, float]]:
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    for name in feature_names:
        vals: List[float] = []
        for day in days:
            for row in day.get("xs") or []:
                if not isinstance(row, dict):
                    continue
                v = row.get(name)
                if v is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv == fv:
                    vals.append(fv)
        if not vals:
            means[name] = 0.0
            stds[name] = 1.0
            continue
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / max(1, len(vals))
        sd = math.sqrt(var) if var > 0 else 1.0
        if sd < 1e-9:
            sd = 1.0
        means[name] = float(mu)
        stds[name] = float(sd)
    return means, stds


def _z_matrix(
    xs: Sequence[dict],
    feature_names: Sequence[str],
    means: Dict[str, float],
    stds: Dict[str, float],
) -> List[List[float]]:
    out: List[List[float]] = []
    for row in xs:
        vec: List[float] = []
        src = row if isinstance(row, dict) else {}
        for name in feature_names:
            v = src.get(name)
            if v is None:
                vec.append(0.0)
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                vec.append(0.0)
                continue
            mu = float(means.get(name) or 0.0)
            sd = float(stds.get(name) or 1.0)
            if sd < 1e-9:
                sd = 1.0
            vec.append((fv - mu) / sd)
        out.append(vec)
    return out


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def fit_ranknet_linear(
    days: Sequence[Dict[str, Any]],
    *,
    feature_names: Optional[Sequence[str]] = None,
    top_k: int = DEFAULT_TOP_K,
    bottom_k: int = DEFAULT_BOTTOM_K,
    top_frac: float = DEFAULT_TOP_FRAC,
    bottom_frac: float = DEFAULT_BOTTOM_FRAC,
    min_abs_gap: float = 0.0,
    max_pairs: int = DEFAULT_MAX_PAIRS_PER_DAY,
    extra_random: int = DEFAULT_EXTRA_RANDOM_PAIRS,
    l2: float = DEFAULT_L2,
    lr: float = DEFAULT_LR,
    epochs: int = DEFAULT_EPOCHS,
) -> Dict[str, Any]:
    """线性 RankNet：s=β·z，无截距；pair loss = softplus(-(s_i-s_j))."""
    names = list(feature_names) if feature_names else _collect_feature_names(days)
    if not names:
        return {"success": False, "error": "no_features"}
    means, stds = _fit_zscore(days, names)
    p = len(names)
    beta = [0.0] * p
    pair_count = 0
    day_packs: List[Tuple[List[List[float]], List[Tuple[int, int, float]]]] = []
    for di, day in enumerate(days):
        ys = [float(y) for y in (day.get("ys") or [])]
        xs = list(day.get("xs") or [])
        if len(ys) < 4 or len(xs) != len(ys):
            continue
        pairs = sample_top_bottom_pairs(
            ys,
            top_k=top_k,
            bottom_k=bottom_k,
            top_frac=top_frac,
            bottom_frac=bottom_frac,
            min_abs_gap=min_abs_gap,
            max_pairs=max_pairs,
            extra_random=extra_random,
            seed=di,
        )
        if not pairs:
            continue
        Z = _z_matrix(xs, names, means, stds)
        day_packs.append((Z, pairs))
        pair_count += len(pairs)
    if pair_count < 20 or not day_packs:
        return {
            "success": False,
            "error": f"pairs_insufficient n_pairs={pair_count}",
            "n_pairs": pair_count,
            "n_days": len(day_packs),
        }

    lam = max(0.0, float(l2))
    step = max(1e-4, float(lr))
    n_epoch = max(1, int(epochs))
    for _ in range(n_epoch):
        grad = [lam * beta[j] for j in range(p)]
        for Z, pairs in day_packs:
            inv = 1.0 / float(len(pairs))
            for i, j, gap in pairs:
                w = 1.0 + min(5.0, abs(float(gap)) / 5.0)
                delta = 0.0
                zi, zj = Z[i], Z[j]
                for k in range(p):
                    delta += beta[k] * (zi[k] - zj[k])
                # dL/ddelta = -sigmoid(-delta)
                pull = _sigmoid(-delta) * w * inv
                for k in range(p):
                    grad[k] -= pull * (zi[k] - zj[k])
        for k in range(p):
            beta[k] -= step * grad[k]

    coefs = {names[k]: round(float(beta[k]), 8) for k in range(p) if abs(beta[k]) > 1e-12}
    return {
        "success": True,
        "intercept": 0.0,
        "coefficients": coefs,
        "active_features": list(coefs.keys()),
        "zscore_means": {k: round(float(means[k]), 6) for k in names},
        "zscore_stds": {k: round(float(stds[k]), 6) for k in names},
        "z_means": {k: round(float(means[k]), 6) for k in names},
        "z_stds": {k: round(float(stds[k]), 6) for k in names},
        "standardized": True,
        "solver": "ranknet_gd",
        "ridge_lambda": lam,
        "sample_count": pair_count,
        "n_pair_days": len(day_packs),
        "n_pairs": pair_count,
    }


def _rank_labels(ys: Sequence[float], *, mode: str = "rank_int") -> List[int]:
    """日截面 ys → LambdaRank 整数标签（高 ys = 高 label）。

    - ``rank_int``：等距 0..n-1（默认，LightGBM LambdaRank 文档推荐）
    - ``gain``：按 |Δy| 加权的 bucket rank（更稀疏的分级）
    """
    n = len(ys)
    if n == 0:
        return []
    order = np.argsort(list(ys))  # 升序索引
    labels = [0] * n
    if str(mode or "rank_int").strip().lower() == "gain":
        # 用 ys 数值排序后的 rank 当 label（0..n-1，但相等带 Δy 权重）
        ranks = np.empty(n, dtype=np.int64)
        ranks[order] = np.arange(n)
        # 把 ranks 缩放到 0..n-1 整数（已经是）
        for i in range(n):
            labels[i] = int(ranks[i])
    else:
        for rank, idx in enumerate(order):
            labels[int(idx)] = int(rank)
    return labels


def fit_ranknet_lightgbm(
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
    label_mode: str = "rank_int",
) -> Dict[str, Any]:
    """LightGBM LambdaRank：按日截面 group，优化 NDCG。

    与 ``fit_ranknet_linear`` 输出同形，便于 ``predict_oo_rank_from_features`` 复用：
    - ``coefficients``：feature importance (gain) → {name: weight}，仅用于诊断/特征筛选
    - ``zscore_means`` / ``zscore_stds``：推理时仍 z-score 以保持字段一致
    - ``solver`` = ``lambdarank``：``_predict_rows`` dispatch 用（旧包 ``lightgbm_lambda`` 仍可读）
    - ``booster_b64``：base64 编码的 LightGBM 模型字符串
    """
    import lightgbm as lgb

    names = list(feature_names) if feature_names else _collect_feature_names(days)
    if not names:
        return {"success": False, "error": "no_features"}
    means, stds = _fit_zscore(days, names)
    p = len(names)
    X_blocks: List[List[List[float]]] = []
    y_blocks: List[List[int]] = []
    group_sizes: List[int] = []
    n_pair_days = 0
    for day in days:
        ys = [float(y) for y in (day.get("ys") or [])]
        xs = list(day.get("xs") or [])
        if len(ys) < 4 or len(xs) != len(ys):
            continue
        Z = _z_matrix(xs, names, means, stds)
        labels = _rank_labels(ys, mode=label_mode)
        if len(set(labels)) < 2:
            continue  # 单 group 全同 label 学不出排序
        X_blocks.append(Z)
        y_blocks.append(labels)
        group_sizes.append(len(ys))
        n_pair_days += 1
    if n_pair_days < 5 or not group_sizes:
        return {
            "success": False,
            "error": f"groups_insufficient n_days={n_pair_days}（需≥5）",
            "n_days": n_pair_days,
            "n_groups": len(group_sizes),
        }
    X = np.asarray([row for block in X_blocks for row in block], dtype=np.float64)
    y = np.asarray([lab for block in y_blocks for lab in block], dtype=np.int32)
    group = np.asarray(group_sizes, dtype=np.int32)
    if X.shape[0] != y.shape[0] or X.shape[0] != int(group.sum()):
        return {"success": False, "error": "shape_mismatch"}
    lam = max(0.0, float(l2))
    try:
        # 原生 lgb.train（不依赖 sklearn），objective=lambdarank
        dtrain = lgb.Dataset(X, label=y, group=group)
        booster = lgb.train(
            {
                "objective": "lambdarank",
                "metric": ["ndcg"],
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
        "standardized": True,
        "solver": "lambdarank",
        "backend": "lambdarank",
        "booster_b64": booster_b64,
        "ridge_lambda": lam,
        "sample_count": int(y.shape[0]),
        "n_pair_days": n_pair_days,
        "n_pairs": int(y.shape[0]),  # LambdaRank 用整日 group，无 pair 数概念
        "label_mode": str(label_mode),
        "n_groups": len(group_sizes),
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


def _resolve_oo_rank_fit(
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    # 线性模型：coefficients 非空；lambdarank / 旧 lightgbm_lambda：booster_b64 非空
    def _is_valid(m: Dict[str, Any]) -> bool:
        if not isinstance(m, dict):
            return False
        if m.get("coefficients"):
            return True
        solver = str(m.get("solver") or "").strip().lower()
        if solver in {"lambdarank", "lightgbm_lambda"} and m.get("booster_b64"):
            return True
        return False

    if _is_valid(fit):
        return fit
    doc = model_doc
    if doc is None:
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
    """对单行 sub_scores 打 ŷ_oo_rank。

    单票调用时若缺 ``cs_*`` 列则按 0 填（与训练 feature_mode 可能不一致）；
    批打分请用 ``apply_oo_rank_scores``（同批挂日截面）。
    """
    from core.research.tc_ridge import _predict_rows

    model = _resolve_oo_rank_fit(model_doc=model_doc, fit=fit)
    if not isinstance(model, dict):
        return None
    row = dict(sub_scores or {})
    if any(str(k).endswith(("_cs_rank", "_cs_zscore")) for k in (model.get("coefficients") or {})):
        if not any(str(k).endswith(("_cs_rank", "_cs_zscore")) for k in row):
            logger.debug(
                "predict_oo_rank_from_features: model expects cs_* but row has none; "
                "impute 0 — prefer apply_oo_rank_scores for batch CS"
            )
    if str(model.get("solver") or "").strip().lower() in {"lambdarank", "lightgbm_lambda"}:
        preds = _predict_rows_lightgbm(model, [row], impute_missing=True)
    else:
        preds = _predict_rows(model, [row], impute_missing=True)
    if not preds:
        return None
    return preds[0]


def apply_oo_rank_scores(
    items: Sequence[dict],
    *,
    model_doc: Optional[Dict[str, Any]] = None,
    fit: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """就地写入 y_oo_rank；缺 sub_scores 则跳过。不改 ranking。

    同批 items 视为同日观察池：先挂 ``cs_*``，再按模型 ``feature_mode`` 选列后预测。
    """
    from core.research.oo_rank_panel import (
        DEFAULT_FEATURE_MODE,
        apply_feature_mode_to_row,
        attach_cs_features_to_rows,
        normalize_feature_mode,
    )

    doc = model_doc
    if doc is None and fit is None:
        doc = load_oo_rank_model(prefer_research=True)
    model = _resolve_oo_rank_fit(model_doc=doc, fit=fit)
    mode = DEFAULT_FEATURE_MODE
    if isinstance(model, dict) and model.get("feature_mode"):
        mode = normalize_feature_mode(model.get("feature_mode"))
    elif isinstance(doc, dict) and doc.get("feature_mode"):
        mode = normalize_feature_mode(doc.get("feature_mode"))

    out = list(items)
    indexed: List[Tuple[int, dict]] = []
    raw_rows: List[dict] = []
    for i, it in enumerate(out):
        if not isinstance(it, dict):
            continue
        subs = it.get("sub_scores") or it.get("sub_scores_raw")
        if not isinstance(subs, dict):
            continue
        indexed.append((i, it))
        raw_rows.append(dict(subs))

    if not indexed:
        return out

    if len(raw_rows) >= 2:
        scored_rows = attach_cs_features_to_rows(raw_rows)
    else:
        scored_rows = raw_rows
        logger.debug(
            "apply_oo_rank_scores: batch size < 2, skip day CS attach"
        )

    for (_i, it), row in zip(indexed, scored_rows):
        feat_row = apply_feature_mode_to_row(row, mode)
        hat = predict_oo_rank_from_features(feat_row, model_doc=doc, fit=fit or model)
        write_y_oo_rank_hat(it, hat)
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


def _pair_accuracy(
    pred: Sequence[float],
    ys: Sequence[float],
    *,
    top_k: int,
    bottom_k: int,
) -> Optional[float]:
    pairs = sample_top_bottom_pairs(ys, top_k=top_k, bottom_k=bottom_k)
    if len(pairs) < 5:
        return None
    hit = 0
    for i, j, _gap in pairs:
        if float(pred[i]) > float(pred[j]):
            hit += 1
    return round(hit / float(len(pairs)), 4)


def _day_scores(
    day: Dict[str, Any], fit: Dict[str, Any]
) -> Tuple[List[Optional[float]], List[float]]:
    from core.research.tc_ridge import _predict_rows

    xs = list(day.get("xs") or [])
    ys = [float(y) for y in (day.get("ys") or [])]
    preds = _predict_rows(fit, xs, impute_missing=True) if xs else []
    return preds, ys


def _oos_day_metrics(
    days: Sequence[Dict[str, Any]],
    fit: Dict[str, Any],
    *,
    top_k: int,
    bottom_k: int,
    topk_track: int,
) -> Dict[str, Any]:
    spearman_days: List[float] = []
    overlap_days: List[float] = []
    pair_acc_days: List[float] = []
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
        pa = _pair_accuracy(pp, yy, top_k=top_k, bottom_k=bottom_k)
        if pa is not None:
            pair_acc_days.append(pa)
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
        "pair_accuracy": _mean(pair_acc_days),
        "topk_mean_y_oo": _mean(topk_rets),
        "topk": int(topk_track),
    }


def _fit_ridge_baseline(
    days: Sequence[Dict[str, Any]],
    *,
    feature_names: Sequence[str],
    ridge_lambda: float = 1.0,
) -> Dict[str, Any]:
    from core.research.factor_ols_fit import fit_factor_ols_from_panel
    from core.research.oo_rank_panel import stack_day_panels

    xs, ys, _dates, _codes = stack_day_panels(days)
    if len(ys) < 20:
        return {"success": False, "error": f"ridge_n={len(ys)}"}
    return fit_factor_ols_from_panel(
        xs,
        ys,
        feature_names=list(feature_names),
        ridge_lambda=float(ridge_lambda),
        standardize=True,
        collinearity_policy="drop_redundant",
    )


def fit_oo_rank_report(
    stock_bars: Sequence[Dict[str, Any]],
    *,
    horizon_days: int = 1,
    min_history: int = 12,
    min_names: int = DEFAULT_MIN_NAMES,
    holdout_trading_days: int = DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK,
    top_k: Optional[int] = None,
    bottom_k: Optional[int] = None,
    top_frac: Optional[float] = None,
    bottom_frac: Optional[float] = None,
    min_abs_gap: Optional[float] = None,
    max_pairs: int = DEFAULT_MAX_PAIRS_PER_DAY,
    extra_random: int = DEFAULT_EXTRA_RANDOM_PAIRS,
    l2: float = DEFAULT_L2,
    lr: float = DEFAULT_LR,
    epochs: int = DEFAULT_EPOCHS,
    topk_track: int = DEFAULT_TOPK_TRACK,
    ridge_lambda: float = 1.0,
    index_bars: Optional[List[dict]] = None,
    excess_mode: str = "none",
    feature_mode: Optional[str] = None,
    pair_preset: Optional[str] = None,
    persist: bool = False,
    day_panels: Optional[Sequence[Dict[str, Any]]] = None,
    backend: str = "lambdarank",
    lightgbm_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """拟合 ŷ_oo_rank + Holdout OOS，并与同窗 Ridge ŷ_oo 对照。

    ``day_panels``：若已预计算日截面（含 raw），可跳过建面板；仍按 ``feature_mode`` enrich。

    ``backend``：仅 ``"lambdarank"``（LightGBM LambdaRank，按日 group，优化 NDCG）。
    旧别名 ``lightgbm_lambda`` 仍接受。``lightgbm_params`` 可覆盖超参。
    """
    from core.research.holdout import (
        attach_holdout_meta,
        calendar_dates_from_stock_bars,
        make_research_model,
        resolve_ridge_split,
    )
    from core.research.oo_rank_panel import (
        DEFAULT_FEATURE_MODE,
        build_oo_rank_day_panels,
        enrich_day_panels_features,
        feature_mode_meta,
        normalize_feature_mode,
    )

    feat_mode = normalize_feature_mode(feature_mode or DEFAULT_FEATURE_MODE)
    sampling = resolve_pair_preset(
        pair_preset or DEFAULT_PAIR_PRESET,
        top_k=top_k,
        bottom_k=bottom_k,
        top_frac=top_frac,
        bottom_frac=bottom_frac,
        min_abs_gap=min_abs_gap,
    )
    tk = int(sampling["top_k"])
    bk = int(sampling["bottom_k"])
    tfrac = float(sampling["top_frac"])
    bfrac = float(sampling["bottom_frac"])
    gap = float(sampling["min_abs_gap"])
    preset_key = str(sampling["pair_preset"])

    if day_panels is not None:
        # 预计算面板：从 raw 副本按 mode enrich（调用方应传未过滤的 raw/raw_cs）
        days = enrich_day_panels_features(day_panels, feature_mode=feat_mode)
    else:
        days = build_oo_rank_day_panels(
            stock_bars,
            horizon_days=horizon_days,
            min_history=min_history,
            min_names=min_names,
            index_bars=index_bars,
            excess_mode=excess_mode,
            feature_mode=feat_mode,
        )
        days = enrich_day_panels_features(days, feature_mode=feat_mode)
    if len(days) < 8:
        return {
            "success": False,
            "error": f"day_panels_insufficient n_days={len(days)}（需≥8）",
            "task": "oo_rank_pairwise",
            "n_days": len(days),
            "feature_mode": feat_mode,
            "pair_preset": preset_key,
        }

    # 用「每日一行占位日期」做 holdout：把每天重复成一行日期序列
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
            "task": "oo_rank_pairwise",
            "n_days": len(days),
            "feature_mode": feat_mode,
            "pair_preset": preset_key,
        }

    feat_names = _collect_feature_names(days_tr)
    backend_key = str(backend or "lambdarank").strip().lower()
    if backend_key in {"lightgbm", "lgb", "lambda", "lambdarank", "lightgbm_lambda"}:
        backend_key = "lambdarank"
    if backend_key in {"ranknet", "ranknet_linear", "linear"}:
        return {
            "success": False,
            "error": "ŷ_oo_rank 已下掉 RankNet，仅支持 lambdarank",
            "task": "oo_rank_pairwise",
            "n_days": len(days),
            "feature_mode": feat_mode,
            "pair_preset": preset_key,
            "backend": backend_key,
        }
    if backend_key != "lambdarank":
        return {
            "success": False,
            "error": f"unsupported backend={backend_key}；仅支持 lambdarank",
            "task": "oo_rank_pairwise",
            "n_days": len(days),
            "feature_mode": feat_mode,
            "pair_preset": preset_key,
            "backend": backend_key,
        }
    lgb_kw: Dict[str, Any] = dict(l2=l2)
    if isinstance(lightgbm_params, dict) and lightgbm_params:
        lgb_kw.update(lightgbm_params)
    fit_tr = fit_ranknet_lightgbm(
        days_tr,
        feature_names=feat_names,
        **lgb_kw,
    )
    if not fit_tr.get("success"):
        return {
            "success": False,
            "error": fit_tr.get("error") or "lambdarank_fit_failed",
            "task": "oo_rank_pairwise",
            "n_days": len(days),
            "feature_mode": feat_mode,
            "pair_preset": preset_key,
            "backend": backend_key,
            "detail": fit_tr,
        }

    oos_rank = _oos_day_metrics(
        days_te,
        fit_tr,
        top_k=tk,
        bottom_k=bk,
        topk_track=topk_track,
    )
    ridge = _fit_ridge_baseline(days_tr, feature_names=feat_names, ridge_lambda=ridge_lambda)
    oos_ridge: Dict[str, Any] = {"success": bool(ridge.get("success"))}
    if ridge.get("success"):
        oos_ridge.update(
            _oos_day_metrics(
                days_te,
                ridge,
                top_k=tk,
                bottom_k=bk,
                topk_track=topk_track,
            )
        )

    # 全样本重估（影子执行套）
    fit_full = fit_ranknet_lightgbm(
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
        "note": "pairwise LTR shadow；相对分，非收益百分点；不进 ranking",
    }
    model["y_spec"] = y_spec
    model["horizon_days"] = int(horizon_days)
    model["horizon_mode"] = "oo_rank"
    model["target"] = "oo_rank"
    model["model_role"] = "live"
    model["shadow_only"] = True
    model["feature_mode"] = feat_mode
    model["pair_preset"] = preset_key

    research_model = make_research_model(fit_tr, y_mean=0.0)
    for k in (
        "y_spec",
        "horizon_days",
        "horizon_mode",
        "target",
        "shadow_only",
        "feature_mode",
        "pair_preset",
    ):
        research_model[k] = model.get(k)
    research_model["model_role"] = "research"
    research_model["intercept"] = 0.0

    feat_meta = feature_mode_meta(
        feat_names,
        model.get("coefficients") or {},
        feature_mode=feat_mode,
    )
    pair_meta = {
        "pair_preset": preset_key,
        "top_k": tk,
        "bottom_k": bk,
        "top_frac": tfrac,
        "bottom_frac": bfrac,
        "max_pairs_per_day": int(max_pairs),
        "extra_random": int(extra_random),
        "min_abs_gap": gap,
        "l2": float(l2),
        "lr": float(lr),
        "epochs": int(epochs),
        "min_names": int(min_names),
    }
    report: Dict[str, Any] = {
        "success": True,
        "task": "oo_rank_pairwise",
        "schema": SCHEMA,
        "backend": backend_key,
        "solver": str(model.get("solver") or ""),
        "n_days": len(days),
        "n_train_days": len(days_tr),
        "n_test_days": len(days_te),
        "sample_count": int(model.get("n_pairs") or model.get("sample_count") or 0),
        "feature_mode": feat_mode,
        "pair_preset": preset_key,
        "feature_meta": feat_meta,
        "oos": {
            "oo_rank": oos_rank,
            "ridge_oo_baseline": oos_ridge,
            "holdout_trading_days": hold_n,
            "topk_track": int(topk_track),
            "feature_mode": feat_mode,
            "pair_preset": preset_key,
        },
        "return_model": model,
        "return_model_research": research_model,
        "y_spec": y_spec,
        "pair_sampling": pair_meta,
        "note": (
            f"ŷ_oo_rank = {backend_key}（preset={preset_key} · mode={feat_mode}）；"
            "影子头，不进 live ranking；OOS 含与同窗 Ridge ŷ_oo 对照"
        ),
    }
    attach_holdout_meta(report, split_meta)
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
        "feature_mode": report.get("feature_mode"),
        "pair_preset": report.get("pair_preset"),
        "feature_meta": report.get("feature_meta"),
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
        "pair_sampling": report.get("pair_sampling"),
        "note": report.get("note"),
        "return_model": report.get("return_model"),
        "return_model_research": report.get("return_model_research"),
    }


__all__ = [
    "DEFAULT_HOLDOUT_TRADING_DAYS_OO_RANK",
    "DEFAULT_PAIR_PRESET",
    "FORMULA_OO_RANK",
    "PAIR_PRESETS",
    "SCHEMA",
    "Y_OO_RANK_HAT_KEYS",
    "Y_OO_RANK_LABEL_KEYS",
    "apply_oo_rank_scores",
    "compare_oo_rank_shadow_track",
    "fit_oo_rank_report",
    "fit_ranknet_linear",
    "load_oo_rank_model",
    "normalize_pair_preset",
    "oo_rank_last_report_path",
    "oo_rank_model_path",
    "persist_oo_rank_model",
    "pick_y_oo_rank_hat",
    "pick_y_oo_rank_label",
    "predict_oo_rank_from_features",
    "resolve_pair_preset",
    "sample_top_bottom_pairs",
    "save_oo_rank_last_report",
    "write_y_oo_rank_hat",
    "write_y_oo_rank_realized",
]
