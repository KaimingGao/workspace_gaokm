"""研究 / 执行两套模型：holdout 切分与版本槽。

拟合只训 **一套**：近 N 交易日 Holdout 不进训练（默认 20），在 Holdout 前样本上估 β。
``research`` / ``live`` 是同一份 β 的两个落盘槽（版本前后），不是两套系数：
- **启用研究** = 草稿 → 研究槽（供历史回测）
- **启用执行** = 研究槽 → 执行槽（晋级；禁止从草稿直接上 live）

观察池分档的命中用 Holdout 前半，截面指标与等权买持用整段；回测天数由 /replay「回测窗口」独立设置。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

MODEL_ROLE_LIVE = "live"
MODEL_ROLE_RESEARCH = "research"
DEFAULT_HOLDOUT_TRADING_DAYS = 20

_SCORING_MODEL_ROLE: ContextVar[str] = ContextVar(
    "scoring_model_role", default=MODEL_ROLE_LIVE
)


def normalize_model_role(role: Optional[str]) -> str:
    raw = str(role or "").strip().lower()
    if raw in {"research", "holdout", "oos", "backtest"}:
        return MODEL_ROLE_RESEARCH
    return MODEL_ROLE_LIVE


def normalize_backtest_model_role(role: Optional[str] = None) -> str:
    """回测默认研究套；仅显式 live / 执行 才用执行槽版本。

    两槽系数同源（Holdout 前训）；差别是版本晋级前后，故须显式选择才切 live。
    """
    raw = str(role or "").strip().lower()
    if raw in {"live", "execution", "exec", "执行"}:
        return MODEL_ROLE_LIVE
    return MODEL_ROLE_RESEARCH


def current_scoring_model_role() -> str:
    return normalize_model_role(_SCORING_MODEL_ROLE.get())


@contextmanager
def scoring_model_role_context(role: str) -> Iterator[None]:
    """回测加载研究模型；live 路径不要进入本上下文。"""
    token = _SCORING_MODEL_ROLE.set(normalize_model_role(role))
    try:
        yield
    finally:
        _SCORING_MODEL_ROLE.reset(token)


def research_model_path(live_path: str) -> str:
    """``tau_ridge_model.json`` → ``tau_ridge_model_research.json``."""
    root, ext = os.path.splitext(str(live_path or ""))
    if not root:
        return str(live_path or "")
    return f"{root}_research{ext or '.json'}"


def model_fit_id(doc: Optional[Mapping[str, Any]]) -> Optional[str]:
    """该份模型的拟合时间标识（不是启用写入时间）。"""
    if not isinstance(doc, Mapping):
        return None
    for key in ("fitted_at", "source_saved_at", "saved_at"):
        val = doc.get(key)
        if val:
            return str(val)
    nested = doc.get("return_model") or doc.get("model")
    if isinstance(nested, Mapping):
        for key in ("fitted_at", "fitted_as_of"):
            val = nested.get(key)
            if val:
                return str(val)
    # 旧盘只有 promoted_at：退化为启用时间，避免卡头空白
    val = doc.get("promoted_at")
    return str(val) if val else None


def stamp_fitted_at(doc: Dict[str, Any]) -> Dict[str, Any]:
    """拟合产物打 fitted_at；已有则保留（启用不得改标识）。"""
    if not isinstance(doc, dict):
        return doc
    if not doc.get("fitted_at"):
        from core.numbers import now_iso_utc

        existing = model_fit_id(doc)
        doc["fitted_at"] = existing or now_iso_utc()
    return doc


def load_research_promoted_json(live_path: str) -> Optional[Dict[str, Any]]:
    """只读研究套文件；缺文件返回 None（不回退 live）。"""
    import json

    path = research_model_path(live_path)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return None
    if isinstance(doc, dict) and isinstance(doc.get("return_model"), dict):
        return doc
    return None


def research_sidecar_flags(live_path: str) -> Dict[str, Any]:
    """研究套 sidecar 是否已启用（不回退 live）。"""
    path = research_model_path(live_path)
    doc = load_research_promoted_json(live_path)
    return {
        "research_exists": bool(doc),
        "research_path": path,
        "research_promoted_at": (doc or {}).get("promoted_at") if doc else None,
        "research_fitted_at": model_fit_id(doc) if doc else None,
    }


def calendar_dates_from_stock_bars(stock_bars: Sequence[Any]) -> List[str]:
    """观察池日线日历：各 Ridge 头共用，避免标签窗短的头把 Holdout 窗口平移。"""
    days = set()
    for item in stock_bars or []:
        if not isinstance(item, dict):
            continue
        for bar in item.get("bars") or []:
            if not isinstance(bar, dict):
                continue
            d = str(bar.get("date") or bar.get("trade_date") or "")[:10]
            if len(d) >= 10:
                days.add(d)
    return sorted(days)


def split_by_holdout_days(
    dates: Sequence[str],
    *,
    holdout_trading_days: int = DEFAULT_HOLDOUT_TRADING_DAYS,
    label_horizon_days: int = 0,
    calendar_dates: Optional[Sequence[str]] = None,
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """按唯一交易日切分：截止日前全训，近 N 日全测。同日多行不拆到两侧。

    ``label_horizon_days=h``：标签在决策日 +h 才实现，再从训练集末尾
    隔离 h 个交易日，避免训练 y 落进评估窗。embargo 日不进训也不进测。

    ``calendar_dates``：共用交易日历（通常来自日线）。缺省用行日期。
    隔夜等标签较短的面板仍对齐同一 eval_start。
    """
    n = len(dates)
    requested = max(1, int(holdout_trading_days or DEFAULT_HOLDOUT_TRADING_DAYS))
    hold_n = requested
    h = max(0, int(label_horizon_days or 0))
    row_days = [str(d)[:10] for d in dates]
    uniq = sorted({d for d in row_days if d})
    if calendar_dates is not None:
        cal = sorted({str(d)[:10] for d in calendar_dates if str(d)[:10]})
        if len(cal) >= 4:
            uniq = cal
    empty_meta = {
        "fit_end": None,
        "eval_start": None,
        "holdout_trading_days": hold_n,
        "label_horizon_days": h,
        "n_train_days": 0,
        "n_test_days": 0,
        "train_days": [],
        "test_days": [],
        "embargo_days": [],
        "split_mode": None,
        "requested_holdout_trading_days": requested,
    }
    if n < 4 or len(uniq) < 4:
        all_idx = list(range(n))
        return all_idx, [], dict(empty_meta)

    hold_n = min(hold_n, max(1, len(uniq) - 3))
    eval_start = uniq[-hold_n]
    try:
        idx_d = uniq.index(eval_start)
    except ValueError:
        idx_d = max(0, len(uniq) - hold_n)
    embargo_n = min(h, max(0, idx_d - 3))
    train_end_idx = max(0, idx_d - embargo_n)
    train_days = uniq[:train_end_idx]
    embargo_days = uniq[train_end_idx:idx_d]
    test_days = uniq[idx_d:]
    if len(train_days) < 3 or len(test_days) < 1:
        cut = max(3, len(uniq) - hold_n)
        train_days = uniq[:cut]
        embargo_days = []
        test_days = uniq[cut:]
        eval_start = test_days[0] if test_days else None

    train_set = set(train_days)
    test_set = set(test_days)
    train_idx = [i for i, d in enumerate(row_days) if d in train_set]
    test_idx = [i for i, d in enumerate(row_days) if d in test_set]
    fit_end = train_days[-1] if train_days else None
    meta = {
        "fit_end": fit_end,
        "eval_start": eval_start,
        "holdout_trading_days": hold_n,
        "label_horizon_days": h,
        "n_train_days": len(train_days),
        "n_test_days": len(test_days),
        "train_days": list(train_days),
        "test_days": list(test_days),
        "embargo_days": list(embargo_days),
        "split_mode": "holdout_days",
        "requested_holdout_trading_days": requested,
    }
    if len(train_idx) < 4:
        return list(range(n)), [], dict(empty_meta)
    return train_idx, test_idx, meta


def resolve_ridge_split(
    dates: Sequence[str],
    *,
    holdout_trading_days: int = DEFAULT_HOLDOUT_TRADING_DAYS,
    label_horizon_days: int = 0,
    calendar_dates: Optional[Sequence[str]] = None,
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """近 N 交易日 Holdout。切不出评估窗则全训、不测，不回退时间比例。"""
    train_idx, test_idx, split_meta = split_by_holdout_days(
        dates,
        holdout_trading_days=holdout_trading_days,
        label_horizon_days=label_horizon_days,
        calendar_dates=calendar_dates,
    )
    pack = dict(split_meta)
    if test_idx or pack.get("eval_start"):
        pack.setdefault("split_mode", "holdout_days")
    return train_idx, test_idx, pack


def make_research_model(train_fit: Dict[str, Any]) -> Dict[str, Any]:
    """Holdout 前训练集拟合 → 唯一 β（落盘前可暂标 research）。"""
    research = dict(train_fit or {})
    research["model_role"] = MODEL_ROLE_RESEARCH
    return research


def select_fit_return_model(report: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """从拟合/草稿报告取唯一 Holdout 前训 β（多键仅为兼容别名，应同系数）。"""
    if not isinstance(report, Mapping):
        return None
    for key in (
        "return_model_research",
        "return_model",
        "research_model",
        "model",
    ):
        rm = report.get(key)
        if isinstance(rm, dict) and (
            rm.get("coefficients") or rm.get("intercept") is not None
        ):
            return dict(rm)
    return None


def require_research_suite(live_path: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """启用执行前读取研究槽；缺则返回错误包。"""
    import json

    path = research_model_path(live_path)
    doc = load_research_promoted_json(live_path)
    if not isinstance(doc, dict) and path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                doc = raw
        except Exception:
            doc = None
    if isinstance(doc, dict) and (
        isinstance(doc.get("return_model"), dict) or isinstance(doc.get("model"), dict)
    ):
        return doc, {}
    return None, {
        "success": False,
        "error": "无研究套可晋级；请先「启用研究」并通过回测后再启用执行",
        "research_path": path,
        "live_path": live_path,
    }


def stamp_live_suite_doc(
    research_doc: Mapping[str, Any],
    *,
    note: str = "",
) -> Dict[str, Any]:
    """研究槽文档 → 执行槽文档（同 β，改 role / promoted_at）。"""
    from core.numbers import now_iso_utc

    doc = dict(research_doc)
    doc["model_role"] = MODEL_ROLE_LIVE
    doc["promoted_at"] = now_iso_utc()
    doc["promoted_from"] = "research"
    if note:
        doc["note"] = note
    elif not doc.get("note"):
        doc["note"] = "research → live promote"
    rm = doc.get("return_model")
    if isinstance(rm, dict):
        rm = dict(rm)
        rm["model_role"] = MODEL_ROLE_LIVE
        doc["return_model"] = rm
    model = doc.get("model")
    if isinstance(model, dict):
        model = dict(model)
        model["model_role"] = MODEL_ROLE_LIVE
        doc["model"] = model
    # 执行槽不保留第二套键（旧双 β 草稿残留）
    doc.pop("return_model_research", None)
    doc.pop("research_model", None)
    return doc


def attach_holdout_meta(report: Dict[str, Any], meta: Optional[Dict[str, Any]]) -> None:
    pack = dict(meta or {})
    report["fit_end"] = pack.get("fit_end")
    report["eval_start"] = pack.get("eval_start")
    report["holdout_trading_days"] = pack.get("holdout_trading_days")
    if pack.get("label_horizon_days") is not None:
        report["label_horizon_days"] = pack.get("label_horizon_days")
    if pack.get("split_mode") is not None:
        report["split_mode"] = pack.get("split_mode")
    oos = report.get("oos")
    if isinstance(oos, dict):
        oos["fit_end"] = pack.get("fit_end")
        oos["eval_start"] = pack.get("eval_start")
        oos["holdout_trading_days"] = pack.get("holdout_trading_days")
        oos["n_train_days"] = pack.get("n_train_days")
        oos["n_test_days"] = pack.get("n_test_days")
        if pack.get("split_mode") is not None:
            oos["split_mode"] = pack.get("split_mode")


def select_persist_return_model(
    report: Dict[str, Any],
    *,
    role: Optional[str] = None,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """从拟合报告取唯一 Holdout 前训 β（研究/执行系数相同，仅槽位不同）。

    ``role`` 只决定返回的角色标签；β 始终是 Holdout 前训那一套。
    启用执行应走 ``require_research_suite``（研究槽→执行槽），勿用本函数从草稿直接上 live。
    """
    role_n = normalize_model_role(role)
    return role_n, select_fit_return_model(report)
