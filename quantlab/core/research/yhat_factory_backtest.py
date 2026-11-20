"""回测中心组合回测。

四头各拿一版工厂权重，在评测窗交集里按 09:30 开盘价走 rank_lots。
四头可以来自 Arm·R / Arm·L / Arm·T，报告只写 live/yhat_bt。
不调用历史回测页，不写研究套 / 执行套，不改 live，也不写三家工厂目录。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from core.io_atomic import atomic_write_json
from core.research.yhat_tf_desk import factory_scope, normalize_factory

logger = logging.getLogger(__name__)

HEADS: Tuple[str, ...] = ("oo", "tc", "oo_rank", "tc_rank")
FILL_CLOCK = "09:30"
START_CASH = 200_000.0
_NET = ("d_model", "nhead", "nlayers", "factor_hidden")
_QUANTLAB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_MIX = "BT"
_ARM_ALIASES = {
    "L": "L",
    "ARM-L": "L",
    "ARM_L": "L",
    "ARM·L": "L",
    "LGB": "L",
    "LIGHTGBM": "L",
    "R": "R",
    "ARM-R": "R",
    "ARM_R": "R",
    "ARM·R": "R",
    "RIDGE": "R",
    "T": "T",
    "ARM-T": "T",
    "ARM_T": "T",
    "ARM·T": "T",
    "TF": "T",
    "TRANSFORMER": "T",
}
_BACKENDS = {"transformer", "lightgbm", "ridge"}


def _factory_key(factory: str) -> str:
    """L/R/T 走工厂目录。BT 是回测中心，不能落进 normalize 的默认 T。"""
    raw = str(factory or "").strip().upper()
    if raw in ("BT", "MIX", "YHAT-BT", "YHAT_BT"):
        return _MIX
    return normalize_factory(factory)


def _store_dir(factory: str) -> str:
    if _factory_key(factory) == _MIX:
        from core.paths import LIVE_DIR

        path = os.path.join(LIVE_DIR, "yhat_bt")
        os.makedirs(path, exist_ok=True)
        return path
    from core.research.yhat_tf_desk import _store_dir as desk_store

    with factory_scope(factory):
        path = desk_store()
    os.makedirs(path, exist_ok=True)
    return path


def _job_path(factory: str) -> str:
    return os.path.join(_store_dir(factory), "backtest_job.json")


def _last_path(factory: str) -> str:
    return os.path.join(_store_dir(factory), "backtest_last.json")


def _rules_path(factory: str) -> str:
    return os.path.join(_store_dir(factory), "backtest_rules.json")


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _norm_token(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return f"{round(float(value), 6):g}"
    try:
        return f"{round(float(value), 6):g}"
    except (TypeError, ValueError):
        return str(value).strip()


def _resolved_seq_feat(raw: Any) -> str:
    from core.research.yhat_transformer.seq_dataset import resolve_seq_feat_set

    return resolve_seq_feat_set(None if raw in (None, "") else raw)


def _fit_param(spec: Mapping[str, Any], *keys: str) -> Any:
    params = spec.get("fit_params") if isinstance(spec.get("fit_params"), dict) else {}
    for key in keys:
        if key not in params or params.get(key) in (None, ""):
            continue
        return params.get(key)
    return None


def _agree(specs: Sequence[Mapping[str, Any]], key: str, label: str) -> None:
    present: List[Any] = []
    missing = False
    for spec in specs:
        value = _fit_param(spec, key)
        if value is None:
            missing = True
            continue
        if key == "seq_feat_set":
            from core.research.yhat_transformer.seq_dataset import resolve_seq_feat_set

            value = resolve_seq_feat_set(value)
        present.append(value)
    if not present:
        return
    if missing or len({_norm_token(v) for v in present}) != 1:
        raise ValueError(f"{label}不一致，不能合成一次离线回测")


def _max_lookback(specs: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """日线按最长回看加载。Arm·L / Arm·R 各头窗长本来就可以不同。"""
    vals: List[int] = []
    for spec in specs:
        raw = _fit_param(spec, "lookback")
        if raw is None:
            continue
        try:
            vals.append(int(raw))
        except (TypeError, ValueError):
            continue
    return max(vals) if vals else None


def _fit_span_note(specs: Mapping[str, Any]) -> str:
    """池子或回看不同时写明各头数值。不因此拒绝这次回测。"""
    pools: List[str] = []
    looks: List[str] = []
    pool_vals = set()
    look_vals = set()
    for head in HEADS:
        spec = specs.get(head) if isinstance(specs.get(head), Mapping) else {}
        pool = _fit_param(spec, "pool_n", "watching_limit")
        look = _fit_param(spec, "lookback")
        if pool is not None:
            token = _norm_token(pool)
            pools.append(f"{head}={token}")
            pool_vals.add(token)
        if look is not None:
            token = _norm_token(look)
            looks.append(f"{head}={token}")
            look_vals.add(token)
    bits: List[str] = []
    if len(pool_vals) > 1:
        bits.append("拟合池 " + " ".join(pools))
    if len(look_vals) > 1:
        bits.append("回看 " + " ".join(looks))
    if not bits:
        return ""
    return "；".join(bits) + "。截面按当前观察池，日线按最长回看"


def _version_stamp(spec: Mapping[str, Any], vid: str) -> str:
    """版本菜单上的时间标识。没有拟合时刻时退回版本号。"""
    snap = spec.get("snapshot") if isinstance(spec.get("snapshot"), Mapping) else {}
    raw = str(snap.get("started_at") or snap.get("finished_at") or "")
    if len(raw) >= 16 and raw[4] == "-" and raw[7] == "-":
        return f"{raw[5:10]} {raw[11:16]}"
    return str(vid or snap.get("run_id") or spec.get("run_id") or "").strip()


def _combo_console(spec: Mapping[str, Any]) -> str:
    """组合方案。每个模型名带上工厂和版本标识。"""
    factories = spec.get("factories") if isinstance(spec.get("factories"), Mapping) else {}
    versions = spec.get("versions") if isinstance(spec.get("versions"), Mapping) else {}
    specs = spec.get("specs") if isinstance(spec.get("specs"), Mapping) else {}
    names: List[str] = []
    for head in HEADS:
        arm = str(factories.get(head) or "").strip()
        vid = str(versions.get(head) or "").strip()
        one = specs.get(head) if isinstance(specs.get(head), Mapping) else {}
        mark = _version_stamp(one, vid)
        names.append(" ".join(bit for bit in (head, arm, mark) if bit))
    text = "组合 " + " · ".join(names)
    if str(spec.get("rank_blend") or "") == "day_rank":
        text += " · 买序 当日名次"
    return text


def _console_num(raw: Any, *, digits: Optional[int] = None, scale: float = 1.0, suffix: str = "") -> str:
    try:
        value = float(raw) * scale
    except (TypeError, ValueError):
        return "—"
    text = f"{value:.{digits}f}" if digits is not None else str(int(round(value)))
    return text + suffix


def _rules_console(brief: Mapping[str, Any]) -> str:
    """这次回测实际用的页面配置。成交钟固定，不在这里重复。"""
    feature = str(brief.get("feature_set") or "").strip().lower()
    if feature in ("hs300", "csi300", "沪深300", "000300"):
        pool = "沪深300"
    elif feature in ("pool", "pool1600", "1600", "full", "大池"):
        pool = "大池"
    else:
        pool = "观察池"
    rank = "关" if brief.get("oo_rank_max") in (None, "") else _console_num(brief.get("oo_rank_max"))
    strong = (
        "关"
        if brief.get("oo_rank_strong_max") in (None, "")
        else _console_num(brief.get("oo_rank_strong_max"))
    )
    return (
        "配置 "
        f"w_oo {_console_num(brief.get('w_oo'), digits=2)} "
        f"w_τc {_console_num(brief.get('w_tc'), digits=2)} "
        f"入场 fuse {_console_num(brief.get('fuse_enter'), digits=2, scale=100.0, suffix='%')} "
        f"名次 {rank} "
        f"金额 {_console_num(brief.get('lot_base_amount'))} "
        f"强档 fuse {_console_num(brief.get('fuse_strong'), digits=2, scale=100.0, suffix='%')} "
        f"名次 {strong} "
        f"金额 {_console_num(brief.get('lot_strong_amount'))} "
        f"本金 {_console_num(brief.get('start_cash'))} "
        f"y_*>0 {'开' if brief.get('y_oo_gt0') else '关'} "
        f"形态闸 {'开' if brief.get('morph_gate_enabled') else '关'} "
        f"物化 {pool}"
    )


def _pack(spec: Mapping[str, Any]) -> str:
    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    return str(meta.get("factor_pack") or "").strip()


def _span(spec: Mapping[str, Any]) -> Tuple[str, str]:
    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    snap = spec.get("snapshot") if isinstance(spec.get("snapshot"), dict) else {}
    metrics = snap.get("metrics") if isinstance(snap.get("metrics"), dict) else {}
    start = str(meta.get("eval_start") or metrics.get("eval_start") or "")[:10]
    end = str(meta.get("eval_end") or metrics.get("eval_end") or "")[:10]
    return start, end


def check_suite(specs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """四头同一后端、因子包成对、评测窗有交集。不读行情。

    拟合池和回看不要求相同。打分截面用当前观察池，日线按最长回看加载。
    """
    by_head = {str(s.get("head") or ""): s for s in specs}
    missing = [h for h in HEADS if h not in by_head]
    if missing:
        raise ValueError("离线回测需要四头版本")
    ordered = [by_head[h] for h in HEADS]
    backends = {str(s.get("backend") or "") for s in ordered}
    if backends not in ({"transformer"}, {"lightgbm"}, {"ridge"}):
        raise ValueError("四头须为同一后端")
    backend = next(iter(backends))
    oo_pack, oo_rank_pack = _pack(by_head["oo"]), _pack(by_head["oo_rank"])
    tc_pack, tc_rank_pack = _pack(by_head["tc"]), _pack(by_head["tc_rank"])
    if not oo_pack.startswith("aoo") or oo_pack != oo_rank_pack:
        raise ValueError("ŷ_oo 与 oo_rank 因子包须同为 aoo")
    if not tc_pack.startswith("atc") or tc_pack != tc_rank_pack:
        raise ValueError("ŷ_τc 与 τc_rank 因子包须同为 atc")
    for key, label in (
        ("t_sample_pct", "采样比例"),
        ("seq_len", "序列长度"),
        ("seq_feat_set", "序列特征集"),
    ):
        _agree(ordered, key, label)
    if backend == "transformer":
        _agree_arch(ordered, message="四头网络结构不一致")
    starts: List[str] = []
    ends: List[str] = []
    for spec in ordered:
        start, end = _span(spec)
        head = str(spec.get("head") or "")
        if len(start) < 10 or len(end) < 10 or start > end:
            raise ValueError(f"{head} 缺少评测窗")
        starts.append(start)
        ends.append(end)
    window_start, window_end = max(starts), min(ends)
    if window_start > window_end:
        raise ValueError("四头评测窗没有交集")
    params = ordered[0].get("fit_params") if isinstance(ordered[0].get("fit_params"), dict) else {}
    lookback = _max_lookback(ordered)
    return {
        "backend": backend,
        "window_start": window_start,
        "window_end": window_end,
        "versions": {h: str(by_head[h].get("run_id") or "") for h in HEADS},
        "specs": {h: by_head[h] for h in HEADS},
        "pool_n": _fit_param(ordered[0], "pool_n", "watching_limit"),
        "lookback": lookback if lookback is not None else params.get("lookback"),
        "seq_len": params.get("seq_len"),
        "seq_feat_set": _resolved_seq_feat(params.get("seq_feat_set")),
    }


def _pack_family(pack: str) -> str:
    text = str(pack or "").strip()
    if text.startswith("aoo"):
        return "aoo"
    if text.startswith("atc"):
        return "atc"
    return ""


def _arm_of(raw: Any, head: str) -> str:
    token = str(raw or "").strip().upper()
    if token not in _ARM_ALIASES:
        raise ValueError(f"{head} 工厂须为 Arm·R、Arm·L 或 Arm·T")
    return _ARM_ALIASES[token]


def _transformer_heads(specs: Sequence[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return [spec for spec in specs if str(spec.get("backend") or "") == "transformer"]


def _meta_agrees(specs: Sequence[Mapping[str, Any]], key: str) -> bool:
    vals = []
    blank = False
    for spec in specs:
        meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
        if meta.get(key) in (None, ""):
            blank = True
            continue
        vals.append(meta.get(key))
    if not vals:
        return True
    if blank or len({_norm_token(v) for v in vals}) != 1:
        return False
    return True


def _agree_seq_width(specs: Sequence[Mapping[str, Any]], message: str) -> None:
    """旧价量序列共用一块面板，序列宽必须相同。

    seq_core / seq_full 的宽跟因子族走：oo 与 τc 可以不同，同一族的两头必须相同。
    """
    from core.research.yhat_transformer.seq_dataset import is_factor_seq

    feat = "seq_core"
    for spec in specs:
        raw = _fit_param(spec, "seq_feat_set")
        if raw is not None:
            feat = _resolved_seq_feat(raw)
            break
    if is_factor_seq(feat):
        groups: Dict[str, List[Mapping[str, Any]]] = {}
        for spec in specs:
            groups.setdefault(_head_family(str(spec.get("head") or "")), []).append(spec)
        chunks = list(groups.values())
    else:
        chunks = [list(specs)]
    for chunk in chunks:
        if len(chunk) >= 2 and not _meta_agrees(chunk, "d_seq_in"):
            raise ValueError(message)


def _agree_arch(specs: Sequence[Mapping[str, Any]], *, message: str) -> None:
    """d_model / nhead / nlayers / factor_hidden 在 Transformer 头之间相同。

    序列输入宽另看 ``_agree_seq_width``。
    """
    if len(specs) < 2:
        return
    for key in _NET:
        if not _meta_agrees(specs, key):
            raise ValueError(message)
    _agree_seq_width(specs, message)


def check_mix(specs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """四头可以来自不同工厂。因子族成对，core / full 可以不同。

    序列长度、采样比例和 d_model / nhead / nlayers / factor_hidden 只在选中的 Transformer 头之间要求一致。seq_core / seq_full 的序列宽按因子族比较，oo 与 τc 可以不同。
    两条排序头后端相同时买序用原始分；不同时用当日名次。
    """
    by_head = {str(s.get("head") or ""): s for s in specs}
    missing = [h for h in HEADS if h not in by_head]
    if missing:
        raise ValueError("离线回测需要四头版本")
    ordered = [by_head[h] for h in HEADS]
    backends: Dict[str, str] = {}
    factories: Dict[str, str] = {}
    for spec in ordered:
        head = str(spec.get("head") or "")
        backend = str(spec.get("backend") or "")
        if backend not in _BACKENDS:
            raise ValueError(f"{head} 后端无法用于回测")
        backends[head] = backend
        factories[head] = _arm_of(spec.get("factory"), head)
    oo_fam, oo_rank_fam = _pack_family(_pack(by_head["oo"])), _pack_family(_pack(by_head["oo_rank"]))
    tc_fam, tc_rank_fam = _pack_family(_pack(by_head["tc"])), _pack_family(_pack(by_head["tc_rank"]))
    if oo_fam != "aoo" or oo_rank_fam != "aoo":
        raise ValueError("ŷ_oo 与 oo_rank 因子包须同为 aoo")
    if tc_fam != "atc" or tc_rank_fam != "atc":
        raise ValueError("ŷ_τc 与 τc_rank 因子包须同为 atc")
    tf = _transformer_heads(ordered)
    if tf:
        for key, label in (
            ("t_sample_pct", "采样比例"),
            ("seq_len", "序列长度"),
            ("seq_feat_set", "序列特征集"),
        ):
            _agree(tf, key, label)
        _agree_arch(tf, message="Transformer 头的网络结构不一致")
    starts: List[str] = []
    ends: List[str] = []
    for spec in ordered:
        start, end = _span(spec)
        head = str(spec.get("head") or "")
        if len(start) < 10 or len(end) < 10 or start > end:
            raise ValueError(f"{head} 缺少评测窗")
        starts.append(start)
        ends.append(end)
    window_start, window_end = max(starts), min(ends)
    if window_start > window_end:
        raise ValueError("四头评测窗没有交集")
    lookback = _max_lookback(ordered)
    rank_blend = "raw" if backends["oo_rank"] == backends["tc_rank"] else "day_rank"
    seq_src = tf[0] if tf else ordered[0]
    seq_params = seq_src.get("fit_params") if isinstance(seq_src.get("fit_params"), dict) else {}
    picks = " · ".join(f"{head} {factories[head]}" for head in HEADS)
    return {
        "mix": True,
        "factory": _MIX,
        "backend": "mix",
        "backends": backends,
        "factories": factories,
        "rank_blend": rank_blend,
        "picks": picks,
        "window_start": window_start,
        "window_end": window_end,
        "versions": {h: str(by_head[h].get("run_id") or "") for h in HEADS},
        "sources": {
            h: {"factory": factories[h], "run_id": str(by_head[h].get("run_id") or "")}
            for h in HEADS
        },
        "specs": {h: by_head[h] for h in HEADS},
        "lookback": lookback,
        "seq_len": seq_params.get("seq_len") if tf else None,
        "seq_feat_set": _resolved_seq_feat(seq_params.get("seq_feat_set")) if tf else None,
    }


def _mix_source(versions: Mapping[str, Any], head: str) -> Tuple[str, str]:
    raw = (versions or {}).get(head)
    if not isinstance(raw, Mapping):
        raise ValueError("离线回测需要四头的工厂和版本")
    arm = _arm_of(raw.get("factory"), head)
    vid = str(raw.get("run_id") or raw.get("id") or "").strip()
    if not vid:
        raise ValueError(f"{head} 缺少版本")
    return arm, vid


def load_mix(versions: Mapping[str, Any]) -> Dict[str, Any]:
    """按头读取三家工厂里的版本和权重 meta。不把权重载入内存。"""
    from core.research.yhat_factory_versions import preview_factory_version
    from core.research.yhat_factory_weights import version_weights_dir

    specs: List[Dict[str, Any]] = []
    for head in HEADS:
        arm, vid = _mix_source(versions, head)
        with factory_scope(arm):
            snap = preview_factory_version(head, vid, factory=arm)
            if not snap.get("success"):
                raise ValueError(snap.get("error") or f"{head} 版本不存在")
            doc = snap.get("version") if isinstance(snap.get("version"), dict) else snap
            meta_path = os.path.join(version_weights_dir(head, vid), "meta.json")
            meta = _read_json(meta_path)
            if not meta:
                raise ValueError(f"{head} 没有权重")
            backend = str(meta.get("backend") or "")
            if backend not in _BACKENDS:
                raise ValueError(f"{head} 权重缺少后端")
            specs.append(
                {
                    "head": head,
                    "run_id": vid,
                    "factory": arm,
                    "backend": backend,
                    "meta": meta,
                    "fit_params": doc.get("fit_params") if isinstance(doc.get("fit_params"), dict) else {},
                    "snapshot": doc,
                }
            )
    return check_mix(specs)


def list_mix_catalog() -> Dict[str, Any]:
    """三家工厂里、带权重的成功版本。每个头一份，新的在前。"""
    from core.research.yhat_factory_versions import list_factory_versions

    heads: Dict[str, List[Dict[str, Any]]] = {}
    for head in HEADS:
        rows: List[Dict[str, Any]] = []
        for arm in ("R", "L", "T"):
            listed = list_factory_versions(head, factory=arm)
            if not listed.get("success"):
                continue
            for it in listed.get("items") or []:
                if not isinstance(it, dict) or not it.get("weights_ref"):
                    continue
                if str(it.get("status") or "") == "error":
                    continue
                vid = str(it.get("id") or "").strip()
                if not vid:
                    continue
                rows.append(
                    {
                        "id": vid,
                        "factory": arm,
                        "label": str(it.get("label") or vid),
                        "weights_ref": it.get("weights_ref"),
                        "status": str(it.get("status") or ""),
                    }
                )
        heads[head] = rows
    return {"success": True, "ok": True, "heads": heads}


def _isolated_paper(start_cash: float, cfg: Mapping[str, Any]) -> Dict[str, Any]:
    """只在内存里记账。rank_lots 规则抄到这份 dict，避免落地时再读 live 账本。"""
    return {
        "cash": float(start_cash),
        "holdings": [],
        "trades": [],
        "cost_model": "simple_cn",
        "rules": {"execution": {"rebalance_timing": {"rank_lots": dict(cfg)}}},
    }


def _legs_at_open(legs: Sequence[Mapping[str, Any]], prices: Mapping[str, float]) -> List[dict]:
    """成交价钉在 09:30 开盘。计划腿本身不带价格。"""
    out: List[dict] = []
    for leg in legs:
        if not isinstance(leg, Mapping):
            continue
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        try:
            shares = float(leg.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        if not code or not px or px <= 0 or shares <= 0:
            continue
        out.append({**leg, "price": float(px), "amount": round(shares * float(px), 2)})
    return out


def _mark_equity(paper: Mapping[str, Any], marks: Mapping[str, float]) -> float:
    equity = float(paper.get("cash") or 0)
    for row in paper.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "")
        try:
            shares = float(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        px = marks.get(code)
        if px is None:
            try:
                px = float(row.get("cost") or 0)
            except (TypeError, ValueError):
                px = 0.0
        if shares > 0 and px and px > 0:
            equity += shares * float(px)
    return equity


_FILL_ACTION = {
    "open": "开",
    "add": "加",
    "exit": "清",
    "reduce": "减",
}


def _fill_with_plan_weights(trade: Mapping[str, Any], legs: Sequence[Mapping[str, Any]]) -> dict:
    """成交落地时丢掉了 rank 权重。打印用计划腿上的同一套权。"""
    code = str(trade.get("stock_code") or "")
    side = str(trade.get("side") or "").strip().lower()
    for leg in legs:
        if str(leg.get("stock_code") or "") != code:
            continue
        if str(leg.get("side") or "").strip().lower() != side:
            continue
        extra = {
            key: leg.get(key)
            for key in (
                "fusion_w_oo",
                "fusion_w_oc",
                "fusion_w_oo_rank",
                "fusion_w_tc_rank",
                "y_oo",
                "y_τc",
                "y_oo_fuse",
                "y_oo_rank",
                "y_tc_rank",
                "y_oo_rank_n",
                "y_tc_rank_n",
                "y_oo_rank_score",
                "y_tc_rank_score",
            )
            if leg.get(key) is not None and trade.get(key) is None
        }
        if extra:
            return {**trade, **extra}
        break
    return dict(trade)


def _ordinal_rank(raw: Any) -> Optional[int]:
    v = _finite(raw)
    if v is None or v < 1:
        return None
    n = int(round(v))
    if n < 1 or abs(v - float(n)) > 1e-6:
        return None
    return n


def _finite(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _fmt_px(value: Any) -> str:
    v = _finite(value)
    if v is None:
        return "—"
    return f"{v:.2f}"


def _fmt_pct2(value: Any) -> str:
    """与成交明细表列相同：两位、正数带 +。"""
    v = _finite(value)
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f}%"


def _pred_real(pred: Any, real: Any) -> str:
    """成交明细格子：预估(真实)。缺真实留 —，不另起「兑现」行。"""
    if _finite(pred) is None and _finite(real) is None:
        return "—"
    # 不换行空格，避免控制台在预估和真实之间折开
    return f"{_fmt_pct2(pred)}\u00a0({_fmt_pct2(real)})"


def _chg_pct(new: Any, old: Any) -> Optional[float]:
    base = _finite(old)
    nxt = _finite(new)
    if base is None or nxt is None or base <= 0 or nxt <= 0:
        return None
    return (nxt / base - 1.0) * 100.0


def _action_label(trade: Mapping[str, Any]) -> str:
    action = str(trade.get("matrix_action") or trade.get("action") or "").strip().lower()
    label = _FILL_ACTION.get(action)
    if label:
        return label
    return "买" if str(trade.get("side") or "") == "buy" else "卖"


def _rank_cell(trade: Mapping[str, Any], key: str) -> str:
    n = _ordinal_rank(trade.get(key))
    return "—" if n is None else str(n)


def _fuse_pred(trade: Mapping[str, Any]) -> Optional[float]:
    got = _finite(trade.get("y_oo_fuse"))
    if got is not None:
        return got
    y_oo = _finite(trade.get("y_oo"))
    y_tc = _finite(trade.get("y_τc"))
    if y_oo is None and y_tc is None:
        return None
    try:
        w_oo = float(trade.get("fusion_w_oo") if trade.get("fusion_w_oo") is not None else 0.6)
        w_tc = float(trade.get("fusion_w_oc") if trade.get("fusion_w_oc") is not None else 0.4)
    except (TypeError, ValueError):
        w_oo, w_tc = 0.6, 0.4
    total = w_oo + w_tc
    if total <= 1e-12:
        return None
    return (w_oo * (0.0 if y_oo is None else y_oo) + w_tc * (0.0 if y_tc is None else y_tc)) / total


def _queue_fill(
    date: str,
    trade: Mapping[str, Any],
    *,
    day_open: Optional[float],
    day_close: Optional[float],
) -> Optional[dict]:
    """收成一行的材料。次日开盘还没到，先不打印。"""
    code = str(trade.get("stock_code") or "").strip()
    if not code:
        return None
    name = str(trade.get("stock_name") or "").strip()
    if name == code:
        name = ""
    fill_px = _finite(trade.get("price"))
    basis = day_open if day_open is not None and day_open > 0 else fill_px
    shares = _finite(trade.get("shares"))
    return {
        "date": date,
        "action": _action_label(trade),
        "code": code,
        "name": name,
        "shares": f"{shares:.0f}股" if shares is not None else "—",
        "open": _fmt_px(basis),
        "close": _fmt_px(day_close),
        "fill": _fmt_px(fill_px),
        "basis": basis,
        "fuse": _fuse_pred(trade),
        "y_oo": _finite(trade.get("y_oo")),
        "y_tc": _finite(trade.get("y_τc")),
        "y_tc_real": _chg_pct(day_close, basis),
        "oo_rank": _rank_cell(trade, "y_oo_rank"),
        "tc_rank": _rank_cell(trade, "y_tc_rank"),
    }


_FILL_HEAD = (
    "日期",
    "动作",
    "代码",
    "名称",
    "股数",
    "开盘",
    "收盘",
    "成交",
    "fuse",
    "y_oo",
    "y_τc",
    "oo_rank",
    "τc_rank",
)


def _fill_cell(value: Any) -> str:
    text = str(value or "").strip()
    return text or "—"


def _fill_line(cells: Sequence[Any]) -> str:
    return " · ".join(_fill_cell(cell) for cell in cells)


def _fmt_book_yuan(value: Any) -> str:
    """日总结现金/净值：千分位，两位小数。"""
    v = _finite(value)
    if v is None:
        return "—"
    return f"{v:,.2f}"


def _holding_count(paper: Mapping[str, Any]) -> int:
    n = 0
    for row in paper.get("holdings") or []:
        if not isinstance(row, dict):
            continue
        try:
            shares = float(row.get("shares") or 0)
        except (TypeError, ValueError):
            shares = 0.0
        if shares > 0:
            n += 1
    return n


def _day_summary_lines(date: str, book: Mapping[str, Any]) -> List[str]:
    """一日收口。现金是调仓后，净值=现金+收盘市值。笔数不另起行。"""
    return [
        date,
        (
            f"现金 {_fmt_book_yuan(book.get('cash'))} · "
            f"持仓 {int(book.get('n_hold') or 0)} · "
            f"净值 {_fmt_book_yuan(book.get('equity'))}"
        ),
    ]


def _ensure_fill_legend(legend: Dict[str, Any]) -> None:
    if legend.get("on"):
        return
    print("成交明细 · fuse=预估 · y_oo/y_τc=预估(真实)", flush=True)
    print(_fill_line(_FILL_HEAD), flush=True)
    legend["on"] = True


def _print_fill_line(row: Mapping[str, Any], next_open: Optional[float]) -> None:
    """一笔一行。fuse 只打预估；y_oo / y_τc 仍是预估(真实)。"""
    oo_real = _chg_pct(next_open, row.get("basis"))
    print(
        _fill_line(
            (
                row.get("date"),
                row.get("action"),
                row.get("code"),
                row.get("name"),
                row.get("shares"),
                row.get("open"),
                row.get("close"),
                row.get("fill"),
                _fmt_pct2(row.get("fuse")),
                _pred_real(row.get("y_oo"), oo_real),
                _pred_real(row.get("y_tc"), row.get("y_tc_real")),
                row.get("oo_rank"),
                row.get("tc_rank"),
            )
        ),
        flush=True,
    )


def _flush_day_books(
    day_books: Dict[str, Dict[str, Any]],
    pending: List[dict],
    prices: Mapping[str, float],
    legend: Dict[str, Any],
    *,
    current: Optional[str],
    final: bool,
) -> None:
    """按日打出成交总结和明细。当天还没走完不打；缺次日开盘的日子先压住，避免日后插到前面。"""
    ready: Dict[str, List[Tuple[dict, Optional[float]]]] = {}
    blocked = set()
    for row in pending:
        date = str(row.get("date") or "")
        nxt = _finite(row.get("next_open"))
        if nxt is None and not final:
            nxt = _finite(prices.get(str(row.get("code") or "")))
            if nxt is None:
                blocked.add(date)
                continue
            row["next_open"] = nxt
        ready.setdefault(date, []).append((row, nxt))
    released: List[str] = []
    for date, book in day_books.items():
        if book.get("flushed"):
            continue
        if not final and (not current or date >= current):
            break
        if date in blocked or not book.get("snap"):
            break
        released.append(date)
    if not released:
        return
    released_set = set(released)
    _ensure_fill_legend(legend)
    for date in released:
        book = day_books[date]
        for line in _day_summary_lines(date, book):
            print(line, flush=True)
        for row, nxt in ready.get(date, []):
            _print_fill_line(row, nxt)
        book["flushed"] = True
    pending[:] = [row for row in pending if str(row.get("date") or "") not in released_set]


def _max_drawdown_pct(equities: Sequence[float]) -> float:
    peak = 0.0
    worst = 0.0
    for raw in equities:
        try:
            eq = float(raw)
        except (TypeError, ValueError):
            continue
        if eq > peak:
            peak = eq
        if peak > 0:
            worst = max(worst, (peak - eq) / peak)
    return round(worst * 100.0, 2)


def _stamp_next_open(
    legs: Sequence[Dict[str, Any]],
    opens_by_date: Mapping[str, Mapping[str, float]],
) -> None:
    order = sorted(d for d in opens_by_date if d)
    nxt = {order[i]: order[i + 1] for i in range(len(order) - 1)}
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        day = str(leg.get("as_of") or "")[:10]
        code = str(leg.get("stock_code") or "")
        px0 = (opens_by_date.get(day) or {}).get(code)
        nd = nxt.get(day)
        px1 = (opens_by_date.get(nd) or {}).get(code) if nd else None
        try:
            a = float(px0)
            b = float(px1)
        except (TypeError, ValueError):
            leg["realized_oo"] = None
            continue
        if a > 0 and b > 0:
            leg["realized_oo"] = round((b / a - 1.0) * 100.0, 4)
        else:
            leg["realized_oo"] = None


def _pool_excess_pct(
    strat_pct: float,
    days: Sequence[Mapping[str, Any]],
    dates: Sequence[str],
) -> Tuple[Optional[float], Optional[float]]:
    """超额 = 策略累计 − 观察池等权买持。没有日线就不编一个数。"""
    bars = None
    for day in days:
        raw = day.get("bars") if isinstance(day, Mapping) else None
        if isinstance(raw, dict) and raw:
            bars = raw
            break
    if not bars or not dates:
        return None, None
    from core.backtest.topk_benchmark import (
        _pool_ew_equity_curve,
        _pool_return_from_equity_curve,
    )

    bench = _pool_return_from_equity_curve(_pool_ew_equity_curve(bars, list(dates)))
    if bench is None:
        return None, None
    return round(float(strat_pct) - float(bench), 2), float(bench)


def simulate_scored_days(
    days: Sequence[Mapping[str, Any]],
    *,
    cfg: Optional[Mapping[str, Any]] = None,
    start_cash: float = START_CASH,
) -> Dict[str, Any]:
    """已打分的交易日 → 累计收益、超额、回撤和决策指标。价格是 09:30 开盘。"""
    from core.paper.rebalance.rank_lots import get_rank_lot_cfg, plan_rank_lot_day
    from core.paper.rebalance.watching_matrix import _apply_matrix_trades

    rules = dict(cfg) if isinstance(cfg, Mapping) else dict(get_rank_lot_cfg())
    paper = _isolated_paper(start_cash, rules)
    traded = 0.0
    n_buys = 0
    n_sells = 0
    marks: Dict[str, float] = {}
    total = len(days)
    print(f"开始调仓 {total} 日 本金 {float(start_cash):.0f}", flush=True)
    opens_by_date: Dict[str, Dict[str, float]] = {}
    pending_fills: List[dict] = []
    day_books: Dict[str, Dict[str, Any]] = {}
    fill_legend: Dict[str, Any] = {"on": False}
    legs: List[Dict[str, Any]] = []
    equities: List[float] = [float(start_cash)]
    for i, day in enumerate(days):
        date = str(day.get("date") or "")[:10]
        scored = [dict(row) for row in (day.get("scored") or []) if isinstance(row, dict)]
        prices = {
            str(k): float(v)
            for k, v in (day.get("prices") or {}).items()
            if v is not None
        }
        day_marks = day.get("marks") if isinstance(day.get("marks"), dict) else prices
        for code, px in day_marks.items():
            try:
                marks[str(code)] = float(px)
            except (TypeError, ValueError):
                continue
        bars = day.get("bars") if isinstance(day.get("bars"), dict) else {}
        plan = plan_rank_lot_day(
            scored=scored,
            holdings=list(paper.get("holdings") or []),
            cash=float(paper.get("cash") or 0),
            prices=prices,
            cfg=dict(rules),
            as_of=date,
            opens=prices,
            bars_by_code=bars,
            sentiment_clock=FILL_CLOCK,
        )
        applied = _apply_matrix_trades(
            paper,
            _legs_at_open(plan.get("sells") or [], prices),
            _legs_at_open(plan.get("buys") or [], prices),
            as_of=date,
        )
        for trade in list(applied.get("sell_trades") or []) + list(applied.get("buy_trades") or []):
            try:
                traded += float(trade.get("amount") or 0)
            except (TypeError, ValueError):
                continue
        day_buys = len(applied.get("buy_trades") or [])
        day_sells = len(applied.get("sell_trades") or [])
        n_buys += day_buys
        n_sells += day_sells
        opens_by_date[date] = dict(prices)
        _flush_day_books(
            day_books,
            pending_fills,
            prices,
            fill_legend,
            current=date,
            final=False,
        )
        traded_codes = set()
        plan_legs = [leg for leg in list(plan.get("sells") or []) + list(plan.get("buys") or []) if isinstance(leg, dict)]
        for trade in list(applied.get("sell_trades") or []) + list(applied.get("buy_trades") or []):
            if not isinstance(trade, dict):
                continue
            code = str(trade.get("stock_code") or "")
            side = str(trade.get("side") or "").strip().lower()
            if not code or side not in ("buy", "sell"):
                continue
            traded_codes.add(code)
            action = str(trade.get("matrix_action") or trade.get("action") or ("open" if side == "buy" else "exit"))
            close_px = _finite((day_marks or {}).get(code))
            open_px = _finite(prices.get(code))
            filled = _fill_with_plan_weights(trade, plan_legs)
            queued = _queue_fill(
                date,
                filled,
                day_open=open_px,
                day_close=close_px,
            )
            if queued:
                pending_fills.append(queued)
            legs.append(
                {
                    "as_of": date,
                    "stock_code": code,
                    "side": side,
                    "action": action,
                    "status": "filled",
                }
            )
        for row in paper.get("holdings") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("stock_code") or "")
            try:
                held = float(row.get("shares") or 0)
            except (TypeError, ValueError):
                held = 0.0
            if not code or held <= 0 or code in traded_codes or code not in prices:
                continue
            legs.append(
                {
                    "as_of": date,
                    "stock_code": code,
                    "side": "hold",
                    "action": "hold",
                    "status": "held",
                }
            )
        book = day_books.setdefault(date, {})
        book["cash"] = float(paper.get("cash") or 0)
        book["n_hold"] = _holding_count(paper)
        book["equity"] = _mark_equity(paper, marks)
        book["snap"] = True
        equities.append(float(book["equity"]))
        if i == 0 or i + 1 == total or (i + 1) % 10 == 0:
            print(
                f"调仓 {date} {i + 1}/{total} 买 {day_buys} 卖 {day_sells}",
                flush=True,
            )
    _flush_day_books(
        day_books,
        pending_fills,
        {},
        fill_legend,
        current=None,
        final=True,
    )
    end_equity = _mark_equity(paper, marks)
    end_shares = 0.0
    for row in paper.get("holdings") or []:
        try:
            end_shares += float((row or {}).get("shares") or 0)
        except (TypeError, ValueError):
            continue
    base = float(start_cash) if start_cash else 1.0
    total_return_pct = round(((end_equity / base) - 1.0) * 100.0, 2) if base else 0.0
    _stamp_next_open(legs, opens_by_date)
    from core.backtest.replay_sim_rows import (
        fuse_accuracy_metrics,
        fuse_decision_return,
        fuse_hit_metrics,
    )

    acc_pct, acc_n, acc_hits = fuse_accuracy_metrics(legs)
    hit_pct, hit_n, hit_hits = fuse_hit_metrics(legs)
    dec_pct, dec_n = fuse_decision_return(legs)
    excess_pct, bench_pct = _pool_excess_pct(total_return_pct, days, sorted(opens_by_date))
    drawdown_pct = _max_drawdown_pct(equities)
    def _shown(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            return "—"
        return f"{float(value):.2f}%"

    print(
        f"指标 收益 {_shown(total_return_pct)} 回撤 {_shown(drawdown_pct)} "
        f"决策 {_shown(dec_pct)} 准确 {_shown(acc_pct)} 命中 {_shown(hit_pct)} "
        f"超额 {_shown(excess_pct)}",
        flush=True,
    )
    return {
        "cumulative_return": (end_equity / base) - 1.0,
        "total_return_pct": total_return_pct,
        "turnover": traded / base,
        "max_drawdown_pct": drawdown_pct,
        "excess_pct": excess_pct,
        "benchmark_return_pct": bench_pct,
        "benchmark_label": "观察池等权" if excess_pct is not None else None,
        "decision_return_pct": dec_pct,
        "decision_n": dec_n,
        "accuracy_pct": acc_pct,
        "accuracy_n": acc_n,
        "accuracy_hits": acc_hits,
        "hit_rate_pct": hit_pct,
        "hit_n": hit_n,
        "hit_hits": hit_hits,
        "n_days": len(list(days)),
        "n_buys": n_buys,
        "n_sells": n_sells,
        "start_cash": float(start_cash),
        "end_equity": round(end_equity, 2),
        "end_shares": end_shares,
        "fill_clock": FILL_CLOCK,
    }


def _clip_days(
    days: Sequence[Mapping[str, Any]],
    window_start: str,
    window_end: str,
) -> List[Mapping[str, Any]]:
    kept: List[Mapping[str, Any]] = []
    for day in days:
        date = str(day.get("date") or "")[:10]
        if len(date) < 10:
            continue
        if window_start <= date <= window_end:
            kept.append(day)
    kept.sort(key=lambda row: str(row.get("date") or ""))
    return kept


def run_factory_backtest(
    factory: str,
    versions: Mapping[str, str],
    *,
    days: Optional[Sequence[Mapping[str, Any]]] = None,
    suite: Optional[Mapping[str, Any]] = None,
    cfg: Optional[Mapping[str, Any]] = None,
    rules: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """校验四头，预估（或使用调用方已打分的日子），写出收益。"""
    fac = _factory_key(factory)
    if isinstance(suite, Mapping):
        spec = dict(suite)
    elif fac == _MIX:
        spec = load_mix(versions)
    else:
        raise ValueError("组合回测只写 live/yhat_bt")
    window_start = str(spec.get("window_start") or "")
    window_end = str(spec.get("window_end") or "")
    vids = spec.get("versions") if isinstance(spec.get("versions"), dict) else dict(versions)
    print(
        f"四头窗口 {window_start}→{window_end} "
        + " ".join(f"{h}={vids.get(h) or ''}" for h in HEADS),
        flush=True,
    )
    note = _fit_span_note(spec.get("specs") if isinstance(spec.get("specs"), Mapping) else {})
    if note:
        print(note, flush=True)
    planner, cash, rules_brief = _resolve_run_rules(fac, cfg=cfg, rules=rules, suite=spec)
    if spec.get("mix"):
        print(_combo_console(spec), flush=True)
    print(_rules_console(rules_brief), flush=True)
    feature_set = str((rules_brief or {}).get("feature_set") or "")
    prev_feature = os.environ.get("QUANTLAB_FEATURE_SET")
    if feature_set:
        os.environ["QUANTLAB_FEATURE_SET"] = feature_set
    try:
        scored = (
            list(days)
            if days is not None
            else score_open_window(fac, spec)
        )
    finally:
        if not feature_set:
            pass
        elif prev_feature is None:
            os.environ.pop("QUANTLAB_FEATURE_SET", None)
        else:
            os.environ["QUANTLAB_FEATURE_SET"] = prev_feature
    kept = _clip_days(scored, window_start, window_end)
    if spec.get("mix"):
        stamp_day_ranks(kept, blend=str(spec.get("rank_blend") or "raw"))
    print(f"计入收益 {len(kept)} 日", flush=True)
    if not kept:
        raise ValueError("评测窗内没有交易日")
    report = simulate_scored_days(kept, cfg=planner, start_cash=cash)
    print(
        f"模拟结束 买 {report.get('n_buys')} 卖 {report.get('n_sells')} "
        f"期末权益 {report.get('end_equity')}",
        flush=True,
    )
    report.update(
        {
            "factory": fac,
            "fill_clock": FILL_CLOCK,
            "window_start": window_start,
            "window_end": window_end,
            "versions": {h: str(vids.get(h) or "") for h in HEADS},
            "rules": rules_brief,
        }
    )
    if spec.get("mix"):
        report["factories"] = dict(spec.get("factories") or {})
        report["backends"] = dict(spec.get("backends") or {})
        report["rank_blend"] = str(spec.get("rank_blend") or "")
        report["picks"] = str(spec.get("picks") or "")
    return report


def apply_seq_clip(X: np.ndarray, gap: np.ndarray, clip: Optional[Mapping[str, Any]]) -> None:
    """按训练时留下的 1%/99% 界裁序列。原地修改调用方自己的副本。"""
    if not isinstance(clip, Mapping):
        return
    try:
        lo = np.asarray(clip.get("x_lo"), dtype=np.float32)
        hi = np.asarray(clip.get("x_hi"), dtype=np.float32)
        gap_lo = float(clip.get("gap_lo"))
        gap_hi = float(clip.get("gap_hi"))
    except (TypeError, ValueError):
        return
    if lo.shape[-1:] != X.shape[-1:] or hi.shape != lo.shape:
        return
    np.clip(X, lo, hi, out=X)
    np.clip(gap, gap_lo, gap_hi, out=gap)


def _open_legacy_fill(name: str) -> Optional[float]:
    """旧开盘权重点名已裁列时的填值。τc 分钟列用中性值，oo 常数包分用 50。"""
    from core.signal.factors.alpha_oo import open_oo_neutral_value
    from core.signal.factors.alpha_tc import open_tau_neutral_value

    fill = open_tau_neutral_value(name)
    if fill is not None:
        return fill
    return open_oo_neutral_value(name)


def take_factor_columns(X: np.ndarray, names: Sequence[str], want: Sequence[str]) -> np.ndarray:
    """按权重列序取列。开盘已裁掉的常数列，用训练时的值补回。"""
    have = {str(n): i for i, n in enumerate(names)}
    src = np.asarray(X)
    out = np.empty((int(src.shape[0]), len(list(want))), dtype=np.float64)
    for j, name in enumerate(want):
        key = str(name)
        i = have.get(key)
        if i is None:
            fill = _open_legacy_fill(key)
            if fill is None:
                raise ValueError(f"因子列缺失 {key}")
            out[:, j] = float(fill)
            continue
        out[:, j] = src[:, i]
    return np.ascontiguousarray(out, dtype=np.float64)


def _legacy_seq_names(want_n: int) -> Optional[List[str]]:
    """旧开盘序列列序。对得上列宽才返回。"""
    from core.signal.factors.alpha_oo import apply_aoo_core_policy, open_aoo_full_names
    from core.signal.factors.alpha_tc import open_atc_full_names, select_atc_core_names

    tc_full = open_atc_full_names()
    tc_core = select_atc_core_names(tc_full)
    oo_full = open_aoo_full_names(drop_open_const=False)
    oo_core = apply_aoo_core_policy(oo_full)
    if want_n == len(tc_full):
        return tc_full
    if want_n == len(tc_core):
        return tc_core
    if want_n == len(oo_full):
        return oo_full
    if want_n == len(oo_core):
        return oo_core
    return None


def align_seq_open_width(seq: Any, d_seq_in: int) -> Any:
    """旧开盘序列仍含已裁常数列时，按原列序补回。

    τc 分钟列补中性值，oo 的 ``alpha_th`` / 尾盘 / 资金流补 50。
    列序对不上时原样返回，交给后面的列宽校验。
    """
    try:
        X = np.asarray(seq.X)
        width = int(X.shape[-1])
    except Exception:  # noqa: BLE001
        return seq
    want_n = int(d_seq_in or 0)
    if want_n <= 0 or width == want_n or X.ndim != 3:
        return seq
    from core.research.yhat_transformer.seq_dataset import SeqPanel

    want = _legacy_seq_names(want_n)
    if not want:
        return seq
    have = [str(n) for n in (getattr(seq, "feature_names", ()) or ())]
    if len(have) != width:
        return seq
    index = {n: i for i, n in enumerate(have)}
    want_set = set(want)
    if any(n not in want_set for n in have):
        return seq
    missing = [n for n in want if n not in index]
    if not missing or any(_open_legacy_fill(n) is None for n in missing):
        return seq
    out = np.empty((X.shape[0], X.shape[1], len(want)), dtype=np.float32)
    for j, name in enumerate(want):
        i = index.get(name)
        if i is None:
            out[:, :, j] = np.float32(_open_legacy_fill(name))
        else:
            out[:, :, j] = X[:, :, i]
    return SeqPanel(
        X=out,
        gap=np.asarray(seq.gap),
        y=np.asarray(seq.y),
        dates=list(seq.dates),
        codes=list(seq.codes),
        seq_len=int(getattr(seq, "seq_len", 0) or X.shape[1]),
        feature_names=tuple(want),
    )


def _bar_day(row: Mapping[str, Any]) -> str:
    for key in ("date", "trade_date", "dt", "time"):
        raw = row.get(key)
        if raw:
            return str(raw)[:10]
    return ""


def _bar_px(row: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        try:
            val = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if val > 0 and val == val:
            return val
    return None


def _index_bars(stock_bars: Mapping[str, Sequence[Mapping[str, Any]]]) -> Dict[Tuple[str, str], Mapping[str, Any]]:
    out: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for code, rows in (stock_bars or {}).items():
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            day = _bar_day(row)
            if len(day) >= 10:
                out[(str(code), day)] = row
    return out


def _window_idx(dates: Sequence[str], start: str, end: str) -> List[int]:
    keep: List[int] = []
    for i, raw in enumerate(dates):
        day = str(raw)[:10]
        if start <= day <= end:
            keep.append(i)
    return keep


def _zscore(
    X: np.ndarray,
    names: Sequence[str],
    dates: Sequence[str],
    z_clip: Any,
    *,
    fill_missing: bool = True,
) -> np.ndarray:
    from core.research.materialize.cube_z import cs_z_matrix_prefer_cube

    out = cs_z_matrix_prefer_cube(np.array(X, dtype=np.float64, copy=True), list(names), list(dates), clock="open")
    try:
        clip = float(z_clip) if z_clip is not None else 0.0
    except (TypeError, ValueError):
        clip = 0.0
    if clip > 0:
        out = np.clip(out, -clip, clip)
    if fill_missing:
        out = np.where(np.isfinite(out), out, 0.0)
    return out.astype(np.float32, copy=False)


def _head_family(head: str) -> str:
    return "tc" if str(head).startswith("tc") else "oo"


def _factor_seq_panel(
    stock_bars: Mapping[str, Sequence[dict]],
    head: str,
    feat_set: str,
    seq_len: int,
    full: Any,
) -> Any:
    """core / full 序列：决策日前 L 日的因子列，宽与这一头的因子档相同。"""
    from core.research.yhat_transformer.factor_panel import (
        sequence_from_factor_panel,
        slice_arm_t_panel,
    )
    from core.research.yhat_transformer.seq_dataset import resolve_seq_feat_set

    family = _head_family(head)
    src = full
    if resolve_seq_feat_set(feat_set) == "seq_core":
        src = slice_arm_t_panel(
            full,
            family,
            aoo_core_only=family == "oo",
            atc_core_only=family == "tc",
        )
    return sequence_from_factor_panel(src, stock_bars, seq_len=int(seq_len))


def _full_factor_panel(stock_bars: Mapping[str, Sequence[dict]], head: str):
    from core.research.yhat_transformer.factor_panel import build_factor_panel

    return build_factor_panel(
        stock_bars,
        head=_head_family(head),
        horizon_days=1,
        progress_every=0,
    )


def _factor_panel(stock_bars: Mapping[str, Sequence[dict]], head: str, pack: str):
    from core.research.yhat_transformer.factor_panel import build_factor_panel, slice_arm_t_panel

    family = "tc" if head.startswith("tc") else "oo"
    panel = build_factor_panel(stock_bars, head=family, horizon_days=1, progress_every=0)
    if str(pack).endswith("_core"):
        panel = slice_arm_t_panel(
            panel,
            family,
            aoo_core_only=family == "oo",
            atc_core_only=family == "tc",
        )
    return panel


def _predict_matrix(bundle: Mapping[str, Any], X: np.ndarray, seq, gap) -> np.ndarray:
    meta = bundle.get("meta") if isinstance(bundle.get("meta"), dict) else {}
    backend = str(bundle.get("backend") or meta.get("backend") or "")
    n = int(X.shape[0])
    if n <= 0:
        return np.zeros((0,), dtype=np.float64)
    if backend == "lightgbm":
        import lightgbm as lgb

        from core.research.yhat_factory_weights import load_factory_weights

        loaded = load_factory_weights(
            str(bundle.get("head") or ""),
            str(bundle.get("run_id") or ""),
            factory=bundle.get("factory"),
        )
        text = loaded.get("model_text") or ""
        if not loaded.get("success") or not text:
            raise ValueError(loaded.get("error") or "权重不存在")
        booster = lgb.Booster(model_str=text)
        return np.asarray(booster.predict(X), dtype=np.float64).reshape(-1)
    if backend == "ridge":
        from core.research.yhat_factory_weights import load_factory_weights
        from core.research.yhat_ridge import predict_ridge

        loaded = load_factory_weights(
            str(bundle.get("head") or ""),
            str(bundle.get("run_id") or ""),
            factory=bundle.get("factory"),
        )
        if not loaded.get("success"):
            raise ValueError(loaded.get("error") or "权重不存在")
        require_complete = str(meta.get("missing") or "") == "complete"
        return predict_ridge(
            X,
            loaded.get("intercept") or 0.0,
            loaded.get("coef") or [],
            require_complete=require_complete,
        )
    from core.research.yhat_factory_weights import load_factory_weights
    from core.research.yhat_transformer.train import predict_seq_factor_transformer

    loaded = load_factory_weights(
        str(bundle.get("head") or ""),
        str(bundle.get("run_id") or ""),
        factory=bundle.get("factory"),
    )
    state = loaded.get("state_dict")
    if not loaded.get("success") or not isinstance(state, dict):
        raise ValueError(loaded.get("error") or "权重不存在")
    d_seq = int(meta.get("d_seq_in") or seq.shape[-1])
    if int(seq.shape[-1]) != d_seq:
        raise ValueError("序列特征维与权重不一致")
    if int(X.shape[1]) != int(meta.get("d_factor") or X.shape[1]):
        raise ValueError("因子列数与权重不一致")
    return predict_seq_factor_transformer(
        state,
        seq,
        gap,
        X,
        list(range(n)),
        d_model=int(meta.get("d_model") or 64),
        nhead=int(meta.get("nhead") or 4),
        nlayers=int(meta.get("nlayers") or 3),
        factor_hidden=int(meta.get("factor_hidden") or meta.get("d_model") or 64),
    )


def _watching_pool_codes() -> List[str]:
    """打分名单。选了物化截面就用那一套，否则用观察池全池。"""
    try:
        from core.universe.feature_sets import active_feature_codes

        chosen = active_feature_codes()
        if chosen:
            return list(chosen)
    except Exception:  # noqa: BLE001
        logger.debug("factory backtest feature set failed", exc_info=True)
    try:
        from core.watching.store import read_watching

        raw = list((read_watching() or {}).get("watchlist") or [])
    except Exception:  # noqa: BLE001
        logger.debug("factory backtest read_watching failed", exc_info=True)
        raw = []
    seen = set()
    out: List[str] = []
    for item in raw:
        code = str(item).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _scores_for_head(
    bundle: Mapping[str, Any],
    panel,
    seq,
    window_start: str,
    window_end: str,
    score_codes: Optional[Collection[str]] = None,
) -> Dict[Tuple[str, str], float]:
    meta = bundle.get("meta") if isinstance(bundle.get("meta"), dict) else {}
    want = [str(n) for n in (meta.get("factor_names") or [])]
    if not want:
        raise ValueError(f"{bundle.get('head')} 权重缺少因子列序")
    dates = [str(d)[:10] for d in panel.dates]
    keep = _window_idx(dates, window_start, window_end)
    if not keep:
        return {}
    names = [str(n) for n in panel.feature_names]
    X_all = take_factor_columns(np.asarray(panel.X), names, want)
    if seq is not None:
        seq = align_seq_open_width(seq, int(meta.get("d_seq_in") or 0))
    backend = str(bundle.get("backend") or meta.get("backend") or "")
    # ŷ_oo：缺测不加项。ŷ_τc：任一入模列缺测则整行不算。排序头仍把缺测填 0。
    missing = str(meta.get("missing") or "")
    keep_missing = backend == "ridge" and missing in ("skip", "complete")
    if keep_missing:
        X = _zscore(
            X_all[keep],
            want,
            [dates[i] for i in keep],
            meta.get("z_clip"),
            fill_missing=False,
        )
    else:
        X = _zscore(X_all[keep], want, [dates[i] for i in keep], meta.get("z_clip"))
    seq_rows = None
    gap_rows = None
    if seq is not None:
        seq_rows = np.array(seq.X, dtype=np.float32, copy=True)[keep]
        gap_rows = np.array(seq.gap, dtype=np.float32, copy=True)[keep]
        apply_seq_clip(seq_rows, gap_rows, meta.get("seq_clip") if isinstance(meta.get("seq_clip"), dict) else None)
    keep_out = list(keep)
    if score_codes is not None:
        allow = {str(code).strip() for code in score_codes if str(code).strip()}
        pos = [i for i, row_i in enumerate(keep) if str(panel.codes[row_i]) in allow]
        if not pos:
            return {}
        idx = np.asarray(pos, dtype=np.intp)
        X = X[idx]
        if seq_rows is not None:
            seq_rows = seq_rows[idx]
            gap_rows = gap_rows[idx]
        keep_out = [keep[i] for i in pos]
    pred = _predict_matrix({**bundle, "meta": meta}, X, seq_rows, gap_rows)
    out: Dict[Tuple[str, str], float] = {}
    for pos, row_i in enumerate(keep_out):
        try:
            val = float(pred[pos])
        except (TypeError, ValueError, IndexError):
            continue
        if val != val:
            continue
        code = str(panel.codes[row_i])
        out[(code, dates[row_i])] = val
    return out


def stamp_day_ranks(days: Sequence[Mapping[str, Any]], *, blend: str) -> None:
    """给每条排序头编当日 1..n（1=该头分数最高）。

    blend=raw 时保留原始分，名次只供名次闸。
    blend=day_rank 时把原始分换成越高越好的当日分位，供两条不同后端的买序相加。
    """
    use_rank = str(blend or "") == "day_rank"
    slots = (
        ("y_oo_rank_score", "y_oo_rank", "y_oo_rank_hat", "predicted_score_oo_rank", "y_oo_rank_n"),
        ("y_tc_rank_score", "y_tc_rank", "y_tc_rank_hat", "predicted_score_tc_rank", "y_tc_rank_n"),
    )
    for day in days:
        scored = [row for row in (day.get("scored") or []) if isinstance(row, dict)]
        for score_key, rank_key, hat_key, pred_key, n_key in slots:
            rows = []
            for row in scored:
                raw = _finite(row.get(score_key))
                if raw is None:
                    continue
                rows.append((float(raw), str(row.get("stock_code") or ""), row))
            rows.sort(key=lambda item: (-item[0], item[1]))
            n = len(rows)
            for i, (_raw, _code, row) in enumerate(rows):
                rank = i + 1
                row[rank_key] = rank
                row[hat_key] = rank
                row[pred_key] = rank
                row[n_key] = n
                if use_rank and n > 0:
                    row[score_key] = (n - rank + 1) / float(n)


def _spec_backend(spec: Mapping[str, Any], fallback: str) -> str:
    meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
    return str(spec.get("backend") or meta.get("backend") or fallback or "")


def _spec_factory(spec: Mapping[str, Any], fallback: str) -> str:
    raw = str(spec.get("factory") or "").strip().upper()
    if raw in _ARM_ALIASES:
        return _ARM_ALIASES[raw]
    if fallback in ("L", "R", "T"):
        return fallback
    raise ValueError(f"{spec.get('head') or '头'} 缺少工厂")


def score_open_window(
    factory: str,
    suite: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """重建因子（Arm·T 再加序列），按权重预估，合成每日分数。

    截面 z 与打分都用观察池全池。组合回测按每头自己的后端决定要不要对齐序列。
    """
    from scripts.run_yhat_transformer_holdout import (
        _drop_aligned_rows_without_cube,
        _load_bars,
    )

    fac = _factory_key(factory)
    specs = suite.get("specs") if isinstance(suite.get("specs"), dict) else {}
    start = str(suite.get("window_start") or "")
    end = str(suite.get("window_end") or "")
    try:
        lookback = int(suite.get("lookback") or 120)
    except (TypeError, ValueError):
        lookback = 120
    try:
        seq_len = int(suite.get("seq_len") or 20)
    except (TypeError, ValueError):
        seq_len = 20
    feat_set = _resolved_seq_feat(suite.get("seq_feat_set"))
    codes = _watching_pool_codes()
    if not codes:
        raise ValueError("观察池为空，无法预估")
    score_codes = None
    print(f"截面 z {len(codes)} 只 · 打分全池 {len(codes)} 只 · 回看 {lookback}", flush=True)
    stock_bars = _load_bars(codes, lookback=lookback)
    if not stock_bars:
        raise ValueError("没有可用日线")
    backend = str(suite.get("backend") or "")
    head_backends = {
        head: _spec_backend(specs.get(head) or {}, backend) for head in HEADS
    }
    from core.research.yhat_transformer.seq_dataset import is_factor_seq

    factor_seq = is_factor_seq(feat_set)
    seq = None
    full_by_family: Dict[str, Any] = {}
    if any(b == "transformer" for b in head_backends.values()) and not factor_seq:
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        seq = build_seq_panel(
            stock_bars,
            head="oo",
            seq_len=seq_len,
            horizon_days=1,
            feat_set=feat_set,
        )
        if int(getattr(seq, "n", 0) or 0) <= 0:
            raise ValueError("序列面板为空")
    panels: Dict[Tuple[str, bool, str], Any] = {}
    scores: Dict[str, Dict[Tuple[str, str], float]] = {}
    for head in HEADS:
        spec = specs.get(head) or {}
        meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
        pack = str(meta.get("factor_pack") or "")
        head_backend = head_backends[head]
        aligned = head_backend == "transformer"
        key = (pack or head, aligned, feat_set if factor_seq else "")
        if key not in panels:
            if aligned and factor_seq:
                family = _head_family(head)
                if family not in full_by_family:
                    full_by_family[family] = _full_factor_panel(stock_bars, head)
                full = full_by_family[family]
                panel = full
                if str(pack).endswith("_core"):
                    from core.research.yhat_transformer.factor_panel import (
                        slice_arm_t_panel,
                    )

                    panel = slice_arm_t_panel(
                        full,
                        family,
                        aoo_core_only=family == "oo",
                        atc_core_only=family == "tc",
                    )
                seq_h = _factor_seq_panel(stock_bars, head, feat_set, seq_len, full)
                if int(getattr(seq_h, "n", 0) or 0) <= 0:
                    raise ValueError("序列面板为空")
                from core.research.yhat_transformer.factor_panel import (
                    align_seq_and_factor,
                )

                seq_a, fac_a = align_seq_and_factor(seq_h, panel)
                seq_a, fac_a = _drop_aligned_rows_without_cube(seq_a, fac_a)
                panels[key] = (fac_a, seq_a)
            else:
                panel = _factor_panel(stock_bars, head, pack)
                if aligned:
                    from core.research.yhat_transformer.factor_panel import (
                        align_seq_and_factor,
                    )

                    seq_a, fac_a = align_seq_and_factor(seq, panel)
                    seq_a, fac_a = _drop_aligned_rows_without_cube(seq_a, fac_a)
                    panels[key] = (fac_a, seq_a)
                else:
                    panels[key] = (panel, None)
        fac_a, seq_a = panels[key]
        bundle = {
            "head": head,
            "run_id": spec.get("run_id"),
            "backend": head_backend,
            "meta": meta,
            "factory": _spec_factory(spec, fac),
        }
        scores[head] = _scores_for_head(bundle, fac_a, seq_a, start, end, score_codes=score_codes)
        if suite.get("mix"):
            print(
                f"预估 {head} {bundle['factory']} {head_backend} {len(scores[head])} 条",
                flush=True,
            )
        else:
            print(f"预估 {head} {len(scores[head])} 条", flush=True)
    indexed = _index_bars(stock_bars)
    days: Dict[str, Dict[str, Any]] = {}
    for code, date in scores.get("oo") or {}:
        if not all((code, date) in (scores.get(h) or {}) for h in HEADS):
            continue
        bar = indexed.get((code, date))
        if not bar:
            continue
        px = _bar_px(bar, "open", "Open")
        mark = _bar_px(bar, "close", "Close") or px
        if px is None:
            continue
        bucket = days.setdefault(
            date,
            {"date": date, "scored": [], "prices": {}, "marks": {}, "bars": stock_bars},
        )
        bucket["prices"][code] = px
        bucket["marks"][code] = mark
        bucket["scored"].append(
            {
                "stock_code": code,
                "y_oo": scores["oo"][(code, date)],
                "y_τc": scores["tc"][(code, date)],
                "predicted_score": scores["oo"][(code, date)],
                "predicted_score_τc": scores["tc"][(code, date)],
                "y_oo_rank_score": scores["oo_rank"][(code, date)],
                "y_tc_rank_score": scores["tc_rank"][(code, date)],
            }
        )
    out = [days[k] for k in sorted(days)]
    print(f"可交易日 {len(out)}", flush=True)
    if not out:
        raise ValueError("评测窗内没有可预估的交易日")
    return out


def enabled_replay_specs() -> List[Dict[str, Any]]:
    """历史回测四头：当前研究/执行档在工厂索引里已启用、且带权重的版本。"""
    from core.research.holdout import MODEL_ROLE_LIVE, current_scoring_model_role
    from core.research.yhat_factory_versions import (
        list_factory_versions,
        preview_factory_version,
    )
    from core.research.yhat_factory_weights import version_weights_dir

    from core.research.yhat_hub_arms import read_hub_arms

    suite = "live" if current_scoring_model_role() == MODEL_ROLE_LIVE else "research"
    arms = read_hub_arms()
    specs: List[Dict[str, Any]] = []
    for head in HEADS:
        fac = str(arms.get(head) or "T")
        with factory_scope(fac):
            listed = list_factory_versions(head, factory=fac, suite=suite)
            vid = str(listed.get("enabled_id") or "").strip()
            if not vid:
                continue
            hit = next(
                (
                    it
                    for it in (listed.get("items") or [])
                    if str(it.get("id") or "") == vid
                ),
                None,
            )
            if not isinstance(hit, dict) or not hit.get("weights_ref"):
                continue
            snap = preview_factory_version(head, vid, factory=fac)
            doc = snap.get("version") if isinstance(snap.get("version"), dict) else {}
            meta = _read_json(os.path.join(version_weights_dir(head, vid), "meta.json"))
            if not meta:
                continue
            fit = doc.get("fit_params") if isinstance(doc.get("fit_params"), dict) else {}
            badges = hit.get("badges") if isinstance(hit.get("badges"), dict) else {}
            ic = badges.get("cs_rank_ic")
            specs.append(
                {
                    "head": head,
                    "run_id": vid,
                    "label": str(hit.get("label") or ""),
                    "rank_ic": ic if isinstance(ic, (int, float)) else None,
                    "backend": str(meta.get("backend") or ""),
                    "meta": meta,
                    "fit_params": fit,
                    "factory": fac,
                    "suite": suite,
                }
            )
    return specs


def _bars_for_replay_score(
    stock_bars: Mapping[str, Sequence[dict]],
) -> Mapping[str, Sequence[dict]]:
    """只读消费时丢掉完整日线之后的会话日。

    盘中 overlay 会把当天塞进 bars。物化闸若把这一天当成 oo/tc 硬缺，
    四头预估整段失败，回测清掉入场分，结果是 0 笔成交。
    """
    from core.research.materialize.consumer import consumer_read_only

    if not consumer_read_only() or not stock_bars:
        return stock_bars
    from core.research.materialize.coverage import complete_daily_as_of

    as_of = str(complete_daily_as_of(list(stock_bars)) or "")[:10]
    if len(as_of) < 10:
        return stock_bars
    out: Dict[str, Sequence[dict]] = {}
    for code, rows in stock_bars.items():
        kept = [
            row
            for row in (rows or [])
            if isinstance(row, Mapping) and (not _bar_day(row) or _bar_day(row) <= as_of)
        ]
        if kept:
            out[str(code)] = kept
    return out or stock_bars


def score_enabled_replay_heads(
    stock_bars: Mapping[str, Sequence[dict]],
) -> Dict[str, Any]:
    """用已启用的工厂权重给回测日线打四头。没有启用或没有权重的头不出现。

    ŷ_τc / ŷ_τc_rank 的序列就是开盘（本期训练只接受 tau_end 09:30）。
    某一头预估失败则这一头留空，不改去套槽。
    """
    from scripts.run_yhat_transformer_holdout import _drop_aligned_rows_without_cube

    specs = enabled_replay_specs()
    suite = str((specs[0].get("suite") if specs else "") or "")
    table: Dict[str, Any] = {
        "suite": suite,
        "heads": [str(s.get("head") or "") for s in specs],
        "runs": {str(s.get("head") or ""): str(s.get("run_id") or "") for s in specs},
        "cards": _replay_cards(specs),
        "by_day": {},
    }
    if not specs or not stock_bars:
        return table
    stock_bars = _bars_for_replay_score(stock_bars)
    try:
        lookback = max(_fit_int(s.get("fit_params"), "lookback", 120) for s in specs)
    except ValueError:
        lookback = 120
    seq_len = max(_fit_int(s.get("fit_params"), "seq_len", 20) for s in specs)
    feat_set = "seq_core"
    for spec in specs:
        raw = str((spec.get("fit_params") or {}).get("seq_feat_set") or "").strip()
        if raw:
            feat_set = _resolved_seq_feat(raw)
            break
    from core.research.yhat_transformer.seq_dataset import is_factor_seq

    factor_seq = is_factor_seq(feat_set)
    seq = None
    full_by_family: Dict[str, Any] = {}
    if any(str(s.get("backend") or "") == "transformer" for s in specs) and not factor_seq:
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        seq = build_seq_panel(
            stock_bars,
            head="oo",
            seq_len=seq_len,
            horizon_days=1,
            feat_set=feat_set,
        )
    panels: Dict[str, Any] = {}
    scored: Dict[str, Dict[Tuple[str, str], float]] = {}
    for spec in specs:
        head = str(spec.get("head") or "")
        try:
            meta = spec.get("meta") if isinstance(spec.get("meta"), dict) else {}
            pack = str(meta.get("factor_pack") or "")
            transformer = str(spec.get("backend") or "") == "transformer"
            key = (
                f"{pack or head}|{feat_set}|{_head_family(head)}"
                if transformer and factor_seq
                else (pack or head)
            )
            if key not in panels:
                if transformer and factor_seq:
                    family = _head_family(head)
                    if family not in full_by_family:
                        full_by_family[family] = _full_factor_panel(stock_bars, head)
                    full = full_by_family[family]
                    panel = full
                    if str(pack).endswith("_core"):
                        from core.research.yhat_transformer.factor_panel import (
                            slice_arm_t_panel,
                        )

                        panel = slice_arm_t_panel(
                            full,
                            family,
                            aoo_core_only=family == "oo",
                            atc_core_only=family == "tc",
                        )
                    seq_h = _factor_seq_panel(
                        stock_bars, head, feat_set, seq_len, full
                    )
                    from core.research.yhat_transformer.factor_panel import (
                        align_seq_and_factor,
                    )

                    seq_a, fac_a = align_seq_and_factor(seq_h, panel)
                    seq_a, fac_a = _drop_aligned_rows_without_cube(seq_a, fac_a)
                    panels[key] = (fac_a, seq_a)
                else:
                    panel = _factor_panel(stock_bars, head, pack)
                    if seq is not None and transformer:
                        from core.research.yhat_transformer.factor_panel import (
                            align_seq_and_factor,
                        )

                        seq_a, fac_a = align_seq_and_factor(seq, panel)
                        seq_a, fac_a = _drop_aligned_rows_without_cube(seq_a, fac_a)
                        panels[key] = (fac_a, seq_a)
                    else:
                        panels[key] = (panel, None)
            fac_a, seq_a = panels[key]
            dates = [str(d)[:10] for d in fac_a.dates]
            if not dates:
                continue
            scored[head] = _scores_for_head(
                spec,
                fac_a,
                seq_a,
                min(dates),
                max(dates),
            )
        except Exception as exc:  # noqa: BLE001
            from core.research.materialize.consumer import MaterializeRequired

            if isinstance(exc, MaterializeRequired):
                raise
            logger.exception("replay arm-t score failed head=%s", head)
    by_day: Dict[str, Dict[str, Dict[str, float]]] = {}
    keys = {
        "oo": "y_oo",
        "tc": "y_τc",
        "oo_rank": "y_oo_rank_score",
        "tc_rank": "y_tc_rank_score",
    }
    for head, pairs in scored.items():
        field = keys.get(head)
        if not field:
            continue
        for (code, date), val in pairs.items():
            bucket = by_day.setdefault(date, {})
            row = bucket.setdefault(code, {})
            row[field] = val
    table["by_day"] = by_day
    table["lookback"] = lookback
    return table


def _replay_cards(specs: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """四头启用版本的短卡，给成交 tip。不含权重。"""
    cards: Dict[str, Dict[str, Any]] = {}
    for spec in specs or []:
        head = str(spec.get("head") or "")
        if not head:
            continue
        ic = spec.get("rank_ic")
        ic_n = None
        if isinstance(ic, (int, float)) and ic == ic:
            ic_n = round(float(ic), 3)
        cards[head] = {
            "run_id": str(spec.get("run_id") or ""),
            "label": str(spec.get("label") or ""),
            "rank_ic": ic_n,
            "factory": str(spec.get("factory") or "T"),
            "backend": str(spec.get("backend") or ""),
        }
    return cards


def _fit_int(params: Any, key: str, default: int) -> int:
    src = params if isinstance(params, dict) else {}
    try:
        return int(src.get(key) or default)
    except (TypeError, ValueError):
        return int(default)


def _backtest_log_path(factory: str) -> str:
    return os.path.join(_store_dir(factory), "backtest.log")


def _tail_path(path: str, limit: int = 80000) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > int(limit):
                f.seek(size - int(limit))
                f.readline()
            data = f.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def _log_tail(factory: str, limit: int = 80000) -> str:
    return _tail_path(_backtest_log_path(factory), limit)


def _blank_backtest_log(factory: str) -> None:
    """失败记录已经从页面拿掉时，当前这次的失败日志也不再留着。"""
    path = _backtest_log_path(factory)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("")
    except OSError:
        logger.exception("clear dropped backtest log failed path=%s", path)


_RUN_KEEP = 20
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}(?:-[0-9]+)?$")


def _check_run_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text or not _RUN_ID.fullmatch(text) or ".." in text:
        return ""
    return text


def _runs_dir(factory: str) -> str:
    return os.path.join(_store_dir(factory), "backtests")


def _runs_index_path(factory: str) -> str:
    return os.path.join(_runs_dir(factory), "index.json")


def _run_snap_path(factory: str, run_id: str) -> str:
    return os.path.join(_runs_dir(factory), f"{run_id}.json")


def _run_log_path(factory: str, run_id: str) -> str:
    return os.path.join(_runs_dir(factory), f"{run_id}.log")


def _new_run_id(factory: str, started: str) -> str:
    digits = "".join(ch for ch in str(started or "") if ch.isdigit())
    if len(digits) < 14:
        digits = time.strftime("%Y%m%d%H%M%S")
    base = f"{digits[:8]}-{digits[8:14]}"
    run_id = base
    n = 2
    while os.path.exists(_run_snap_path(factory, run_id)) or os.path.exists(_run_log_path(factory, run_id)):
        run_id = f"{base}-{n}"
        n += 1
        if n > 50:
            break
    return run_id


def _stamp_label(started: str) -> str:
    text = str(started or "")
    if len(text) >= 16 and text[4] == "-" and text[10] == "T":
        return text[5:16].replace("T", " ")
    return text or "—"


def _run_label(started: str, status: str, report: Optional[Mapping[str, Any]]) -> str:
    stamp = _stamp_label(started)
    if status == "running":
        return f"{stamp} · 进行中"
    if status != "done":
        return f"{stamp} · 失败"
    raw = report.get("cumulative_return") if isinstance(report, Mapping) else None
    try:
        pct = float(raw) * 100.0
    except (TypeError, ValueError):
        return stamp
    if pct != pct or pct in (float("inf"), float("-inf")):
        return stamp
    sign = "+" if pct > 0 else ""
    return f"{stamp} · {sign}{pct:.2f}%"


def _read_run_index(factory: str) -> List[Dict[str, Any]]:
    raw = _read_json(_runs_index_path(factory))
    items = (raw or {}).get("items")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in items:
        if isinstance(it, dict) and _check_run_id(it.get("id")):
            out.append(dict(it))
    return out


def _attach_archived_run(factory: str, job: Mapping[str, Any]) -> Dict[str, Any]:
    """结束任务还没有 run_id 时，对上同一开始时间的归档，并写回任务文件。"""
    out = dict(job)
    if _check_run_id(out.get("run_id")):
        return out
    started = str(out.get("started_at") or "")
    if not started:
        return out
    for it in _read_run_index(factory):
        run_id = _check_run_id(it.get("id"))
        if run_id and str(it.get("started_at") or "") == started:
            out["run_id"] = run_id
            _write_job(factory, out)
            return out
    return out


def _run_entries(factory: str, live: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    items = _read_run_index(factory)
    if not isinstance(live, Mapping):
        return items
    if str(live.get("status") or "") != "running":
        return items
    run_id = _check_run_id(live.get("run_id"))
    if not run_id or any(str(it.get("id") or "") == run_id for it in items):
        return items
    live_item = {
        "id": run_id,
        "label": _run_label(str(live.get("started_at") or ""), "running", None),
        "status": "running",
        "started_at": live.get("started_at"),
        "finished_at": None,
        "versions": live.get("versions") if isinstance(live.get("versions"), dict) else {},
        "window_start": live.get("window_start") or "",
        "window_end": live.get("window_end") or "",
    }
    if live.get("picks"):
        live_item["picks"] = live.get("picks")
    if isinstance(live.get("factories"), dict):
        live_item["factories"] = dict(live.get("factories") or {})
    if live.get("rank_blend"):
        live_item["rank_blend"] = live.get("rank_blend")
    return [live_item, *items]


def _archive_backtest(factory: str, job: Mapping[str, Any]) -> None:
    """把这一次的 console 和报告留在工厂目录。进行中的不写。用户删掉的不再补回。"""
    if not isinstance(job, Mapping):
        return
    if job.get("history_dropped"):
        return
    status = str(job.get("status") or "")
    if status not in ("done", "error"):
        return
    fac = _factory_key(factory)
    run_id = _check_run_id(job.get("run_id"))
    if run_id and os.path.isfile(_run_snap_path(fac, run_id)):
        return
    if not run_id:
        started = str(job.get("started_at") or "")
        if not started:
            return
        for it in _read_run_index(fac):
            if str(it.get("started_at") or "") == started:
                return
        run_id = _new_run_id(fac, started)
    report = job.get("report") if isinstance(job.get("report"), dict) else None
    versions = job.get("versions") if isinstance(job.get("versions"), dict) else {}
    if report and isinstance(report.get("versions"), dict):
        versions = report.get("versions")
    snap = {
        "id": run_id,
        "status": status,
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": "" if status == "done" else str(job.get("error") or ""),
        "versions": {k: str(v or "") for k, v in versions.items()},
        "window_start": (report or {}).get("window_start") or job.get("window_start") or "",
        "window_end": (report or {}).get("window_end") or job.get("window_end") or "",
        "report": report,
    }
    picks = str((report or {}).get("picks") or job.get("picks") or "")
    if picks:
        snap["picks"] = picks
    factories = (report or {}).get("factories") if isinstance((report or {}).get("factories"), dict) else job.get("factories")
    if isinstance(factories, dict) and factories:
        snap["factories"] = {str(k): str(v) for k, v in factories.items()}
    blend = str((report or {}).get("rank_blend") or job.get("rank_blend") or "")
    if blend:
        snap["rank_blend"] = blend
    root = _runs_dir(fac)
    os.makedirs(root, exist_ok=True)
    text = _log_tail(fac)
    try:
        with open(_run_log_path(fac, run_id), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        logger.exception("archive factory backtest log failed")
        return
    atomic_write_json(_run_snap_path(fac, run_id), snap)
    brief = {
        "id": run_id,
        "label": _run_label(str(job.get("started_at") or ""), status, report),
        "status": status,
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "versions": snap["versions"],
        "window_start": snap["window_start"],
        "window_end": snap["window_end"],
    }
    if snap.get("picks"):
        brief["picks"] = snap["picks"]
    if snap.get("factories"):
        brief["factories"] = snap["factories"]
    if snap.get("rank_blend"):
        brief["rank_blend"] = snap["rank_blend"]
    items = [it for it in _read_run_index(fac) if str(it.get("id") or "") != run_id]
    items.insert(0, brief)
    dropped = items[_RUN_KEEP:]
    items = items[:_RUN_KEEP]
    for old in dropped:
        old_id = _check_run_id(old.get("id"))
        if not old_id:
            continue
        for path in (_run_snap_path(fac, old_id), _run_log_path(fac, old_id)):
            try:
                os.remove(path)
            except OSError:
                pass
    atomic_write_json(
        _runs_index_path(fac),
        {"version": 1, "factory": fac, "items": items},
    )


def delete_factory_backtest(factory: str, run_id: str) -> Dict[str, Any]:
    """去掉一条离线回测记录。不改正在跑的那次。

    成功的结果和当前 console 还留着。删掉的是当前这次失败时，错误提示和这次日志一起清掉。
    """
    fac = _factory_key(factory)
    rid = _check_run_id(run_id)
    if not rid:
        return {"success": False, "ok": False, "error": "invalid run_id"}
    job = _read_json(_job_path(fac)) or {}
    if str(job.get("status") or "") == "running" and _check_run_id(job.get("run_id")) == rid:
        return {"success": False, "ok": False, "error": "进行中的回测不能删除", "run_id": rid}
    items = _read_run_index(fac)
    matched = next((it for it in items if str(it.get("id") or "") == rid), None)
    snap = _run_snap_path(fac, rid)
    log_path = _run_log_path(fac, rid)
    if matched is None and not os.path.isfile(snap) and not os.path.isfile(log_path):
        return {"success": False, "ok": False, "error": "记录不存在", "run_id": rid}
    for path in (snap, log_path):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            logger.exception("delete factory backtest failed path=%s", path)
            return {"success": False, "ok": False, "error": "删除失败", "run_id": rid}
    kept = [it for it in items if str(it.get("id") or "") != rid]
    os.makedirs(_runs_dir(fac), exist_ok=True)
    atomic_write_json(
        _runs_index_path(fac),
        {"version": 1, "factory": fac, "items": kept},
    )
    started = str((matched or {}).get("started_at") or "")
    same_job = _check_run_id(job.get("run_id")) == rid or (
        bool(started) and str(job.get("started_at") or "") == started
    )
    if same_job and str(job.get("status") or "") in ("done", "error"):
        updated = dict(job)
        updated["history_dropped"] = True
        if _check_run_id(updated.get("run_id")) == rid:
            updated["run_id"] = ""
        if str(job.get("status") or "") == "error":
            updated["status"] = "idle"
            updated["running"] = False
            updated["error"] = ""
            _write_job(fac, updated)
            _blank_backtest_log(fac)
        else:
            _write_job(fac, updated)
    return {
        "success": True,
        "ok": True,
        "factory": fac,
        "run_id": rid,
        "runs": _run_entries(fac, None),
    }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _write_job(factory: str, doc: Mapping[str, Any]) -> None:
    atomic_write_json(_job_path(factory), dict(doc))


def _default_rules() -> Dict[str, Any]:
    return {
        "name": "rank_lots",
        "w_oo": 0.6,
        "w_tc": 0.4,
        "fuse_enter": 0.001,
        "fuse_strong": 0.001,
        "oo_rank_max": 6,
        "oo_rank_strong_max": 12,
        "y_oo_gt0": False,
        "morph_gate_enabled": False,
        "lot_base_amount": 10_000.0,
        "lot_strong_amount": 20_000.0,
        "fill_clock": FILL_CLOCK,
        "start_cash": START_CASH,
        "cost_model": "simple_cn",
        "holdings_mv_cap": 150_000.0,
        "top_k": 200,
        "feature_set": "",
    }


def _num(raw: Any, lo: float, hi: float, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if value != value or value in (float("inf"), float("-inf")):
        return float(default)
    return max(float(lo), min(float(hi), value))


def _pick(src: Mapping[str, Any], *keys: str) -> Tuple[Any, bool]:
    for key in keys:
        if key in src:
            return src.get(key), True
    return None, False


def _rank_gate(raw: Any, default: Any) -> Optional[int]:
    """名次上限。空 / null = 关。"""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and raw.strip().lower() in {"", "null", "none", "关"}:
        return None
    fallback = 6 if default is None else int(default)
    return int(round(_num(raw, 2.0, 5000.0, float(fallback))))


def _amount(raw: Any, default: float) -> float:
    value = _num(raw, 1_000.0, 1_000_000.0, default)
    return float(int(round(value / 100.0)) * 100)


def normalize_backtest_rules(
    raw: Optional[Mapping[str, Any]] = None,
    *,
    base: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """把页面上的规则收成一份工厂回测配置。成交钟固定 09:30。"""
    brief = dict(base) if isinstance(base, Mapping) else _default_rules()
    for key, value in _default_rules().items():
        brief.setdefault(key, value)
    src = raw if isinstance(raw, Mapping) else {}
    for dest, keys in (
        ("w_oo", ("w_oo", "fusion_w_oo")),
        ("w_tc", ("w_tc", "fusion_w_oc")),
    ):
        val, ok = _pick(src, *keys)
        if ok:
            brief[dest] = _num(val, 0.0, 1.0, float(brief[dest]))
    for key in ("fuse_enter", "fuse_strong"):
        val, ok = _pick(src, key)
        if ok:
            brief[key] = _num(val, 0.0, 0.10, float(brief[key]))
    if float(brief["fuse_strong"]) < float(brief["fuse_enter"]):
        brief["fuse_strong"] = float(brief["fuse_enter"])
    for key in ("oo_rank_max", "oo_rank_strong_max"):
        val, ok = _pick(src, key)
        if ok:
            brief[key] = _rank_gate(val, brief.get(key))
    brief.pop("oo_tc_score_min", None)
    brief.pop("oo_tc_score_strong_min", None)
    brief.pop("w_oo_rank", None)
    brief.pop("w_tc_rank", None)
    brief.pop("fusion_w_oo_rank", None)
    brief.pop("fusion_w_tc_rank", None)
    brief.pop("tier_a_only", None)
    for key in ("lot_base_amount", "lot_strong_amount"):
        val, ok = _pick(src, key)
        if ok:
            brief[key] = _amount(val, float(brief[key]))
    if float(brief["lot_strong_amount"]) < float(brief["lot_base_amount"]):
        brief["lot_strong_amount"] = float(brief["lot_base_amount"])
    val, ok = _pick(src, "start_cash")
    if ok:
        brief["start_cash"] = _num(val, 10_000.0, 100_000_000.0, float(brief["start_cash"]))
    val, ok = _pick(src, "feature_set")
    if ok:
        from core.universe.feature_sets import normalize_feature_set

        brief["feature_set"] = normalize_feature_set(val)
    for key in ("y_oo_gt0", "morph_gate_enabled"):
        val, ok = _pick(src, key)
        if ok:
            brief[key] = bool(val)
    val, ok = _pick(src, "holdings_mv_cap")
    if ok:
        brief["holdings_mv_cap"] = _num(val, 0.0, 1.0e8, float(brief["holdings_mv_cap"]))
    val, ok = _pick(src, "top_k")
    if ok:
        brief["top_k"] = int(_num(val, 1.0, 500.0, float(brief["top_k"])))
    brief["name"] = "rank_lots"
    brief["fill_clock"] = FILL_CLOCK
    brief["cost_model"] = "simple_cn"
    return brief


def rules_to_planner(brief: Mapping[str, Any]) -> Dict[str, Any]:
    """页面规则 → plan_rank_lot_day 读的 cfg。不带成交钟，调用方另钉 09:30。"""

    def num(key: str, default: float) -> float:
        raw = brief.get(key)
        if raw is None or raw == "":
            return float(default)
        return float(raw)

    strong = num("lot_strong_amount", 20_000.0)
    return {
        "fusion_w_oo": num("w_oo", 0.6),
        "fusion_w_oc": num("w_tc", 0.4),
        "fuse_enter": num("fuse_enter", 0.0),
        "fuse_strong": num("fuse_strong", 0.0),
        "oo_rank_max": brief.get("oo_rank_max"),
        "oo_rank_strong_max": brief.get("oo_rank_strong_max"),
        "lot_base_amount": num("lot_base_amount", 10_000.0),
        "lot_strong_amount": strong,
        "y_oo_gt0": bool(brief.get("y_oo_gt0")),
        "morph_gate_enabled": bool(brief.get("morph_gate_enabled")),
        "cash_floor": 0.0,
        "holdings_mv_cap": num("holdings_mv_cap", 0.0),
        "top_k": int(num("top_k", 200)),
        "sentiment_gate_enabled": False,
        "stock_mv_cap": strong * 1.2,
    }


def backtest_rules_brief() -> Dict[str, Any]:
    """还没有本页配置时，用当前 rank_lots 做初值。只读，不写回。"""
    brief = _default_rules()
    try:
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        cfg = get_rank_lot_cfg()
    except Exception:  # noqa: BLE001
        logger.debug("read rank_lots for factory backtest failed", exc_info=True)
        return brief
    seeded = normalize_backtest_rules(
        {
            "w_oo": cfg.get("fusion_w_oo"),
            "w_tc": cfg.get("fusion_w_oc"),
            "fuse_enter": cfg.get("fuse_enter"),
            "fuse_strong": cfg.get("fuse_strong"),
            "oo_rank_max": cfg.get("oo_rank_max"),
            "oo_rank_strong_max": cfg.get("oo_rank_strong_max"),
            "lot_base_amount": cfg.get("lot_base_amount"),
            "lot_strong_amount": cfg.get("lot_strong_amount"),
            "y_oo_gt0": bool(cfg.get("y_oo_gt0")),
            "morph_gate_enabled": bool(cfg.get("morph_gate_enabled")),
            "holdings_mv_cap": cfg.get("holdings_mv_cap"),
            "top_k": cfg.get("top_k"),
        },
        base=brief,
    )
    seeded["start_cash"] = START_CASH
    return seeded


def load_factory_rules(factory: str) -> Dict[str, Any]:
    """本页自己的规则。没有文件时用 rank_lots 初值，不落盘。"""
    saved = _read_json(_rules_path(_factory_key(factory)))
    if not saved:
        return backtest_rules_brief()
    return normalize_backtest_rules(saved, base=_default_rules())


def save_factory_rules(factory: str, raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """写入回测目录。不改历史回测页，不改 live，不改三家工厂目录。"""
    fac = _factory_key(factory)
    brief = normalize_backtest_rules(raw, base=load_factory_rules(fac))
    atomic_write_json(_rules_path(fac), brief)
    return brief


def _resolve_run_rules(
    factory: str,
    *,
    cfg: Optional[Mapping[str, Any]],
    rules: Optional[Mapping[str, Any]],
    suite: Mapping[str, Any],
) -> Tuple[Dict[str, Any], float, Dict[str, Any]]:
    """显式 planner cfg 优先（单测）。否则用本次传入的页面规则，再否则读工厂文件。"""
    if isinstance(cfg, Mapping):
        planner = dict(cfg)
        cash = START_CASH
        brief = normalize_backtest_rules(cfg, base=_default_rules())
        brief["start_cash"] = cash
        return planner, cash, brief
    suite_cfg = suite.get("cfg") if isinstance(suite.get("cfg"), Mapping) else None
    if rules is None and isinstance(suite_cfg, Mapping):
        return dict(suite_cfg), START_CASH, normalize_backtest_rules(suite_cfg, base=_default_rules())
    if isinstance(rules, Mapping):
        brief = normalize_backtest_rules(rules, base=_default_rules())
    else:
        brief = load_factory_rules(factory)
    return rules_to_planner(brief), float(brief["start_cash"]), brief


def _status_body(
    fac: str,
    *,
    status: str,
    live_status: str,
    job: Mapping[str, Any],
    report: Any,
    log_tail: str,
    run_id: str,
    viewing: bool,
    runs: Sequence[Mapping[str, Any]],
    pid: int = 0,
) -> Dict[str, Any]:
    return {
        "success": True,
        "ok": True,
        "factory": fac,
        "status": status,
        "live_status": live_status,
        "viewing": viewing,
        "run_id": run_id,
        "runs": [dict(it) for it in runs],
        "fill_clock": FILL_CLOCK,
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error") or "",
        "versions": job.get("versions") or {},
        "window_start": job.get("window_start"),
        "window_end": job.get("window_end"),
        "pid": pid,
        "rules": load_factory_rules(fac),
        "report": report,
        "log_tail": log_tail,
        "picks": job.get("picks") or (report.get("picks") if isinstance(report, Mapping) else "") or "",
        "factories": job.get("factories")
        or (report.get("factories") if isinstance(report, Mapping) else {})
        or {},
        "rank_blend": job.get("rank_blend")
        or (report.get("rank_blend") if isinstance(report, Mapping) else "")
        or "",
    }


def factory_backtest_status(factory: str, run_id: str = "") -> Dict[str, Any]:
    fac = _factory_key(factory)
    job = _read_json(_job_path(fac)) or {}
    last = _read_json(_last_path(fac))
    status = str(job.get("status") or "idle")
    pid = int(job.get("pid") or 0) if str(job.get("pid") or "").lstrip("-").isdigit() else 0
    if status == "running" and pid and not _pid_alive(pid):
        status = "error"
        job = {
            **job,
            "status": "error",
            "running": False,
            "error": job.get("error") or "",
            "finished_at": job.get("finished_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _write_job(fac, job)
        _archive_backtest(fac, job)
    if status == "error" and job.get("history_dropped"):
        job = {**job, "status": "idle", "running": False, "error": ""}
        _write_job(fac, job)
        _blank_backtest_log(fac)
        status = "idle"
    if status in ("done", "error") and job:
        _archive_backtest(fac, job)
        job = _attach_archived_run(fac, job)
    live_status = status if job else "idle"
    runs = _run_entries(fac, job if live_status == "running" else None)
    wanted = _check_run_id(run_id)
    live_id = _check_run_id(job.get("run_id"))
    if wanted and not (live_status == "running" and wanted == live_id):
        snap = _read_json(_run_snap_path(fac, wanted))
        if isinstance(snap, dict) and _check_run_id(snap.get("id")) == wanted:
            snap_status = str(snap.get("status") or "done")
            return _status_body(
                fac,
                status=snap_status,
                live_status=live_status,
                job=snap,
                report=snap.get("report"),
                log_tail=_tail_path(_run_log_path(fac, wanted)),
                run_id=wanted,
                viewing=True,
                runs=runs,
                pid=0,
            )
    if not job:
        return _status_body(
            fac,
            status="idle",
            live_status="idle",
            job={},
            report=last,
            log_tail=_log_tail(fac),
            run_id="",
            viewing=False,
            runs=runs,
        )
    if status == "done":
        report = job.get("report")
    elif status == "idle" and job.get("history_dropped"):
        report = None
    else:
        report = last
    return _status_body(
        fac,
        status=status,
        live_status=live_status,
        job=job,
        report=report,
        log_tail=_log_tail(fac),
        run_id=live_id,
        viewing=False,
        runs=runs,
        pid=pid,
    )


def start_factory_backtest(
    factory: str,
    versions: Mapping[str, Any],
    rules: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """校验四头后拉起独立进程。请求本身不等待预估。只接受回测中心。"""
    fac = _factory_key(factory)
    if fac != _MIX:
        raise ValueError("组合回测只写 live/yhat_bt")
    suite = load_mix(versions)
    current = factory_backtest_status(fac)
    if current.get("status") == "running":
        return {"success": False, "ok": False, "error": "组合回测进行中", "status": "running"}
    brief = save_factory_rules(fac, rules) if isinstance(rules, Mapping) else load_factory_rules(fac)
    prev = _read_json(_job_path(fac)) or {}
    if str(prev.get("status") or "") in ("done", "error"):
        _archive_backtest(fac, prev)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    run_id = _new_run_id(fac, started)
    job = {
        "role": "yhat_factory_backtest",
        "factory": fac,
        "status": "running",
        "running": True,
        "pid": 0,
        "run_id": run_id,
        "started_at": started,
        "finished_at": None,
        "error": "",
        "versions": suite.get("versions"),
        "window_start": suite.get("window_start"),
        "window_end": suite.get("window_end"),
        "fill_clock": FILL_CLOCK,
        "live_allowed": False,
        "rules": brief,
        "report": None,
    }
    if fac == _MIX:
        job["factories"] = suite.get("factories")
        job["picks"] = suite.get("picks")
        job["rank_blend"] = suite.get("rank_blend")
        job["sources"] = suite.get("sources")
    _write_job(fac, job)
    script = os.path.join(_QUANTLAB, "scripts", "run_yhat_factory_backtest.py")
    payload = suite.get("sources") if fac == _MIX else (suite.get("versions") or {})
    cmd = [
        sys.executable,
        "-u",
        script,
        "--factory",
        fac,
        "--versions",
        json.dumps(payload, ensure_ascii=False),
        "--rules",
        json.dumps(brief, ensure_ascii=False),
    ]
    log_path = _backtest_log_path(fac)
    child_env = os.environ.copy()
    child_env.setdefault("PYTHONUNBUFFERED", "1")
    child_env.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    for key in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "HORIZON_LGB_NUM_THREADS",
    ):
        child_env[key] = "1"
    child_env["TORCH_NUM_THREADS"] = "4"
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.write(
        log_fd,
        f"==== 组合回测 {started} ====\n".encode("utf-8"),
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=_QUANTLAB,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=child_env,
        )
    except OSError as exc:
        os.close(log_fd)
        failed = {**job, "status": "error", "running": False, "error": f"无法启动组合回测：{exc}"}
        _write_job(fac, failed)
        _archive_backtest(fac, failed)
        return {"success": False, "ok": False, "error": failed["error"], "status": "error"}
    os.close(log_fd)
    fresh = _read_json(_job_path(fac)) or {}
    if str(fresh.get("status") or "") == "running":
        fresh["pid"] = int(proc.pid)
        _write_job(fac, fresh)
    return {
        "success": True,
        "ok": True,
        "status": "running",
        "factory": fac,
        "pid": int(proc.pid),
        "fill_clock": FILL_CLOCK,
        "window_start": suite.get("window_start"),
        "window_end": suite.get("window_end"),
        "versions": suite.get("versions"),
        "picks": suite.get("picks") or "",
        "rank_blend": suite.get("rank_blend") or "",
        "run_id": run_id,
    }


def finish_factory_backtest(
    factory: str,
    *,
    report: Optional[Mapping[str, Any]] = None,
    error: str = "",
) -> None:
    """子进程收尾。成功时报告同时留在 backtest_last.json。"""
    fac = _factory_key(factory)
    prev = _read_json(_job_path(fac)) or {}
    done = not error
    job = {
        **prev,
        "status": "done" if done else "error",
        "running": False,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "error": "" if done else str(error),
        "report": dict(report) if done and isinstance(report, Mapping) else None,
        "live_allowed": False,
    }
    if done and isinstance(report, Mapping):
        job["window_start"] = report.get("window_start")
        job["window_end"] = report.get("window_end")
        job["versions"] = report.get("versions")
        atomic_write_json(_last_path(fac), dict(report))
    _write_job(fac, job)
    _archive_backtest(fac, job)
