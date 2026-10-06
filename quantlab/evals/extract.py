"""从 Skill JSON 结果中抽取关键数字 / 字段，供 checklist 与 Agent 对齐。"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


# 优先展示的字段（出现则进 checklist）
PRIORITY_KEYS = (
    "stock_code",
    "stock_name",
    "price",
    "price_raw",
    "change",
    "change_raw",
    "change_amount",
    "score",
    "hard_reject",
    "data_source",
    "pattern",
    "patterns",
    "signal",
    "action",
    "stance",
    "pe",
    "pb",
    "roe",
    "pe_ttm",
    "relative_strength",
    "relative_stance",
    "stock_return_pct",
    "benchmark_return_pct",
    "excess_return_pct",
    "vs_index",
    "horizon_days",
    "summary",
    "error",
)


def _get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def path_ok(obj: Any, path: str) -> bool:
    val = _get_path(obj, path)
    if val is None:
        return False
    if val is False and path.endswith("success"):
        return False
    if path.endswith("success"):
        return bool(val)
    return True


def flatten_checklist(result: Dict[str, Any], prefix: str = "") -> List[Tuple[str, Any]]:
    """扁平化关键字段为 (path, value)。"""
    rows: List[Tuple[str, Any]] = []

    def walk(node: Any, path: str, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(node, dict):
            for key in PRIORITY_KEYS:
                if key in node and node[key] is not None:
                    p = f"{path}.{key}" if path else key
                    rows.append((p, node[key]))
            # 常见列表容器
            # 嵌套 metrics / latest
            for nest_key in ("metrics", "latest", "fundamentals"):
                if nest_key in node and isinstance(node[nest_key], dict):
                    walk(
                        node[nest_key],
                        f"{path}.{nest_key}" if path else nest_key,
                        depth + 1,
                    )
            for list_key in (
                "items",
                "stocks",
                "holdings",
                "news",
                "peers",
                "observation_pool",
                "candles",
            ):
                if list_key in node and isinstance(node[list_key], list):
                    for i, item in enumerate(node[list_key][:5]):
                        walk(
                            item,
                            f"{path}.{list_key}[{i}]" if path else f"{list_key}[{i}]",
                            depth + 1,
                        )
            if "success" in node and (not path or path.count(".") < 1):
                p = f"{path}.success" if path else "success"
                if not any(r[0] == p for r in rows):
                    rows.append((p, node.get("success")))
        elif isinstance(node, list):
            for i, item in enumerate(node[:5]):
                walk(item, f"{path}[{i}]", depth + 1)

    walk(result, prefix)
    # 去重保序
    seen = set()
    out: List[Tuple[str, Any]] = []
    for k, v in rows:
        if k in seen:
            continue
        seen.add(k)
        out.append((k, v))
    return out


def collect_verify_tokens(result: Dict[str, Any]) -> List[str]:
    """
    从结果中收集应在 Agent 回复中出现的可核对片段
   （价格、代码、评分等）。
    """
    tokens: List[str] = []
    for path, val in flatten_checklist(result):
        if val is None or val is True or val is False:
            continue
        if path.endswith("error"):
            continue
        if isinstance(val, (int, float)):
            # 原始数 + 常见格式化
            tokens.append(str(val))
            if isinstance(val, float):
                tokens.append(f"{val:.2f}")
                tokens.append(f"{val:.1f}")
        elif isinstance(val, str):
            s = val.strip()
            if not s or len(s) > 40:
                continue
            # 跳过纯叙述
            if re.fullmatch(r"[\d.%+\-]+", s) or re.search(r"\d", s):
                tokens.append(s)
            elif path.endswith(("stock_code", "stock_name", "data_source", "pattern")):
                tokens.append(s)
    # 去重
    seen = set()
    out: List[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def check_required(
    bundled: Dict[str, Any],
    required_paths: Iterable[str],
    min_items: Optional[int] = None,
) -> List[str]:
    """返回未满足的 required_paths 列表。"""
    missing: List[str] = []
    for path in required_paths or []:
        if not path_ok(bundled, path):
            missing.append(path)
    if min_items is not None:
        items = None
        for key in ("stocks", "items", "observation_pool"):
            cand = bundled.get(key)
            if isinstance(cand, list):
                items = cand
                break
        if not isinstance(items, list):
            for v in bundled.values():
                if not isinstance(v, dict):
                    continue
                for key in ("stocks", "items", "observation_pool"):
                    if isinstance(v.get(key), list):
                        items = v[key]
                        break
                if items is not None:
                    break
        if not isinstance(items, list) or len(items) < min_items:
            missing.append(f"stocks/items(len>={min_items})")
    return missing


def numbers_found_in_text(tokens: List[str], text: str) -> Tuple[List[str], List[str]]:
    """返回 (hit, miss)。字符串匹配；浮点允许去掉尾零后匹配。"""
    hit: List[str] = []
    miss: List[str] = []
    if not text:
        return hit, list(tokens)
    for t in tokens:
        if not t:
            continue
        if t in text:
            hit.append(t)
            continue
        # 尝试规范化小数
        try:
            f = float(t.replace("%", "").replace("+", ""))
            variants = {t, f"{f:g}", f"{f:.2f}", f"{f:.1f}", f"{f:.0f}"}
            if any(v and v in text for v in variants):
                hit.append(t)
                continue
        except ValueError:
            pass
        miss.append(t)
    return hit, miss
