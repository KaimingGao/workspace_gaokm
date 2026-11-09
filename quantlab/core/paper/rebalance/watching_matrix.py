"""观察池实时算分 + rank_lots 预演/落账。

手动路径：dry_run 预演 → 确认后同算法落账（改 paper 持仓/现金）。

消费路径与拟合/回测同口径：只读已物化会话日 L1/L2。
缺覆盖 → ``MaterializeRequired``（开盘可 ``do_session_open_l2`` 正式补齐）。
禁止昨收回退、禁止消费侧临时拼截面。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Collection, Dict, List, Optional, Sequence, Tuple

from core.paper.rebalance.rank_lots import (
    AUX_YHAT_KEYS,
    _TAU_TIP_PIN,
    _Y_HEAD_FACTOR_N,
    tip_explain_fields,
)

logger = logging.getLogger(__name__)


def _f(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def _ymd(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().replace("Z", "")
    d = s[:10]
    if len(d) == 10 and d[4] == "-" and d[7] == "-":
        return d
    return None


def _open_date_of(
    holding: Optional[dict],
    fallback: Optional[str] = None,
) -> Optional[str]:
    """持仓最早开日（lots.bought_date 优先，避免 ts=墙钟把开日写成今天）。"""
    dates: List[str] = []
    if isinstance(holding, dict):
        try:
            from core.paper.tplus1 import ensure_lots

            ensure_lots(holding)
        except Exception:  # noqa: BLE001
            logger.debug("ensure_lots failed in _open_date_of", exc_info=True)
        lots = holding.get("lots") if isinstance(holding.get("lots"), list) else []
        for lot in lots:
            if not isinstance(lot, dict):
                continue
            d = _ymd(lot.get("bought_date")) or _ymd(lot.get("bought_at"))
            if d:
                dates.append(d)
        if not dates:
            d = _ymd(holding.get("bought_date")) or _ymd(holding.get("bought_at"))
            if d:
                dates.append(d)
    if dates:
        return min(dates)
    return _ymd(fallback)


def _watching_codes(*, include_held: Sequence[str] = ()) -> Tuple[List[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"source": "watching", "n_watch": 0, "n_held_extra": 0}
    name_by_code: Dict[str, str] = {}
    try:
        from core.watching.store import read_watching, refresh_watchlist, watchlist_names_for

        uni = read_watching()
        codes = [str(c).strip() for c in (uni.get("watchlist") or []) if str(c).strip()]
        if not codes:
            refreshed = refresh_watchlist(uni)
            codes = [str(c).strip() for c in (refreshed.get("watchlist") or []) if str(c).strip()]
            uni = refreshed
        meta["n_watch"] = len(codes)
        meta["watching_name"] = uni.get("name")
        try:
            names = watchlist_names_for(uni)
            for c, n in zip(codes, names):
                nn = str(n or "").strip()
                if c and nn and nn != c:
                    name_by_code[c] = nn
        except Exception:  # noqa: BLE001
            logger.debug("watchlist names map skipped", exc_info=True)
    except FileNotFoundError:
        return [], {"error": "watching.json 不存在", "source": "watching"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("read watching failed: %s", exc, exc_info=True)
        return [], {"error": str(exc), "source": "watching"}

    seen = set(codes)
    extra = 0
    for raw in include_held or []:
        c = str(raw or "").strip()
        if c and c not in seen:
            codes.append(c)
            seen.add(c)
            extra += 1
    meta["n_held_extra"] = extra
    meta["n_total"] = len(codes)
    meta["name_by_code"] = name_by_code
    return codes, meta


def _resolve_rank_day_cube(
    items: Sequence[dict],
    *,
    day_cube: Any = None,
    clock: str = "open",
) -> Any:
    """复用已建 Cube；否则按行上湖日 load（内存/盘），再 ensure。

    优先显式 ``day_cube``（open-pit / 会话日立方），避免只靠 remember 键对齐。
    """
    if day_cube is not None:
        return day_cube
    rows = [it for it in (items or []) if isinstance(it, dict)]
    if len(rows) < 2:
        return None
    try:
        from core.research.batch_score import ensure_day_cube_for_items
        from core.research.day_cube import load_day_cube
        from core.research.materialize.coverage import read_cube_codes

        day = _live_rank_asof(rows)
        uh = None
        try:
            uh = read_cube_codes() or None
        except Exception:  # noqa: BLE001
            uh = None
        lookup = list(uh) if uh else [
            str(it.get("stock_code") or "").strip()
            for it in rows
            if str(it.get("stock_code") or "").strip()
        ]
        clk = str(clock or "open")
        if len(day) >= 10 and len(lookup) >= 2:
            hit = load_day_cube(day, lookup, clock=clk)
            if hit is not None:
                return hit
        return ensure_day_cube_for_items(
            rows,
            date=day,
            clock=clk,
            persist=False,
            universe_codes=uh,
        )
    except Exception:  # noqa: BLE001
        logger.debug("resolve rank day_cube failed", exc_info=True)
        return None


def _attach_oo_rank_scores(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
) -> None:
    """ŷ_oo_rank：有已启用模型则写入当日截面 1..n 名次；缺则跳过。不改 y_oo_fuse；过入场后作买序。

    截面 z 只用全池 L2；``score_codes`` 只限制写名次。
    模型 ``train_universe``（``tier_a`` / ``tier``）不影响打分 μ/σ。
    """
    try:
        from core.research.oo_rank_lambdarank import apply_oo_rank_scores, load_oo_rank_model

        # live 调仓只用执行套；缺则跳过，不静默换研究套。
        doc = load_oo_rank_model(prefer_research=False)
        if doc:
            cube = _resolve_rank_day_cube(
                items, day_cube=day_cube, clock=clock
            )
            apply_oo_rank_scores(
                list(items or []),
                model_doc=doc,
                score_codes=score_codes,
                day_cube=cube,
                clock=str(clock or "open"),
            )
    except Exception:  # noqa: BLE001
        # 缺 L2 / open-pit 立方时排序头整列空；勿只打 debug 导致手动表「全是 —」。
        logger.warning("attach live y_oo_rank failed", exc_info=True)


def _attach_tc_rank_scores(
    items: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
) -> None:
    """ŷ_τc_rank：有执行套模型则写入当日截面名次；缺则跳过。宇宙语义同 oo_rank。"""
    try:
        from core.research.tc_rank import apply_tc_rank_scores, load_tc_rank_model

        doc = load_tc_rank_model(prefer_research=False)
        if doc:
            cube = _resolve_rank_day_cube(
                items, day_cube=day_cube, clock=clock
            )
            apply_tc_rank_scores(
                list(items or []),
                model_doc=doc,
                score_codes=score_codes,
                day_cube=cube,
                clock=str(clock or "open"),
            )
    except Exception:  # noqa: BLE001
        logger.warning("attach live y_tc_rank failed", exc_info=True)


# 持仓表 / 盯盘 / 交易记录展示用名次字段。
LIVE_RANK_CORE_KEYS = (
    "y_oo_rank",
    "y_oo_rank_hat",
    "y_oo_rank_n",
    "y_oo_rank_score",
    "predicted_score_oo_rank",
    "y_tc_rank",
    "y_tc_rank_hat",
    "y_tc_rank_n",
    "y_tc_rank_score",
    "predicted_score_tc_rank",
)
# 内存行间可拷 tip 拆解；落盘快照不含（tip 走出分行 / tip_explain）。
LIVE_RANK_TIP_KEYS = (
    "formula_terms_oo_rank",
    "score_formula_terms_oo_rank",
    "formula_terms_tc_rank",
    "score_formula_terms_tc_rank",
)
LIVE_RANK_KEYS = LIVE_RANK_CORE_KEYS + LIVE_RANK_TIP_KEYS


def _live_rank_snapshot_path() -> str:
    from core.paths import LIVE_DIR

    return os.path.join(LIVE_DIR, "live_rank_snapshot.json")


def _rank_slice(item: dict) -> Dict[str, Any]:
    """快照只留核心名次，避免公式拆解把文件胀到数 MB。"""
    out: Dict[str, Any] = {}
    for key in LIVE_RANK_CORE_KEYS:
        if item.get(key) is not None:
            out[key] = item.get(key)
    return out


_RANK_SNAP_CACHE: Dict[str, Any] = {"key": None, "by_code": {}, "meta": {}}


def _clear_live_rank_snap_cache() -> None:
    _RANK_SNAP_CACHE["key"] = None
    _RANK_SNAP_CACHE["by_code"] = {}
    _RANK_SNAP_CACHE["meta"] = {}


def live_rank_universe_fp(codes: Optional[Collection[str]]) -> str:
    """名次截面指纹：可交易档（或观察池）代码排序后 sha1。"""
    import hashlib

    uniq = sorted(
        {str(c).strip() for c in (codes or []) if str(c or "").strip()}
    )
    raw = "\n".join(uniq).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def _resolve_live_rank_pool(
    *, paper: Optional[dict] = None, held: Optional[Sequence[str]] = None
) -> Tuple[List[str], Optional[Collection[str]], List[str], Dict[str, Any]]:
    """解析名次池。返回 (pool_codes, tradable_allow, held, watching_meta)。

    ``tradable_allow`` 为 A 档可交易集；无闸时为 None（整池）。
    """
    paper = paper if isinstance(paper, dict) else {}
    if held is None:
        held_list = [
            str(h.get("stock_code") or "").strip()
            for h in (paper.get("holdings") or [])
            if isinstance(h, dict) and str(h.get("stock_code") or "").strip()
        ]
    else:
        held_list = [str(c).strip() for c in held if str(c or "").strip()]
    try:
        codes, meta = _watching_codes(include_held=held_list)
    except Exception:  # noqa: BLE001
        logger.debug("resolve live rank pool: watching read failed", exc_info=True)
        return [], None, held_list, {}
    meta = meta if isinstance(meta, dict) else {}
    if not codes:
        return [], None, held_list, meta
    n_watch = int(meta.get("n_watch") or 0)
    fp_codes = codes[:n_watch] if n_watch > 0 else codes
    allow: Optional[Collection[str]] = None
    try:
        _, fit_meta = _apply_universe_fit_tier_filter(
            codes, keep=held_list, pool_codes_for_fp=fp_codes
        )
        raw_allow = fit_meta.get("tradable_codes")
        if isinstance(raw_allow, (list, tuple, set)):
            allow = [
                str(c).strip() for c in raw_allow if str(c or "").strip()
            ]
    except Exception:  # noqa: BLE001
        logger.debug("resolve live rank pool: tier filter failed", exc_info=True)
        allow = None
    return list(codes), allow, held_list, meta


def current_live_rank_universe(
    *, paper: Optional[dict] = None
) -> Tuple[List[str], str]:
    """当前应用名次截面：优先 A 档可交易，否则整观察池。返回 (codes, fp)。"""
    codes, allow, _held, meta = _resolve_live_rank_pool(paper=paper)
    if allow:
        uni = list(allow)
    elif codes:
        n_watch = int((meta or {}).get("n_watch") or 0)
        uni = codes[:n_watch] if n_watch > 0 else list(codes)
    else:
        uni = []
    return uni, live_rank_universe_fp(uni)


def _live_rank_coverage_ok(n_ranked: int, n_universe: int) -> bool:
    """名次覆盖相对可交易集是否够用（与落盘闸同一阈值）。

    大池：至少 max(8, 50% universe)；小池（<8）：要求全覆盖。
    """
    if n_ranked <= 0:
        return False
    if n_universe <= 0:
        return n_ranked >= 8
    if n_universe < 8:
        return n_ranked >= n_universe
    return n_ranked >= max(8, int(0.5 * n_universe))


def save_live_rank_snapshot(
    items: Sequence[dict],
    *,
    as_of: str,
    universe_codes: Optional[Collection[str]] = None,
    clock: str = "open",
) -> bool:
    """把当日观察池名次落到 live 快照，供持仓表在未重算全池时读取。

    写入 ``universe_fp``：观察池/可交易档变更后旧快照自动失效。
    覆盖过窄时不写盘，返回 False。
    """
    import json
    import os

    day = str(as_of or "")[:10]
    if len(day) < 10:
        return False
    by_code: Dict[str, Any] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        code = str(it.get("stock_code") or "").strip()
        if not code:
            continue
        packed = _rank_slice(it)
        if packed:
            by_code[code] = packed
    if not by_code:
        return False
    if universe_codes is None:
        uni_codes = list(by_code.keys())
    else:
        uni_codes = [
            str(c).strip() for c in universe_codes if str(c or "").strip()
        ] or list(by_code.keys())
    n_uni = len(uni_codes)
    n_ranked = len(by_code)
    if not _live_rank_coverage_ok(n_ranked, n_uni):
        logger.info(
            "skip save live ranks: ranked=%s universe=%s",
            n_ranked,
            n_uni,
        )
        return False
    uni_fp = live_rank_universe_fp(uni_codes)
    payload: Dict[str, Any] = {
        "as_of": day,
        "by_code": by_code,
        "universe_fp": uni_fp,
        "n_universe": n_uni,
        "n_ranked": n_ranked,
        "clock": str(clock or "open"),
    }
    try:
        from core.signal.score_display import json_safe

        payload = json_safe(payload)
    except Exception:  # noqa: BLE001
        pass
    path = _live_rank_snapshot_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        logger.debug("save live rank snapshot failed", exc_info=True)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False
    _clear_live_rank_snap_cache()
    return True


def invalidate_live_rank_snapshot() -> None:
    """观察池/分档变更后丢掉名次快照，避免持仓表继续读旧截面。"""
    path = _live_rank_snapshot_path()
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        logger.debug("invalidate live rank snapshot remove failed", exc_info=True)
    _clear_live_rank_snap_cache()


def load_live_rank_snapshot(
    *,
    as_of: str,
    require_universe_match: bool = True,
    paper: Optional[dict] = None,
) -> Dict[str, Dict[str, Any]]:
    """读当日名次快照。默认校验 ``universe_fp`` 与当前观察池/可交易档一致。

    另校验覆盖：``n_ranked`` 相对 ``n_universe`` 过窄时当作无效。
    """
    import json

    day = str(as_of or "")[:10]
    if len(day) < 10:
        return {}
    path = _live_rank_snapshot_path()
    try:
        st = os.stat(path)
    except OSError:
        return {}
    expect_fp = ""
    if require_universe_match:
        try:
            _uni, expect_fp = current_live_rank_universe(paper=paper)
        except Exception:  # noqa: BLE001
            logger.debug("live rank expected universe fp failed", exc_info=True)
            expect_fp = ""
    key = (
        path,
        getattr(st, "st_mtime_ns", st.st_mtime),
        st.st_size,
        day,
        expect_fp if require_universe_match else "",
    )
    if _RANK_SNAP_CACHE.get("key") == key:
        cached = _RANK_SNAP_CACHE.get("by_code")
        return cached if isinstance(cached, dict) else {}

    def _empty() -> Dict[str, Dict[str, Any]]:
        _RANK_SNAP_CACHE["key"] = key
        _RANK_SNAP_CACHE["by_code"] = {}
        _RANK_SNAP_CACHE["meta"] = {}
        return {}

    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError, TypeError):
        return _empty()
    if not isinstance(doc, dict) or str(doc.get("as_of") or "")[:10] != day:
        return _empty()
    if require_universe_match:
        got_fp = str(doc.get("universe_fp") or "").strip()
        # 无指纹的旧快照、或与当前观察池不一致 → 当作无效，迫使重算。
        if not got_fp or not expect_fp or got_fp != expect_fp:
            return _empty()
    raw = doc.get("by_code")
    if not isinstance(raw, dict):
        return _empty()
    out: Dict[str, Dict[str, Any]] = {}
    for code, row in raw.items():
        c = str(code or "").strip()
        if not c or not isinstance(row, dict):
            continue
        # 兼容旧快照：读入时剥掉公式拆解，避免缓存/回写继续膨胀。
        slim = {k: row[k] for k in LIVE_RANK_CORE_KEYS if row.get(k) is not None}
        if slim:
            out[c] = slim
    try:
        n_uni = int(doc.get("n_universe") or 0)
    except (TypeError, ValueError):
        n_uni = 0
    if n_uni <= 0:
        n_uni = len(out)
    if require_universe_match and not _live_rank_coverage_ok(len(out), n_uni):
        logger.info(
            "reject live rank snapshot: ranked=%s universe=%s",
            len(out),
            n_uni,
        )
        return _empty()
    _RANK_SNAP_CACHE["key"] = key
    _RANK_SNAP_CACHE["by_code"] = out
    _RANK_SNAP_CACHE["meta"] = {
        "universe_fp": doc.get("universe_fp"),
        "n_universe": doc.get("n_universe"),
        "clock": doc.get("clock"),
    }
    return out


def ensure_live_rank_snapshot(
    *,
    as_of: str,
    paper: Optional[dict] = None,
    heal: bool = True,
    need_codes: Optional[Collection[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """读当日快照；缺/失效、或缺指定代码时可选会话日立方自愈。

    自愈只走会话日官方立方（快路径）。缺立方不临时补数；完整截面待 L2 物化后
    由手动执行 / ``refresh_live_open_ranks`` 写入。
    """
    day = str(as_of or "")[:10]
    if len(day) < 10:
        return {}
    snap = load_live_rank_snapshot(as_of=day, paper=paper)
    want = [
        str(c).strip() for c in (need_codes or []) if str(c or "").strip()
    ]
    missing = [c for c in want if c not in snap] if snap else want
    if (snap and not missing) or not heal:
        return snap
    try:
        out = refresh_live_open_ranks(paper=paper)
    except Exception:  # noqa: BLE001
        logger.debug("ensure live rank snapshot refresh failed", exc_info=True)
        return snap
    if not isinstance(out, dict) or not out.get("ok"):
        return snap
    return load_live_rank_snapshot(as_of=day, paper=paper)


def _copy_rank_fields(dst: dict, src: dict, *, overwrite: bool) -> bool:
    wrote = False
    for key in LIVE_RANK_KEYS:
        val = src.get(key)
        if val is None:
            continue
        if not overwrite and dst.get(key) is not None:
            continue
        dst[key] = val
        wrote = True
    return wrote


def _lake_cover_ok(n_rows: int, n_codes: int) -> bool:
    if n_rows < 2 or n_codes < 2:
        return False
    if n_codes <= 12:
        return n_rows >= n_codes - 1
    return n_rows >= 8 and n_rows >= int(0.5 * n_codes)


def _feature_overlap_ok(sample: dict, names: Sequence[str]) -> bool:
    cols = [str(n) for n in names if str(n or "").strip()]
    if not cols or not isinstance(sample, dict):
        return False
    hit = sum(1 for name in cols if sample.get(name) is not None)
    return hit >= max(4, int(0.4 * len(cols)))


def _live_rank_asof(items: Optional[Sequence[dict]] = None) -> str:
    """Live 名次日：优先行上会话日 ``lake_score_day`` / ``as_of``，否则 ŷ_oo 周期日。

    忽略仍带 ``lake_score_fallback_from`` 的旧行（历史脏数据），名次钉会话日。
    """
    for it in items or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("lake_score_fallback_from") or "")[:10]:
            continue
        day = str(it.get("lake_score_day") or it.get("as_of") or "")[:10]
        if len(day) >= 10:
            return day
    try:
        from core.signal.session_pit import oo_cycle_date

        day = str(oo_cycle_date() or "")[:10]
        if len(day) >= 10:
            return day
    except Exception:  # noqa: BLE001
        logger.debug("live rank asof oo_cycle_date failed", exc_info=True)
    from datetime import date

    return date.today().isoformat()


def _stamp_ranks_from_lake(focus: Sequence[dict]) -> None:
    """用 Feature Lake 的 oo_rank / tc_rank 行编观察池名次，写回 focus。

    只用行上 / 周期日的**会话日**湖行；会话日不够则留空，禁止静默回退昨收名次。
    只补 focus 上还没有的键。特征与模型对不上则跳过该头。
    """
    from core.research.feature_lake import load_day_rows
    from core.research.lake_bridge import FAMILY_OO_RANK, FAMILY_TC_RANK

    held = [
        str(it.get("stock_code") or "").strip()
        for it in focus
        if isinstance(it, dict) and str(it.get("stock_code") or "").strip()
    ]
    if not held:
        return
    codes, _meta = _watching_codes(include_held=held)
    if len(codes) < 2:
        return
    day = _live_rank_asof(focus)
    if len(day) < 10:
        return
    oo_rows = load_day_rows(FAMILY_OO_RANK, codes, day) or {}
    tc_rows = load_day_rows(FAMILY_TC_RANK, codes, day) or {}
    if not (
        _lake_cover_ok(len(oo_rows), len(codes))
        or _lake_cover_ok(len(tc_rows), len(codes))
    ):
        return
    oo_ok = _lake_cover_ok(len(oo_rows), len(codes))
    tc_ok = _lake_cover_ok(len(tc_rows), len(codes))
    if oo_ok:
        try:
            from core.research.oo_rank_lambdarank import (
                _oo_rank_feature_names,
                _resolve_oo_rank_fit,
                load_oo_rank_model,
            )

            doc = load_oo_rank_model(prefer_research=False)
            model = _resolve_oo_rank_fit(model_doc=doc)
            names = _oo_rank_feature_names(model) if isinstance(model, dict) else []
            sample = next(iter(oo_rows.values()), {})
            if not _feature_overlap_ok(sample, names):
                oo_ok = False
        except Exception:  # noqa: BLE001
            logger.debug("oo_rank lake overlap check failed", exc_info=True)
            oo_ok = False
    if tc_ok:
        try:
            from core.research.oo_rank_lambdarank import _oo_rank_feature_names
            from core.research.tc_rank import _resolve_tc_rank_fit, load_tc_rank_model

            doc = load_tc_rank_model(prefer_research=False)
            model = _resolve_tc_rank_fit(doc)
            names = _oo_rank_feature_names(model) if isinstance(model, dict) else []
            sample = next(iter(tc_rows.values()), {})
            if not _feature_overlap_ok(sample, names):
                tc_ok = False
        except Exception:  # noqa: BLE001
            logger.debug("tc_rank lake overlap check failed", exc_info=True)
            tc_ok = False
    if not oo_ok and not tc_ok:
        return
    n_watch = int((_meta or {}).get("n_watch") or 0) if isinstance(_meta, dict) else 0
    fp_codes = codes[:n_watch] if n_watch > 0 else codes
    _, fit_meta = _apply_universe_fit_tier_filter(
        codes, keep=held, pool_codes_for_fp=fp_codes
    )
    if fit_meta.get("gate_ok") is False:
        logger.debug(
            "stamp ranks skip: tier gate rejected (%s)",
            fit_meta.get("gate_error") or "gate_ok=False",
        )
        return
    score_codes = fit_meta.get("tradable_codes")
    if not isinstance(score_codes, (list, tuple, set)):
        score_codes = None
    batch: List[dict] = []
    for code in codes:
        row: Dict[str, Any] = {"stock_code": code, "lake_score_day": day}
        if oo_ok and isinstance(oo_rows.get(code), dict):
            row["sub_scores"] = dict(oo_rows[code])
        if tc_ok and isinstance(tc_rows.get(code), dict):
            row["features_tau"] = dict(tc_rows[code])
        if row.get("sub_scores") or row.get("features_tau"):
            batch.append(row)
    if len(batch) < 2:
        return
    if oo_ok:
        _attach_oo_rank_scores(batch, score_codes=score_codes, clock="open")
    if tc_ok:
        _attach_tc_rank_scores(batch, score_codes=score_codes, clock="open")
    by_code = {
        str(it.get("stock_code") or "").strip(): it
        for it in batch
        if str(it.get("stock_code") or "").strip()
    }
    for it in focus:
        if not isinstance(it, dict):
            continue
        src = by_code.get(str(it.get("stock_code") or "").strip())
        if isinstance(src, dict):
            _copy_rank_fields(it, src, overwrite=False)


def stamp_live_ranks(
    focus: Sequence[dict],
    *,
    paper: Optional[dict] = None,
    heal: bool = True,
) -> None:
    """给已打分的持仓行补 oo_rank / τc_rank。

    先用**同一会话日**调仓快照；缺/失效/缺码时可 DayCube 自愈重写；再试同一日 Feature Lake。
    禁止回退到昨日快照/湖行（避免展示错日名次）。两侧都没有则留空（—）。
    """
    rows = [it for it in focus or [] if isinstance(it, dict)]
    if not rows:
        return
    asof = _live_rank_asof(rows)
    if len(asof) >= 10:
        need = [
            str(it.get("stock_code") or "").strip()
            for it in rows
            if str(it.get("stock_code") or "").strip()
            and (it.get("y_oo_rank") is None or it.get("y_tc_rank") is None)
        ]
        snap = ensure_live_rank_snapshot(
            as_of=asof, paper=paper, heal=heal, need_codes=need or None
        )
        if snap:
            for it in rows:
                src = snap.get(str(it.get("stock_code") or "").strip())
                if isinstance(src, dict):
                    _copy_rank_fields(it, src, overwrite=False)
    pending = [
        it
        for it in rows
        if it.get("y_oo_rank") is None or it.get("y_tc_rank") is None
    ]
    if not pending:
        return
    try:
        _stamp_ranks_from_lake(pending)
    except Exception:  # noqa: BLE001
        logger.debug("stamp live ranks from lake failed", exc_info=True)


def _ranked_name_count(rows: Sequence[dict]) -> int:
    n = 0
    for it in rows or []:
        if not isinstance(it, dict):
            continue
        if it.get("y_oo_rank") is not None or it.get("y_tc_rank") is not None:
            n += 1
    return n


def _live_rank_persist_ok(
    rows: Sequence[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
) -> bool:
    """截面过窄时不落盘，避免持仓表分母被写成 2 之类的残缺快照。"""
    n_ranked = _ranked_name_count(rows)
    if score_codes is None:
        return n_ranked >= 8
    expect = len({str(c).strip() for c in score_codes if str(c or "").strip()})
    return _live_rank_coverage_ok(n_ranked, expect)


def _live_rank_session_day_ok(rows: Sequence[dict], *, as_of: str) -> bool:
    """名次快照只接受会话日开盘截面；昨收/补洞回退不得落成「今日」快照。"""
    day = str(as_of or "")[:10]
    if len(day) < 10:
        return False
    n_ranked = 0
    for it in rows or []:
        if not isinstance(it, dict):
            continue
        if it.get("y_oo_rank") is None and it.get("y_tc_rank") is None:
            continue
        n_ranked += 1
        if str(it.get("lake_score_fallback_from") or "")[:10]:
            return False
        lake_day = str(it.get("lake_score_day") or "")[:10]
        if len(lake_day) >= 10 and lake_day != day:
            return False
    return n_ranked > 0


def attach_and_persist_live_ranks(
    scored: Sequence[dict],
    *,
    paper: Optional[dict] = None,
    score_codes: Optional[Collection[str]] = None,
    day_cube: Any = None,
    clock: str = "open",
    persist: bool = True,
) -> None:
    """对已出分池编 oo_rank / τc_rank，并落当日 live 快照。

    与手动执行同口径时用 ``clock="open"``。持仓表读这份快照，故手动 /
    持仓出分都须刷新，避免分母卡在旧截面（如 484）。
    出分池相对可交易集过窄、或非会话日回退截面时只写行内名次、不覆盖快照。
    """
    rows = [it for it in scored or [] if isinstance(it, dict)]
    if not rows:
        return
    allow = score_codes
    if allow is None and paper is not None:
        held = [
            str(it.get("stock_code") or "").strip()
            for it in rows
            if str(it.get("stock_code") or "").strip()
        ]
        _codes, allow, _held, _meta = _resolve_live_rank_pool(
            paper=paper, held=held
        )
    clk = str(clock or "open")
    _attach_oo_rank_scores(
        rows, score_codes=allow, day_cube=day_cube, clock=clk
    )
    _attach_tc_rank_scores(
        rows, score_codes=allow, day_cube=day_cube, clock=clk
    )
    if not persist:
        return
    if not _live_rank_persist_ok(rows, score_codes=allow):
        logger.info(
            "skip persist live ranks: ranked=%s expect=%s",
            _ranked_name_count(rows),
            None if allow is None else len(list(allow)),
        )
        return
    asof = _live_rank_asof(rows)
    if not _live_rank_session_day_ok(rows, as_of=asof):
        logger.info(
            "skip persist live ranks: non-session score day as_of=%s",
            asof,
        )
        return
    try:
        uni_codes = (
            list(allow)
            if allow is not None
            else [str(it.get("stock_code") or "").strip() for it in rows]
        )
        save_live_rank_snapshot(
            rows,
            as_of=asof,
            universe_codes=uni_codes,
            clock=clk,
        )
    except Exception:  # noqa: BLE001
        logger.debug("save live rank snapshot failed", exc_info=True)


def refresh_live_open_ranks(*, paper: Optional[dict] = None) -> Dict[str, Any]:
    """用会话日官方 open 立方编名次并落快照。

    只读已物化 L2；缺立方返回 ``open_session_unavailable``，不临时拼截面。
    **禁止**用昨收立方冒充今日快照。
    """
    from core.research.materialize.coverage import read_cube_codes

    codes, allow, held, _meta = _resolve_live_rank_pool(paper=paper)
    if not codes:
        return {"ok": False, "error": "empty_pool"}
    if allow is not None:
        want = list(
            dict.fromkeys(
                [str(c).strip() for c in allow if str(c or "").strip()] + held
            )
        )
    else:
        want = list(codes)
    cube_codes = read_cube_codes() or want
    scored_pack = _try_score_pool_from_cube(
        want,
        clock="open",
        cube_codes=cube_codes,
        score_codes=allow,
        paper=paper,
    )
    if scored_pack is None:
        from core.research.materialize.session_open import (
            session_open_materialize_request,
        )
        from core.signal.session_pit import oo_cycle_date

        day = str(oo_cycle_date() or "")[:10]
        return {
            "ok": False,
            "error": "open_session_unavailable",
            "detail": f"会话日 {day or '?'} 开盘 L2 不可用，请先补齐当日开盘 L2",
            "error_code": "materialize_required",
            "layer": "l2",
            "hint": "补齐当日开盘 L2",
            "cycle_day": day or None,
            "request": session_open_materialize_request(day),
        }
    scored, _rejected, cube = scored_pack
    attach_and_persist_live_ranks(
        scored,
        score_codes=allow,
        day_cube=cube,
        clock="open",
        persist=True,
    )
    return {
        "ok": True,
        "n_scored": len(scored),
        "n_ranked": _ranked_name_count(scored),
        "n_tradable": None if allow is None else len(list(allow)),
    }


def _apply_universe_fit_tier_filter(
    codes: Sequence[str],
    *,
    keep: Sequence[str] = (),
    pool_codes_for_fp: Optional[Sequence[str]] = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """可预测性分档 live 闸的元数据。

    截面 z 用调用方传入的整段观察池（调用方勿用返回列表缩小打分池）。
    ``tradable_codes``：允许写 ŷ/名次并开仓；``force_exit_held``：掉档持仓硬清仓。
    无 active / 闸失败时 ``tradable_codes`` 为 None（全员打分 vs 禁止套用）。
    ``pool_codes_for_fp``：指纹只对观察池（不含持仓并入票）。
    """
    from core.research.predictability_tiers import filter_codes_by_predictability_live

    return filter_codes_by_predictability_live(
        codes, keep=keep, pool_codes_for_fp=pool_codes_for_fp
    )


def _mark_predictability_tier_exits(
    item_by_code: Dict[str, dict],
    *,
    force_exit_held: Sequence[str],
    allowed_tiers: Sequence[str],
) -> None:
    """持仓掉出 live 允许档 → hard_reject，rank_lots 清仓。"""
    exits = [str(c).strip() for c in (force_exit_held or []) if str(c).strip()]
    if not exits:
        return
    allow = [
        str(t).strip().upper()
        for t in (allowed_tiers or [])
        if str(t or "").strip()
    ]
    label = "".join(allow) if allow else "A"
    why = f"可预测性非{label}档 清仓"
    for code in exits:
        item = item_by_code.get(code)
        if not isinstance(item, dict):
            item = {"stock_code": code}
            item_by_code[code] = item
        prev = str(item.get("reject_reason") or "").strip()
        item["hard_reject"] = True
        item["reject_reason"] = f"{prev}；{why}" if prev and prev != why else why
        item["predictability_tier_exit"] = True


def _mark_predictability_tier_blocks(
    item_by_code: Dict[str, dict],
    *,
    tradable_codes: Any,
    allowed_tiers: Sequence[str],
    gate_ok: Any = True,
) -> None:
    """不在允许档、且还没标清仓的名字：留在截面特征里（供 z），禁止新开。

    闸失败（``gate_ok is False``）或 ``tradable_codes is None`` 时不改行——
    与「允许集为空」区分，避免误把全池标成硬拒绝。
    """
    if gate_ok is False:
        return
    if not isinstance(tradable_codes, (list, tuple, set)):
        return
    allow = {str(c).strip() for c in tradable_codes if str(c).strip()}
    label = "".join(
        str(t).strip().upper()
        for t in (allowed_tiers or [])
        if str(t or "").strip()
    ) or "A"
    why = f"可预测性非{label}档 不开仓"
    for code, item in item_by_code.items():
        if not isinstance(item, dict) or code in allow or item.get("hard_reject"):
            continue
        prev = str(item.get("reject_reason") or "").strip()
        item["hard_reject"] = True
        item["reject_reason"] = f"{prev}；{why}" if prev and prev != why else why
        item["predictability_tier_block"] = True


def _resolve_report_name(
    code: str,
    *,
    item: Optional[dict] = None,
    paper: Optional[dict] = None,
    name_by_code: Optional[Dict[str, str]] = None,
) -> str:
    """报告行股票名：signal → 持仓 → 观察池 → a_code_name。"""
    c = str(code or "").strip()
    fallback = ""
    if isinstance(item, dict):
        fallback = str(item.get("stock_name") or "").strip()
    if paper and isinstance(paper.get("holdings"), list):
        for h in paper["holdings"]:
            if not isinstance(h, dict):
                continue
            if str(h.get("stock_code") or "").strip() != c:
                continue
            hn = str(h.get("stock_name") or "").strip()
            if hn and hn != c:
                return hn
            break
    try:
        from core.t0.intraday import resolve_stock_name

        return resolve_stock_name(
            c, fallback=fallback, name_by_code=name_by_code or {}
        ) or fallback or c
    except Exception:  # noqa: BLE001
        logger.debug("resolve_stock_name failed for %s", c, exc_info=True)
        if fallback and fallback != c:
            return fallback
        mapped = (name_by_code or {}).get(c) or ""
        return mapped or fallback or c


def live_rebalance_wants_minute_tau(clock: Optional[str]) -> bool:
    """启动时间晚于 09:30 才用当天 5m 重算 ŷ_τc。

    ≤09:30 与历史回测成交钟 09:30 同口径：开盘 Z，不拉分钟线。
    """
    head = str(clock or "09:30").replace("：", ":").strip()[:5]
    return bool(head) and head > "09:30"


def score_session_from_lake(
    codes: Sequence[str],
    *,
    paper: Optional[dict] = None,
) -> Tuple[List[dict], List[dict]]:
    """观察池并上 ``codes``，读会话日同钟 L1/L2。

    缺覆盖抛 ``MaterializeRequired``；不回退昨收、不临时补数。
    """
    from datetime import datetime

    held = [str(c).strip() for c in codes if str(c or "").strip()]
    uni, _meta = _watching_codes(include_held=held)
    pool = uni or held
    clock = ""
    try:
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        clock = str((get_rank_lot_cfg(paper or {}) or {}).get("live_fill_clock") or "")
    except Exception:  # noqa: BLE001
        logger.debug("live fill clock unavailable", exc_info=True)
        clock = ""
    if len(clock) < 4:
        clock = datetime.now().strftime("%H:%M")
    scored, rejected, _cube = _score_pool_from_lake(
        pool,
        use_minute_tau=live_rebalance_wants_minute_tau(clock),
        tau_clock=clock,
        paper=paper,
    )
    return scored, rejected


def _consumer_score_clock(use_minute_tau: Optional[bool], tau_clock: Optional[str]) -> str:
    """消费路径的 L2 钟：开盘为 ``open``，更晚的成交钟对齐物化网格（``HHMM``）。"""
    if not use_minute_tau:
        return "open"
    from core.research.lake_bridge import normalize_tau_clock

    clock = normalize_tau_clock(str(tau_clock or "open"))
    return "open" if clock == "open" else clock


def _cube_family_feats(feats: Dict[str, float]) -> Tuple[dict, dict]:
    """立方列按模型前缀拆回 oo / τ。裸名不灌进去，避免别的族盖住 canon。"""
    oo: Dict[str, float] = {}
    tau: Dict[str, float] = {}
    for key, val in feats.items():
        if key.startswith("z_oo_") or key.startswith("raw_aoo_"):
            oo[key] = val
        if key.startswith(("raw_atc_", "z_τc_", "z_tc_", "z_τh_")):
            tau[key] = val
    return oo, tau


def _items_from_day_cube(cube: Any, day: str) -> List[dict]:
    """用当日立方的 ``X_raw`` 组装出分行，不再按票打开 L1 全历史。"""
    import numpy as np

    X = np.asarray(getattr(cube, "X_raw", None))
    names = [str(n) for n in (getattr(cube, "names", None) or [])]
    codes = list(getattr(cube, "codes", None) or [])
    if X.ndim != 2 or not names or not codes:
        return []
    n = min(len(codes), int(X.shape[0]))
    p = min(len(names), int(X.shape[1]))
    items: List[dict] = []
    for i in range(n):
        code = str(codes[i] or "").strip()
        if not code:
            continue
        row = X[i, :p]
        finite = np.isfinite(row)
        if not bool(finite.any()):
            continue
        feats = {names[j]: float(row[j]) for j in range(p) if bool(finite[j])}
        oo, tau = _cube_family_feats(feats)
        if not oo and not tau:
            continue
        item: Dict[str, Any] = {
            "stock_code": code,
            "as_of": day,
            "date": day,
            "trade_date": day,
            "success": True,
            "_score_source": "day_cube",
            "lake_score_day": day,
        }
        if oo:
            item["sub_scores"] = oo
        if tau:
            item["features_tau"] = tau
        gap = feats.get("gap_pct")
        if gap is None:
            gap = tau.get("gap_pct")
        if gap is not None:
            item["gap_pct"] = gap
        items.append(item)
    return items


def _cube_z_matrix(cube: Any):
    """当日 raw → 截面 z。缺测、μ 无效或 σ 过小记 0（预测里等于填当天均值）。"""
    import numpy as np

    X = np.asarray(getattr(cube, "X_raw", None), dtype=np.float64)
    mu = np.asarray(getattr(cube, "z_mean", None), dtype=np.float64)
    sd = np.asarray(getattr(cube, "z_std", None), dtype=np.float64)
    if X.ndim != 2 or mu.shape[0] != X.shape[1] or sd.shape[0] != X.shape[1]:
        return None
    valid = np.isfinite(X) & np.isfinite(mu) & np.isfinite(sd) & (sd >= 1e-12)
    Z = np.zeros(X.shape, dtype=np.float64)
    np.subtract(X, mu, out=Z, where=valid)
    np.divide(Z, sd, out=Z, where=valid)
    return Z


def _active_beta_columns(
    names: Sequence[str],
    coefficients: Dict[str, Any],
    active: Sequence[str],
):
    """立方列与 β 对齐。缺列不进点积（等价 z=0）。"""
    import numpy as np

    index = {str(n): i for i, n in enumerate(names)}
    cols: List[int] = []
    betas: List[float] = []
    keys: List[str] = []
    for name in active:
        j = index.get(str(name))
        if j is None:
            continue
        try:
            beta = float(coefficients.get(name))
        except (TypeError, ValueError):
            continue
        if beta != beta:
            continue
        cols.append(j)
        betas.append(beta)
        keys.append(str(name))
    if not cols:
        return None
    return cols, np.asarray(betas, dtype=np.float64), keys


def _dot_head(
    Z: Any,
    names: Sequence[str],
    coefficients: Dict[str, Any],
    intercept: float,
    active: Sequence[str],
):
    """ŷ = intercept + β·z。缺列当 z=0。"""
    import numpy as np

    aligned = _active_beta_columns(names, coefficients, active)
    if aligned is None:
        return None
    cols, beta_arr, _keys = aligned
    pred = float(intercept) + Z[:, cols] @ beta_arr
    return np.round(pred, 6)


def _term_pinned(key: str, pin_keys: Sequence[str]) -> bool:
    """裸名或 ``z_τc_gap_pct`` 这类前缀名都算钉住。"""
    if key in pin_keys:
        return True
    return any(key.endswith("_" + pin) for pin in pin_keys if pin)


def _topk_abs_index(abs_c: Any, k: int):
    """每行 |贡献| 最大的 k 列，已按降序。"""
    import numpy as np

    _n, p = abs_c.shape
    kk = min(int(k), p)
    if kk <= 0:
        return np.zeros((abs_c.shape[0], 0), dtype=int)
    if kk < p:
        part = np.argpartition(-abs_c, kth=kk - 1, axis=1)[:, :kk]
        part_abs = np.take_along_axis(abs_c, part, axis=1)
        order = np.argsort(-part_abs, axis=1)
        return np.take_along_axis(part, order, axis=1)
    return np.argsort(-abs_c, axis=1)


def _term_col_index(abs_c: Any, keys: Sequence[str], pin_keys: Optional[Sequence[str]]):
    """Top 因子列。钉住键占名额，贡献再小也留在表里。"""
    import numpy as np

    _n, p = abs_c.shape
    limit = min(_Y_HEAD_FACTOR_N, p)
    pins = [pin for pin in (pin_keys or []) if pin]
    if not pins or p <= limit:
        return _topk_abs_index(abs_c, limit)
    pin_local = [j for j, key in enumerate(keys) if _term_pinned(key, pins)]
    if not pin_local:
        return _topk_abs_index(abs_c, limit)
    pin_set = set(pin_local)
    non = [j for j in range(p) if j not in pin_set]
    budget = max(0, _Y_HEAD_FACTOR_N - len(pin_local))
    pin_arr = np.asarray(pin_local, dtype=int)
    if budget <= 0 or not non:
        pin_abs = abs_c[:, pin_arr]
        order = np.argsort(-pin_abs, axis=1)
        return np.take_along_axis(np.broadcast_to(pin_arr, (abs_c.shape[0], len(pin_local))), order, axis=1)
    non_arr = np.asarray(non, dtype=int)
    top_local = _topk_abs_index(abs_c[:, non_arr], min(budget, len(non)))
    top_non = non_arr[top_local]
    pin_b = np.broadcast_to(pin_arr, (abs_c.shape[0], len(pin_local)))
    combined = np.concatenate([top_non, pin_b], axis=1)
    comb_abs = np.take_along_axis(abs_c, combined, axis=1)
    order = np.argsort(-comb_abs, axis=1)
    return np.take_along_axis(combined, order, axis=1)


def _cube_tip_explains(
    Z: Any,
    names: Sequence[str],
    coefficients: Dict[str, Any],
    intercept: float,
    active: Sequence[str],
    pred: Any,
    *,
    row_ids: Sequence[int],
    score_model_role: str = "",
    total_key: str = "",
    pin_keys: Optional[Sequence[str]] = None,
) -> Dict[int, dict]:
    """按立方 z 拆 β·z，每行只留 |贡献| 最大的因子，供股票表 tip。"""
    import numpy as np

    from core.signal.factors.meta.registry import factor_label

    aligned = _active_beta_columns(names, coefficients, active)
    if aligned is None:
        return {}
    cols, beta_arr, keys = aligned
    Zc = np.asarray(Z[:, cols], dtype=np.float64)
    contrib = Zc * beta_arr
    abs_c = np.abs(contrib)
    top = _term_col_index(abs_c, keys, pin_keys)
    labels = {key: factor_label(key) for key in keys}
    intercept_r = round(float(intercept), 6)
    role = str(score_model_role or "").strip().lower()
    pred_arr = np.asarray(pred, dtype=np.float64)
    out: Dict[int, dict] = {}
    for i in row_ids:
        if i < 0 or i >= len(pred_arr) or i >= contrib.shape[0]:
            continue
        total = float(pred_arr[i])
        if total != total:
            continue
        terms: List[dict] = []
        for j in top[i]:
            jj = int(j)
            z = float(Zc[i, jj])
            c = float(contrib[i, jj])
            if z != z or c != c:
                continue
            key = keys[jj]
            terms.append(
                {
                    "key": key,
                    "label": labels.get(key) or key,
                    "beta": round(float(beta_arr[jj]), 6),
                    "z": round(z, 4),
                    "contrib": round(c, 6),
                }
            )
        if not terms:
            continue
        expl: Dict[str, Any] = {
            "intercept": intercept_r,
            "terms": terms,
            "total": round(total, 6),
        }
        if role in {"research", "live"}:
            expl["score_model_role"] = role
        if total_key:
            expl[total_key] = expl["total"]
        out[i] = expl
    return out


def _attach_cube_formula(
    items: Sequence[dict],
    codes: Sequence[str],
    Z: Any,
    names: Sequence[str],
    coefficients: Dict[str, Any],
    intercept: float,
    active: Sequence[str],
    pred: Any,
    fields: Sequence[str],
    score_codes: Optional[Collection[str]],
    explain_codes: Optional[Collection[str]],
    *,
    score_model_role: str = "",
    total_key: str = "",
    pin_keys: Optional[Sequence[str]] = None,
    stamp_item_role: bool = False,
) -> None:
    """把立方点积的因子拆解写到出分行。``explain_codes`` 给定时只拆这些票。"""
    allow = None
    if score_codes is not None:
        allow = {str(c or "").strip() for c in score_codes if str(c or "").strip()}
    explain = None
    if explain_codes is not None:
        explain = {str(c or "").strip() for c in explain_codes if str(c or "").strip()}
    index = {str(c or "").strip(): i for i, c in enumerate(codes)}
    row_ids: List[int] = []
    for it in items:
        code = str(it.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            continue
        if explain is not None and code not in explain:
            continue
        i = index.get(code)
        if i is None:
            continue
        row_ids.append(i)
    if not row_ids:
        return
    explains = _cube_tip_explains(
        Z,
        names,
        coefficients,
        intercept,
        active,
        pred,
        row_ids=row_ids,
        score_model_role=score_model_role,
        total_key=total_key,
        pin_keys=pin_keys,
    )
    if not explains:
        return
    role = str(score_model_role or "").strip().lower()
    for it in items:
        code = str(it.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            continue
        if explain is not None and code not in explain:
            continue
        i = index.get(code)
        if i is None:
            continue
        expl = explains.get(i)
        if not expl:
            continue
        for key in fields:
            it[key] = expl
        if stamp_item_role and role in {"research", "live"}:
            it["score_model_role"] = role


def _write_head(items: Sequence[dict], codes: Sequence[str], pred: Any, fields: Sequence[str], score_codes: Optional[Collection[str]]) -> None:
    allow = None
    if score_codes is not None:
        allow = {str(c or "").strip() for c in score_codes if str(c or "").strip()}
    index = {str(c or "").strip(): i for i, c in enumerate(codes)}
    for it in items:
        code = str(it.get("stock_code") or "").strip()
        if allow is not None and code not in allow:
            continue
        i = index.get(code)
        if i is None or i >= len(pred):
            continue
        val = float(pred[i])
        if val != val:
            continue
        for key in fields:
            it[key] = val


def _stamp_fusion_on_items(
    items: Sequence[dict],
    paper: Optional[dict] = None,
) -> None:
    """用策略 rank_lots 的 w_oo / w_τc 写 y_oo_fuse。

    缺 paper 时才落到默认 0.6/0.4。数据中心 fuse tip 读这些键，必须和调仓回测同一套权。
    """
    from core.paper.rebalance.path_matrix import get_path_matrix_cfg
    from core.signal.yhat_windows import stamp_window_scores

    cfg = get_path_matrix_cfg(paper=paper)
    for it in items:
        if not isinstance(it, dict):
            continue
        stamped = stamp_window_scores(it, cfg)
        for key in ("y_oo_fuse", "y_oo", "y_τc"):
            if stamped.get(key) is not None:
                it[key] = stamped[key]
        it["fusion_w_oo"] = cfg.get("fusion_w_oo")
        it["fusion_w_oc"] = cfg.get("fusion_w_oc")


def _stamp_cube_head_scores(
    cube: Any,
    items: List[dict],
    *,
    score_codes: Optional[Collection[str]] = None,
    explain_codes: Optional[Collection[str]] = None,
    paper: Optional[dict] = None,
) -> None:
    """用立方矩阵点积写 ŷ_oo / ŷ_τc / fuse，并附因子拆解供股票表 tip。"""
    from core.signal.factors.sum_identity import (
        cs_batch_preds_look_collapsed,
        filter_predict_active_names,
    )

    Z = _cube_z_matrix(cube)
    names = [str(n) for n in (getattr(cube, "names", None) or [])]
    codes = [str(c or "").strip() for c in (getattr(cube, "codes", None) or [])]
    if Z is None or not names or not items:
        return

    def _refuse(head: str, pred: Any, intercept: float) -> None:
        vals = [float(v) for v in pred]
        if cs_batch_preds_look_collapsed(vals, intercept=intercept):
            raise RuntimeError(f"{head} 截面出分拒绝：批 ŷ 塌缩远离 intercept")

    try:
        from core.research.oo_ridge import load_scoring_return_model

        model, _meta = load_scoring_return_model()
    except Exception:  # noqa: BLE001
        logger.debug("cube oo model load failed", exc_info=True)
        model = None
    if model is not None and getattr(model, "coefficients", None):
        active = filter_predict_active_names(model.coefficients.keys(), model.coefficients)
        pred = _dot_head(Z, names, model.coefficients, float(model.intercept or 0.0), active)
        if pred is not None:
            _refuse("oo", pred, float(model.intercept or 0.0))
            _write_head(
                items,
                codes,
                pred,
                ("y_oo", "predicted_score", "predicted_score_oo"),
                score_codes,
            )
            try:
                _attach_cube_formula(
                    items,
                    codes,
                    Z,
                    names,
                    model.coefficients,
                    float(model.intercept or 0.0),
                    active,
                    pred,
                    ("score_formula_terms", "formula_terms"),
                    score_codes,
                    explain_codes,
                    score_model_role=str(getattr(model, "model_role", "") or ""),
                    stamp_item_role=True,
                )
            except Exception:  # noqa: BLE001 — 组成失败不挡 ŷ
                logger.debug("cube oo formula terms skipped", exc_info=True)

    try:
        from core.research.tc_ridge import load_tau_model

        doc = load_tau_model() or {}
        rm = doc.get("return_model") if isinstance(doc, dict) else {}
    except Exception:  # noqa: BLE001
        logger.debug("cube tau model load failed", exc_info=True)
        rm = {}
    if isinstance(rm, dict) and rm.get("coefficients"):
        active = filter_predict_active_names(
            rm.get("active_features") or rm["coefficients"].keys(),
            rm["coefficients"],
        )
        pred = _dot_head(Z, names, rm["coefficients"], float(rm.get("intercept") or 0.0), active)
        if pred is not None:
            _refuse("tau", pred, float(rm.get("intercept") or 0.0))
            _write_head(
                items,
                codes,
                pred,
                ("y_τc", "predicted_score_τc", "y_tau_raw"),
                score_codes,
            )
            try:
                tau_role = str(
                    (doc.get("model_role") if isinstance(doc, dict) else "")
                    or rm.get("model_role")
                    or ""
                )
                _attach_cube_formula(
                    items,
                    codes,
                    Z,
                    names,
                    rm["coefficients"],
                    float(rm.get("intercept") or 0.0),
                    active,
                    pred,
                    ("formula_terms_tau", "score_formula_terms_tau"),
                    score_codes,
                    explain_codes,
                    score_model_role=tau_role,
                    total_key="y_τc",
                    pin_keys=_TAU_TIP_PIN,
                )
            except Exception:  # noqa: BLE001 — 组成失败不挡 ŷ
                logger.debug("cube tau formula terms skipped", exc_info=True)

    try:
        from core.research.feature_standardize import is_cross_section_zscore
        from core.research.horizon_ridge import _doc_backend, load_ridge_model, write_y_hat

        for head in ("t30", "t45", "t60", "t75", "t90"):
            doc = load_ridge_model(head)
            if not doc or not is_cross_section_zscore(doc) or _doc_backend(doc) == "tree":
                continue
            rm = doc.get("return_model") if isinstance(doc.get("return_model"), dict) else doc
            coefs = rm.get("coefficients") if isinstance(rm, dict) else None
            if not isinstance(coefs, dict) or not coefs:
                continue
            active = filter_predict_active_names(
                rm.get("active_features") or coefs.keys(),
                coefs,
            )
            intercept = float(rm.get("intercept") or 0.0)
            pred = _dot_head(Z, names, coefs, intercept, active)
            if pred is None:
                continue
            _refuse(head, pred, intercept)
            allow = None
            if score_codes is not None:
                allow = {str(c or "").strip() for c in score_codes if str(c or "").strip()}
            index = {code: i for i, code in enumerate(codes)}
            for it in items:
                code = str(it.get("stock_code") or "").strip()
                if allow is not None and code not in allow:
                    continue
                i = index.get(code)
                if i is None:
                    continue
                write_y_hat(head, it, float(pred[i]))
    except Exception:  # noqa: BLE001
        logger.debug("cube horizon heads skipped", exc_info=True)

    try:
        _stamp_fusion_on_items(items, paper)
    except Exception:  # noqa: BLE001
        logger.debug("cube fuse stamp failed", exc_info=True)


def _scored_has_lake_fallback(scored: Sequence[dict]) -> bool:
    for it in scored or []:
        if not isinstance(it, dict):
            continue
        fb = str(it.get("lake_score_fallback_from") or "")[:10]
        if len(fb) >= 10:
            return True
    return False


def _try_score_pool_from_cube_on_day(
    codes: Sequence[str],
    *,
    day: str,
    clock: str,
    cube_codes: Optional[Sequence[str]],
    score_codes: Optional[Collection[str]],
    paper: Optional[dict] = None,
    clock_use: Optional[str] = None,
) -> Optional[Tuple[List[dict], List[dict], Any]]:
    """指定日官方立方出分；没有立方返回 None。返回 ``(scored, rejected, cube)``。"""
    from core.research.day_cube import load_day_cube

    day_s = str(day or "")[:10]
    if len(day_s) < 10:
        return None
    lookup = [str(c or "").strip() for c in (cube_codes or []) if str(c or "").strip()]
    if len(lookup) < 2:
        lookup = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if len(lookup) < 2:
        return None
    clk = str(clock_use or clock or "open")
    cube = load_day_cube(day_s, lookup, clock=clk)
    if cube is None:
        return None
    scored_all = _items_from_day_cube(cube, day_s)
    if len(scored_all) < 2:
        return None
    want_n = len([c for c in codes if str(c or "").strip()])
    if want_n >= 2:
        from core.research.materialize.cover_policy import cover_reaches

        # 只写了几只、却挂着整池 hash 的立方不能当成当日截面。
        if not cover_reaches(len(scored_all), want_n):
            logger.info(
                "lake score cube too thin day=%s clock=%s n=%s want=%s",
                day_s,
                clk,
                len(scored_all),
                want_n,
            )
            return None
    _stamp_cube_head_scores(
        cube,
        scored_all,
        score_codes=score_codes,
        explain_codes=codes,
        paper=paper,
    )
    want = [c for c in codes if c]
    want_set = set(want)
    scored = (
        [it for it in scored_all if it.get("stock_code") in want_set]
        if want_set
        else list(scored_all)
    )
    have = {str(it.get("stock_code") or "") for it in scored}
    rejected = [
        {"stock_code": c, "reason": f"L2 缺 {day_s}"} for c in want if c not in have
    ]
    logger.info(
        "lake score from cube day=%s clock=%s n=%s rejected=%s",
        day_s,
        clk,
        len(scored),
        len(rejected),
    )
    return scored, rejected, cube


def _try_score_pool_from_cube(
    codes: Sequence[str],
    *,
    clock: str,
    cube_codes: Optional[Sequence[str]],
    score_codes: Optional[Collection[str]],
    paper: Optional[dict] = None,
) -> Optional[Tuple[List[dict], List[dict], Any]]:
    """会话日官方立方出分。没有立方返回 None。"""
    from core.research.day_cube import snap_clock_to_disk
    from core.signal.session_pit import oo_cycle_date

    lookup = [str(c or "").strip() for c in (cube_codes or []) if str(c or "").strip()]
    if len(lookup) < 2:
        lookup = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if len(lookup) < 2:
        return None
    clock_use = snap_clock_to_disk(lookup, clock)
    if clock_use != clock:
        logger.info("lake score clock %s -> %s (latest cube)", clock, clock_use)
    cycle_day = str(oo_cycle_date() or "")[:10]
    if not cycle_day:
        return None
    return _try_score_pool_from_cube_on_day(
        codes,
        day=cycle_day,
        clock=clock,
        cube_codes=lookup,
        score_codes=score_codes,
        paper=paper,
        clock_use=clock_use,
    )


def _score_pool_from_lake(
    pool: Sequence[str],
    *,
    use_minute_tau: Optional[bool] = None,
    score_codes: Optional[Collection[str]] = None,
    tau_clock: Optional[str] = None,
    paper: Optional[dict] = None,
) -> Tuple[List[dict], List[dict], Any]:
    """调仓出分：只读会话日已物化 L2。

    缺覆盖 → ``MaterializeRequired``（开盘可提示 ``do_session_open_l2``）。
    禁止昨收回退、禁止临时拼截面。第三项为供 rank 复用的 DayCube。
    """
    from core.research.materialize.consumer import MaterializeRequired
    from core.research.materialize.coverage import read_cube_codes
    from core.signal.session_pit import oo_cycle_date

    codes = [str(c or "").strip() for c in pool if str(c or "").strip()]
    clock = _consumer_score_clock(use_minute_tau, tau_clock)
    cube_codes = read_cube_codes()
    cycle_day = str(oo_cycle_date() or "")[:10]

    if cycle_day:
        session_cube = _try_score_pool_from_cube(
            codes,
            clock=clock,
            cube_codes=cube_codes,
            score_codes=score_codes,
            paper=paper,
        )
        if session_cube is not None:
            return session_cube

    if clock == "open":
        from core.research.materialize.session_open import (
            session_open_materialize_request,
        )

        raise MaterializeRequired(
            {
                "error_code": "materialize_required",
                "error": (
                    f"会话日 {cycle_day or '?'} 开盘 L2 不可用，请先补齐当日开盘 L2"
                ),
                "layer": "l2",
                "hint": "补齐当日开盘 L2",
                "clocks": [clock],
                "cycle_day": cycle_day or None,
                "request": session_open_materialize_request(cycle_day or ""),
            }
        )
    raise MaterializeRequired(
        {
            "error_code": "materialize_required",
            "error": (
                f"会话日 {cycle_day or '?'} {clock} L2 不可用，请先完成分钟线物化"
            ),
            "layer": "l2",
            "hint": "分钟线物化",
            "clocks": [clock],
            "cycle_day": cycle_day or None,
        }
    )


def _score_pool(
    codes: Sequence[str],
    *,
    horizon_days: int = 1,
    offline_only: bool = True,
    use_minute_tau: Optional[bool] = None,
    score_codes: Optional[Collection[str]] = None,
    tau_clock: Optional[str] = None,
    paper: Optional[dict] = None,
) -> Tuple[List[dict], List[dict], Any]:
    """观察池并行 score_stock。默认仅本地缓存；``offline_only=False`` 可补远端。

    ``use_minute_tau=False``：开盘 Z，不拉当天 5m。
    ``score_codes`` 给定时，截面 z 用整池特征，ŷ 只写允许档。
    产品调仓打开消费开关后只读会话日 L1/L2。
    第三项为 DayCube（供 rank 显式复用；非消费现算路径常为 None）。
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from core.research.materialize.consumer import MaterializeRequired, consumer_read_only
    from core.signal_service import score_one

    scored: List[dict] = []
    rejected: List[dict] = []
    pool = [str(c or "").strip() for c in codes if str(c or "").strip()]
    if not pool:
        return scored, rejected, None
    if consumer_read_only():
        return _score_pool_from_lake(
            pool,
            use_minute_tau=use_minute_tau,
            score_codes=score_codes,
            tau_clock=tau_clock,
            paper=paper,
        )

    use_offline = bool(offline_only)
    t0 = time.perf_counter()
    try:
        from core.t0.score_policy import (
            build_tau_pool_watching_holdings,
            seed_tau_cross_section_pool,
            set_t0_cs_universe_codes,
            t0_cs_universe_codes,
        )

        uni = t0_cs_universe_codes(*list(pool))
        set_t0_cs_universe_codes(uni)
        seed_tau_cross_section_pool(
            build_tau_pool_watching_holdings(extra_codes=pool)
        )
    except Exception:  # noqa: BLE001
        logger.debug("watching_matrix tau CS seed failed", exc_info=True)
    try:
        from core.signal.live_features import fetch_live_index_bars

        fetch_live_index_bars(market="CN", limit=75, offline_only=use_offline)
    except Exception:  # noqa: BLE001
        logger.debug("warm index bars failed", exc_info=True)

    # batch 截面开启时：单票只抽特征，ŷ 留给池级 DayCube 单遍 predict
    defer_predict = False
    try:
        from core.research.batch_score import enabled as batch_score_enabled

        defer_predict = bool(batch_score_enabled())
    except Exception:  # noqa: BLE001
        defer_predict = False

    def _score_one(code: str) -> Tuple[str, Any]:
        try:
            result = score_one(
                code,
                horizon_days=max(1, int(horizon_days or 1)),
                skip_fundamentals=True,
                skip_sentiment=True,
                offline_only=use_offline,
                quote_timeout=5.0 if use_offline else 8.0,
                use_minute_tau=use_minute_tau,
                defer_cs_predict=defer_predict,
            )
        except Exception as exc:  # noqa: BLE001
            return code, {"success": False, "stock_code": code, "error": str(exc)[:120]}
        return code, result

    workers = max(1, min(8, len(pool)))
    by_code: Dict[str, Any] = {}
    from core.research.materialize.consumer import (
        bind_materialize_writer,
        context_initargs,
    )

    with ThreadPoolExecutor(
        max_workers=workers,
        initializer=bind_materialize_writer,
        initargs=context_initargs(),
    ) as ex:
        futs = {ex.submit(_score_one, c): c for c in pool}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                _, result = fut.result()
                by_code[code] = result
            except Exception as exc:  # noqa: BLE001
                logger.debug("score future failed for %s", code, exc_info=True)
                by_code[code] = {
                    "success": False,
                    "stock_code": code,
                    "error": str(exc)[:120],
                }

    for code in pool:
        result = by_code.get(code)
        if not isinstance(result, dict) or result.get("success") is False:
            rejected.append(
                {
                    "stock_code": code,
                    "reason": (result or {}).get("error") or "score_stock 失败",
                }
            )
            continue
        item = result.get("signal_item") if isinstance(result.get("signal_item"), dict) else result
        if not isinstance(item, dict):
            rejected.append({"stock_code": code, "reason": "无 signal_item"})
            continue
        if item.get("hard_reject"):
            rejected.append(
                {
                    "stock_code": item.get("stock_code") or code,
                    "stock_name": item.get("stock_name"),
                    "reason": item.get("reject_reason") or "hard_reject",
                }
            )
            continue
        if item.get("stock_code") is None:
            item["stock_code"] = code
        scored.append(item)

    try:
        from core.research.batch_score import (
            enabled as batch_score_enabled,
            rescore_fitted_cross_section_batched,
        )
        from core.signal.cross_section_rescore import rescore_fitted_cross_section

        if batch_score_enabled():
            clock = "open"
            if use_minute_tau:
                try:
                    from datetime import datetime

                    clock = f"tau_{datetime.now().strftime('%H%M')}"
                except Exception:  # noqa: BLE001
                    clock = "tau_live"
            # L2 键用完整观察池；score_codes 可能是分档子集
            uh = None
            try:
                from core.research.materialize.coverage import read_cube_codes

                uh = read_cube_codes() or None
            except Exception:  # noqa: BLE001
                uh = None
            rescore_fitted_cross_section_batched(
                scored,
                score_codes=score_codes,
                clock=clock,
                persist_cube=False,
                universe_codes=uh or list(pool),
            )
        else:
            rescore_fitted_cross_section(scored, score_codes=score_codes)
    except MaterializeRequired:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("watching_matrix cross-section rescore failed")
        raise RuntimeError(
            f"watching_matrix 截面 rescore 失败（禁止 raw×β）: {exc}"
        ) from exc

    logger.info(
        "watching_matrix score_pool n=%s scored=%s rejected=%s workers=%s "
        "elapsed=%.1fs offline_only=%s defer_cs=%s",
        len(pool),
        len(scored),
        len(rejected),
        workers,
        time.perf_counter() - t0,
        int(use_offline),
        int(defer_predict),
    )
    return scored, rejected, None


