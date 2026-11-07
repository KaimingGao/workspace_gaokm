"""Slim backtest result snapshots（产品 / T0 落盘）。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.persistence.json_doc import atomic_write_json
from core.paths import LAST_PORTFOLIO_BACKTEST_PATH, LAST_T0_BACKTEST_PATH

logger = logging.getLogger(__name__)

# 历史回测页只画 KPI / 净值 / 成交账 / 分票贡献；其余研究块不落盘。
_KEEP_KEYS = (
    "success",
    "error",
    "error_code",
    "hint",
    "metrics",
    "equity_curve",
    "equity_curve_tail",
    "benchmark",
    "sim_trades",
    "skip_summary",
    "stock_contrib",
    "request",
    "params",
    "universe",
    "alpha_beta_legs",
    "loaded_stocks",
    "minute_meta",
    "cost_model",
    "engine",
)

# 跳过腿全量曾把 last_portfolio 撑到 GB；落盘抽样。生成侧已不挂 tip，此处仍剥 formula 作兜底。
_SKIP_SAMPLE_CAP = 400
_SKIP_SAMPLE_PER_REASON = 50
_PORTFOLIO_REWRITE_IF_LARGER = 80_000_000

# 与 web/static/js/quant/bt_result.js REPLAY_SKIP_ORDER / classifyReplaySkipReason 对齐。
_SKIP_REASON_ORDER = (
    "分档外",
    "fuse未过",
    "名次",
    "y_oo≤0",
    "y_τc≤0",
    "现金/手数",
    "T+1",
    "报价",
    "其他",
)
# 抽样优先保留排查向原因；分档外/fuse 未过数量大、信息密度低。
_SKIP_KEEP_PRIORITY = (
    "现金/手数",
    "T+1",
    "报价",
    "y_oo≤0",
    "y_τc≤0",
    "名次",
    "fuse未过",
    "其他",
    "分档外",
)


def _slim_universe(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    fails = raw.get("load_failures") or []
    if not isinstance(fails, list):
        fails = []
    return {
        "source": raw.get("source"),
        "candidate_count": raw.get("candidate_count"),
        "loaded_count": raw.get("loaded_count"),
        "note": raw.get("note"),
        "load_failures": fails[:20],
    }


def _is_sim_trade_bulk_key(key: Any) -> bool:
    """落盘/轮询去掉特征与全量系数；公式组成另做 capped 保留（成交 tip 因子表）。"""
    ks = str(key or "")
    if ks.startswith("features_") or ks.startswith("factor_coefficients"):
        return True
    if ks.startswith("y_spec"):
        return True
    return False


def _is_skip_leg(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    act = str(row.get("action") or row.get("matrix_action") or "").lower()
    st = str(row.get("status") or "").lower()
    return act == "skip" or st == "skipped"


def _is_hold_leg(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    act = str(row.get("action") or row.get("matrix_action") or "").lower()
    st = str(row.get("status") or "").lower()
    return act == "hold" or st == "held"


def classify_replay_skip_reason(reason: Any) -> str:
    """与前端 classifyReplaySkipReason 同口径。"""
    r = str(reason or "").strip()
    if (
        not r
        or r == "hard_reject"
        or "分档" in r
        or "可预测性" in r
    ):
        return "分档外"
    if r.startswith("fuse=") or r.startswith("fuse≤") or "fuse 缺失" in r:
        return "fuse未过"
    if "y_oo" in r:
        return "y_oo≤0"
    if "y_τc" in r or "y_tc" in r:
        return "y_τc≤0"
    if "rank" in r:
        return "名次"
    if "现金" in r or "地板" in r or "一手" in r:
        return "现金/手数"
    if "T+1" in r:
        return "T+1"
    if "价" in r or "分钟" in r:
        return "报价"
    return "其他"


def _slim_sim_trade(row: Any, *, keep_formula: bool = True) -> Any:
    if not isinstance(row, dict):
        return row
    # 与开盘分缓存同帽：≤24 + τ pin，见 tip_explain_fields / _cache_tip_formula
    from core.backtest.replay_score_cache import _cache_tip_formula

    out: Dict[str, Any] = {}
    for k, v in row.items():
        if _is_sim_trade_bulk_key(k):
            continue
        ks = str(k or "")
        if "formula_terms" in ks:
            if not keep_formula:
                continue
            out[k] = _cache_tip_formula(ks, v)
        else:
            out[k] = v
    return out


def _new_skip_accum() -> Tuple[List[Any], List[Any], Dict[str, List[Any]], Dict[str, int]]:
    return (
        [],
        [],
        {k: [] for k in _SKIP_REASON_ORDER},
        {k: 0 for k in _SKIP_REASON_ORDER},
    )


def _accum_sim_trade(
    raw: Any,
    *,
    filled: List[Any],
    held: List[Any],
    skip_buckets: Dict[str, List[Any]],
    skip_counts: Dict[str, int],
) -> None:
    if _is_skip_leg(raw):
        key = classify_replay_skip_reason(
            raw.get("reason") if isinstance(raw, dict) else None
        )
        if key not in skip_counts:
            key = "其他"
        skip_counts[key] = int(skip_counts.get(key) or 0) + 1
        bucket = skip_buckets.setdefault(key, [])
        if len(bucket) < _SKIP_SAMPLE_PER_REASON:
            bucket.append(_slim_sim_trade(raw, keep_formula=False))
        return
    if _is_hold_leg(raw):
        held.append(_slim_sim_trade(raw, keep_formula=False))
        return
    filled.append(_slim_sim_trade(raw, keep_formula=True))


def _finalize_sim_trade_accum(
    filled: List[Any],
    held: List[Any],
    skip_buckets: Dict[str, List[Any]],
    skip_counts: Dict[str, int],
) -> Tuple[List[Any], Dict[str, Any]]:
    skips_out: List[Any] = []
    for key in _SKIP_KEEP_PRIORITY:
        for row in skip_buckets.get(key) or []:
            if len(skips_out) >= _SKIP_SAMPLE_CAP:
                break
            skips_out.append(row)
        if len(skips_out) >= _SKIP_SAMPLE_CAP:
            break
    total_skips = int(sum(skip_counts.values()))
    skip_summary = {
        "total": total_skips,
        "kept": len(skips_out),
        "truncated": total_skips > len(skips_out),
        "by_reason": {k: int(v) for k, v in skip_counts.items() if int(v) > 0},
    }
    return filled + held + skips_out, skip_summary


def _slim_sim_trades(rows: List[Any]) -> Tuple[List[Any], Dict[str, Any]]:
    """成交保留 capped tip；跳过腿抽样（去 formula 兜底）；续持去 formula。"""
    filled, held, skip_buckets, skip_counts = _new_skip_accum()
    for raw in rows or []:
        _accum_sim_trade(
            raw,
            filled=filled,
            held=held,
            skip_buckets=skip_buckets,
            skip_counts=skip_counts,
        )
    return _finalize_sim_trade_accum(filled, held, skip_buckets, skip_counts)


def _slim_result(result: Dict[str, Any]) -> Dict[str, Any]:
    slim: Dict[str, Any] = {}
    skip_summary: Optional[Dict[str, Any]] = None
    for key in _KEEP_KEYS:
        if key == "skip_summary":
            continue
        if key not in result:
            continue
        val = result[key]
        if key == "universe":
            slim[key] = _slim_universe(val)
        elif key == "loaded_stocks" and isinstance(val, list):
            slim[key] = [str(c) for c in val[:200]]
        elif key == "sim_trades" and isinstance(val, list):
            slim[key], skip_summary = _slim_sim_trades(val)
        else:
            slim[key] = val
    if skip_summary is None and isinstance(result.get("skip_summary"), dict):
        skip_summary = dict(result["skip_summary"])
    if skip_summary is not None:
        slim["skip_summary"] = skip_summary
        metrics = slim.get("metrics")
        if isinstance(metrics, dict) and metrics.get("skip_count") is None:
            metrics = dict(metrics)
            metrics["skip_count"] = int(skip_summary.get("total") or 0)
            slim["metrics"] = metrics
    if slim.get("success") is None:
        slim["success"] = True
    return slim


def save_last_portfolio_backtest(
    result: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> str:
    """成功产品回测后落盘，供 /replay 刷新恢复。"""
    p = path or LAST_PORTFOLIO_BACKTEST_PATH
    from core.signal.score_display import json_safe

    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "result": json_safe(_slim_result(result or {})),
    }
    atomic_write_json(p, payload)
    return p


def slim_portfolio_backtest_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """任务轮询 / 落盘共用：成交保留 capped tip；跳过腿抽样且去 formula。"""
    return _slim_result(result or {})


def _find_bytes_pos(path: str, marker: bytes, *, start: int = 0) -> int:
    """流式查找 marker，避免整文件读入内存。"""
    overlap = max(0, len(marker) - 1)
    with open(path, "rb") as f:
        if start:
            f.seek(start)
        buf = b""
        pos = start
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                return -1
            data = buf + chunk
            i = data.find(marker)
            if i >= 0:
                return pos - len(buf) + i
            buf = data[-overlap:] if overlap else b""
            pos += len(chunk)


def _match_json_array_end(path: str, array_open_pos: int) -> int:
    """返回匹配 ``]`` 之后的字节偏移（不含该位置）。"""
    depth = 0
    in_str = False
    esc = False
    with open(path, "rb") as f:
        f.seek(array_open_pos)
        pos = array_open_pos
        while True:
            chunk = f.read(8 * 1024 * 1024)
            if not chunk:
                break
            for i, b in enumerate(chunk):
                c = chr(b)
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                    continue
                if c == '"':
                    in_str = True
                    continue
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        return pos + i + 1
            pos += len(chunk)
    raise ValueError("sim_trades array end not found")


def _iter_json_array_objects_at(path: str, array_open_pos: int):
    """从 ``array_open_pos``（指向 ``[``）流式产出数组元素。"""
    decoder = json.JSONDecoder()
    with open(path, "r", encoding="utf-8") as f:
        f.seek(array_open_pos)
        if f.read(1) != "[":
            raise ValueError("sim_trades array open not at expected position")
        buf = ""
        while True:
            while True:
                j = 0
                while j < len(buf) and buf[j].isspace():
                    j += 1
                if j:
                    buf = buf[j:]
                if not buf:
                    break
                if buf[0] == "]":
                    return
                if buf[0] == ",":
                    buf = buf[1:]
                    continue
                try:
                    obj, end = decoder.raw_decode(buf)
                except json.JSONDecodeError:
                    break
                yield obj
                buf = buf[end:]
            more = f.read(1024 * 1024)
            if not more:
                return
            buf += more


def _stream_reslim_portfolio_snapshot(path: str) -> bool:
    """超大 last_portfolio：只流式处理 sim_trades，原位瘦写。成功返回 True。"""
    import re

    key_pos = _find_bytes_pos(path, b'"sim_trades"')
    if key_pos < 0:
        return False
    with open(path, "rb") as f:
        f.seek(key_pos)
        head_chunk = f.read(64)
    rel = head_chunk.find(b"[")
    if rel < 0:
        return False
    array_open = key_pos + rel
    array_end = _match_json_array_end(path, array_open)

    filled, held, skip_buckets, skip_counts = _new_skip_accum()
    n_objs = 0
    for obj in _iter_json_array_objects_at(path, array_open):
        n_objs += 1
        _accum_sim_trade(
            obj,
            filled=filled,
            held=held,
            skip_buckets=skip_buckets,
            skip_counts=skip_counts,
        )
    slim_trades, skip_summary = _finalize_sim_trade_accum(
        filled, held, skip_buckets, skip_counts
    )

    with open(path, "rb") as f:
        prefix = f.read(array_open)  # 不含 '['
        f.seek(array_end)
        suffix_text = f.read().decode("utf-8")

    from core.signal.score_display import json_safe

    trades_json = json.dumps(json_safe(slim_trades), ensure_ascii=False, indent=2)
    summary_json = json.dumps(json_safe(skip_summary), ensure_ascii=False, indent=2)
    if '"skip_summary"' in suffix_text:
        suffix_text = re.sub(
            r',?\s*"skip_summary"\s*:\s*\{.*?\}(?=\s*[,}])',
            "",
            suffix_text,
            count=1,
            flags=re.S,
        )
    insert = trades_json + ',\n    "skip_summary": ' + summary_json
    out_bytes = prefix + insert.encode("utf-8") + suffix_text.encode("utf-8")
    tmp = path + ".slim.tmp"
    with open(tmp, "wb") as f:
        f.write(out_bytes)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    logger.info(
        "stream-reslim portfolio snapshot path=%s objs=%s kept_trades=%s skips=%s→%s",
        path,
        n_objs,
        len(slim_trades),
        skip_summary.get("total"),
        skip_summary.get("kept"),
    )
    return True


def load_last_portfolio_backtest(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or LAST_PORTFOLIO_BACKTEST_PATH
    try:
        raw_size = os.path.getsize(p) if os.path.isfile(p) else 0
    except OSError:
        raw_size = 0
    if raw_size > _PORTFOLIO_REWRITE_IF_LARGER:
        try:
            _stream_reslim_portfolio_snapshot(p)
        except Exception:  # noqa: BLE001
            logger.warning(
                "stream-reslim oversized portfolio snapshot failed path=%s",
                p,
                exc_info=True,
            )
    pack = _load_snapshot(p)
    result = pack.get("result")
    if not isinstance(result, dict):
        return pack
    trades = result.get("sim_trades")
    if (
        isinstance(trades, list)
        and len(trades) > _SKIP_SAMPLE_CAP * 2
        and not isinstance(result.get("skip_summary"), dict)
    ):
        slim = _slim_result(result)
        pack = dict(pack)
        pack["result"] = slim
        try:
            from core.signal.score_display import json_safe

            atomic_write_json(
                p,
                {
                    "saved_at": pack.get("saved_at")
                    or datetime.now().isoformat(timespec="seconds"),
                    "result": json_safe(slim),
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("rewrite slim portfolio snapshot failed", exc_info=True)
    return pack


# 做 T 回测页只画指标 / 图 / 成交样本；逐票全日 days 不落盘。
_T0_DROP_KEYS = ("results",)
_T0_DAY_DROP_KEYS = ("minute_bars", "hist_bars", "_t0_score_snap")
_T0_REWRITE_IF_LARGER = 1_500_000


def _is_t0_feat_bulk_key(key: Any) -> bool:
    """features / 规格 / 系数：体积大，展开 tip 不依赖；仅成交触发根保留对照。"""
    s = str(key or "")
    return (
        s.startswith("score_formula")
        or s.startswith("features_")
        or s.startswith("y_spec")
        or s
        in (
            "factor_coefficients",
            "active_features",
            "zscore_means",
            "zscore_stds",
        )
    )


def _is_t0_tip_bulk_key(key: Any) -> bool:
    s = str(key or "")
    return s.startswith("formula_terms") or _is_t0_feat_bulk_key(s)


def _slim_t0_scan_row(row: Any) -> Optional[dict]:
    """扫描行保留 formula_terms_*，展开日期明细各钟 tip 才有组成。

    features / y_spec / 重复的 score_formula 只留 leg1，控制 last_t0 体积。
    """
    if not isinstance(row, dict):
        return None
    keep_feat = bool(row.get("leg1"))
    out = {
        k: v
        for k, v in row.items()
        if v is not None and (keep_feat or not _is_t0_feat_bulk_key(k))
    }
    return out or None


def _strip_t0_tip_bulk(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_t0_tip_bulk(v)
            for k, v in obj.items()
            if not _is_t0_tip_bulk_key(k)
        }
    if isinstance(obj, list):
        return [_strip_t0_tip_bulk(x) for x in obj]
    return obj


def _slim_t0_day(day: Any) -> Any:
    if not isinstance(day, dict):
        return day
    out = {k: v for k, v in day.items() if k not in _T0_DAY_DROP_KEYS}
    scan = out.pop("close_band_scan", None)
    out = _strip_t0_tip_bulk(out)
    if isinstance(scan, list):
        out["close_band_scan"] = [r for r in (_slim_t0_scan_row(x) for x in scan) if r]
    return out


def _slim_t0_result(result: Dict[str, Any]) -> Dict[str, Any]:
    slim = {k: v for k, v in (result or {}).items() if k not in _T0_DROP_KEYS}
    sample = slim.get("trade_days_sample")
    days = slim.get("days")
    if (
        isinstance(sample, list)
        and sample
        and isinstance(days, list)
        and len(days) > len(sample) + 20
    ):
        slim["days"] = sample
    if isinstance(slim.get("days"), list):
        slim["days"] = [_slim_t0_day(d) for d in slim["days"]]
        slim.pop("trade_days_sample", None)
    elif isinstance(sample, list):
        slim["trade_days_sample"] = [_slim_t0_day(d) for d in sample]
    if slim.get("success") is None:
        slim["success"] = True
    return slim


def save_last_t0_backtest(
    result: Dict[str, Any],
    *,
    path: Optional[str] = None,
) -> str:
    """成功做 T 回测后落盘，供 /follow 刷新恢复。"""
    p = path or LAST_T0_BACKTEST_PATH
    from core.signal.score_display import json_safe

    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "result": json_safe(_slim_t0_result(result or {})),
    }
    atomic_write_json(p, payload)
    return p


def load_last_t0_backtest(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or LAST_T0_BACKTEST_PATH
    pack = _load_snapshot(p)
    result = pack.get("result")
    if not isinstance(result, dict):
        return pack
    slim = _slim_t0_result(result)
    pack["result"] = slim
    try:
        raw_size = os.path.getsize(p) if os.path.isfile(p) else 0
    except OSError:
        raw_size = 0
    if raw_size > _T0_REWRITE_IF_LARGER:
        try:
            from core.signal.score_display import json_safe

            atomic_write_json(
                p,
                {
                    "saved_at": pack.get("saved_at")
                    or datetime.now().isoformat(timespec="seconds"),
                    "result": json_safe(slim),
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("rewrite slim t0 snapshot failed", exc_info=True)
    return pack


def _load_snapshot(path: str) -> Dict[str, Any]:
    p = path
    if not os.path.isfile(p):
        return {"ok": False, "empty": True, "result": None, "path": p}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {
            "ok": False,
            "empty": True,
            "error": str(e),
            "result": None,
            "path": p,
        }
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or not result.get("success"):
        return {"ok": False, "empty": True, "result": None, "path": p}
    return {
        "ok": True,
        "empty": False,
        "path": p,
        "saved_at": data.get("saved_at"),
        "result": result,
    }
