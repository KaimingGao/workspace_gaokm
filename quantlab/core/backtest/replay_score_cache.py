"""调仓回测（paper_replay）开盘日打分磁盘缓存。

缓存 ``_score_open_day`` 产出（09:30 信息集 + ŷ_oo_rank）；
``fill_clock≠09:30`` 的 ŷ_τc 前缀重算不进缓存，命中后仍走 rescore。
改 fuse / 手数 / 本金 / 权重不改变指纹 → 可复用。

``run_key`` 只含代码集合 + 模型套 + 窗长（**不含**日线末 bar 屑），
避免新交易日写入后整库失效；每日文件另存 ``inputs_fp``（该日开盘信息集），
加载时校验，历史日可跨 fuse / 手数更新复用。
因子组成按 tip 条数上限瘦身留在行内（schema 3）。schema 2 及更早的文件不再读取，下次回测会重算。

布局::

    {REPLAY_SCORE_CACHE_DIR}/v{SCHEMA}/{run_key}/{YYYY-MM-DD}.json.gz
    {REPLAY_SCORE_CACHE_DIR}/v{SCHEMA}/{run_key}/meta.json

开关：``QUANTLAB_REPLAY_SCORE_CACHE=0`` 强制绕过。
写入新 ``run_key`` meta 时，默认只保留同 schema 下最近
``QUANTLAB_REPLAY_SCORE_CACHE_KEEP`` 个目录（默认 2，``0``=不裁剪），
并删除更旧 schema 目录（如 ``v1/``）。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

REPLAY_SCORE_CACHE_SCHEMA = 3
_ENV_DISABLE = "QUANTLAB_REPLAY_SCORE_CACHE"
_ENV_KEEP = "QUANTLAB_REPLAY_SCORE_CACHE_KEEP"
_DEFAULT_KEEP_RUNS = 2
_SCHEMA_DIR_RE = re.compile(r"^v(\d+)$")

# 体积大且 fuse / rescore / 买序不依赖的字段（ŷ 已写回顶层）。
# 因子组成不在此列：成交明细 tip 要 β·z 表，见 ``_cache_tip_formula``。
_DROP_KEYS = frozenset(
    {
        "features",
        "features_fill",
        "features_raw",
        "features_oo",
        "sub_scores",
        "sub_scores_raw",
        "factor_coefficients",
        "factor_coefficients_tau",
        "factor_coefficients_co",
    }
)

_TAU_FORMULA_KEYS = frozenset(
    {
        "formula_terms_tau",
        "score_formula_terms_tau",
    }
)


def enabled() -> bool:
    raw = str(os.environ.get(_ENV_DISABLE, "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cache_root() -> str:
    override = os.environ.get("QUANTLAB_REPLAY_SCORE_CACHE_DIR")
    if override:
        return str(override)
    from core.paths import REPLAY_SCORE_CACHE_DIR

    return str(REPLAY_SCORE_CACHE_DIR)


def _file_fp(path: Optional[str]) -> str:
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return "missing"
    try:
        st = os.stat(p)
        return f"{os.path.basename(p)}:{int(st.st_mtime_ns)}:{int(st.st_size)}"
    except OSError:
        return f"{os.path.basename(p)}:err"


def bars_universe_fingerprint(stock_bars: Dict[str, Any]) -> str:
    """观察池日线指纹：代码序 + 每票跨度与末 bar 屑（调试 / 元数据；不进 run_key）。"""
    from core.research.panel_disk_cache import bars_fingerprint

    parts: List[str] = []
    for code in sorted(str(c) for c in (stock_bars or {}).keys()):
        parts.append(f"{code}:{bars_fingerprint(stock_bars.get(code) or [])}")
    blob = "|".join(parts) if parts else "empty"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def codes_universe_fingerprint(stock_bars: Dict[str, Any]) -> str:
    """观察池代码集合指纹（排序后）；日线 tip 更新不改变。"""
    codes = sorted(str(c).strip() for c in (stock_bars or {}).keys() if str(c).strip())
    blob = ",".join(codes) if codes else "empty"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def day_open_inputs_fingerprint(
    *,
    codes: Sequence[str],
    dates: Sequence[str],
    date_maps: Mapping[str, Mapping[str, dict]],
    day_i: int,
    max_window: int,
) -> str:
    """单日开盘打分信息集指纹：昨收窗 + 今开屑（与 ``_score_open_day`` 一致）。"""
    from core.research.panel_disk_cache import bars_fingerprint

    if day_i <= 0 or not dates or day_i >= len(dates):
        return "empty"
    prev_i = day_i - 1
    today = str(dates[day_i])[:10]
    win = max(1, int(max_window))
    start = max(0, prev_i - win + 1)
    parts: List[str] = []
    for code in sorted(str(c).strip() for c in codes if str(c).strip()):
        dm = date_maps.get(code) or {}
        window = [dm[d] for d in dates[start : prev_i + 1] if d in dm]
        today_bar = dm.get(today) if isinstance(dm.get(today), dict) else None
        if today_bar is None:
            # date_maps 键可能是完整 date
            today_bar = dm.get(dates[day_i]) if isinstance(dm.get(dates[day_i]), dict) else None
        open_crumb = "noopen"
        if isinstance(today_bar, dict):
            open_crumb = (
                f"{today_bar.get('open')}|{today_bar.get('high')}|"
                f"{today_bar.get('low')}|{today_bar.get('volume')}"
            )
        parts.append(f"{code}:{bars_fingerprint(window)}:{open_crumb}")
    blob = "|".join(parts) if parts else "empty"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def model_suite_fingerprint(
    *,
    score_model_role: str,
    score_backend: str,
) -> str:
    """研究/执行套与相关落盘模型文件指纹。"""
    role = str(score_model_role or "research").strip().lower()
    backend = str(score_backend or "ridge").strip().lower()
    paths: List[str] = []
    try:
        from core.paths import (
            RETURN_SCORE_MODEL_ACTIVE_PATH,
            RETURN_SCORE_MODEL_RESEARCH_PATH,
            SIGNAL_CONFIG_PATH,
        )

        if role == "live":
            paths.append(RETURN_SCORE_MODEL_ACTIVE_PATH)
        else:
            paths.append(RETURN_SCORE_MODEL_RESEARCH_PATH)
            paths.append(RETURN_SCORE_MODEL_ACTIVE_PATH)
        paths.append(SIGNAL_CONFIG_PATH)
    except Exception:  # noqa: BLE001
        logger.debug("replay score cache oo paths failed", exc_info=True)

    try:
        from core.research.co_ridge import co_model_path
        from core.research.holdout import research_model_path
        from core.research.tc_ridge import tau_model_path

        live_tau = tau_model_path()
        live_co = co_model_path()
        if role == "live":
            paths.extend([live_tau, live_co])
        else:
            paths.extend(
                [
                    research_model_path(live_tau),
                    research_model_path(live_co),
                    live_tau,
                    live_co,
                ]
            )
    except Exception:  # noqa: BLE001
        logger.debug("replay score cache tau/co paths failed", exc_info=True)

    try:
        from core.research.holdout import research_model_path
        from core.research.oo_rank_lambdarank import oo_rank_model_path
        from core.research.tc_rank import tc_rank_model_path

        oo_r = oo_rank_model_path()
        tc_r = tc_rank_model_path()
        if role == "live":
            paths.extend([oo_r, tc_r])
        else:
            paths.extend(
                [research_model_path(oo_r), research_model_path(tc_r), oo_r, tc_r]
            )
    except Exception:  # noqa: BLE001
        logger.debug("replay score cache rank paths failed", exc_info=True)

    bits = [f"role={role}", f"backend={backend}"]
    bits.extend(_file_fp(p) for p in paths)
    return hashlib.sha1("|".join(bits).encode("utf-8")).hexdigest()[:20]


def make_run_key(
    *,
    codes_fp: str,
    model_fp: str,
    score_model_role: str,
    score_backend: str,
    max_window: int,
    horizon_days: int,
    required_factor_keys: Optional[Sequence[str]] = None,
) -> str:
    keys = sorted(str(k) for k in (required_factor_keys or []) if str(k).strip())
    key_fp = hashlib.sha1(",".join(keys).encode("utf-8")).hexdigest()[:12]
    parts = [
        f"schema={REPLAY_SCORE_CACHE_SCHEMA}",
        f"codes={codes_fp}",
        f"models={model_fp}",
        f"role={score_model_role}",
        f"backend={score_backend}",
        f"win={int(max_window)}",
        f"hz={int(horizon_days)}",
        f"feat={key_fp}",
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _run_dir(run_key: str) -> str:
    safe = "".join(ch for ch in str(run_key) if ch.isalnum())[:32] or "x"
    return os.path.join(_cache_root(), f"v{REPLAY_SCORE_CACHE_SCHEMA}", safe)


def _day_path(run_key: str, day: str) -> str:
    d = str(day or "")[:10]
    return os.path.join(_run_dir(run_key), f"{d}.json.gz")


def _cache_tip_formula(key: str, value: Any) -> Any:
    """留下 tip 因子表：条数与成交行 ``tip_explain_fields`` 同帽，τ 键钉住。"""
    from core.paper.rebalance.rank_lots import (
        _TAU_TIP_PIN,
        _Y_HEAD_FACTOR_N,
        _slim_tip_formula,
    )

    if key in _TAU_FORMULA_KEYS:
        return _slim_tip_formula(value, limit=_Y_HEAD_FACTOR_N, pin_keys=_TAU_TIP_PIN)
    return _slim_tip_formula(value, limit=_Y_HEAD_FACTOR_N)


def slim_day_items(rows: Sequence[dict]) -> List[dict]:
    out: List[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = str(r.get("stock_code") or "").strip()
        if not code:
            continue
        slim: Dict[str, Any] = {}
        for k, v in r.items():
            if "formula_terms" in k:
                slim[k] = _cache_tip_formula(k, v)
                continue
            if k in _DROP_KEYS:
                continue
            if k.startswith("_pool") or k.startswith("_score_prefix"):
                continue
            if isinstance(v, (str, int, float, bool)) or v is None:
                slim[k] = v
            elif isinstance(v, (list, dict)):
                # 保留小结构（名次辅字段等）；超大 dict 已在 DROP 里
                try:
                    json.dumps(v, ensure_ascii=False)
                except (TypeError, ValueError):
                    continue
                slim[k] = v
        slim["stock_code"] = code
        out.append(slim)
    return out


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".replay_sc_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_day(
    run_key: str,
    day: str,
    rows: Sequence[dict],
    *,
    inputs_fp: str = "",
) -> bool:
    if not enabled():
        return False
    d = str(day or "")[:10]
    if len(d) < 10:
        return False
    try:
        payload = {
            "schema": REPLAY_SCORE_CACHE_SCHEMA,
            "day": d,
            "inputs_fp": str(inputs_fp or ""),
            "n": len(rows or []),
            "rows": slim_day_items(rows),
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        _atomic_write_bytes(_day_path(run_key, d), gzip.compress(raw, compresslevel=3))
        return True
    except Exception:  # noqa: BLE001
        logger.debug("replay score cache save %s failed", day, exc_info=True)
        return False


def load_day(
    run_key: str,
    day: str,
    *,
    inputs_fp: Optional[str] = None,
) -> Optional[List[dict]]:
    if not enabled():
        return None
    d = str(day or "")[:10]
    path = _day_path(run_key, d)
    if not os.path.isfile(path):
        return None
    try:
        with gzip.open(path, "rb") as fh:
            doc = json.loads(fh.read().decode("utf-8"))
        if not isinstance(doc, dict):
            return None
        if int(doc.get("schema") or 0) != REPLAY_SCORE_CACHE_SCHEMA:
            return None
        if inputs_fp is not None:
            cached = str(doc.get("inputs_fp") or "")
            if not cached or cached != str(inputs_fp):
                return None
        rows = doc.get("rows")
        if not isinstance(rows, list):
            return None
        out = [dict(r) for r in rows if isinstance(r, dict) and r.get("stock_code")]
        return out
    except Exception:  # noqa: BLE001
        logger.debug("replay score cache load %s failed", day, exc_info=True)
        return None


def keep_run_limit() -> int:
    """同 schema 下最多保留几个 run_key 目录；``0`` 表示不自动裁剪。"""
    raw = str(os.environ.get(_ENV_KEEP, str(_DEFAULT_KEEP_RUNS)) or "").strip()
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_KEEP_RUNS
    return max(0, n)


def prune_old_runs(keep_run_key: str, *, keep: Optional[int] = None) -> Dict[str, Any]:
    """写入新缓存目录后裁剪：保留当前 key + 同 schema 最近若干个；删旧 schema。"""
    stats: Dict[str, Any] = {
        "removed_runs": [],
        "removed_schemas": [],
        "kept": 0,
        "limit": 0,
    }
    limit = _DEFAULT_KEEP_RUNS if keep is None else int(keep)
    stats["limit"] = limit
    root = _cache_root()
    if not os.path.isdir(root):
        return stats

    # 旧 schema 整目录可删（当前代码只读 v{SCHEMA}）
    try:
        for name in os.listdir(root):
            m = _SCHEMA_DIR_RE.match(name)
            if not m:
                continue
            ver = int(m.group(1))
            if ver >= int(REPLAY_SCORE_CACHE_SCHEMA):
                continue
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            try:
                shutil.rmtree(path)
                stats["removed_schemas"].append(name)
            except OSError:
                logger.debug("replay score cache rmtree schema %s failed", name, exc_info=True)
    except OSError:
        logger.debug("replay score cache list schemas failed", exc_info=True)

    if limit <= 0:
        return stats

    schema_root = os.path.join(root, f"v{REPLAY_SCORE_CACHE_SCHEMA}")
    if not os.path.isdir(schema_root):
        return stats
    safe_keep = "".join(ch for ch in str(keep_run_key) if ch.isalnum())[:32] or "x"
    runs: List[tuple] = []
    try:
        for name in os.listdir(schema_root):
            path = os.path.join(schema_root, name)
            if not os.path.isdir(path):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            runs.append((mtime, name, path))
    except OSError:
        logger.debug("replay score cache list runs failed", exc_info=True)
        return stats

    runs.sort(key=lambda t: t[0], reverse=True)
    protected = {safe_keep}
    for _mtime, name, _path in runs:
        if len(protected) >= limit:
            break
        protected.add(name)

    for _mtime, name, path in runs:
        if name in protected:
            continue
        try:
            shutil.rmtree(path)
            stats["removed_runs"].append(name)
        except OSError:
            logger.debug("replay score cache rmtree run %s failed", name, exc_info=True)
    stats["kept"] = len(protected)
    return stats


def write_run_meta(run_key: str, meta: Dict[str, Any]) -> None:
    if not enabled():
        return
    path = os.path.join(_run_dir(run_key), "meta.json")
    try:
        from core.io_atomic import atomic_write_json

        payload = {"schema": REPLAY_SCORE_CACHE_SCHEMA, "run_key": run_key, **(meta or {})}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_json(path, payload)
        try:
            prune_old_runs(run_key, keep=keep_run_limit())
        except Exception:  # noqa: BLE001
            logger.debug("replay score cache prune failed", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.debug("replay score cache meta write failed", exc_info=True)


def build_run_key_for_bars(
    stock_bars: Dict[str, Any],
    *,
    score_model_role: str,
    score_backend: str,
    max_window: int,
    horizon_days: int = 1,
    required_factor_keys: Optional[Sequence[str]] = None,
) -> Dict[str, str]:
    codes_fp = codes_universe_fingerprint(stock_bars)
    bars_fp = bars_universe_fingerprint(stock_bars)
    model_fp = model_suite_fingerprint(
        score_model_role=score_model_role,
        score_backend=score_backend,
    )
    run_key = make_run_key(
        codes_fp=codes_fp,
        model_fp=model_fp,
        score_model_role=score_model_role,
        score_backend=score_backend,
        max_window=max_window,
        horizon_days=horizon_days,
        required_factor_keys=required_factor_keys,
    )
    return {
        "run_key": run_key,
        "codes_fp": codes_fp,
        "bars_fp": bars_fp,
        "model_fp": model_fp,
    }