def _live_quote_dict(code: str) -> Optional[dict]:
    try:
        from core.data.facade import get_quote

        q = get_quote(code) or {}
        return q if isinstance(q, dict) else None
    except Exception:  # noqa: BLE001
        logger.debug("live quote failed for %s", code, exc_info=True)
        return None


def _px_from_quote_last(q: dict) -> Optional[float]:
    px = _f(q.get("price_raw"))
    if px is None:
        px = _f(q.get("price"))
    return float(px) if px is not None and px > 0 else None


def _px_from_quote_open(q: dict) -> Optional[float]:
    px = _f(q.get("open_raw"))
    if px is None:
        px = _f(q.get("open"))
    return float(px) if px is not None and px > 0 else None


def _px_from_quote_prev(q: dict) -> Optional[float]:
    for key in ("prev_close", "pre_close", "yesterday_close"):
        px = _f(q.get(key))
        if px is not None and px > 0:
            return float(px)
    return None


def _live_fill_px(code: str) -> Optional[float]:
    """实时成交价：现价；集合竞价未出、现价仍停在昨收则用今开。不退昨收。"""
    q = _live_quote_dict(code)
    if not q:
        return None
    last = _px_from_quote_last(q)
    opx = _px_from_quote_open(q)
    prev = _px_from_quote_prev(q)
    if (
        last is not None
        and prev is not None
        and opx is not None
        and abs(last - prev) < 1e-9
        and abs(opx - prev) > 1e-9
    ):
        return float(opx)
    if last is not None:
        return float(last)
    if opx is not None:
        return float(opx)
    return None


