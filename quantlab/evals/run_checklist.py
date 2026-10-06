#!/usr/bin/env python3
"""
QuantLab 黄金用例回归：Skills 关键数字 checklist（可选对照 Agent 回复）。

用法（在 quantlab/ 目录下）:
  python3 evals/run_checklist.py
  python3 evals/run_checklist.py --mock
  python3 evals/run_checklist.py --case buy_kuaishou
  python3 evals/run_checklist.py --mock --with-agent --case advise_moutai
  python3 evals/run_checklist.py --with-agent --json-out evals/last_run.json

说明:
  - 默认只跑 Skills，打印关键字段与「应对齐到回复中的数字」。
  - --mock 对带 mock 配置的 case 注入离线日线/行情（index/backtest 可离线通过）。
  - --with-agent 会再调 LLM，检查免责声明 + 数字是否出现在回复中（弱匹配）。
  - 建议改 prompts / Skill 后先跑本脚本，再人工扫一眼买入类长文结构。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.env import load_env_file  # noqa: E402
from evals.extract import (  # noqa: E402
    check_required,
    collect_verify_tokens,
    flatten_checklist,
    numbers_found_in_text,
)
from agent.routing import prepare_tool_params, wants_position_stance, is_quant_question  # noqa: E402
from evals.mock_context import apply_case_mocks  # noqa: E402

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_cases.json")


def load_cases() -> List[dict]:
    with open(CASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("cases") or [])


def filter_quant_cases(cases: List[dict]) -> List[dict]:
    return [c for c in cases if str(c.get("id", "")).startswith("quant_")]


def run_skills(case: dict, *, use_mock: bool = False) -> Dict[str, Any]:
    from agent.registry import create_handlers

    handlers = create_handlers()
    bundled: Dict[str, Any] = {}
    skill_runs: List[dict] = []
    mock_cfg = case.get("mock") if use_mock else None

    with apply_case_mocks(mock_cfg):
        for step in case.get("skills") or []:
            name = step["name"]
            params = step.get("parameters") or {}
            handler = handlers.get(name)
            t0 = time.time()
            if handler is None:
                payload = {"success": False, "error": f"未知工具: {name}"}
                raw = json.dumps(payload, ensure_ascii=False)
            else:
                raw = handler.execute({"name": name, "parameters": params})
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"success": False, "error": "非 JSON 结果", "raw": raw[:200]}
            elapsed = round(time.time() - t0, 2)
            bundled[name] = payload
            skill_runs.append(
                {
                    "name": name,
                    "parameters": params,
                    "elapsed_sec": elapsed,
                    "result": payload,
                }
            )

    # 单 skill 时把结果提升到顶层，方便 required_paths 写 success/price
    if len(skill_runs) == 1:
        only = skill_runs[0]["result"]
        if isinstance(only, dict):
            for k, v in only.items():
                bundled.setdefault(k, v)

    return {"bundled": bundled, "skill_runs": skill_runs, "used_mock": bool(mock_cfg)}


def check_routing_expect(case: dict) -> List[str]:
    """校验 golden case 的 routing 预期（离线，不依赖 LLM）。"""
    expect = case.get("routing_expect")
    if not expect:
        return []
    question = case.get("question") or ""
    failures: List[str] = []
    cid = case["id"]

    if "wants_position_stance" in expect:
        got = wants_position_stance(question)
        want = bool(expect["wants_position_stance"])
        if got != want:
            failures.append(
                f"{cid}/routing: wants_position_stance={got} expected={want}"
            )

    if expect.get("position_include_stance"):
        params = prepare_tool_params("position", {}, question)
        if not params.get("include_stance"):
            failures.append(f"{cid}/routing: position.include_stance not set")

    if "is_quant_question" in expect:
        got = is_quant_question(question)
        want = bool(expect["is_quant_question"])
        if got != want:
            failures.append(f"{cid}/routing: is_quant_question={got} expected={want}")

    if expect.get("quant_task"):
        params = prepare_tool_params("quant", {}, question)
        if params.get("task") != expect["quant_task"]:
            failures.append(
                f"{cid}/routing: quant.task={params.get('task')!r} expected={expect['quant_task']!r}"
            )

    return failures


def evaluate_case_skills(case: dict, run: dict) -> tuple:
    """评估 Skills 结果，返回 (failures, verify_tokens)。"""
    failures: List[str] = []
    cid = case["id"]
    all_tokens: List[str] = []

    for step in run["skill_runs"]:
        name = step["name"]
        result = step["result"]
        if not result.get("success"):
            failures.append(f"{cid}/{name}: success=false ({result.get('error')})")
        all_tokens.extend(collect_verify_tokens(result))

    seen = set()
    uniq_tokens: List[str] = []
    for t in all_tokens:
        if t in seen:
            continue
        seen.add(t)
        uniq_tokens.append(t)

    missing = check_required(
        run["bundled"],
        case.get("required_paths") or [],
        case.get("min_items"),
    )
    if missing:
        failures.append(f"{cid}: missing {missing}")

    return failures, uniq_tokens


def print_case_skills(case: dict, run: dict):
    """打印 checklist，返回 (failures, verify_tokens)。"""
    cid = case["id"]
    print()
    print("=" * 64)
    print(f"CASE {cid}  |  {case.get('intent', '')}")
    print(f"Q: {case.get('question')}")
    print("-" * 64)

    for step in run["skill_runs"]:
        name = step["name"]
        result = step["result"]
        ok = bool(result.get("success"))
        flag = "OK" if ok else "FAIL"
        print(f"[{name}] {flag}  ({step['elapsed_sec']}s)")
        rows = flatten_checklist(result)
        if not rows:
            print("  (无优先字段)")
        for path, val in rows[:24]:
            print(f"  - {path}: {val}")

    failures, uniq_tokens = evaluate_case_skills(case, run)
    missing = [f for f in failures if "missing" in f]
    if missing:
        print(f"  !! required 未满足: {missing[0].split('missing ', 1)[-1]}")

    print("应对齐到 Agent 回复的关键片段（抽检用）:")
    if uniq_tokens:
        print("  " + " · ".join(uniq_tokens[:20]))
    else:
        print("  (无)")

    return failures, uniq_tokens


def run_agent(case: dict, tokens: List[str]) -> Dict[str, Any]:
    from agent.agent import Agent
    from agent.prompts import DISCLAIMER

    agent = Agent()
    if not agent.llm.is_available():
        return {
            "skipped": True,
            "reason": agent.llm.get_last_error() or "LLM 不可用",
        }

    t0 = time.time()
    reply = agent.chat(case["question"])
    elapsed = round(time.time() - t0, 2)

    # 去掉 token footer 再比对
    body = reply
    if "\n\n---\n" in reply:
        body = reply.rsplit("\n\n---\n", 1)[0]

    must = list(case.get("agent_must_contain") or [])
    if DISCLAIMER and DISCLAIMER not in must:
        # 至少检查 disclaimer 关键词
        for frag in ("不保证收益", "不代客下单"):
            if frag not in must:
                must.append(frag)

    missing_phrases = [p for p in must if p not in body]
    hit, miss = numbers_found_in_text(tokens, body)

    return {
        "skipped": False,
        "elapsed_sec": elapsed,
        "reply": reply,
        "body": body,
        "missing_phrases": missing_phrases,
        "token_hit": hit,
        "token_miss": miss,
        "turn_usage": agent.get_last_turn_usage(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="QuantLab evals checklist")
    parser.add_argument("--case", help="只跑指定 case id")
    parser.add_argument(
        "--quant-only",
        action="store_true",
        help="只跑 id 以 quant_ 开头的 golden case",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="对 golden case 中带 mock 配置的 Skills 注入离线日线/行情",
    )
    parser.add_argument(
        "--with-agent",
        action="store_true",
        help="再跑 Agent，检查 disclaimer 与数字弱对齐",
    )
    parser.add_argument(
        "--json-out",
        help="把完整运行结果写入 JSON 文件",
    )
    parser.add_argument(
        "--presets",
        action="store_true",
        help="额外校验 daily preset 组合（quant_paper 等）",
    )
    parser.add_argument(
        "--core-paths",
        action="store_true",
        help="R5.6：跑北极星 KPI · PIT · 调仓硬拦黄金路径（可与 --mock 并用）",
    )
    args = parser.parse_args(argv)

    load_env_file(os.path.join(ROOT, ".env"))

    cases = load_cases()
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"未找到 case: {args.case}", file=sys.stderr)
            return 2
    if args.quant_only:
        cases = filter_quant_cases(cases)
        if not cases:
            print("未找到 quant_* case", file=sys.stderr)
            return 2

    report: Dict[str, Any] = {
        "cases": [],
        "failures": [],
        "with_agent": bool(args.with_agent),
        "use_mock": bool(args.mock),
        "quant_only": bool(args.quant_only),
    }
    all_failures: List[str] = []

    print(f"Loaded {len(cases)} case(s) from {CASES_PATH}")
    if args.mock:
        print("Mock mode: ON（仅对带 mock 字段的 case 生效）")
    if args.quant_only:
        print(f"Quant-only: ON（{len(cases)} quant_* case(s)）")

    for case in cases:
        routing_failures = check_routing_expect(case)
        if routing_failures:
            all_failures.extend(routing_failures)
            print()
            print("=" * 64)
            print(f"CASE {case['id']} routing FAIL")
            for f in routing_failures:
                print(f"  - {f}")

        run = run_skills(case, use_mock=args.mock)
        failures, tokens = print_case_skills(case, run)
        all_failures.extend(failures)

        entry: Dict[str, Any] = {
            "id": case["id"],
            "question": case.get("question"),
            "skills": run["skill_runs"],
            "verify_tokens": tokens,
            "routing_failures": routing_failures,
            "skill_failures": failures,
            "used_mock": run.get("used_mock"),
        }

        if args.with_agent:
            print("-" * 64)
            print("Agent 对照…")
            agent_res = run_agent(case, tokens)
            entry["agent"] = {
                k: v
                for k, v in agent_res.items()
                if k not in ("reply", "body")  # 全文另存
            }
            if agent_res.get("skipped"):
                print(f"  SKIP Agent: {agent_res.get('reason')}")
                if case.get("agent_required"):
                    all_failures.append(
                        f"{case['id']}/agent: required but skipped ({agent_res.get('reason')})"
                    )
            else:
                entry["agent"]["reply"] = agent_res.get("reply")
                print(f"  耗时 {agent_res.get('elapsed_sec')}s")
                mp = agent_res.get("missing_phrases") or []
                if mp:
                    print(f"  !! 缺少短语: {mp}")
                    all_failures.append(f"{case['id']}/agent phrases: {mp}")
                else:
                    print("  免责/必含短语: OK")
                hit = agent_res.get("token_hit") or []
                miss = agent_res.get("token_miss") or []
                print(f"  数字弱对齐: hit={len(hit)} miss={len(miss)}")
                if miss:
                    print(f"  miss 样例: {' · '.join(miss[:12])}")
                    # 数字 miss 只警告，不直接判整案失败（LLM 可能用「见上表」）
                    entry["agent"]["number_miss_warning"] = miss

        report["cases"].append(entry)

    if args.presets or not args.case:
        from evals.preset_check import check_daily_presets
        from evals.readme_check import check_readme_coverage

        preset_out = check_daily_presets()
        report["presets"] = preset_out
        if not preset_out.get("ok"):
            all_failures.extend(preset_out.get("failures") or [])

        readme_out = check_readme_coverage()
        report["readme"] = readme_out
        if not readme_out.get("ok"):
            all_failures.extend(readme_out.get("failures") or [])

    if args.core_paths or not args.case:
        from evals.core_golden_paths import run_all_core_paths

        core_out = run_all_core_paths()
        report["core_paths"] = core_out
        print()
        print(f"Core golden paths: {core_out.get('count')} · ok={core_out.get('ok')}")
        for p in core_out.get("paths") or []:
            print(f"  [{'OK' if p.get('ok') else 'FAIL'}] {p.get('id')}")
        if not core_out.get("ok"):
            all_failures.extend(core_out.get("failures") or ["core_paths failed"])

    report["failures"] = all_failures
    print()
    print("=" * 64)
    if all_failures:
        print(f"DONE with {len(all_failures)} issue(s):")
        for f in all_failures:
            print(f"  - {f}")
        code = 1
    else:
        print("DONE: Skills checklist 全部通过。")
        if args.with_agent:
            print("（Agent 数字 miss 仅为警告，需人工结合上下文确认）")
        code = 0

    if args.json_out:
        out_path = args.json_out
        if not os.path.isabs(out_path):
            out_path = os.path.join(ROOT, out_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"Wrote {out_path}")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
