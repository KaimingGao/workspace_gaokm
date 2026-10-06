#!/usr/bin/env python3
"""全量 Agent 黄金回归：mock Skills + 11 case LLM 对照（需 DASHSCOPE_API_KEY）。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.env import load_env_file  # noqa: E402
from evals.run_checklist import main  # noqa: E402


if __name__ == "__main__":
    load_env_file(os.path.join(ROOT, ".env"))
    extra = [a for a in sys.argv[1:] if a not in ("--mock", "--with-agent")]
    raise SystemExit(main(["--mock", "--with-agent", *extra]))