def _bar_close_px(code: str) -> Optional[float]:
    try:
        from core.data.facade import get_bars

        pack = get_bars(
            code,
            limit=5,
            offline_only=True,
            reject_quote_fallback=True,
        ) or {}
        bars = list(pack.get("bars") or [])
        if bars:
            px = _f((bars[-1] or {}).get("close"))
            if px is not None and px > 0:
                return float(px)
    except Exception:  # noqa: BLE001
        logger.debug("cached bar price failed for %s", code, exc_info=True)
    return None


def _quote_px(code: str, *, offline_only: bool = True) -> Optional[float]:
    """调仓成交价。

    ``offline_only=True``：本地日线末收（盘中即昨收，仅离线预演）。
    ``offline_only=False``：实时现价，缺则今开。不把昨收写成成交/成本。
    """
    if not offline_only:
        return _live_fill_px(code)
    return _bar_close_px(code)


def _quote_open_px(code: str, *, offline_only: bool = True) -> Optional[float]:
    """当日开盘：仅实时行情。offline 预演不扣 remaining，避免用昨收/昨开误映射。"""
    if offline_only:
        return None
    q = _live_quote_dict(code)
    return _px_from_quote_open(q) if q else None


_SCORE_PASSTHROUGH_KEYS = (
    "predicted_score",
    "predicted_score_eod",
    "predicted_score_eod_rem",
    "predicted_score_tau",
    "y_oo",
    "y_τc",
    *AUX_YHAT_KEYS,
    "y_oo_fuse",
    "residual",
    "y_oo_fuse_score",
    "gap_pct",
    "open_gap_pct",
    "change_pct",
    "prev_close",
    "last_price",
    "hard_reject",
    "reject_reason",
    "sector",
)


