#!/usr/bin/env python3
"""前端 JS 静态门禁：拦住会让整页模块加载失败的语法问题。

检查项：
1. ``??`` 与 ``||`` / ``&&`` 在同一表达式中未加括号混用（ES 语法错误）
2. 必要文件存在、chat 页脚本顺序（chat_boot 先于 CDN marked）
3. 可选：若本机有 esbuild，则做真实 parse/bundle

用法::

    python3 scripts/check_frontend_js.py
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "web" / "static"
JS_DIR = STATIC / "js"
TEMPLATES = STATIC / "templates"

REQUIRED_JS = (
    "app.js",
    "js/chat_boot.js",
    "js/chat.js",
    "js/shared.js",
    "js/paper.js",
    "js/quant.js",
    "js/results.js",
    "js/evals.js",
)

EXPORT_EXPECT = {
    "js/chat.js": "export function initChat",
    "js/paper.js": "export function initPaper",
    "js/quant.js": "export function initQuant",
    "js/results.js": "export function initResults",
    "js/evals.js": "export function initEvals",
}


def _strip_strings(expr: str) -> str:
    return re.sub(
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`',
        '""',
        expr,
    )


def _remove_parens(s: str) -> str:
    while True:
        n = re.sub(r"\([^()]*\)", "", s)
        if n == s:
            return s
        s = n


def has_illegal_nullish_logic_mix(expr: str) -> bool:
    """ES：同一 ShortCircuit 表达式不能无括号混用 ?? 与 ||/&&。

    在每一层括号内独立检查：先递归子表达式，再看本层（去掉子括号后）是否同时出现。
    """
    e = _strip_strings(expr)
    e = re.sub(r"//.*?$", "", e, flags=re.M)
    e = re.sub(r"/\*.*?\*/", "", e, flags=re.S)

    def check(s: str) -> bool:
        i = 0
        n = len(s)
        while i < n:
            if s[i] == "(":
                depth = 1
                j = i + 1
                while j < n and depth:
                    if s[j] == "(":
                        depth += 1
                    elif s[j] == ")":
                        depth -= 1
                    j += 1
                if check(s[i + 1 : j - 1]):
                    return True
                i = j
            else:
                i += 1
        flat = _remove_parens(s)
        if "??" not in flat:
            return False
        return bool(re.search(r"\|\||&&", flat))

    return check(e)


def extract_template_exprs(source: str) -> List[str]:
    out: List[str] = []
    i = 0
    while True:
        j = source.find("${", i)
        if j < 0:
            break
        depth = 0
        k = j + 2
        start = k
        while k < len(source):
            c = source[k]
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    out.append(source[start:k])
                    k += 1
                    break
                depth -= 1
            elif c in ("\"", "'", "`"):
                quote = c
                k += 1
                while k < len(source) and source[k] != quote:
                    if source[k] == "\\":
                        k += 1
                    k += 1
            k += 1
        else:
            break
        i = k
    return out


def find_nullish_mix_issues(path: Path, source: str) -> List[str]:
    issues: List[str] = []
    for expr in extract_template_exprs(source):
        if has_illegal_nullish_logic_mix(expr):
            snippet = re.sub(r"\s+", " ", expr.strip())[:120]
            issues.append(f"{path.relative_to(ROOT)}: template `${{...}}` 非法混用 ??/||/&&: {snippet}")
    for lineno, line in enumerate(source.splitlines(), 1):
        if "${" in line:
            continue
        if has_illegal_nullish_logic_mix(line):
            issues.append(
                f"{path.relative_to(ROOT)}:{lineno}: 非法混用 ??/||/&&: {line.strip()[:120]}"
            )
    return issues


def check_panel_div_balance() -> List[str]:
    """partials/*_panel.html 的 <div> 必须成对，否则会把后续 Tab 嵌进量化面板。"""
    issues: List[str] = []
    for path in sorted((STATIC / "partials").glob("*_panel.html")):
        text = path.read_text(encoding="utf-8")
        opens = len(re.findall(r"<div\b", text, flags=re.I))
        closes = len(re.findall(r"</div>", text, flags=re.I))
        if opens != closes:
            issues.append(
                f"{path.relative_to(ROOT)}: <div> 未闭合 (open={opens}, close={closes})"
            )
    return issues


def check_required_files() -> List[str]:
    issues: List[str] = []
    for rel in REQUIRED_JS:
        p = STATIC / rel
        if not p.is_file():
            issues.append(f"缺少文件: web/static/{rel}")
            continue
        expect = EXPORT_EXPECT.get(rel)
        if expect and expect not in p.read_text(encoding="utf-8"):
            issues.append(f"{rel} 缺少导出: {expect}")
    boot = (JS_DIR / "chat_boot.js").read_text(encoding="utf-8")
    if "fetch(\"/api/chat\"" not in boot and "fetch('/api/chat'" not in boot:
        issues.append("js/chat_boot.js 未调用 /api/chat")
    return issues


def check_chat_html_script_order() -> List[str]:
    issues: List[str] = []
    chat = (TEMPLATES / "chat.html").read_text(encoding="utf-8")
    boot_i = chat.find("chat_boot.js")
    marked_i = chat.find("marked/marked.min.js")
    app_i = chat.find("/static/app.js")
    if boot_i < 0:
        issues.append("chat.html 未引入 chat_boot.js")
    if marked_i >= 0 and boot_i >= 0 and boot_i > marked_i:
        issues.append("chat.html: chat_boot.js 必须在 CDN marked 之前，避免 CDN 卡住导致对话无响应")
    if "marked.min.js" in chat and "defer" not in chat[chat.find("marked") : chat.find("marked") + 120]:
        # marked script tag should prefer defer
        m = re.search(r"<script[^>]*marked/marked\.min\.js[^>]*>", chat)
        if m and "defer" not in m.group(0):
            issues.append("chat.html: marked CDN 建议带 defer，避免阻塞")
    if app_i >= 0 and boot_i >= 0 and boot_i > app_i:
        issues.append("chat.html: chat_boot.js 应在 app.js module 之前")
    return issues


def find_esbuild() -> Optional[str]:
    env = os.environ.get("ESBUILD")
    if env and Path(env).is_file():
        return env
    which = shutil.which("esbuild")
    if which:
        return which
    for cand in (
        Path("/tmp/package/bin/esbuild"),
        Path("/tmp/esbuild-pkg/package/bin/esbuild"),
    ):
        if cand.is_file():
            return str(cand)
    return None


def check_with_esbuild(esbuild: str) -> List[str]:
    issues: List[str] = []
    targets = [STATIC / "app.js"] + sorted(JS_DIR.glob("*.js"))
    for path in targets:
        rel = path.relative_to(STATIC)
        proc = subprocess.run(
            [esbuild, str(path), "--bundle", "--outfile=/dev/null", "--format=esm"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip().splitlines()
            brief = " | ".join(msg[:4]) if msg else f"exit {proc.returncode}"
            issues.append(f"esbuild 解析失败 {rel}: {brief}")
    return issues


def run_checks(*, use_esbuild: bool = True) -> Tuple[List[str], List[str]]:
    """返回 (errors, notes)。"""
    errors: List[str] = []
    notes: List[str] = []

    errors.extend(check_required_files())
    errors.extend(check_chat_html_script_order())
    errors.extend(check_panel_div_balance())

    for path in sorted(STATIC.rglob("*.js")):
        source = path.read_text(encoding="utf-8")
        errors.extend(find_nullish_mix_issues(path, source))

    if use_esbuild:
        esbuild = find_esbuild()
        if esbuild:
            errors.extend(check_with_esbuild(esbuild))
            notes.append(f"esbuild OK via {esbuild}")
        else:
            notes.append("未找到 esbuild，已跳过 bundle 解析（启发式门禁仍生效）")
    return errors, notes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="前端 JS 静态门禁")
    parser.add_argument("--no-esbuild", action="store_true", help="跳过 esbuild")
    args = parser.parse_args(argv)
    errors, notes = run_checks(use_esbuild=not args.no_esbuild)
    for n in notes:
        print(f"[note] {n}")
    if errors:
        print(f"[FAIL] {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("[OK] frontend JS checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
