#!/usr/bin/env python3
"""检查 daily_last_run.json，供 cron MAILTO / scripts/daily_check.sh 调用（P18.3）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.paths import DAILY_LAST_RUN_PATH  # noqa: E402


def check_daily_last_run(path: str | None = None, *, require_run: bool = False) -> dict:
    p = path or DAILY_LAST_RUN_PATH
    if not os.path.isfile(p):
        return {
            "success": True,
            "ok": not require_run,
            "empty": True,
            "path": p,
            "message": "尚无 daily 运行记录",
        }

    with open(p, encoding="utf-8") as f:
        data = json.load(f)

    if data.get("empty"):
        return {
            "success": True,
            "ok": not require_run,
            "empty": True,
            "path": p,
            "message": "daily 记录为空",
        }

    ok = bool(data.get("ok"))
    failures = list(data.get("failures") or [])
    return {
        "success": True,
        "ok": ok,
        "empty": False,
        "path": p,
        "preset": data.get("preset"),
        "finished_at": data.get("finished_at"),
        "failures": failures,
        "message": "daily 任务正常" if ok else "daily 任务存在失败",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查 daily_last_run.json")
    parser.add_argument("--path", default="", help="覆盖 daily_last_run.json 路径")
    parser.add_argument(
        "--require-run",
        action="store_true",
        help="无记录时也视为失败（严格 cron 模式）",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    out = check_daily_last_run(args.path or None, require_run=bool(args.require_run))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(out.get("message") or ("OK" if out.get("ok") else "FAIL"))
        if not out.get("ok") and out.get("failures"):
            for item in out["failures"][:5]:
                print(f"  - {item}")

    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