def _score_fields_for_report(item: dict) -> Dict[str, Any]:
    """透传 signal_item 打分字段，供前端 resolve*Score 解析表列。"""
    out: Dict[str, Any] = {}
    if isinstance(item, dict):
        for k in _SCORE_PASSTHROUGH_KEYS:
            if k in item and item.get(k) is not None:
                out[k] = item.get(k)
    return out


def _row_score_payload(
    item: Optional[dict],
    src: Optional[dict] = None,
    cfg: Optional[dict] = None,
    *,
    open_px: Optional[float] = None,
    price_tau: Optional[float] = None,
) -> Dict[str, Any]:
    """表列用 ŷ_oo / ŷ_τc；y_oo_fuse（UI fuse）按头现算。

    计划腿 / 跳过行（``src``）里的原始名次盖过 item，避免历史软先验缩放名次进表。
    """
    from core.paper.rebalance.path_matrix import scores_from_rebalance_item
    from core.paper.rebalance.rank_lots import aux_yhat_fields, y_oo_fuse_pct_of

    item = item if isinstance(item, dict) else {}
    src = src if isinstance(src, dict) else {}
    sc = scores_from_rebalance_item(item, cfg)
    payload = _score_fields_for_report(item)
    for k in ("y_oo", "y_τc", "y_oo_fuse", "residual"):
        if sc.get(k) is not None:
            payload[k] = sc.get(k)
    o = _f(open_px)
    if o is None or o <= 0:
        o = _f(item.get("day_open"))
    p = _f(price_tau)
    if p is None or p <= 0:
        p = _f(src.get("price"))
    yf = y_oo_fuse_pct_of(item, cfg, open_px=o, price_tau=p)
    if yf is None:
        yf = y_oo_fuse_pct_of(src, cfg, open_px=o, price_tau=p)
    if yf is not None:
        payload["y_oo_fuse"] = yf
    rs = src.get("y_oo_fuse_score")
    if rs is not None:
        payload["y_oo_fuse_score"] = rs
    # item 先、计划 dbg（原始名次）后盖，避免缩放名次进手动执行结果表。
    payload.update(aux_yhat_fields(item))
    payload.update(aux_yhat_fields(src))
    payload.update(tip_explain_fields(item))
    payload.update(tip_explain_fields(src))
    return payload


