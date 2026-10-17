"""调仓回测（paper_replay）开盘日打分磁盘缓存。

缓存 ``_score_open_day`` 产出（09:30 信息集 + ŷ_oo_rank）；
``fill_clock≠09:30`` 的 ŷ_τc 前缀重算不进缓存，命中后仍走 rescore。
改 fuse / 手数 / 本金 / 权重不改变指纹 → 可复用。

``run_key`` 只含代码集合 + 模型套内容指纹 + 窗长（**不含**日线 tip），
避免新交易日写入后整库失效；每日文件另存 ``inputs_fp``（该日开盘信息集：
昨收窗全量 bar 屑 + 今开），加载时校验。schema 6：bar 屑预计算 + 滑动窗，
窗内任意一根变更都会 miss；模型文件按内容 hash（``mtime+size`` memo）；
开盘缓存含 oo_rank **与** τc_rank（09:30 命中整包复用；可交易集全缺
τc_rank 才补挂）。``fill_clock≠09:30`` 走 ``refresh_tau_heads_after_prefix``
（前缀 ŷ_τc + 重挂 τc_rank，z 缩可交易）。见 ``replay_scoring`` 模块契约。

布局::

    {REPLAY_SCORE_CACHE_DIR}/v{SCHEMA}/{run_key}/{YYYY-MM-DD}.json.gz
    {REPLAY_SCORE_CACHE_DIR}/v{SCHEMA}/{run_key}/meta.json

开关：``QUANTLAB_REPLAY_SCORE_CACHE=0`` 强制绕过。
写入新 ``run_key`` meta 时，默认只保留同 schema 下最近
``QUANTLAB_REPLAY_SCORE_CACHE_KEEP`` 个目录（默认 20，``0``=不裁剪），
并删除更旧 schema 目录（如 ``v3/``）。
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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

REPLAY_SCORE_CACHE_SCHEMA = 6
_ENV_DISABLE = "QUANTLAB_REPLAY_SCORE_CACHE"
_ENV_KEEP = "QUANTLAB_REPLAY_SCORE_CACHE_KEEP"
_DEFAULT_KEEP_RUNS = 20
_SCHEMA_DIR_RE = re.compile(r"^v(\d+)$")
_CONTENT_FP_MEMO: Dict[Tuple[str, int, int], str] = {}
_CONTENT_FP_MEMO_MAX = 256

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


def _file_content_fp(path: Optional[str]) -> str:
    """模型文件内容指纹；``(path, mtime_ns, size)`` memo，避免重复读盘。"""
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return "missing"
    try:
        st = os.stat(p)
        key = (p, int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return f"{os.path.basename(p)}:err"
    hit = _CONTENT_FP_MEMO.get(key)
    if hit is not None:
        return hit
    try:
        h = hashlib.sha1()
        with open(p, "rb") as fh:
            while True:
                chunk = fh.read(262144)
                if not chunk:
                    break
                h.update(chunk)
        dig = f"{os.path.basename(p)}:{h.hexdigest()[:16]}"
    except OSError:
        dig = f"{os.path.basename(p)}:err"
    if len(_CONTENT_FP_MEMO) >= _CONTENT_FP_MEMO_MAX:
        _CONTENT_FP_MEMO.clear()
    _CONTENT_FP_MEMO[key] = dig
    return dig


def clear_content_fp_memo() -> None:
    """单测用：清空模型内容指纹 memo。"""
    _CONTENT_FP_MEMO.clear()


def _bar_ohlcv_crumb(bar: Mapping[str, Any]) -> str:
    return (
        f"{bar.get('open')}|{bar.get('high')}|{bar.get('low')}|"
        f"{bar.get('close')}|{bar.get('volume')}"
    )


def _lookup_bar(
    dm: Mapping[str, Any], day: str, day_raw: Optional[str] = None
) -> Optional[dict]:
    bar = dm.get(day)
    if isinstance(bar, dict):
        return bar
    if day_raw is not None and day_raw != day:
        bar = dm.get(day_raw)
        if isinstance(bar, dict):
            return bar
    return None


def bars_universe_fingerprint(stock_bars: Dict[str, Any]) -> str:
    """观察池日线指纹：代码序 + 每票跨度与末 bar 屑（调试 / 元数据；不进 run_key）。"""
    from core.research.panel_fingerprint import bars_fingerprint

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


class DayOpenInputsIndex:
    """开盘日信息集指纹：开跑预计算 bar 屑，顺序回测用滑动窗。

    与 ``_score_open_day`` 一致：窗截至昨收（全量 OHLCV 屑），今开只取 ``open``。
    """

    __slots__ = (
        "codes",
        "dates",
        "win",
        "_crumbs",
        "_opens",
        "_day_i",
        "_start",
        "_prev_i",
        "_win_crumbs",
        "_fp",
    )

    def __init__(
        self,
        *,
        codes: Sequence[str],
        dates: Sequence[str],
        date_maps: Mapping[str, Mapping[str, dict]],
        max_window: int,
    ) -> None:
        self.codes = sorted(str(c).strip() for c in codes if str(c).strip())
        self.dates = [str(d) for d in dates]
        self.win = max(1, int(max_window))
        self._crumbs: Dict[str, List[Optional[str]]] = {}
        self._opens: Dict[str, List[Optional[str]]] = {}
        for code in self.codes:
            dm = date_maps.get(code) or {}
            crumbs: List[Optional[str]] = []
            opens: List[Optional[str]] = []
            for d_raw in self.dates:
                day = d_raw[:10]
                bar = _lookup_bar(dm, day, d_raw)
                if bar is None:
                    crumbs.append(None)
                    opens.append(None)
                else:
                    crumbs.append(_bar_ohlcv_crumb(bar))
                    o = bar.get("open")
                    opens.append("noopen" if o is None else str(o))
            self._crumbs[code] = crumbs
            self._opens[code] = opens
        self._day_i = -1
        self._start = 0
        self._prev_i = -1
        self._win_crumbs: Dict[str, List[str]] = {c: [] for c in self.codes}
        self._fp = "empty"

    def _rebuild(self, day_i: int) -> None:
        prev_i = day_i - 1
        start = max(0, prev_i - self.win + 1)
        self._start = start
        self._prev_i = prev_i
        for code in self.codes:
            crumbs = self._crumbs[code]
            buf: List[str] = []
            for j in range(start, prev_i + 1):
                c = crumbs[j]
                if c is not None:
                    buf.append(c)
            self._win_crumbs[code] = buf

    def _slide_to(self, day_i: int) -> None:
        new_prev = day_i - 1
        new_start = max(0, new_prev - self.win + 1)
        old_start = self._start
        # 丢掉滑出窗左端的屑
        while old_start < new_start:
            for code in self.codes:
                left = self._crumbs[code][old_start]
                if left is not None:
                    buf = self._win_crumbs[code]
                    if buf and buf[0] == left:
                        buf.pop(0)
                    else:
                        # 错位时回退全量重建
                        self._rebuild(day_i)
                        return
            old_start += 1
        # 纳入新的昨收
        if new_prev > self._prev_i:
            for code in self.codes:
                add = self._crumbs[code][new_prev]
                if add is not None:
                    self._win_crumbs[code].append(add)
        self._start = new_start
        self._prev_i = new_prev

    def _hash_state_for(self, day_i: int) -> str:
        parts: List[str] = []
        for code in self.codes:
            wh = hashlib.sha1(
                "|".join(self._win_crumbs[code]).encode("utf-8")
            ).hexdigest()[:16]
            op = self._opens[code][day_i]
            open_crumb = "noopen" if op is None else op
            parts.append(f"{code}:{wh}:{open_crumb}")
        blob = "|".join(parts) if parts else "empty"
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]

    def fingerprint(self, day_i: int) -> str:
        if day_i <= 0 or day_i >= len(self.dates):
            return "empty"
        if day_i == self._day_i:
            return self._fp
        if self._day_i >= 1 and day_i == self._day_i + 1:
            self._slide_to(day_i)
        else:
            self._rebuild(day_i)
        self._day_i = day_i
        self._fp = self._hash_state_for(day_i)
        return self._fp


def day_open_inputs_fingerprint(
    *,
    codes: Sequence[str],
    dates: Sequence[str],
    date_maps: Mapping[str, Mapping[str, dict]],
    day_i: int,
    max_window: int,
) -> str:
    """单日开盘打分信息集指纹（一次性；顺序回测请用 ``DayOpenInputsIndex``）。"""
    idx = DayOpenInputsIndex(
        codes=codes,
        dates=dates,
        date_maps=date_maps,
        max_window=max_window,
    )
    return idx.fingerprint(day_i)


def model_suite_fingerprint(
    *,
    score_model_role: str,
    score_backend: str,
) -> str:
    """研究/执行套与相关落盘模型文件**内容**指纹。"""
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
    bits.extend(_file_content_fp(p) for p in paths)
    return hashlib.sha1("|".join(bits).encode("utf-8")).hexdigest()[:20]


def make_run_key(
    *,
    codes_fp: str,
    model_fp: str,
    max_window: int,
    horizon_days: int,
    required_factor_keys: Optional[Sequence[str]] = None,
    score_codes_fp: str = "",
) -> str:
    keys = sorted(str(k) for k in (required_factor_keys or []) if str(k).strip())
    key_fp = hashlib.sha1(",".join(keys).encode("utf-8")).hexdigest()[:12]
    parts = [
        f"codes={codes_fp}",
        f"models={model_fp}",
        f"win={int(max_window)}",
        f"hz={int(horizon_days)}",
        f"feat={key_fp}",
    ]
    scored = str(score_codes_fp or "").strip()
    if scored:
        parts.append(f"score={scored}")
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
                logger.debug(
                    "replay score cache rmtree schema %s failed", name, exc_info=True
                )
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

        payload = {
            "schema": REPLAY_SCORE_CACHE_SCHEMA,
            "run_key": run_key,
            **(meta or {}),
        }
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
    score_codes_fp: str = "",
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
        max_window=max_window,
        horizon_days=horizon_days,
        required_factor_keys=required_factor_keys,
        score_codes_fp=score_codes_fp,
    )
    return {
        "run_key": run_key,
        "codes_fp": codes_fp,
        "bars_fp": bars_fp,
        "model_fp": model_fp,
    }
