"""概念板块成分股图谱：核查公告 code × 概念成员交集。"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_CONCEPT_HINTS = (
    "机器人",
    "人形机器人",
    "半导体",
    "芯片",
    "人工智能",
    "AI",
    "光通信",
    "算力",
)


def _norm_code(raw: Any) -> str:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-6:] if len(digits) >= 6 else ""


def fetch_concept_members(concept: str, *, limit: int = 300) -> List[str]:
    """拉取东财概念板块成分股（6 位代码）。"""
    name = str(concept or "").strip()
    if not name:
        return []
    from skills.common.ak_lock import import_akshare

    ak = import_akshare()
    fn = getattr(ak, "stock_board_concept_cons_em", None)
    if not fn:
        return []
    try:
        df = fn(symbol=name)
        if df is None or getattr(df, "empty", True):
            return []
        out: List[str] = []
        for row in df.to_dict(orient="records"):
            code = _norm_code(row.get("代码") or row.get("code") or row.get("symbol"))
            if code:
                out.append(code)
            if len(out) >= limit:
                break
        return out
    except Exception:
        logger.debug("concept cons failed for %s", name, exc_info=True)
        return []


def build_code_concept_index(
    concepts: List[str],
    *,
    max_concepts: int = 8,
    max_members_per_concept: int = 300,
    use_cache: bool = True,
) -> Dict[str, List[str]]:
    """code -> 所属概念列表（优先日更磁盘缓存，限量网络拉取）。"""
    from core.concept_graph_store import (
        load_concept_graph_cache,
        merge_concept_into_index,
        save_concept_graph_cache,
    )

    concepts_ordered = list(
        dict.fromkeys(str(c or "").strip() for c in (concepts or []) if str(c or "").strip())
    )
    index: Dict[str, List[str]] = {}
    concept_members: Dict[str, List[str]] = {}
    cached_payload = None
    if use_cache:
        cached_payload, _meta = load_concept_graph_cache()
        if cached_payload:
            index = dict(cached_payload.get("code_index") or {})
            concept_members = dict(cached_payload.get("concepts") or {})

    seen: Set[str] = set()
    fetched = 0
    fetch_cap = max(0, int(max_concepts))
    if fetch_cap <= 0:
        return index
    for concept in concepts_ordered:
        if concept in seen:
            continue
        seen.add(concept)
        if fetched >= fetch_cap:
            break
        if use_cache and concept in concept_members and concept_members.get(concept):
            index = merge_concept_into_index(index, concept, concept_members[concept])
            continue
        codes = fetch_concept_members(concept, limit=max_members_per_concept)
        if codes:
            concept_members[concept] = codes
            index = merge_concept_into_index(index, concept, codes)
            fetched += 1

    if use_cache and (fetched > 0 or cached_payload):
        try:
            save_concept_graph_cache(concepts=concept_members, code_index=index)
        except Exception:
            logger.debug("concept graph cache save failed", exc_info=True)

    return index


def enrich_regulatory_with_concepts(
    regulatory: Dict[str, Any],
    *,
    concept_hints: Optional[List[str]] = None,
    max_concepts: int = 8,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """为监管扫描结果补全 code_concepts / penalty_concepts。"""
    reg = dict(regulatory or {})
    penalty_codes = [
        _norm_code(c) for c in (reg.get("penalty_codes") or []) if _norm_code(c)
    ]
    tags = list(reg.get("concept_tags") or [])
    concepts = list(
        dict.fromkeys(
            [
                *(concept_hints or []),
                *tags,
                *DEFAULT_CONCEPT_HINTS,
            ]
        )
    )[: max(1, int(max_concepts) + 4)]

    code_index = build_code_concept_index(
        concepts,
        max_concepts=max_concepts,
        use_cache=use_cache,
    )
    penalty_concepts: Set[str] = set()
    penalty_graph: Dict[str, List[str]] = {}
    for code in penalty_codes:
        member_of = list(code_index.get(code) or [])
        if member_of:
            penalty_graph[code] = member_of
            penalty_concepts.update(member_of)

    reg["code_concepts"] = code_index
    reg["penalty_concepts"] = sorted(penalty_concepts)
    reg["penalty_concept_graph"] = penalty_graph
    reg["concept_graph_built"] = bool(code_index)
    return reg


def stock_in_penalty_concepts(
    stock_code: Optional[str],
    regulatory: Optional[dict],
    *,
    fallback_tags: Optional[List[str]] = None,
) -> bool:
    """个股是否落在核查 code 的概念图谱内。"""
    if not stock_code or not isinstance(regulatory, dict):
        return False
    code = _norm_code(stock_code)
    if not code:
        return False
    graph = regulatory.get("penalty_concept_graph") or {}
    if code in graph:
        return True
    code_concepts = regulatory.get("code_concepts") or {}
    stock_concepts = set(code_concepts.get(code) or [])
    if not stock_concepts:
        return False
    penalty = set(regulatory.get("penalty_concepts") or [])
    if penalty and stock_concepts & penalty:
        return True
    tags = set(fallback_tags or [])
    return bool(stock_concepts & tags)