def _apply_one_leg(
    paper: dict,
    leg: dict,
    *,
    as_of: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """按计划价（含成本模型滑点）落地一笔买卖。先卖后买由调用方保证。"""
    from core.paper.costs import (
        annotate_trade,
        apply_fill_price,
        calc_trade_fees,
        cost_params,
        resolve_cost_model,
    )
    from core.paper.ledger import ORIGIN_STRATEGY, _now_iso, merge_origin
    from core.paper.sizing import _lot_shares as lot_sh

    side = str(leg.get("side") or "").strip().lower()
    code = str(leg.get("stock_code") or "").strip()
    try:
        shares = float(leg.get("shares") or 0)
    except (TypeError, ValueError):
        shares = 0.0
    raw_px = _f(leg.get("price"))
    if side not in ("buy", "sell") or not code or shares <= 0 or not raw_px or raw_px <= 0:
        return None, "invalid_leg"

    model = resolve_cost_model(paper)
    params = cost_params(paper)
    fill_px = apply_fill_price(side, float(raw_px), model=model, params=params)
    cash = float(paper.get("cash") or 0)
    holdings = list(paper.get("holdings") or [])
    existing = next((h for h in holdings if str(h.get("stock_code")) == code), None)
    name = leg.get("stock_name") or (existing or {}).get("stock_name")
    note = leg.get("reason") or leg.get("matrix_action") or "watching_matrix"
    rank_fields = {
        k: leg.get(k)
        for k in (
            "y_oo_fuse",
            "y_oo",
            "y_τc",
            "residual",
            "predicted_score_tau",
            "y_oo_fuse_score",
            "reason",
            *AUX_YHAT_KEYS,
            "rank_i",
            "rank_n",
            "lot_kind",
            "action",
        )
        if leg.get(k) is not None
    }
    if str(leg.get("sentiment_basis") or "").strip() == "gate":
        sent_label = str(leg.get("sentiment_label") or "").strip()
        if sent_label and sent_label != "missing":
            rank_fields["sentiment_label"] = sent_label
            rank_fields["sentiment_basis"] = "gate"
    rank_fields.update(tip_explain_fields(leg))
    if as_of:
        rank_fields["as_of"] = str(as_of)[:10]

    if side == "sell":
        have = float((existing or {}).get("shares") or 0)
        from core.paper.tplus1 import TPLUS1_LOCK_REASON, clip_sell_shares, consume_sell_lots

        sell_shares, t1_meta = clip_sell_shares(
            existing or {}, min(shares, have), as_of=as_of
        )
        sell_shares = float(lot_sh(sell_shares) or sell_shares)
        if sell_shares <= 0 or not existing:
            return None, (t1_meta.get("reason") or TPLUS1_LOCK_REASON) if have > 0 else "no_position"
        cost = float(existing.get("cost") or 0)
        amount = round(sell_shares * fill_px, 2)
        fee_info = calc_trade_fees("sell", amount, model=model, params=params)
        pnl_pct = round((fill_px / cost - 1.0) * 100.0, 2) if cost else None
        trade = annotate_trade(
            {
                "ts": _now_iso(),
                "side": "sell",
                "stock_code": code,
                "stock_name": name,
                "shares": sell_shares,
                "price": round(fill_px, 4),
                "amount": amount,
                "open_date": _open_date_of(existing, as_of),
                "pnl_pct": pnl_pct,
                "score": (
                    leg.get("y_oo_fuse")
                    if leg.get("y_oo_fuse") is not None
                    else leg.get("score")
                ),
                "origin": ORIGIN_STRATEGY,
                "note": f"矩阵调仓 · {note}",
                "matrix_action": leg.get("matrix_action"),
                **rank_fields,
            },
            fee_info,
        )
        paper.setdefault("trades", []).append(trade)
        paper["cash"] = round(cash + float(fee_info.get("net_cash_delta") or 0), 2)
        consume_sell_lots(existing, sell_shares)
        if float(existing.get("shares") or 0) <= 1e-6:
            paper["holdings"] = [h for h in holdings if str(h.get("stock_code")) != code]
        paper["updated_at"] = trade["ts"]
        return trade, None

    buy_shares = float(lot_sh(shares) or shares)
    if buy_shares <= 0:
        return None, "lot"
    amount = round(buy_shares * fill_px, 2)
    fee_info = calc_trade_fees("buy", amount, model=model, params=params)
    need = amount + float(fee_info.get("fees") or 0)
    if need > cash + 1e-6:
        return None, "cash"
    try:
        from core.paper.rebalance.rank_lots import get_rank_lot_cfg

        floor = float(get_rank_lot_cfg(paper).get("cash_floor") or 0.0)
    except Exception:  # noqa: BLE001
        logger.debug("cash_floor resolve failed", exc_info=True)
        floor = 0.0
    if cash - need < floor - 1e-6:
        return None, "cash_floor"
    fill_rounded = round(fill_px, 4)
    old_sh = float(existing.get("shares") or 0) if existing else 0.0
    old_cost = float(existing.get("cost") or 0) if existing else 0.0
    trade = annotate_trade(
        {
            "ts": _now_iso(),
            "side": "buy",
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "price": fill_rounded,
            "amount": amount,
            "open_date": _open_date_of(existing, as_of),
            "score": (
                leg.get("y_oo_fuse")
                if leg.get("y_oo_fuse") is not None
                else leg.get("score")
            ),
            "origin": ORIGIN_STRATEGY,
            "note": f"矩阵调仓 · {note}",
            "matrix_action": leg.get("matrix_action"),
            **rank_fields,
        },
        fee_info,
    )
    paper.setdefault("trades", []).append(trade)
    paper["cash"] = round(cash - need, 2)
    from core.paper.tplus1 import add_buy_lot, stamp_new_holding

    if existing:
        new_sh = old_sh + buy_shares
        if new_sh > 0:
            existing["cost"] = round((old_cost * old_sh + fill_px * buy_shares) / new_sh, 4)
        add_buy_lot(existing, buy_shares, ts=trade["ts"], as_of=as_of)
        existing["origin"] = merge_origin(existing.get("origin"), ORIGIN_STRATEGY)
        if name and not existing.get("stock_name"):
            existing["stock_name"] = name
    else:
        row = {
            "stock_code": code,
            "stock_name": name,
            "shares": buy_shares,
            "cost": round(fill_px, 4),
            "bought_at": trade["ts"],
            "origin": ORIGIN_STRATEGY,
        }
        stamp_new_holding(row, ts=trade["ts"], as_of=as_of)
        holdings.append(row)
        paper["holdings"] = holdings
    paper["updated_at"] = trade["ts"]
    return trade, None


def _apply_matrix_trades(
    paper: dict,
    sell_trades: Sequence[dict],
    buy_trades: Sequence[dict],
    *,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """先卖后买落地；失败腿进 apply_skips，不中断其余。"""
    applied_sells: List[dict] = []
    applied_buys: List[dict] = []
    skips: List[dict] = []
    for leg in sell_trades:
        trade, err = _apply_one_leg(paper, leg, as_of=as_of)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "side": "sell",
                    "reason": err or "apply_failed",
                    "tplus1": bool(err and "T+1" in str(err)),
                }
            )
            continue
        applied_sells.append(trade)
    for leg in buy_trades:
        trade, err = _apply_one_leg(paper, leg, as_of=as_of)
        if trade is None:
            skips.append(
                {
                    "stock_code": (leg or {}).get("stock_code"),
                    "stock_name": (leg or {}).get("stock_name"),
                    "side": "buy",
                    "shares": (leg or {}).get("shares"),
                    "price": (leg or {}).get("price"),
                    "amount": (leg or {}).get("amount"),
                    "reason": err or "apply_failed",
                }
            )
            continue
        applied_buys.append(trade)
    return {
        "sell_trades": applied_sells,
        "buy_trades": applied_buys,
        "apply_skips": skips,
    }


def simulate_watching_matrix_preview(
    paper: dict,
    *,
    top_k: Optional[int] = None,
    dry_run: bool = True,
    offline_only: bool = True,
    open_replay: bool = False,
) -> Dict[str, Any]:
    """观察池算分 + rank_lots（y_oo_fuse 融合 ŷ_oo / ŷ_τc · 已保存金额换手）。

    默认 offline 只约束 ŷ；``dry_run=False`` 落账强制现价，避免成本写成昨收。
    ``open_replay=True``：固定 09:30 开盘 Z 与开盘价，丢掉开仓/加仓，不写账。
    """
    from core.paper.ledger import mark_to_market
    from core.paper.rebalance.rank_lots import (
        ACTION_OPEN,
        ACTION_REDUCE,
        get_rank_lot_cfg,
        plan_rank_lot_day,
    )

    rl_cfg = get_rank_lot_cfg(paper, top_k=top_k)
    k = int(rl_cfg["top_k"])
    score_backend = "ridge"
    _backend_ctx = None
    try:
        from core.research.return_tree import (
            normalize_rebalance_score_backend,
            rebalance_score_backend_context,
        )

        score_backend = normalize_rebalance_score_backend(rl_cfg.get("score_backend"))
        _backend_ctx = rebalance_score_backend_context
    except Exception:  # noqa: BLE001
        logger.debug("watching_matrix score_backend failed", exc_info=True)
        score_backend = "ridge"
    rl_cfg["score_backend"] = score_backend

    held_codes = [
        str(h.get("stock_code") or "").strip()
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict) and float(h.get("shares") or 0) > 0
    ]
    codes, pool_meta = _watching_codes(include_held=held_codes)
    name_by_code = (
        pool_meta.get("name_by_code")
        if isinstance(pool_meta.get("name_by_code"), dict)
        else {}
    )
    if pool_meta.get("error"):
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": pool_meta.get("error"),
            "empty_reason": "empty_book",
            "confirm_supported": True,
        }
    if not codes:
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": "观察池为空",
            "empty_reason": "empty_book",
            "confirm_supported": True,
        }
    n_watch = int(pool_meta.get("n_watch") or 0) if isinstance(pool_meta, dict) else 0
    fp_codes = codes[:n_watch] if n_watch > 0 else codes
    _, fit_meta = _apply_universe_fit_tier_filter(
        codes, keep=held_codes, pool_codes_for_fp=fp_codes
    )
    if fit_meta.get("gate_ok") is False:
        return {
            "success": False,
            "ok": False,
            "mode": "watching_matrix",
            "dry_run": bool(dry_run),
            "matrix_mode": True,
            "error": fit_meta.get("gate_error")
            or "分档报告校验失败；请重跑「观察池分档」",
            "fit_tiers": fit_meta,
            "confirm_supported": True,
        }
    score_codes = fit_meta.get("tradable_codes")
    if not isinstance(score_codes, (list, tuple, set)):
        score_codes = None
    if isinstance(pool_meta, dict):
        pool_meta = dict(pool_meta)
        pool_meta["fit_tiers"] = fit_meta
        pool_meta["n_total"] = len(codes)
        if score_codes is not None:
            pool_meta["n_tradable"] = len(score_codes)

    # 与持仓表 / 数据中心同源：signal_config.scoring.horizon_days
    try:
        from core.signal.config import get_scoring_horizon_days

        horizon = int(get_scoring_horizon_days())
    except Exception:  # noqa: BLE001
        logger.debug("get_scoring_horizon_days failed", exc_info=True)
        horizon = 1
    use_offline = bool(offline_only)
    # 手动执行固定复现 09:30 开盘信息集。自动路径仍跟启动时间。
    if open_replay:
        dry_run = True
        use_minute_tau = False
        score_clock = "09:30"
    else:
        # ≤09:30 与回测 09:30 同口径：开盘 Z，不拉当天 5m。更晚的启动时间才吃前缀。
        use_minute_tau = live_rebalance_wants_minute_tau(rl_cfg.get("live_fill_clock"))
        score_clock = rl_cfg.get("live_fill_clock")
    # ŷ / 日K 可只读本地仓；落账必须现价，否则成本会被写成昨收
    price_offline = bool(use_offline and dry_run)
    # 手动预演与落账同口径：缺会话日 L1/L2 只提示物化
    score_day_cube: Any = None
    if _backend_ctx is not None:
        with _backend_ctx(score_backend):
            scored, rejected, score_day_cube = _score_pool(
                codes,
                horizon_days=horizon,
                offline_only=use_offline,
                use_minute_tau=use_minute_tau,
                score_codes=score_codes,
                tau_clock=score_clock,
                paper=paper,
            )
    else:
        scored, rejected, score_day_cube = _score_pool(
            codes,
            horizon_days=horizon,
            offline_only=use_offline,
            use_minute_tau=use_minute_tau,
            score_codes=score_codes,
            tau_clock=score_clock,
            paper=paper,
        )
    if _scored_has_lake_fallback(scored):
        from core.research.materialize.consumer import MaterializeRequired

        from core.research.materialize.session_open import (
            session_open_materialize_request,
        )
        from core.signal.session_pit import oo_cycle_date

        day = str(oo_cycle_date() or "")[:10]
        raise MaterializeRequired(
            {
                "error_code": "materialize_required",
                "error": "出分回退到非会话日，请先补齐当日开盘 L2",
                "layer": "l2",
                "hint": "补齐当日开盘 L2",
                "cycle_day": day or None,
                "request": session_open_materialize_request(day),
            }
        )
    # 手动执行固定 open；自动跟启动时间。两侧都落快照，持仓表才不会卡旧分母。
    rank_clock = "open" if open_replay else _consumer_score_clock(
        use_minute_tau, score_clock
    )
    attach_and_persist_live_ranks(
        scored,
        score_codes=score_codes,
        day_cube=score_day_cube,
        clock=rank_clock,
        persist=True,
    )
    try:
        from core.signal.cross_section_rescore import drop_unscored_heads

        drop_unscored_heads(scored, score_codes)
    except Exception:  # noqa: BLE001
        logger.debug("watching_matrix drop unscored heads failed", exc_info=True)
    summary = mark_to_market(paper) or {}
    equity = float(summary.get("equity") or 0) or 0.0
    cash_before = float(summary.get("cash") or paper.get("cash") or 0)
    cash = cash_before

    item_by_code = {
        str(it.get("stock_code") or "").strip(): it
        for it in scored
        if str(it.get("stock_code") or "").strip()
    }
    rejected_by = {
        str(r.get("stock_code") or "").strip(): r
        for r in (rejected or [])
        if isinstance(r, dict) and str(r.get("stock_code") or "").strip()
    }
    for code in held_codes:
        if code not in item_by_code:
            rej = rejected_by.get(code) or {}
            stub: Dict[str, Any] = {"stock_code": code}
            why = str(rej.get("reason") or "").strip()
            if why:
                stub["hard_reject"] = True
                stub["reject_reason"] = why
            item_by_code[code] = stub
        name = _resolve_report_name(
            code,
            item=item_by_code.get(code),
            paper=paper,
            name_by_code=name_by_code,
        )
        if name:
            item_by_code[code]["stock_name"] = name

    # 缺 L2 / 未进 scored 的持仓仍盖当日名次快照，避免结果表 oo/τc_rank 全是 —。
    # 本路径刚跑过整池 attach/persist，heal=False 避免重复刷立方。
    try:
        stamp_live_ranks(list(item_by_code.values()), paper=paper, heal=False)
    except Exception:  # noqa: BLE001
        logger.debug("stamp held ranks after score pool failed", exc_info=True)

    _mark_predictability_tier_exits(
        item_by_code,
        force_exit_held=list(fit_meta.get("force_exit_held") or []),
        allowed_tiers=list(fit_meta.get("allowed_tiers") or []),
    )
    _mark_predictability_tier_blocks(
        item_by_code,
        tradable_codes=fit_meta.get("tradable_codes"),
        allowed_tiers=list(fit_meta.get("allowed_tiers") or []),
        gate_ok=fit_meta.get("gate_ok", True),
    )

    prices: Dict[str, float] = {}
    opens: Dict[str, float] = {}
    for code in item_by_code:
        px = _quote_px(code, offline_only=price_offline)
        if px is not None and px > 0:
            prices[code] = float(px)
        opx = _quote_open_px(code, offline_only=price_offline)
        if opx is not None and opx > 0:
            opens[code] = float(opx)
    if open_replay:
        # 9:30 成交价=开盘价，fuse 的 rot 为 0，对照的是开盘预估而不是现价剩余。
        prices = {c: float(px) for c, px in opens.items() if px and float(px) > 0}

    rules = paper.get("rules") if isinstance(paper.get("rules"), dict) else {}
    raw_mto = rules.get("max_turnover_pct", rules.get("max_turnover"))
    max_turnover_pct = None
    if raw_mto is not None and raw_mto != "":
        try:
            max_turnover_pct = float(raw_mto)
        except (TypeError, ValueError):
            max_turnover_pct = None
    from core.paper.rebalance.cash_reserve import resolve_min_cash_pct

    from core.market.calendar import resolve_session_date

    plan = plan_rank_lot_day(
        scored=list(item_by_code.values()),
        holdings=list(paper.get("holdings") or []),
        cash=cash_before,
        prices=prices,
        opens=opens,
        cfg=rl_cfg,
        max_turnover_pct=max_turnover_pct,
        min_cash_pct=resolve_min_cash_pct(rules),
        sentiment_as_of=resolve_session_date(),
        # 手动执行复现开盘信息集。自动路径不传，仍跟 live_fill_clock。
        sentiment_clock="09:30" if open_replay else None,
    )
    oos_excluded = sum(
        1
        for s in (plan.get("skips") or [])
        if "OOS" in str(s.get("reason") or "")
    )

    decisions: List[dict] = []
    buy_trades: List[dict] = []
    sell_trades: List[dict] = []
    report: List[dict] = []
    skips: List[dict] = list(plan.get("skips") or [])

    held_sh_of = {
        str(h.get("stock_code") or "").strip(): float(h.get("shares") or 0)
        for h in (paper.get("holdings") or [])
        if isinstance(h, dict)
    }

    def _base_row(code: str, src: Optional[dict] = None) -> dict:
        item = item_by_code.get(code) or {}
        name = _resolve_report_name(
            code, item=item, paper=paper, name_by_code=name_by_code
        )
        px = prices.get(code)
        opx = opens.get(code)
        row = {
            "stock_code": code,
            "stock_name": name,
            "matrix_mode": True,
            "price": px,
            "day_open": opx,
            **_row_score_payload(
                item, src, rl_cfg, open_px=opx, price_tau=px
            ),
        }
        label = str((plan.get("sentiment_labels") or {}).get(code) or "").strip()
        if label:
            row["sentiment_label"] = label
        basis = str((plan.get("sentiment_basis") or {}).get(code) or "").strip()
        if basis:
            row["sentiment_basis"] = basis
        return row

    def _leg_row(leg: dict, *, side: str) -> dict:
        code = str(leg.get("stock_code") or "")
        act = str(leg.get("action") or "")
        held_sh = float(held_sh_of.get(code) or 0)
        shares = float(leg.get("shares") or 0)
        px = prices.get(code)
        row = {
            **_base_row(code, leg),
            "action": act,
            "reason": leg.get("reason"),
            "execute": True,
            "side": side,
            "shares": shares,
            "amount": round(shares * float(px), 2) if px else None,
        }
        if side == "buy":
            row["decision"] = "买入" if act == ACTION_OPEN else "加仓"
            row["old_shares"] = held_sh
            row["new_shares"] = held_sh + shares
            row["shares_change"] = shares
        else:
            row["decision"] = "减仓" if act == ACTION_REDUCE else "卖出"
            row["old_shares"] = held_sh
            row["new_shares"] = max(0.0, held_sh - shares)
            row["shares_change"] = -shares
        return row

    def _status_row(
        src: dict,
        *,
        decision: str,
        held_sh: float,
        new_sh: Optional[float] = None,
    ) -> dict:
        code = str(src.get("stock_code") or "")
        ns = float(held_sh if new_sh is None else new_sh)
        row = {
            **_base_row(code, src),
            "action": src.get("action"),
            "reason": src.get("reason"),
            "execute": False,
            "decision": decision,
            "old_shares": held_sh,
            "new_shares": ns,
            "shares_change": ns - held_sh,
            "shares": held_sh,
        }
        if src.get("hard_reject"):
            row["hard_reject"] = True
            why = str(src.get("reject_reason") or src.get("reason") or "").strip()
            if why:
                row["reject_reason"] = why
        if src.get("sentiment_prior"):
            row["sentiment_prior"] = src.get("sentiment_prior")
        if src.get("sentiment_gate"):
            row["sentiment_gate"] = True
        return row

    for leg in plan.get("sells") or []:
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        if px is None:
            skips.append({"stock_code": code, "reason": "无有效报价"})
            continue
        trade = {
            **leg,
            "price": px,
            "amount": round(float(leg.get("shares") or 0) * px, 2),
            "dry_run": bool(dry_run),
        }
        sell_trades.append(trade)
        report.append(_leg_row(leg, side="sell"))
        decisions.append({"stock_code": code, **leg})

    for leg in plan.get("buys") or []:
        if open_replay:
            continue
        code = str(leg.get("stock_code") or "")
        px = prices.get(code)
        if px is None:
            skips.append({"stock_code": code, "reason": "无有效报价"})
            continue
        trade = {
            **leg,
            "price": px,
            "amount": round(float(leg.get("shares") or 0) * px, 2),
            "dry_run": bool(dry_run),
        }
        buy_trades.append(trade)
        report.append(_leg_row(leg, side="buy"))
        decisions.append({"stock_code": code, "action": leg.get("action"), **leg})

    reported = {
        str(r.get("stock_code") or "").strip()
        for r in report
        if str(r.get("stock_code") or "").strip()
    }

    for sk in skips:
        code = str(sk.get("stock_code") or "").strip()
        if not code or code in reported:
            continue
        held_sh = float(held_sh_of.get(code) or 0)
        report.append(_status_row(sk, decision="跳过", held_sh=held_sh))
        reported.add(code)

    report.sort(
        key=lambda r: (
            0 if str(r.get("side") or "") == "sell" else 1,
            str(r.get("stock_code") or ""),
        )
    )

    apply_skips: List[dict] = []
    if not dry_run and (buy_trades or sell_trades):
        applied = _apply_matrix_trades(paper, sell_trades, buy_trades)
        sell_trades = list(applied.get("sell_trades") or [])
        buy_trades = list(applied.get("buy_trades") or [])
        apply_skips = list(applied.get("apply_skips") or [])
        if apply_skips:
            skips.extend(apply_skips)
        # 落账后刷新净值摘要
        summary = mark_to_market(paper) or summary
        cash = float(summary.get("cash") or paper.get("cash") or cash)

    buy_amt = sum(float(t.get("amount") or 0) for t in buy_trades)
    sell_amt = sum(float(t.get("amount") or 0) for t in sell_trades)
    empty = None
    empty_detail = None
    floor_val = _f(rl_cfg.get("cash_floor")) or 0.0
    cash_skips = [
        s
        for s in skips
        if "地板" in str(s.get("reason") or "") or "现金不足" in str(s.get("reason") or "")
    ]
    if not buy_trades and not sell_trades:
        if cash_skips or (floor_val > 0 and cash_before + 1e-6 < floor_val):
            empty = "cash_below_floor"
            empty_detail = (
                f"现金 {cash_before:,.0f} < 地板 {floor_val:,.0f}"
                if floor_val > 0
                else f"现金 {cash_before:,.0f} 不足买入"
            )
        elif not scored and not held_codes:
            empty = "empty_book"
        else:
            empty = "no_executable_changes"

    by_action: Dict[str, int] = {}
    for d in decisions:
        a = str(d.get("action") or "")
        by_action[a] = by_action.get(a, 0) + 1

    if open_replay:
        note = "09:30 开盘复现 · 预估分 · 不含开仓/加仓 · 不写账"
    else:
        note = (
            "观察池 + rank_lots 预演（未写账）"
            if dry_run
            else "观察池 + rank_lots 落账"
        )
    strategy_id = paper.get("strategy_id")
    strategy_label = None
    if strategy_id:
        try:
            from core.strategy import get_strategy_spec

            strategy_label = get_strategy_spec(str(strategy_id)).get("label")
        except Exception:  # noqa: BLE001
            logger.debug("strategy_label resolve failed", exc_info=True)
    return {
        "success": True,
        "ok": True,
        "mode": "watching_matrix",
        "dry_run": bool(dry_run),
        "matrix_mode": True,
        "offline_only": use_offline,
        "top_k": k,
        "min_score": rl_cfg.get("fuse_enter"),
        "fuse_enter": rl_cfg.get("fuse_enter"),
        "fuse_enter_pct": round(float(rl_cfg.get("fuse_enter") or 0) * 100.0, 4),
        "observation_pool_count": len(codes),
        "scored_count": len(scored),
        "rejected": rejected[:40],
        "rejected_count": len(rejected),
        "pool_meta": pool_meta,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "rebalance_report": report,
        "risk_budget_skips": skips,
        "apply_skips": apply_skips,
        "summary": summary,
        "cash_impact": {
            "buy_amount": round(buy_amt, 2),
            "sell_amount": round(sell_amt, 2),
            "net_cash": round(sell_amt - buy_amt, 2),
            "cash_before": cash_before,
            "cash_after": float(paper.get("cash") or cash) if not dry_run else None,
            "cash_floor": rl_cfg.get("cash_floor"),
            "turnover_pct": round(
                (buy_amt + sell_amt) / equity * 100.0, 2
            )
            if equity > 0
            else None,
        },
        "empty_reason": empty,
        "empty_detail": empty_detail,
        "path_matrix": {
            "enabled": True,
            "mode": "rank_lots",
            "oos_excluded": oos_excluded,
            "cfg": {
                "fuse_enter": rl_cfg.get("fuse_enter"),
                "fuse_strong": rl_cfg.get("fuse_strong"),
                "cash_floor": rl_cfg.get("cash_floor"),
                "holdings_mv_cap": rl_cfg.get("holdings_mv_cap"),
                "stock_mv_cap": rl_cfg.get("stock_mv_cap"),
                "lot_base_amount": rl_cfg.get("lot_base_amount"),
                "lot_strong_amount": rl_cfg.get("lot_strong_amount"),
                "fusion_w_oo": rl_cfg.get("fusion_w_oo"),
                "fusion_w_oc": rl_cfg.get("fusion_w_oc"),
            },
            "by_action": by_action,
        },
        "note": note,
        "open_replay": bool(open_replay),
        "strategy_id": strategy_id,
        "strategy_label": strategy_label,
        "ops_report": {
            "strategy_id": strategy_id,
            "strategy_label": strategy_label,
            "note": (
                "09:30 开盘复现"
                if open_replay
                else (
                    "watching_matrix 预演"
                    if dry_run
                    else "watching_matrix 落账"
                )
            ),
            "matrix_by_action": by_action,
            "matrix_mode": "rank_lots",
        },
        "confirm_supported": not bool(open_replay),
    }


__all__ = ["simulate_watching_matrix_preview"]
